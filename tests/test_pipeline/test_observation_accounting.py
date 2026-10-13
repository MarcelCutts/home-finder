"""Real storage and orchestration; external effects are replaced at their boundaries."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.main import _run_pipeline_with_storage
from home_finder.models import ListingTextEvidence, PropertySource
from home_finder.pipeline.scraping import _run_scrape
from home_finder.scrapers.base import ScrapeResult
from home_finder.scrapers.openrent import OpenRentScraper


@pytest.fixture
async def storage():
    async with PropertyStorage(":memory:") as db:
        yield db


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        search_areas="e8",
        data_dir=str(tmp_path),
        database_path=":memory:",
        min_price=1500,
        max_price=2500,
        min_bedrooms=1,
        max_bedrooms=2,
        enable_quality_filter=False,
        require_floorplan=False,
        traveltime_app_id="",
        traveltime_api_key=SecretStr(""),
    )


@pytest.fixture
def boundaries():
    scraper = MagicMock()
    scraper.source = PropertySource.OPENRENT
    scraper.max_areas_per_run = None
    scraper.should_skip_remaining_areas = False
    scraper.area_delay = AsyncMock()
    scraper.close = AsyncMock()
    scraper.scrape = AsyncMock(return_value=ScrapeResult(pages_fetched=1))
    with (
        patch("home_finder.pipeline.scraping.OpenRentScraper", return_value=scraper),
        patch("home_finder.main.TelegramNotifier", return_value=AsyncMock()) as notifier,
        patch("home_finder.pipeline.analysis.PropertyQualityFilter") as quality,
        patch("home_finder.pipeline.scraping.random.shuffle"),
    ):
        yield scraper, notifier, quality


async def rows(storage, table):
    async with storage._read_connection() as conn:
        return [
            dict(row) for row in await (await conn.execute(f"SELECT * FROM {table}")).fetchall()
        ]


async def run(settings, storage, **kwargs):
    await _run_pipeline_with_storage(settings, storage, only_scrapers={"openrent"}, **kwargs)


@pytest.mark.parametrize("price", [1750, 3000])
async def test_known_quote_refreshes_before_criteria_without_analysis(
    settings, storage, boundaries, make_property, price
):
    scraper, _, quality = boundaries
    old = make_property(price_pcm=1800)
    await storage.save_property(old)
    await storage.mark_notified(old.unique_id)
    changed = old.model_copy(
        update={
            "price_pcm": price,
            "description": "New listing text",
            "search_text": ListingTextEvidence.observed(
                observed_at=datetime.now(UTC), description="New listing text", origin="search"
            ),
        }
    )
    scraper.scrape.return_value = ScrapeResult(
        properties=[changed], observations=[changed], pages_fetched=1, stop_reason="known_page"
    )

    await run(settings, storage)
    await run(settings, storage)  # Same evidence must not create another price event.

    prop = await storage.get_property(old.unique_id)
    assert prop.property.price_pcm == price
    assert prop.notification_status.value == "sent"
    assert len(await storage.get_price_history(old.unique_id)) == 1
    source = (await rows(storage, "source_listings"))[0]
    assert source["price_pcm"] == price
    assert source["description"] == "New listing text"
    latest = await storage.pipeline.get_last_pipeline_run()
    assert latest["status"] == "completed"
    assert latest["scraped_count"] == 1
    assert latest["criteria_filtered_count"] == (1 if price <= 2500 else 0)
    assert latest["new_count"] == latest["analyzed_count"] == 0
    assert len(await rows(storage, "scraper_runs")) == 2
    quality.assert_not_called()


async def test_cap_retains_extra_observations_and_counts_only_attempted_area(
    settings, storage, boundaries, make_property
):
    scraper, _, quality = boundaries
    first, extra = make_property(price_pcm=3000), make_property(price_pcm=3100)
    settings.search_areas = "e8,n1,e5"
    scraper.scrape.return_value = ScrapeResult(
        properties=[first], observations=[first, extra], pages_fetched=1, stop_reason="result_limit"
    )
    await run(settings, storage, max_per_scraper=1)
    assert {row["unique_id"] for row in await rows(storage, "source_listings")} == {
        first.unique_id,
        extra.unique_id,
    }
    latest = await storage.pipeline.get_last_pipeline_run()
    assert latest["scraped_count"] == 2
    assert latest["criteria_filtered_count"] == 0
    metrics = (await rows(storage, "scraper_runs"))[0]
    assert metrics["areas_attempted"] == metrics["areas_completed"] == 1
    assert metrics["properties_found"] == 2
    scraper.area_delay.assert_not_awaited()
    quality.assert_not_called()


@pytest.mark.parametrize(
    ("result", "status"),
    [
        (ScrapeResult(pages_fetched=1, stop_reason="empty_page"), "completed"),
        (ScrapeResult(pages_failed=1), "failed"),
        (ScrapeResult(pages_fetched=1, parse_errors=1), "failed"),
        (
            ScrapeResult(pages_fetched=1, parse_errors=1, failure_reasons=["challenge"]),
            "failed",
        ),
        (
            ScrapeResult(pages_fetched=1, parse_errors=1, failure_reasons=["unrecognized_page"]),
            "failed",
        ),
    ],
)
async def test_empty_and_failed_collection_remain_distinguishable(
    settings, storage, boundaries, result, status
):
    scraper, _, quality = boundaries
    scraper.scrape.return_value = result
    await run(settings, storage)
    latest = await storage.pipeline.get_last_pipeline_run()
    assert latest["status"] == status
    metrics = (await rows(storage, "scraper_runs"))[0]
    assert metrics["is_healthy"] == (status == "completed")
    assert metrics["pages_failed"] == result.pages_failed
    assert metrics["parse_errors"] == result.parse_errors
    if result.failure_reasons:
        assert metrics["error_message"] == ",".join(result.failure_reasons)
    quality.assert_not_called()


async def test_later_page_failure_retains_prefix_and_marks_run_partial(
    settings, storage, boundaries, make_property
):
    scraper, _, quality = boundaries
    prop = make_property(price_pcm=3000)

    async def scrape(**kwargs):
        return await OpenRentScraper()._paginate(
            AsyncMock(side_effect=[[prop], RuntimeError("later page failed")]), max_pages=2
        )

    scraper.scrape.side_effect = scrape
    await run(settings, storage)
    latest = await storage.pipeline.get_last_pipeline_run()
    assert latest["status"] == "partial"
    assert latest["scraped_count"] == 1
    metrics = (await rows(storage, "scraper_runs"))[0]
    assert metrics["pages_fetched"] == metrics["pages_failed"] == 1
    assert metrics["is_healthy"] == 0
    assert len(await rows(storage, "source_listings")) == 1
    quality.assert_not_called()


async def test_breaker_does_not_count_unattempted_areas(settings, storage, boundaries):
    scraper, _, _ = boundaries
    settings.search_areas = "e8,n1,e5"

    async def scrape(**kwargs):
        scraper.should_skip_remaining_areas = True
        return ScrapeResult(pages_failed=1)

    scraper.scrape.side_effect = scrape
    await run(settings, storage)
    metrics = (await rows(storage, "scraper_runs"))[0]
    assert metrics["areas_attempted"] == metrics["areas_completed"] == 1
    assert metrics["is_healthy"] == 0


async def test_floorplan_exit_keeps_prior_funnel_counts(
    settings, storage, boundaries, make_property
):
    scraper, _, quality = boundaries
    settings.require_floorplan = True
    # The orchestrator fixture uses one source; floorplan eligibility uses the
    # actual property's source, and OpenRent is intentionally exempt.
    prop = make_property(source=PropertySource.RIGHTMOVE)
    scraper.scrape.return_value = ScrapeResult(properties=[prop], pages_fetched=1)

    async def enrich(merged, *args, **kwargs):
        return merged

    with patch("home_finder.pipeline.stages._run_enrichment", side_effect=enrich):
        await run(settings, storage)
    latest = await storage.pipeline.get_last_pipeline_run()
    for key in (
        "scraped_count",
        "criteria_filtered_count",
        "location_filtered_count",
        "new_property_count",
        "enriched_count",
        "post_dedup_count",
    ):
        assert latest[key] == 1, key
    assert latest["post_floorplan_count"] == latest["new_count"] == 0
    assert latest["enrichment_seconds"] >= 0
    assert len(await rows(storage, "scraper_runs")) == 1
    quality.assert_not_called()


async def test_cancellation_keeps_prefix_and_settles_repeated_cancel_during_accounting(
    settings, storage, boundaries, make_property
):
    scraper, _, quality = boundaries
    prop = make_property()
    second_page = asyncio.Event()
    recording = asyncio.Event()
    release_recording = asyncio.Event()
    original_save = storage.pipeline.save_scraper_runs

    async def fetch(index):
        if index == 0:
            return [prop]
        second_page.set()
        await asyncio.Future()

    async def scrape(**kwargs):
        return await OpenRentScraper()._paginate(fetch, max_pages=2)

    async def blocked_save(*args, **kwargs):
        recording.set()
        await release_recording.wait()
        await original_save(*args, **kwargs)

    scraper.scrape.side_effect = scrape
    with patch.object(storage.pipeline, "save_scraper_runs", side_effect=blocked_save):
        task = asyncio.create_task(run(settings, storage))
        await asyncio.wait_for(second_page.wait(), 2)
        task.cancel()
        await asyncio.wait_for(recording.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release_recording.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    latest = await storage.pipeline.get_last_pipeline_run()
    assert latest["status"] == "cancelled"
    assert latest["scraped_count"] == 1
    metrics = (await rows(storage, "scraper_runs"))[0]
    assert metrics["pages_fetched"] == 1
    assert metrics["areas_attempted"] == 1
    assert metrics["areas_completed"] == 0
    assert metrics["error_message"] == "cancelled"
    assert metrics["is_healthy"] == 0
    assert len(await rows(storage, "source_listings")) == 1
    scraper.close.assert_awaited_once()
    quality.assert_not_called()


async def test_cancel_during_observation_write_finishes_received_facts(
    settings, storage, boundaries, make_property
):
    scraper, _, _ = boundaries
    prop = make_property()
    scraper.scrape.return_value = ScrapeResult(properties=[prop], pages_fetched=1)
    writing, release = asyncio.Event(), asyncio.Event()
    original_upsert = storage.upsert_source_listings

    async def blocked_upsert(properties):
        writing.set()
        await release.wait()
        return await original_upsert(properties)

    with patch.object(storage, "upsert_source_listings", side_effect=blocked_upsert):
        task = asyncio.create_task(run(settings, storage))
        await asyncio.wait_for(writing.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    assert len(await rows(storage, "source_listings")) == 1
    assert (await storage.pipeline.get_last_pipeline_run())["status"] == "cancelled"


async def test_observation_storage_failure_is_not_swallowed_as_scraper_failure(
    settings, storage, boundaries, make_property
):
    scraper, _, _ = boundaries
    scraper.scrape.return_value = ScrapeResult(properties=[make_property()], pages_fetched=1)
    with (
        patch.object(storage, "upsert_source_listings", side_effect=RuntimeError("write failed")),
        pytest.raises(RuntimeError, match="write failed"),
    ):
        await run(settings, storage)
    assert (await storage.pipeline.get_last_pipeline_run())["status"] == "failed"
    scraper.close.assert_awaited_once()


@pytest.mark.parametrize("failure_point", ["construction", "entry"])
async def test_notifier_setup_failure_finalizes_created_run(
    settings, storage, boundaries, failure_point
):
    _, notifier, _ = boundaries
    if failure_point == "construction":
        notifier.side_effect = RuntimeError("notifier setup failed")
    else:
        notifier.return_value.__aenter__.side_effect = RuntimeError("notifier setup failed")
    with pytest.raises(RuntimeError, match="notifier setup failed"):
        await run(settings, storage)
    assert (await storage.pipeline.get_last_pipeline_run())["status"] == "failed"


@pytest.mark.parametrize("first_stop", ["page_error", "result_limit"])
async def test_incomplete_search_does_not_hide_later_pages(
    settings, storage, boundaries, make_property, first_stop
):
    scraper, _, _ = boundaries
    first, later = make_property(), make_property()
    scraper.scrape.return_value = ScrapeResult(
        properties=[first],
        observations=[first],
        pages_fetched=1,
        pages_failed=int(first_stop == "page_error"),
        stop_reason=first_stop,
    )
    await _run_scrape(settings, storage, only_scrapers={"openrent"}, max_per_scraper=1)
    # Even if the selected prefix was subsequently processed, it cannot certify
    # that there are no older unseen listings beyond it.
    if first_stop == "result_limit":
        await storage.save_property(first)

    async def resume(**kwargs):
        async def fetch_page(index):
            return [[first], [later], []][index]

        # Exercise the shared paginator; OpenRent itself disables known-ID stops.
        return await OpenRentScraper._paginate(
            scraper,
            fetch_page,
            max_pages=3,
            known_source_ids=kwargs.get("known_source_ids"),
        )

    scraper.scrape.side_effect = resume
    result = await _run_scrape(settings, storage, only_scrapers={"openrent"})
    assert result is not None
    assert {prop.unique_id for prop in result[0]} == {first.unique_id, later.unique_id}
    assert result[1][0].pages_fetched == 3
