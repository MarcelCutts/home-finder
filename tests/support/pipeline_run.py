"""Run the pipeline with its external services faked, and seed what an earlier run left.

``pipeline_boundaries`` replaces collection, detail acquisition, the analyzer, ward
lookup, image downloads and the Telegram Bot API. Storage, candidate preparation,
matching, assessment persistence and delivery planning stay real. Tests own the
storage: pass it to ``run_pipeline`` or let the run open ``settings.database_path``.
"""

import json
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import HttpUrl

from home_finder.analysis.analyzer import TokenUsage
from home_finder.db import PropertyStorage
from home_finder.enrichment.details import EnrichmentResult
from home_finder.models import (
    MergedProperty,
    Property,
    PropertyImage,
    PropertyQualityAnalysis,
    PropertySource,
)
from tests.support.delivery import mock_durable_notifier
from tests.support.matching import as_aggregate

GALLERY_IMAGE = PropertyImage(
    url=HttpUrl("https://example.com/gallery1.jpg"),
    source=PropertySource.OPENRENT,
    image_type="gallery",
)
FLOORPLAN_IMAGE = PropertyImage(
    url=HttpUrl("https://example.com/floorplan.jpg"),
    source=PropertySource.OPENRENT,
    image_type="floorplan",
)

Assess = Callable[..., tuple[MergedProperty, PropertyQualityAnalysis | None]]


async def acquire_usable_media(
    merged_list: list[MergedProperty], fetcher: Any, **kwargs: Any
) -> EnrichmentResult:
    """Detail acquisition that gives every candidate a gallery image and a floorplan."""
    return EnrichmentResult(
        with_usable_media=[
            merged.model_copy(update={"images": (GALLERY_IMAGE,), "floorplan": FLOORPLAN_IMAGE})
            for merged in merged_list
        ]
    )


def _not_assessed(merged: MergedProperty, **kwargs: Any) -> tuple[MergedProperty, None]:
    return merged, None


@dataclass
class PipelineBoundaries:
    scrape: AsyncMock
    """Stands in for ``scrape_all_platforms``; returns ``(listings, scraper_metrics)``."""
    analyzer_factory: MagicMock
    """Stands in for the ``PropertyQualityAnalyzer`` class."""
    analyzer: MagicMock
    """The analyzer every assessment and reassessment in the run receives."""
    notifier: MagicMock
    """Real ``TelegramNotifier`` behaviour behind a recording proxy; only the Bot API is fake."""


@contextmanager
def pipeline_boundaries(
    *,
    observations: Iterable[Property] = (),
    assess: Assess = _not_assessed,
    telegram_accepts: bool = True,
) -> Iterator[PipelineBoundaries]:
    """Patch the external services of ``run_pipeline`` and ``run_dry_run``.

    Args:
        observations: Listings every collection returns. Set ``scrape.side_effect``
            for runs that observe different listings.
        assess: Replaces ``analyze_single_merged``; the default returns no assessment.
        telegram_accepts: When False, the Bot API fake rejects each send as forbidden.
    """
    notifier, construct_notifier = mock_durable_notifier(telegram_accepts)

    fetcher = MagicMock()
    fetcher.__aenter__ = AsyncMock(return_value=fetcher)
    fetcher.__aexit__ = AsyncMock(return_value=False)

    analyzer = MagicMock()
    analyzer.token_usage = TokenUsage()
    analyzer.analyze_single_merged = AsyncMock(side_effect=assess)
    analyzer.__aenter__ = AsyncMock(return_value=analyzer)
    analyzer.__aexit__ = AsyncMock(return_value=False)

    scrape = AsyncMock(return_value=(list(observations), []))

    with (
        patch("home_finder.pipeline.collection.scrape_all_platforms", scrape),
        patch(
            "home_finder.pipeline.candidates.enrich_merged_properties",
            AsyncMock(side_effect=acquire_usable_media),
        ),
        patch("home_finder.pipeline.candidates.DetailFetcher", return_value=fetcher),
        patch(
            "home_finder.pipeline.assessment.PropertyQualityAnalyzer", return_value=analyzer
        ) as analyzer_factory,
        patch("home_finder.pipeline.flow.TelegramNotifier", side_effect=construct_notifier),
        patch("home_finder.pipeline.assessment._lookup_wards", AsyncMock()),
        patch("home_finder.pipeline.image_recovery.download_missing_images", AsyncMock()),
    ):
        yield PipelineBoundaries(
            scrape=scrape,
            analyzer_factory=analyzer_factory,
            analyzer=analyzer,
            notifier=notifier,
        )


async def save_notified_anchor(
    storage: PropertyStorage,
    listing: Property,
    assessment: PropertyQualityAnalysis | None = None,
) -> MergedProperty:
    """Store an anchor as an earlier run left it after notifying: the legacy ``sent``
    status and, when given, its assessment. No delivery intent row is written."""
    anchor = as_aggregate(listing)
    await storage.save_merged_property(anchor)
    await storage.mark_notified(anchor.unique_id)
    if assessment is not None:
        await storage.save_quality_analysis(anchor.unique_id, assessment)
    return anchor


async def stored_sources(storage: PropertyStorage, unique_id: str) -> set[str]:
    """Source names recorded on the stored home's row."""
    async with storage._read_connection() as conn:
        cursor = await conn.execute(
            "SELECT sources FROM properties WHERE unique_id = ?", (unique_id,)
        )
        row = await cursor.fetchone()
    assert row is not None, f"{unique_id} is not stored"
    return set(json.loads(row["sources"]))


async def reanalysis_queue_ids(storage: PropertyStorage) -> list[str]:
    """Unique IDs with a pending reassessment request."""
    return [home.unique_id for home in await storage.processing.get_reanalysis_queue()]
