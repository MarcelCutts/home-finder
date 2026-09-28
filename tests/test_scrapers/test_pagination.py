"""Retention and stop semantics, independent of any portal transport."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from home_finder.scrapers.base import ScrapeCancelled
from home_finder.scrapers.openrent import OpenRentScraper


async def test_known_page_retains_changed_quote_before_stop(make_property):
    changed = make_property(price_pcm=1750)
    fetch = AsyncMock(return_value=[changed])
    result = await OpenRentScraper()._paginate(
        fetch, max_pages=5, known_source_ids={changed.source_id}
    )
    assert result.properties == result.observations == [changed]
    assert result.stop_reason == "known_page"
    assert result.is_healthy
    fetch.assert_awaited_once_with(0)


async def test_processing_cap_retains_whole_fetched_page(make_property):
    page = [make_property() for _ in range(3)]
    fetch = AsyncMock(return_value=page)
    result = await OpenRentScraper()._paginate(fetch, max_pages=5, max_results=1)
    assert result.properties == page[:1]
    assert result.observations == page
    assert result.stop_reason == "result_limit"
    fetch.assert_awaited_once_with(0)


@pytest.mark.parametrize("failure", [None, RuntimeError("fixture failure")])
async def test_later_failure_preserves_prefix_and_is_unhealthy(make_property, failure):
    prop = make_property()
    result = await OpenRentScraper()._paginate(
        AsyncMock(side_effect=[[prop], failure]), max_pages=2
    )
    assert result.properties == result.observations == [prop]
    assert result.pages_fetched == result.pages_failed == 1
    assert not result.is_healthy


async def test_duplicate_page_keeps_latest_quote_without_duplicate_work(make_property):
    original = make_property(price_pcm=1800)
    changed = original.model_copy(update={"price_pcm": 1750})
    fetch = AsyncMock(side_effect=[[original], [original, changed]])
    result = await OpenRentScraper()._paginate(fetch, max_pages=5)
    assert result.properties == result.observations == [changed]
    assert result.stop_reason == "duplicate_page"
    assert fetch.await_count == 2


async def test_zero_cap_does_not_fetch():
    fetch = AsyncMock()
    result = await OpenRentScraper()._paginate(fetch, max_pages=5, max_results=0)
    assert not result.observations
    assert result.stop_reason == "result_limit"
    fetch.assert_not_awaited()


async def test_cancellation_carries_received_prefix(make_property):
    prop = make_property()
    started = asyncio.Event()

    async def fetch(index):
        if index == 0:
            return [prop]
        started.set()
        await asyncio.Future()

    task = asyncio.create_task(OpenRentScraper()._paginate(fetch, max_pages=2))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(ScrapeCancelled) as cancelled:
        await task
    result = cancelled.value.result
    assert result.observations == [prop]
    assert result.pages_fetched == 1
    assert result.stop_reason == "cancelled"
    assert not result.is_healthy
