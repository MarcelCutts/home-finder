"""Tests for the FastAPI application factory."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from home_finder.config import Settings
from home_finder.web.app import (
    PIPELINE_INITIAL_DELAY_SECONDS,
    SecurityHeadersMiddleware,
    create_app,
)
from home_finder.web.routes import router


@pytest.fixture
def settings() -> Settings:
    return Settings(
        telegram_bot_token=SecretStr("fake:token"),
        telegram_chat_id=0,
        database_path=":memory:",
        pipeline_interval_minutes=60,
    )


class TestCreateApp:
    def test_returns_fastapi_instance(self, settings: Settings) -> None:
        app = create_app(settings)
        assert isinstance(app, FastAPI)
        assert app.title == "Home Finder"

    def test_has_routes(self, settings: Settings) -> None:
        app = create_app(settings)
        paths = [route.path for route in app.routes]  # type: ignore[attr-defined]
        assert "/" in paths
        assert "/health" in paths

    def test_has_static_mount(self, settings: Settings) -> None:
        app = create_app(settings)
        route_names = [getattr(route, "name", None) for route in app.routes]
        assert "static" in route_names

    def test_default_settings_if_none(self) -> None:
        with patch("home_finder.web.app.Settings") as mock_settings:
            mock_settings.return_value = Settings(
                telegram_bot_token=SecretStr("fake:token"),
                telegram_chat_id=0,
                database_path=":memory:",
            )
            app = create_app(None)
            assert isinstance(app, FastAPI)


class TestSecurityHeaders:
    def test_headers_present(self, settings: Settings) -> None:
        from fastapi.testclient import TestClient

        test_app = FastAPI()
        test_app.add_middleware(SecurityHeadersMiddleware)
        mock_storage = AsyncMock()
        mock_storage.get_last_pipeline_run.return_value = None
        test_app.state.storage = mock_storage
        test_app.state.settings = settings
        test_app.include_router(router)

        client = TestClient(test_app)
        resp = client.get("/health")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

    def test_csp_header_present(self, settings: Settings) -> None:
        from fastapi.testclient import TestClient

        test_app = FastAPI()
        test_app.add_middleware(SecurityHeadersMiddleware)
        mock_storage = AsyncMock()
        mock_storage.get_last_pipeline_run.return_value = None
        test_app.state.storage = mock_storage
        test_app.state.settings = settings
        test_app.include_router(router)

        client = TestClient(test_app)
        resp = client.get("/health")
        csp = resp.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "script-src" in csp
        assert "frame-ancestors 'none'" in csp
        # Verify unsafe-inline removed from script-src
        script_src = csp.split("script-src")[1].split(";")[0]
        assert "'unsafe-inline'" not in script_src
        assert "'nonce-" in script_src

    def test_csp_nonce_unique_per_request(self, settings: Settings) -> None:
        import re

        from fastapi.testclient import TestClient

        test_app = FastAPI()
        test_app.add_middleware(SecurityHeadersMiddleware)
        mock_storage = AsyncMock()
        mock_storage.get_last_pipeline_run.return_value = None
        test_app.state.storage = mock_storage
        test_app.state.settings = settings
        test_app.include_router(router)

        client = TestClient(test_app)
        resp1 = client.get("/health")
        resp2 = client.get("/health")
        nonce1 = re.search(r"'nonce-([^']+)'", resp1.headers["Content-Security-Policy"]).group(1)
        nonce2 = re.search(r"'nonce-([^']+)'", resp2.headers["Content-Security-Policy"]).group(1)
        assert nonce1 != nonce2


class TestPipelineConfig:
    def test_initial_delay_configured(self) -> None:
        assert PIPELINE_INITIAL_DELAY_SECONDS == 30
