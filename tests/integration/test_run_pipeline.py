"""The run coordinator from collection to delivery, with external services replaced.

``run_pipeline`` opens, migrates and closes its own database here, as the CLI does.
Each test reopens that database afterwards, so assertions read the durable state a
later run would see. Collection, detail acquisition, the analyzer, ward lookup and
the Telegram Bot API are faked (see ``tests/support/pipeline_run.py``).
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import SecretStr

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.models import Property, PropertyQualityAnalysis, PropertySource
from home_finder.models.notifications import NotificationPolicy
from home_finder.pipeline.flow import run_pipeline
from tests.support.pipeline_run import pipeline_boundaries

pytestmark = pytest.mark.e2e


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "telegram_bot_token": SecretStr("fake:test-token"),
        "telegram_chat_id": 0,
        # The image cache lives beside the database, so keep both in tmp_path.
        "database_path": str(tmp_path / "home_finder.db"),
        "search_areas": "e8",
        "min_price": 1500,
        "max_price": 2500,
        "min_bedrooms": 1,
        "max_bedrooms": 2,
        "enable_quality_filter": True,
        "notification_policy": NotificationPolicy(min_fit_score=0),
        "require_floorplan": False,
        "anthropic_api_key": SecretStr("test-anthropic-key"),
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def analysis_settings(tmp_path: Path) -> Settings:
    """Analysis enabled and every assessed home eligible for delivery."""
    return _settings(tmp_path)


@pytest.fixture
def new_listings(make_property: Callable[..., Property]) -> list[Property]:
    """Two distinct homes, neither seen before."""
    return [
        make_property(
            source=PropertySource.OPENRENT, source_id="p100", price_pcm=1800, postcode="E8 3RH"
        ),
        make_property(
            source=PropertySource.OPENRENT,
            source_id="p101",
            price_pcm=2000,
            bedrooms=2,
            postcode="E8 4AB",
        ),
    ]


async def test_run_assesses_persists_and_delivers_each_new_home(
    analysis_settings: Settings,
    new_listings: list[Property],
    make_quality_analysis: Callable[..., PropertyQualityAnalysis],
):
    assessment = make_quality_analysis(rating=4, summary="Nice flat")

    with pipeline_boundaries(
        observations=new_listings, assess=lambda merged, **kw: (merged, assessment)
    ) as boundaries:
        await run_pipeline(analysis_settings)

    assert boundaries.analyzer_factory.call_args.kwargs["api_key"] == "test-anthropic-key"
    assert boundaries.analyzer_factory.call_count == 1
    sends = boundaries.notifier.send_merged_property_notification.call_args_list
    assert sorted(call.args[0].unique_id for call in sends) == sorted(
        listing.unique_id for listing in new_listings
    )
    assert all(call.kwargs["quality_analysis"] == assessment for call in sends)
    boundaries.notifier.close.assert_awaited_once()

    async with PropertyStorage(analysis_settings.database_path) as storage:
        run = await storage.runs.get_last_pipeline_run()
        assert run is not None
        assert run["status"] == "completed"
        assert run["scraped_count"] == 2
        assert run["new_count"] == 2
        assert run["enriched_count"] == 2
        assert run["analyzed_count"] == 2
        assert run["notified_count"] == 2
        assert run["duration_seconds"] is not None
        assert await storage.web.get_property_count() == 2
        assert (await storage.deliveries.report())["counts"] == {"complete": 2}
        for listing in new_listings:
            stored = await storage.get_quality_analysis(listing.unique_id)
            assert stored is not None and stored.summary == "Nice flat"


async def test_empty_collection_completes_run_without_delivery(analysis_settings: Settings):
    with pipeline_boundaries(observations=[]) as boundaries:
        await run_pipeline(analysis_settings)

    boundaries.notifier.send_merged_property_notification.assert_not_awaited()
    boundaries.notifier.close.assert_awaited_once()
    async with PropertyStorage(analysis_settings.database_path) as storage:
        run = await storage.runs.get_last_pipeline_run()
        assert run is not None
        assert run["status"] == "completed"
        assert run["scraped_count"] == 0
        assert (await storage.deliveries.report())["counts"] == {}


async def test_legacy_unsent_notifications_require_review(
    analysis_settings: Settings,
    make_property: Callable[..., Property],
):
    """An untracked old send must not be repeated without reviewing its outcome."""
    listing = make_property(
        source=PropertySource.OPENRENT, source_id="retry-1", price_pcm=1900, postcode="E8 3RH"
    )
    # A row written before delivery intents existed: failed, with no intent row.
    async with (
        PropertyStorage(analysis_settings.database_path) as storage,
        storage._transaction() as conn,
    ):
        await conn.execute(
            """INSERT INTO properties (
                    unique_id, source, source_id, url, title, price_pcm, bedrooms,
                    address, postcode, latitude, longitude, notification_status, first_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                listing.unique_id,
                listing.source.value,
                listing.source_id,
                str(listing.url),
                listing.title,
                listing.price_pcm,
                listing.bedrooms,
                listing.address,
                listing.postcode,
                listing.latitude,
                listing.longitude,
                "failed",
                listing.first_seen.isoformat(),
            ),
        )

    with pipeline_boundaries(observations=[]) as boundaries:
        await run_pipeline(analysis_settings)

    boundaries.notifier.send_property_notification.assert_not_awaited()
    boundaries.notifier.send_merged_property_notification.assert_not_awaited()
    async with PropertyStorage(analysis_settings.database_path) as storage:
        assert (await storage.deliveries.report())["counts"] == {"review": 1}
        # Review preserves the previous failed state.
        tracked = await storage.get_property(listing.unique_id)
        assert tracked is not None
        assert tracked.notification_status.value == "failed"


async def test_collection_failure_marks_run_failed_and_still_closes_notifier(
    analysis_settings: Settings,
):
    with pipeline_boundaries() as boundaries:
        boundaries.scrape.side_effect = RuntimeError("Scraper network timeout")
        with pytest.raises(RuntimeError, match="Scraper network timeout"):
            await run_pipeline(analysis_settings)

    boundaries.notifier.close.assert_awaited_once()
    async with PropertyStorage(analysis_settings.database_path) as storage:
        run = await storage.runs.get_last_pipeline_run()
        assert run is not None
        assert run["status"] == "failed"
        assert "Scraper network timeout" in run["error_message"]


async def test_disabled_analysis_persists_home_and_suppresses_delivery(
    tmp_path: Path, make_property: Callable[..., Property]
):
    """Disabled analysis retains the home and suppresses its discovery alert."""
    settings = _settings(
        tmp_path,
        enable_quality_filter=False,
        notification_policy=NotificationPolicy(),
        anthropic_api_key=SecretStr(""),
    )
    listing = make_property(
        source=PropertySource.OPENRENT, source_id="nq-1", price_pcm=1800, postcode="E8 3RH"
    )

    with pipeline_boundaries(observations=[listing]) as boundaries:
        await run_pipeline(settings)

    boundaries.analyzer_factory.assert_not_called()
    # The callback runs, but an unrated home creates no sendable alert.
    assert boundaries.notifier.send_merged_property_notification.call_count == 1
    async with PropertyStorage(settings.database_path) as storage:
        assert await storage.web.get_property_count() == 1
        run = await storage.runs.get_last_pipeline_run()
        assert run is not None
        assert run["status"] == "completed"
        assert run["notified_count"] == 0
        assert (await storage.deliveries.report())["counts"] == {"suppressed": 1}


async def test_forbidden_delivery_blocks_intent_and_updates_legacy_status(
    analysis_settings: Settings,
    new_listings: list[Property],
    make_quality_analysis: Callable[..., PropertyQualityAnalysis],
):
    """A forbidden Bot API response blocks delivery and projects the legacy failed status."""
    with pipeline_boundaries(
        observations=new_listings,
        assess=lambda merged, **kw: (merged, make_quality_analysis()),
        telegram_accepts=False,
    ):
        await run_pipeline(analysis_settings)

    async with PropertyStorage(analysis_settings.database_path) as storage:
        run = await storage.runs.get_last_pipeline_run()
        assert run is not None
        assert run["status"] == "completed"
        assert run["notified_count"] == 0
        assert (await storage.deliveries.report())["counts"] == {"blocked": 2}
        for listing in new_listings:
            tracked = await storage.get_property(listing.unique_id)
            assert tracked is not None
            assert tracked.notification_status.value == "failed"
