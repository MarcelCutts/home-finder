"""An assessment of cached images is stored and reaches the property detail page.

Boundary: images in the on-disk cache, the analyzer's two provider requests (only
the SDK client is faked), assessment persistence, and the web query behind the
detail page. Response parsing and validation are tested in
``tests/test_analysis/test_analyzer.py``.
"""

import base64
from collections.abc import Callable
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from anthropic.types import Message
from pydantic import HttpUrl

from home_finder.analysis.analyzer import PropertyQualityAnalyzer
from home_finder.db import PropertyStorage
from home_finder.models import MergedProperty, PropertyImage, PropertySource
from home_finder.utils.image_cache import get_cached_image_path
from tests.support.analysis_responses import response
from tests.support.images import populate_image_cache


def _image(name: str, image_type: str) -> PropertyImage:
    return PropertyImage(
        url=HttpUrl(f"https://example.com/{name}.jpg"),
        source=PropertySource.OPENRENT,
        image_type=image_type,
    )


async def test_cached_images_are_assessed_and_the_assessment_reaches_the_detail(
    storage: PropertyStorage,
    make_merged_property: Callable[..., MergedProperty],
    tmp_path: Path,
):
    merged = make_merged_property(
        images=(_image("img1", "gallery"), _image("img2", "gallery")),
        floorplan=_image("floor", "floorplan"),
        price_pcm=1900,
    )
    populate_image_cache(str(tmp_path), merged)
    cached_floorplan = get_cached_image_path(
        str(tmp_path), merged.unique_id, str(merged.floorplan.url), "floorplan", 0
    ).read_bytes()
    analyzer = PropertyQualityAnalyzer(api_key="test-key")
    analyzer._client = MagicMock()
    analyzer._client.messages.create = AsyncMock(
        side_effect=[
            Message.model_validate(response("visual")),
            Message.model_validate(response("evaluation")),
        ]
    )

    _, analysis = await analyzer.analyze_single_merged(merged, data_dir=str(tmp_path))

    visual_request = analyzer._client.messages.create.call_args_list[0].kwargs
    images = [
        block for block in visual_request["messages"][0]["content"] if block["type"] == "image"
    ]
    # Both gallery images and the floorplan are read from the cache, not downloaded.
    assert len(images) == 3
    assert {block["source"]["type"] for block in images} == {"base64"}
    assert base64.b64decode(images[-1]["source"]["data"]) == cached_floorplan
    assert analysis is not None and analysis.analysis_failure is None
    assert analysis.overall_rating == 4

    await storage.save_merged_property(merged)
    await storage.save_quality_analysis(merged.unique_id, analysis)

    stored = await storage.get_quality_analysis(merged.unique_id)
    assert stored is not None
    assert stored.kitchen == analysis.kitchen
    assert (stored.overall_rating, stored.summary) == (analysis.overall_rating, analysis.summary)
    detail = await storage.web.get_property_detail(merged.unique_id)
    assert detail is not None
    assert detail["quality_rating"] == 4
    assert detail["quality_analysis"].summary == analysis.summary
