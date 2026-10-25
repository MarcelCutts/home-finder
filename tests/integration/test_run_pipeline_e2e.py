"""End-to-end tests for run_pipeline() with mocked external boundaries.

Exercises observation retention, candidate preparation, assessment persistence,
delivery, and run finalization with real in-memory storage. Source, provider,
transport, ward lookup, and sleep boundaries are replaced.
"""

from collections.abc import Callable
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import HttpUrl, SecretStr

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.enrichment.details import EnrichmentResult
from home_finder.models import (
    Property,
    PropertyImage,
    PropertyQualityAnalysis,
    PropertySource,
)
from home_finder.models.notifications import NotificationPolicy
from home_finder.pipeline.flow import run_pipeline
from tests.notifier_helpers import mock_durable_notifier

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GALLERY_IMAGE = PropertyImage(
    url=HttpUrl("https://example.com/gallery1.jpg"),
    source=PropertySource.OPENRENT,
    image_type="gallery",
)
_FLOORPLAN_IMAGE = PropertyImage(
    url=HttpUrl("https://example.com/floorplan.jpg"),
    source=PropertySource.OPENRENT,
    image_type="floorplan",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fake_enrich(merged_list, fetcher, *, data_dir=None, storage=None):
    """Enrichment stub that adds images + floorplan so notifications aren't skipped."""
    enriched = []
    for m in merged_list:
        enriched_m = m.model_copy(
            update={
                "images": (_GALLERY_IMAGE,),
                "floorplan": _FLOORPLAN_IMAGE,
            }
        )
        enriched.append(enriched_m)
    return EnrichmentResult(with_usable_media=enriched)


@contextmanager
def _pipeline_mocks(
    *,
    scrape_return: list[Property],
    enrich_side_effect=None,
    quality_side_effect=None,
    notify_return: bool = True,
):
    """Context manager that patches all external boundaries of run_pipeline().

    Yields a namespace SimpleNamespace with references to the mock objects.
    """
    if enrich_side_effect is None:
        enrich_side_effect = _fake_enrich

    # -- Storage: intercept PropertyStorage constructor to use in-memory DB --
    class _StorageCapture:
        """Captures the real storage instance so tests can inspect DB state."""

        instance: PropertyStorage | None = None

    original_storage_init = PropertyStorage.__init__

    def _patched_storage_init(self, *args, **kwargs):
        # Force in-memory DB regardless of what settings.database_path says
        original_storage_init(self, ":memory:")
        _StorageCapture.instance = self

    # -- Notifier mock --
    mock_notifier, construct_notifier = mock_durable_notifier(notify_return)

    # -- DetailFetcher mock --
    mock_fetcher_instance = MagicMock()
    mock_fetcher_instance.close = AsyncMock()

    async def _fetcher_aenter(*a):
        return mock_fetcher_instance

    async def _fetcher_aexit(*a):
        await mock_fetcher_instance.close()

    mock_fetcher_instance.__aenter__ = _fetcher_aenter
    mock_fetcher_instance.__aexit__ = _fetcher_aexit

    # -- Quality filter mock --
    from home_finder.analysis.analyzer import TokenUsage

    mock_quality_instance = MagicMock()
    mock_quality_instance.token_usage = TokenUsage()
    if quality_side_effect is not None:
        mock_quality_instance.analyze_single_merged = AsyncMock(side_effect=quality_side_effect)
    else:
        # Default: return (merged, None) — no analysis
        mock_quality_instance.analyze_single_merged = AsyncMock(
            side_effect=lambda m, **kw: (m, None)
        )
    mock_quality_instance.close = AsyncMock()

    async def _quality_aenter(*a):
        return mock_quality_instance

    async def _quality_aexit(*a):
        await mock_quality_instance.close()

    mock_quality_instance.__aenter__ = _quality_aenter
    mock_quality_instance.__aexit__ = _quality_aexit

    with (
        patch(
            "home_finder.pipeline.collection.scrape_all_platforms",
            new_callable=AsyncMock,
            return_value=(scrape_return, []),
        ),
        patch(
            "home_finder.pipeline.candidates.enrich_merged_properties",
            new_callable=AsyncMock,
            side_effect=enrich_side_effect,
        ),
        patch(
            "home_finder.pipeline.candidates.DetailFetcher",
            return_value=mock_fetcher_instance,
        ) as mock_fetcher_cls,
        patch(
            "home_finder.pipeline.assessment.PropertyQualityAnalyzer",
            return_value=mock_quality_instance,
        ) as mock_quality_cls,
        patch(
            "home_finder.pipeline.flow.TelegramNotifier",
            side_effect=construct_notifier,
        ) as mock_notifier_cls,
        patch("home_finder.pipeline.assessment._lookup_wards", new_callable=AsyncMock),
        patch("home_finder.pipeline.flow.asyncio.sleep", new_callable=AsyncMock),
        patch.object(PropertyStorage, "__init__", _patched_storage_init),
    ):
        # Yield a dict of useful references
        ctx = type(
            "Ctx",
            (),
            {
                "notifier": mock_notifier,
                "notifier_cls": mock_notifier_cls,
                "quality_cls": mock_quality_cls,
                "quality_instance": mock_quality_instance,
                "fetcher_cls": mock_fetcher_cls,
                "fetcher_instance": mock_fetcher_instance,
                "storage_capture": _StorageCapture,
            },
        )()
        yield ctx

    # Clean up: close storage if the pipeline's finally block was intercepted
    if _StorageCapture.instance is not None:
        import asyncio
        import contextlib

        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pipeline_settings() -> Settings:
    """Settings with quality analysis enabled."""
    return Settings(
        telegram_bot_token=SecretStr("fake:test-token"),
        telegram_chat_id=0,
        database_path=":memory:",
        search_areas="e8",
        min_price=1500,
        max_price=2500,
        min_bedrooms=1,
        max_bedrooms=2,
        enable_quality_filter=True,
        notification_policy=NotificationPolicy(min_fit_score=0),
        require_floorplan=False,
        anthropic_api_key=SecretStr("test-anthropic-key"),
    )


@pytest.fixture
def pipeline_properties(make_property: Callable[..., Property]) -> list[Property]:
    """Two synthetic properties for pipeline testing."""
    return [
        make_property(
            source=PropertySource.OPENRENT,
            source_id="p100",
            price_pcm=1800,
            postcode="E8 3RH",
        ),
        make_property(
            source=PropertySource.OPENRENT,
            source_id="p101",
            price_pcm=2000,
            bedrooms=2,
            postcode="E8 4AB",
        ),
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestRunPipelineE2E:
    """Test run_pipeline() end-to-end with mocked external boundaries."""

    async def test_happy_path_full_pipeline(
        self,
        pipeline_settings: Settings,
        pipeline_properties: list[Property],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
    ):
        """Full pipeline: scrape → filter → enrich → analyze → save → notify."""
        qa = make_quality_analysis(rating=4, summary="Nice flat")

        def _quality_side_effect(merged, **kw):
            return (merged, qa)

        with _pipeline_mocks(
            scrape_return=pipeline_properties,
            quality_side_effect=_quality_side_effect,
        ) as ctx:
            # Intercept storage.close() so we can query DB after pipeline
            original_close_called = False

            async def _intercept_close(self_storage):
                nonlocal original_close_called
                original_close_called = True
                # Don't actually close — we need the connection for assertions

            with patch.object(PropertyStorage, "close", _intercept_close):
                await run_pipeline(pipeline_settings)

            storage = ctx.storage_capture.instance
            assert storage is not None

            # Pipeline run tracking
            run = await storage.runs.get_last_pipeline_run()
            assert run is not None
            assert run["status"] == "completed"
            assert run["scraped_count"] == 2
            assert run["new_count"] == 2
            assert run["enriched_count"] == 2
            assert run["analyzed_count"] == 2
            assert run["notified_count"] == 2
            assert run["duration_seconds"] is not None

            # Properties saved
            count = await storage.web.get_property_count()
            assert count == 2

            # Quality filter constructed with correct API key
            ctx.quality_cls.assert_called_once()
            call_kwargs = ctx.quality_cls.call_args
            assert (
                call_kwargs[1]["api_key"] == "test-anthropic-key"
                or call_kwargs[0][0] == "test-anthropic-key"
            )

            # Notifications sent
            assert ctx.notifier.send_merged_property_notification.call_count == 2
            for call in ctx.notifier.send_merged_property_notification.call_args_list:
                assert call.kwargs.get("quality_analysis") is not None or (len(call.args) > 0)

            # Cleanup
            assert original_close_called
            ctx.notifier.close.assert_awaited_once()

            # Actually close now
            await storage.close()

    async def test_empty_scrape_completes_run(self, pipeline_settings: Settings):
        """Empty scrape should complete the pipeline run with no notifications."""
        with _pipeline_mocks(scrape_return=[]) as ctx:
            # Intercept storage.close()
            async def _intercept_close(self_storage):
                pass

            with patch.object(PropertyStorage, "close", _intercept_close):
                await run_pipeline(pipeline_settings)

            storage = ctx.storage_capture.instance
            assert storage is not None

            run = await storage.runs.get_last_pipeline_run()
            assert run is not None
            assert run["status"] == "completed"

            # No notifications
            ctx.notifier.send_merged_property_notification.assert_not_awaited()
            ctx.notifier.send_property_notification.assert_not_awaited()

            # Cleanup still happens
            ctx.notifier.close.assert_awaited_once()

            await storage.close()

    async def test_legacy_unsent_notifications_require_review(
        self,
        pipeline_settings: Settings,
        make_property: Callable[..., Property],
    ):
        """An untracked old send must not be repeated without reviewing its outcome."""
        prop = make_property(
            source=PropertySource.OPENRENT,
            source_id="retry-1",
            price_pcm=1900,
            postcode="E8 3RH",
        )

        with _pipeline_mocks(scrape_return=[]) as ctx:

            async def _intercept_close(self_storage):
                pass

            with patch.object(PropertyStorage, "close", _intercept_close):
                storage = ctx.storage_capture

                # We need to pre-seed the DB before run_pipeline executes.
                # Patch storage.initialize to also seed data after init.
                original_init_method = PropertyStorage.initialize

                async def _init_and_seed(self_storage):
                    await original_init_method(self_storage)
                    storage.instance = self_storage
                    # Manually insert a property with 'failed' notification status
                    conn = await self_storage._get_connection()
                    await conn.execute(
                        """INSERT INTO properties (
                            unique_id, source, source_id, url, title,
                            price_pcm, bedrooms, address, postcode,
                            latitude, longitude, notification_status, first_seen
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            prop.unique_id,
                            prop.source.value,
                            prop.source_id,
                            str(prop.url),
                            prop.title,
                            prop.price_pcm,
                            prop.bedrooms,
                            prop.address,
                            prop.postcode,
                            prop.latitude,
                            prop.longitude,
                            "failed",
                            prop.first_seen.isoformat() if prop.first_seen else None,
                        ),
                    )
                    await conn.commit()

                with patch.object(PropertyStorage, "initialize", _init_and_seed):
                    await run_pipeline(pipeline_settings)

                storage_inst = storage.instance
                assert storage_inst is not None

                # The old outcome is unknown; leave it for owner review.
                ctx.notifier.send_property_notification.assert_not_awaited()
                assert (await storage_inst.deliveries.report())["counts"]["review"] == 1

                # Review must preserve the previous failed state.
                conn = await storage_inst._get_connection()
                cursor = await conn.execute(
                    "SELECT notification_status FROM properties WHERE unique_id = ?",
                    (prop.unique_id,),
                )
                row = await cursor.fetchone()
                assert row is not None
                assert row["notification_status"] == "failed"

                await storage_inst.close()

    async def test_pipeline_exception_marks_run_failed(self, pipeline_settings: Settings):
        """RuntimeError during scrape should mark pipeline run as failed."""
        error_msg = "Scraper network timeout"

        with _pipeline_mocks(scrape_return=[]) as ctx:

            async def _intercept_close(self_storage):
                pass

            # Override scrape to raise after pipeline run is created
            with (
                patch.object(PropertyStorage, "close", _intercept_close),
                patch(
                    "home_finder.pipeline.collection.scrape_all_platforms",
                    new_callable=AsyncMock,
                    side_effect=RuntimeError(error_msg),
                ),
                pytest.raises(RuntimeError, match=error_msg),
            ):
                await run_pipeline(pipeline_settings)

            storage = ctx.storage_capture.instance
            assert storage is not None

            run = await storage.runs.get_last_pipeline_run()
            assert run is not None
            assert run["status"] == "failed"
            assert error_msg in run["error_message"]

            # Cleanup still happens (finally block)
            ctx.notifier.close.assert_awaited_once()

            await storage.close()

    async def test_disabled_analysis_persists_home_and_suppresses_delivery(
        self,
        make_property: Callable[..., Property],
    ):
        """Disabled analysis retains the home and suppresses its discovery alert."""
        settings_no_quality = Settings(
            telegram_bot_token=SecretStr("fake:test-token"),
            telegram_chat_id=0,
            database_path=":memory:",
            search_areas="e8",
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            enable_quality_filter=False,
            require_floorplan=False,
            anthropic_api_key=SecretStr(""),
        )

        props = [
            make_property(
                source=PropertySource.OPENRENT,
                source_id="nq-1",
                price_pcm=1800,
                postcode="E8 3RH",
            ),
        ]

        with _pipeline_mocks(scrape_return=props) as ctx:

            async def _intercept_close(self_storage):
                pass

            with patch.object(PropertyStorage, "close", _intercept_close):
                await run_pipeline(settings_no_quality)

            storage = ctx.storage_capture.instance
            assert storage is not None

            # Quality filter never constructed
            ctx.quality_cls.assert_not_called()

            # Property still saved
            count = await storage.web.get_property_count()
            assert count == 1

            # The callback runs, but an unrated home creates no sendable alert.
            assert ctx.notifier.send_merged_property_notification.call_count == 1

            run = await storage.runs.get_last_pipeline_run()
            assert run is not None
            assert run["status"] == "completed"
            assert run["notified_count"] == 0
            deliveries = await storage.deliveries.report()
            assert deliveries["counts"] == {"suppressed": 1}

            await storage.close()

    async def test_forbidden_delivery_blocks_intent_and_updates_legacy_status(
        self,
        pipeline_settings: Settings,
        pipeline_properties: list[Property],
        make_quality_analysis: Callable[..., PropertyQualityAnalysis],
    ):
        """A forbidden Bot API response blocks delivery and projects the legacy failed status."""
        with _pipeline_mocks(
            scrape_return=pipeline_properties,
            quality_side_effect=lambda merged, **kw: (merged, make_quality_analysis()),
            notify_return=False,  # The Bot API fake raises TelegramForbiddenError.
        ) as ctx:

            async def _intercept_close(self_storage):
                pass

            with patch.object(PropertyStorage, "close", _intercept_close):
                await run_pipeline(pipeline_settings)

            storage = ctx.storage_capture.instance
            assert storage is not None

            # Pipeline still completes
            run = await storage.runs.get_last_pipeline_run()
            assert run is not None
            assert run["status"] == "completed"
            assert run["notified_count"] == 0

            deliveries = await storage.deliveries.report()
            assert deliveries["counts"] == {"blocked": 2}

            conn = await storage._get_connection()
            cursor = await conn.execute("SELECT notification_status FROM properties")
            rows = await cursor.fetchall()
            assert len(rows) == 2
            for row in rows:
                assert row["notification_status"] == "failed"

            await storage.close()
