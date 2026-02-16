"""End-to-end tests for run_pipeline() with mocked external boundaries.

Tests the full pipeline path: scrape → filter → enrich → quality → save → notify,
using a real in-memory SQLite DB and mocking all external I/O (scrapers, Telegram,
Anthropic API, ward lookup, sleep delays).
"""

from collections.abc import Callable
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import HttpUrl

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.filters.detail_enrichment import EnrichmentResult
from home_finder.main import run_pipeline
from home_finder.models import (
    Property,
    PropertyImage,
    PropertyQualityAnalysis,
    PropertySource,
)

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
    return EnrichmentResult(enriched=enriched)


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
    mock_notifier = MagicMock()
    mock_notifier.send_property_notification = AsyncMock(return_value=notify_return)
    mock_notifier.send_merged_property_notification = AsyncMock(return_value=notify_return)
    mock_notifier.close = AsyncMock()

    # -- DetailFetcher mock --
    mock_fetcher_instance = MagicMock()
    mock_fetcher_instance.close = AsyncMock()

    # -- Quality filter mock --
    mock_quality_instance = MagicMock()
    if quality_side_effect is not None:
        mock_quality_instance.analyze_single_merged = AsyncMock(side_effect=quality_side_effect)
    else:
        # Default: return (merged, None) — no analysis
        mock_quality_instance.analyze_single_merged = AsyncMock(
            side_effect=lambda m, **kw: (m, None)
        )
    mock_quality_instance.close = AsyncMock()

    with (
        patch(
            "home_finder.main.scrape_all_platforms",
            new_callable=AsyncMock,
            return_value=scrape_return,
        ),
        patch(
            "home_finder.main.enrich_merged_properties",
            new_callable=AsyncMock,
            side_effect=enrich_side_effect,
        ),
        patch(
            "home_finder.main.DetailFetcher",
            return_value=mock_fetcher_instance,
        ) as mock_fetcher_cls,
        patch(
            "home_finder.main.PropertyQualityFilter",
            return_value=mock_quality_instance,
        ) as mock_quality_cls,
        patch(
            "home_finder.main.TelegramNotifier",
            return_value=mock_notifier,
        ) as mock_notifier_cls,
        patch("home_finder.main._lookup_wards", new_callable=AsyncMock),
        patch("home_finder.main.asyncio.sleep", new_callable=AsyncMock),
        patch.object(PropertyStorage, "__init__", _patched_storage_init),
    ):
        # Yield a dict of useful references
        ctx = type("Ctx", (), {
            "notifier": mock_notifier,
            "notifier_cls": mock_notifier_cls,
            "quality_cls": mock_quality_cls,
            "quality_instance": mock_quality_instance,
            "fetcher_cls": mock_fetcher_cls,
            "fetcher_instance": mock_fetcher_instance,
            "storage_capture": _StorageCapture,
        })()
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
        telegram_bot_token="fake:test-token",
        telegram_chat_id=0,
        database_path=":memory:",
        search_areas="e8",
        min_price=1500,
        max_price=2500,
        min_bedrooms=1,
        max_bedrooms=2,
        enable_quality_filter=True,
        require_floorplan=False,
        anthropic_api_key="test-anthropic-key",
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
            run = await storage.get_last_pipeline_run()
            assert run is not None
            assert run["status"] == "completed"
            assert run["scraped_count"] == 2
            assert run["new_count"] == 2
            assert run["enriched_count"] == 2
            assert run["analyzed_count"] == 2
            assert run["notified_count"] == 2
            assert run["duration_seconds"] is not None

            # Properties saved
            count = await storage.get_property_count()
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
                assert call.kwargs.get("quality_analysis") is not None or (
                    len(call.args) > 0
                )

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

            run = await storage.get_last_pipeline_run()
            assert run is not None
            assert run["status"] == "completed"

            # No notifications
            ctx.notifier.send_merged_property_notification.assert_not_awaited()
            ctx.notifier.send_property_notification.assert_not_awaited()

            # Cleanup still happens
            ctx.notifier.close.assert_awaited_once()

            await storage.close()

    async def test_retry_unsent_notifications(
        self,
        pipeline_settings: Settings,
        make_property: Callable[..., Property],
    ):
        """Pre-seeded failed notification should be retried on next run."""
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

                # Retry should have called send_property_notification for the failed one
                ctx.notifier.send_property_notification.assert_awaited_once()

                # After successful retry, property should be marked as 'sent'
                conn = await storage_inst._get_connection()
                cursor = await conn.execute(
                    "SELECT notification_status FROM properties WHERE unique_id = ?",
                    (prop.unique_id,),
                )
                row = await cursor.fetchone()
                assert row is not None
                assert row["notification_status"] == "sent"

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
                    "home_finder.main.scrape_all_platforms",
                    new_callable=AsyncMock,
                    side_effect=RuntimeError(error_msg),
                ),
                pytest.raises(RuntimeError, match=error_msg),
            ):
                await run_pipeline(pipeline_settings)

            storage = ctx.storage_capture.instance
            assert storage is not None

            run = await storage.get_last_pipeline_run()
            assert run is not None
            assert run["status"] == "failed"
            assert error_msg in run["error_message"]

            # Cleanup still happens (finally block)
            ctx.notifier.close.assert_awaited_once()

            await storage.close()

    async def test_quality_disabled_skips_analysis(
        self,
        make_property: Callable[..., Property],
    ):
        """Quality filter disabled: properties saved and notified without analysis."""
        settings_no_quality = Settings(
            telegram_bot_token="fake:test-token",
            telegram_chat_id=0,
            database_path=":memory:",
            search_areas="e8",
            min_price=1500,
            max_price=2500,
            min_bedrooms=1,
            max_bedrooms=2,
            enable_quality_filter=False,
            require_floorplan=False,
            anthropic_api_key="",
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
            count = await storage.get_property_count()
            assert count == 1

            # Notification sent (with quality_analysis=None)
            assert ctx.notifier.send_merged_property_notification.call_count == 1

            run = await storage.get_last_pipeline_run()
            assert run is not None
            assert run["status"] == "completed"

            await storage.close()

    async def test_notification_failure_marks_failed(
        self,
        pipeline_settings: Settings,
        pipeline_properties: list[Property],
    ):
        """Failed notification should mark property as 'failed' in DB."""
        with _pipeline_mocks(
            scrape_return=pipeline_properties,
            notify_return=False,  # All notifications fail
        ) as ctx:
            async def _intercept_close(self_storage):
                pass

            with patch.object(PropertyStorage, "close", _intercept_close):
                await run_pipeline(pipeline_settings)

            storage = ctx.storage_capture.instance
            assert storage is not None

            # Pipeline still completes
            run = await storage.get_last_pipeline_run()
            assert run is not None
            assert run["status"] == "completed"
            assert run["notified_count"] == 0

            # Properties should be marked as 'failed' notification
            conn = await storage._get_connection()
            cursor = await conn.execute(
                "SELECT notification_status FROM properties"
            )
            rows = await cursor.fetchall()
            assert len(rows) == 2
            for row in rows:
                assert row["notification_status"] == "failed"

            await storage.close()
