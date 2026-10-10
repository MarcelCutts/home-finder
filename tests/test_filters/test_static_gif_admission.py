"""Static GIFs built from the cache must pass the native spending boundary."""

import base64
import json
from io import BytesIO

import httpx2
import pytest
from PIL import Image

from home_finder.config import new_runtime_analysis_config
from home_finder.db import PropertyStorage
from home_finder.filters.quality import PropertyQualityFilter
from home_finder.filters.request_budget import input_reservation_tokens
from home_finder.models import PropertyImage
from home_finder.models.spend import SpendDeferred
from home_finder.utils.image_cache import get_cached_image_path, save_image_bytes
from tests.test_filters.test_analysis_spend import rows
from tests.test_filters.test_sonnet55 import policy, response


def image_bytes(*, animated=False, format="GIF"):
    buffer = BytesIO()
    first = Image.new("RGB", (64, 64), "white")
    if animated:
        first.save(
            buffer,
            format,
            save_all=True,
            append_images=[Image.new("RGB", (64, 64), "black")],
            duration=100,
            loop=0,
        )
    else:
        first.save(buffer, format)
    return buffer.getvalue()


async def test_cached_static_gif_floorplan_passes_native_two_phase_admission(
    cached_analysis_property,
):
    merged, data_dir = cached_analysis_property
    floorplan = PropertyImage(
        url="https://example.com/floorplan.jpg",
        source=merged.canonical.source,
        image_type="floorplan",
        floorplan_origin="source_declared",
    )
    merged = merged.model_copy(update={"floorplan": floorplan})
    raw = image_bytes()
    save_image_bytes(
        get_cached_image_path(data_dir, merged.unique_id, str(floorplan.url), "floorplan", 0),
        raw,
    )
    requests = []
    outputs = iter([response(), response("evaluation", thinking=False)])
    config = new_runtime_analysis_config()

    async def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        report = await storage.spend.report()
        assert report["in_flight"] == 1
        assert report["unresolved"][0]["state"] == "dispatched"
        return httpx2.Response(200, json=next(outputs))

    async with PropertyStorage(":memory:") as storage:
        async with PropertyQualityFilter(
            "fixture",
            analysis_config=config,
            spend_repository=storage.spend,
            spend_policy=policy(),
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
        ) as q:
            _, result = await q.analyze_single_merged(merged, data_dir=data_dir)
        assert result.overall_rating is not None
        assert len(requests) == 2
        visual = requests[0]
        images = [b["source"] for b in visual["messages"][0]["content"] if b["type"] == "image"]
        assert [image["media_type"] for image in images] == ["image/jpeg", "image/gif"]
        assert base64.b64decode(images[-1]["data"], validate=True) == raw
        assert input_reservation_tokens(visual) <= config.visual.input_token_limit
        attempts = await rows(storage)
        assert len(attempts) == 2
        assert all(attempt["state"] == "settled" for attempt in attempts)
        assert all(attempt["result"] == "success" for attempt in attempts)
        report = await storage.spend.report()
        assert report["held_nanousd"] == 0
        assert 0 < report["lifetime_accounted_nanousd"] < 1_000_000_000


@pytest.mark.parametrize(
    "invalid",
    ["animated", "corrupt", "gif_declared_png", "png_declared_gif", "base64", "oversized"],
)
async def test_invalid_gif_payload_stops_before_native_dispatch_or_reservation(invalid):
    raw = image_bytes(animated=invalid == "animated")
    media_type = "image/gif"
    if invalid == "corrupt":
        raw = b"GIF89a" + bytes(20)
    elif invalid == "gif_declared_png":
        media_type = "image/png"
    elif invalid == "png_declared_gif":
        raw = image_bytes(format="PNG")
    data = base64.b64encode(raw).decode()
    if invalid == "base64":
        data += "%"
    elif invalid == "oversized":
        data = "A" * 8_000_004
    content = [
        {"type": "text", "text": "Synthetic floorplan"},
        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
    ]

    async with PropertyStorage(":memory:") as storage:
        async with PropertyQualityFilter(
            "fixture",
            analysis_config=new_runtime_analysis_config(),
            spend_repository=storage.spend,
            spend_policy=policy(),
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(lambda _: pytest.fail("Invalid image dispatched"))
            ),
        ) as q:
            with pytest.raises(SpendDeferred, match="unsupported_input_budget"):
                await q._run_visual_analysis(content, "fixture:invalid-gif")
        assert await rows(storage) == []
        report = await storage.spend.report()
        assert report["attempts"] == report["held_nanousd"] == 0
        assert report["lifetime_accounted_nanousd"] == 0
