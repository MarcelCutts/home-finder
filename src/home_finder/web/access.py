"""Owner-only access and bounded requests for the private dashboard."""

import asyncio
import math
import secrets
from collections import OrderedDict, deque
from time import monotonic
from typing import Final
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from home_finder.config import Settings

MAX_BODY_BYTES: Final = 64 * 1024
BODY_TIMEOUT_SECONDS: Final = 10.0
_SAFE_METHODS: Final = {"GET", "HEAD", "OPTIONS"}
_basic = HTTPBasic(auto_error=False)


class _WindowLimit:
    """Small process-local sliding window with bounded client bookkeeping."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.events: OrderedDict[str, deque[float]] = OrderedDict()

    def retry_after(self, key: str) -> int:
        events = self.events.get(key)
        if events is None:
            return 0
        now = monotonic()
        while events and events[0] <= now - 60:
            events.popleft()
        if not events:
            del self.events[key]
            return 0
        if len(events) >= self.limit:
            return max(1, math.ceil(events[0] + 60 - now))
        return 0

    def consume(self, key: str) -> int:
        retry = self.retry_after(key)
        if retry:
            return retry
        if key not in self.events:
            if len(self.events) >= 1024:
                self.events.popitem(last=False)
            self.events[key] = deque()
        self.events[key].append(monotonic())
        self.events.move_to_end(key)
        return 0


def _origin(value: str) -> tuple[str, str, int] | None:
    try:
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            return None
        return url.scheme, url.hostname, url.port or (443 if url.scheme == "https" else 80)
    except ValueError:
        return None


def valid_webhook_secret(request: Request, settings: Settings) -> bool:
    """Compare bytes so a malformed non-ASCII header cannot raise TypeError."""
    expected = settings.telegram_webhook_secret
    supplied = request.headers.get("x-telegram-bot-api-secret-token", "")
    return bool(expected) and secrets.compare_digest(supplied.encode(), expected.encode())


class OwnerAccessMiddleware:
    """Protect routes and mounted files; health and the secret-checked bot hook are separate."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self._failed_auth = _WindowLimit(10)
        self._mutations = _WindowLimit(30)
        # Serialize HTTP mutations, including callbacks. PR04 still owns the
        # complete DB boundary covering pipeline/CLI writers and unrelated reads.
        self._mutation_lock = asyncio.Lock()

    async def _owner_authorized(self, request: Request) -> bool:
        if len(request.headers.get("authorization", "")) > 4096:
            return False
        try:
            credentials = await _basic(request)
        except HTTPException:
            return False
        if credentials is None:
            return False
        username_ok = secrets.compare_digest(
            credentials.username.encode(), self.settings.web_owner_username.encode()
        )
        password_ok = secrets.compare_digest(
            credentials.password.encode(),
            self.settings.web_owner_password.get_secret_value().encode(),
        )
        return username_ok and password_ok

    def _same_origin(self, request: Request) -> bool:
        if request.headers.get("sec-fetch-site") == "cross-site":
            return False
        source = request.headers.get("origin")
        if source is None:
            source = request.headers.get("referer", "")
        expected = _origin(self.settings.web_base_url or str(request.base_url))
        return expected is not None and _origin(source) == expected

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await self.app(scope, receive, send)
            return

        request = Request(scope)

        async def private_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("Cache-Control", "no-store")
                headers.add_vary_header("Authorization")
            await send(message)

        async def reject(status: int, message: str, *, retry: int = 0) -> None:
            headers = {"Cache-Control": "no-store"}
            if status == 401:
                headers["WWW-Authenticate"] = 'Basic realm="Home Finder"'
            if retry:
                headers["Retry-After"] = str(retry)
            await JSONResponse({"error": message}, status_code=status, headers=headers)(
                scope, receive, private_send
            )

        # Route exemptions must use the server-parsed target, never a URL
        # reconstructed from the client-controlled Host header.
        if scope["path"] == "/health" and request.method in {"GET", "HEAD"}:
            await self.app(scope, receive, private_send)
            return

        webhook = scope["path"] == "/telegram/webhook" and request.method == "POST"
        client_key = request.client.host if request.client else "unknown"
        if webhook:
            if not self.settings.telegram_webhook_secret:
                await reject(404, "Webhook not configured")
                return
            authorized = valid_webhook_secret(request, self.settings)
        else:
            password = self.settings.web_owner_password.get_secret_value()
            if len(password) < 24 or not password.isascii():
                await reject(503, "Owner access is not configured")
                return
            authorized = await self._owner_authorized(request)
        if not authorized:
            # A peer may be a shared reverse proxy. Anonymous traffic must not
            # lock out valid owner credentials or authenticated Telegram hooks.
            retry = self._failed_auth.consume(client_key)
            if retry:
                await reject(429, "Too many authentication attempts", retry=retry)
                return
            await reject(403 if webhook else 401, "Authentication required")
            return

        if request.method in _SAFE_METHODS:
            await self.app(scope, receive, private_send)
            return
        if not webhook and not self._same_origin(request):
            await reject(403, "Same-origin request required")
            return
        retry = self._mutations.consume("telegram" if webhook else "owner")
        if retry:
            await reject(429, "Too many changes; try again shortly", retry=retry)
            return

        length = request.headers.get("content-length")
        if length is not None:
            if not length.isascii() or not length.isdecimal():
                await reject(400, "Invalid content length")
                return
            if len(length.lstrip("0")) > 5 or int(length[-5:]) > MAX_BODY_BYTES:
                await reject(413, "Request body too large")
                return
        body = bytearray()
        try:
            async with asyncio.timeout(BODY_TIMEOUT_SECONDS):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > MAX_BODY_BYTES:
                        await reject(413, "Request body too large")
                        return
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await reject(408, "Request body timed out")
            return

        delivered = False

        async def buffered_receive() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        async with self._mutation_lock:
            await self.app(scope, buffered_receive, private_send)
