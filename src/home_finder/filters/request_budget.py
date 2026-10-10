"""Conservative admission size for the application's single-turn listing requests.

This is a local reservation policy, not a provider dollar-limit API. It accepts
only inline text and static images. Text/schema JSON is charged at twice its
UTF-8 byte length, plus 16,384 tokens for framing and provider overhead. Each
image reserves 5,000 visual tokens (above Sonnet 4.6's 1,568-token maximum).
Unknown request shapes cannot use the smaller reservation.
"""

import base64
import binascii
import json
from io import BytesIO
from typing import Any, Final

from PIL import Image

from home_finder.models.spend import SpendDeferred

POLICY: Final = "bounded_listing_payload_2026_09_29_v1"


def input_reservation_tokens(body: dict[str, Any]) -> int:
    """Return a deliberately generous bound for the supported payload subset."""
    messages = body.get("messages")
    if (
        not isinstance(messages, list)
        or len(messages) != 1
        or not isinstance(messages[0], dict)
        or set(messages[0]) != {"role", "content"}
        or messages[0]["role"] != "user"
    ):
        raise SpendDeferred("unsupported_input_budget")
    content = messages[0]["content"]
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list) or not 1 <= len(content) <= 100:
        raise SpendDeferred("unsupported_input_budget")
    sanitized: list[dict[str, Any]] = []
    images = 0
    for block in content:
        if not isinstance(block, dict):
            raise SpendDeferred("unsupported_input_budget")
        if block.get("type") == "text" and set(block) <= {"type", "text", "cache_control"}:
            if not isinstance(block.get("text"), str):
                raise SpendDeferred("unsupported_input_budget")
            sanitized.append(block)
            continue
        if block.get("type") != "image" or set(block) != {"type", "source"}:
            raise SpendDeferred("unsupported_input_budget")
        source = block["source"]
        if (
            not isinstance(source, dict)
            or set(source) != {"type", "media_type", "data"}
            or source["type"] != "base64"
            or source["media_type"] not in {"image/jpeg", "image/png", "image/webp"}
            or not isinstance(source["data"], str)
            or not 1 <= len(source["data"]) <= 8_000_000
        ):
            raise SpendDeferred("unsupported_input_budget")
        try:
            decoded = base64.b64decode(source["data"], validate=True)
            with Image.open(BytesIO(decoded)) as picture:
                if getattr(picture, "n_frames", 1) != 1:
                    raise ValueError("Only static images have a reservation bound")
                if Image.MIME.get(picture.format or "") != source["media_type"]:
                    raise ValueError("Image type does not match bytes")
                picture.verify()
        except (ValueError, OSError, binascii.Error, Image.DecompressionBombError):
            raise SpendDeferred("unsupported_input_budget") from None
        images += 1
        sanitized.append({"type": "image", "source": {**source, "data": ""}})
    if images > 21:
        raise SpendDeferred("unsupported_input_budget")
    reduced = {**body, "messages": [{"role": "user", "content": sanitized}]}
    size = len(json.dumps(reduced, ensure_ascii=True, allow_nan=False).encode("utf-8"))
    return size * 2 + 16_384 + images * 5_000
