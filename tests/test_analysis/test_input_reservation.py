"""The input token reservation for the supported single-turn listing payload."""

import base64
import json
from io import BytesIO

import pytest
from PIL import Image

from home_finder.analysis.analyzer import PropertyQualityAnalyzer
from home_finder.analysis.request_budget import input_reservation_tokens
from home_finder.models.spend import SpendDeferred
from tests.support.analysis_config import configuration


def body(content=None):
    return {
        "messages": [{"role": "user", "content": content or [{"type": "text", "text": "flat"}]}]
    }


def png_block(size):
    buffer = BytesIO()
    Image.new("RGB", size, "red").save(buffer, "PNG")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(buffer.getvalue()).decode(),
        },
    }


def test_input_reservation_counts_unicode_schema_and_each_static_image():
    image = png_block((64, 64))
    first = body([{"type": "text", "text": "flat"}])
    second = body([{"type": "text", "text": "flat"}, image, image])
    assert input_reservation_tokens(second) >= input_reservation_tokens(first) + 10_000
    assert input_reservation_tokens(
        body([{"type": "text", "text": "🏠" * 100}])
    ) > input_reservation_tokens(first)
    assert (
        input_reservation_tokens({**first, "tools": [{"description": "x" * 1000}]})
        > input_reservation_tokens(first) + 2000
    )
    image["source"]["media_type"] = "image/jpeg"
    with pytest.raises(SpendDeferred, match="unsupported_input_budget"):
        input_reservation_tokens(second)


def test_input_reservation_covers_high_resolution_images_and_the_new_tokenizer():
    analyzer = PropertyQualityAnalyzer("fixture", analysis_config=configuration())
    text = [{"type": "text", "text": "🏠" * 1000}]
    image = png_block((3840, 2160))
    # Image size does not discount admission: every static image reserves 5,000
    # tokens, above Sonnet 5.5's 4,784-token high-resolution maximum.
    plain = input_reservation_tokens(analyzer._visual_request(text))
    with_images = input_reservation_tokens(analyzer._visual_request([*text, *([image] * 21)]))
    assert with_images >= plain + 21 * 5000 > plain + 21 * 4784
    assert plain >= len(json.dumps(analyzer._visual_request(text)).encode()) * 2 + 16_384


@pytest.mark.parametrize(
    "payload",
    [
        {"messages": []},
        {"messages": [{"role": "assistant", "content": "prefix"}]},
        body([{"type": "document", "source": {"type": "url", "url": "https://example.com"}}]),
        body([{"type": "image", "source": {"type": "url", "url": "https://example.com/image"}}]),
        body([{"type": "tool_result", "content": "result"}]),
        body([{"type": "text", "text": 100}]),
    ],
)
def test_unknown_payloads_cannot_obtain_a_smaller_reservation(payload):
    with pytest.raises(SpendDeferred, match="unsupported_input_budget"):
        input_reservation_tokens(payload)
