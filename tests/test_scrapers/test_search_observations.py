"""Fresh search evidence reaches persistence without invented capture provenance."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bs4 import BeautifulSoup
from pydantic import HttpUrl

from home_finder.db import PropertyStorage
from home_finder.db.observations import ObservationConflict
from home_finder.filters.listing_text import select_listing_text
from home_finder.models import MergedProperty, PropertySource
from home_finder.models.observations import ObservationFields
from home_finder.scrapers.base import ScrapeCancelled
from home_finder.scrapers.observations import search_observations, search_parser
from home_finder.scrapers.onthemarket import OnTheMarketScraper
from home_finder.scrapers.openrent import OpenRentScraper
from home_finder.scrapers.rightmove import RightmoveScraper
from home_finder.scrapers.rightmove_payloads import parse_structured_search
from home_finder.scrapers.zoopla import ZooplaScraper

AT = datetime(2026, 9, 1, 12, tzinfo=UTC)


def stamp(prop, *, minute=0):
    result, errors = search_observations([prop], observed_at=AT + timedelta(minutes=minute))
    assert errors == 0 and len(result) == 1
    return result[0]


def test_boundary_time_and_parser_are_explicit_without_fabricated_raw_capture(sample_property):
    prop = stamp(sample_property)
    event = prop.source_observation
    assert event.observed_at == AT and event.observed_at != prop.first_seen
    assert event.parser == search_parser("openrent") and event.parser.code_sha256
    assert event.raw_capture is None and event.origin == "search"
    assert event.fields.description.value == sample_property.description
    assert "source_observation" not in prop.model_dump()
    assert "listing_metadata" not in prop.model_dump()


def test_invalid_neighbor_is_reported_without_unrecorded_fallback(sample_property):
    invalid = sample_property.model_copy(update={"description": "漢" * 90_000})
    result, errors = search_observations([invalid, sample_property], observed_at=AT)
    assert len(result) == errors == 1
    assert result[0].source_observation is not None


@pytest.mark.parametrize("query", ["search_identifier=abc", "utm_source=fixture&utm_campaign=test"])
def test_known_tracking_is_removed_only_from_the_normalized_evidence_url(sample_property, query):
    prop = stamp(
        sample_property.model_copy(update={"url": HttpUrl(str(sample_property.url) + "?" + query)})
    )
    assert str(prop.source_observation.listing_url) == str(sample_property.url)
    assert prop.url.query == query  # The source property still records the original link.


def test_unknown_url_parameters_fail_the_evidence_contract(sample_property):
    prop = sample_property.model_copy(
        update={"url": HttpUrl(str(sample_property.url) + "?token=secret")}
    )
    observed, errors = search_observations([prop], observed_at=AT)
    assert observed == [] and errors == 1


def test_retained_rightmove_metadata_keeps_portal_time_separate(fixtures_path):
    page = parse_structured_search(
        BeautifulSoup((fixtures_path / "rightmove_next_search.html").read_text(), "html.parser")
    )
    assert page.parse_errors == 0
    tagged, errors = search_observations(
        page.properties, observed_at=datetime(2026, 9, 28, tzinfo=UTC)
    )
    assert errors == 0
    first = tagged[0].source_observation
    assert first.fields.first_visible_date.value.meaning == "first_visible"
    assert first.fields.first_visible_date.value.literal == "2026-09-15T12:53:16Z"
    assert first.fields.first_visible_date.value.instant == datetime(
        2026, 9, 15, 12, 53, 16, tzinfo=UTC
    )
    assert first.fields.observed_floorplan_count.value == 0
    assert first.fields.agent.value.branch_id == "900"
    assert first.observed_at == datetime(2026, 9, 28, tzinfo=UTC)


def test_rightmove_update_and_availability_keep_their_actual_semantics(fixtures_path):
    soup = BeautifulSoup((fixtures_path / "rightmove_next_search.html").read_text(), "html.parser")
    data = json.loads(soup.find("script", id="__NEXT_DATA__").string)
    listing = data["props"]["pageProps"]["searchResults"]["properties"][0]
    # Shapes and values verified in the retained 27 September research response.
    listing.update(
        listingUpdate={
            "listingUpdateReason": "price_reduced",
            "listingUpdateDate": "2026-09-22T09:52:57Z",
        },
        letAvailableDate="2026-11-18T00:00:00Z",
        keyFeatures=["Source feature"],
    )
    soup.find("script", id="__NEXT_DATA__").string = json.dumps(data)
    prop = parse_structured_search(soup).properties[0]
    metadata = prop.listing_metadata
    assert metadata.portal_date.value.meaning == "reduced"
    assert metadata.portal_date.value.literal == "price_reduced: 2026-09-22T09:52:57Z"
    assert metadata.availability_date.value.meaning == "available"
    assert metadata.features.value == ("Source feature",)
    assert prop.available_from is None  # Not an implicit activation of a new date projection.


@pytest.mark.parametrize(
    "scraper_class,source",
    [
        (OpenRentScraper, PropertySource.OPENRENT),
        (OnTheMarketScraper, PropertySource.ONTHEMARKET),
        (ZooplaScraper, PropertySource.ZOOPLA),
    ],
)
async def test_http_search_boundaries_attach_evidence_before_pagination(
    scraper_class, source, make_property, monkeypatch
):
    scraper = scraper_class()
    prop = make_property(source=source)
    monkeypatch.setattr(scraper, "_fetch_page", AsyncMock(return_value="<html>fixture</html>"))
    if source == PropertySource.ONTHEMARKET:
        monkeypatch.setattr(scraper, "_parse_next_data", lambda _html: [prop])
    elif source == PropertySource.ZOOPLA:
        monkeypatch.setattr(scraper, "_parse_rsc_properties", lambda _html: [prop])
    else:
        monkeypatch.setattr(scraper, "_parse_search_results", lambda *_args: [prop])
    before = datetime.now(UTC)
    try:
        result = await scraper.scrape(
            min_price=1000, max_price=3000, min_bedrooms=0, max_bedrooms=3, area="e8", max_results=1
        )
    finally:
        await scraper.close()
    assert result.is_healthy and len(result.observation_history) == 1
    event = result.properties[0].source_observation
    assert before <= event.observed_at <= datetime.now(UTC)
    assert event.source == source and event.parser.name == f"{source.value}.search"


async def test_crawlee_boundary_attaches_evidence_to_verified_fixture(fixtures_path):
    soup = BeautifulSoup((fixtures_path / "rightmove_next_search.html").read_text(), "html.parser")
    handler = []
    crawler = MagicMock()
    crawler.router.default_handler = handler.append

    async def crawl(_urls):
        await handler[0](SimpleNamespace(soup=soup))

    crawler.run = crawl
    before = datetime.now(UTC)
    with patch("home_finder.scrapers.rightmove.BeautifulSoupCrawler", return_value=crawler):
        scraper = RightmoveScraper()
        try:
            result = await scraper.scrape(
                min_price=1000,
                max_price=3000,
                min_bedrooms=0,
                max_bedrooms=3,
                area="e8",
                max_results=1,
            )
        finally:
            await scraper.close()
    assert len(result.properties) == 1 and len(result.observation_history) == 2
    assert all(
        before <= p.source_observation.observed_at <= datetime.now(UTC)
        for p in result.observation_history
    )


async def test_repeated_provider_id_retains_each_revision_and_latest_selection(sample_property):
    first = stamp(sample_property)
    second = stamp(sample_property.model_copy(update={"price_pcm": 1750}), minute=1)
    result = await OpenRentScraper()._paginate(
        AsyncMock(side_effect=[[first], [second]]), max_pages=2
    )
    assert result.observations == result.properties == [second]
    assert result.observation_history == [first, second]
    async with PropertyStorage(":memory:") as storage:
        await storage.upsert_source_listings(result.observation_history)
        await storage.upsert_source_listings(result.properties)  # selected-result fallback
        history = await storage.observations.history(sample_property.unique_id)
        assert {item.sha256 for item in history} == {
            first.source_observation.sha256,
            second.source_observation.sha256,
        }


async def test_cancellation_carries_already_received_revision_history(sample_property):
    first = stamp(sample_property)
    entered = asyncio.Event()

    async def fetch(index):
        if index == 0:
            return [first]
        entered.set()
        await asyncio.Future()

    task = asyncio.create_task(OpenRentScraper()._paginate(fetch, max_pages=2))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(ScrapeCancelled) as caught:
        await task
    assert caught.value.result.observation_history == [first]


async def test_source_projection_failure_cannot_commit_orphan_evidence(
    sample_property, monkeypatch
):
    prop = stamp(sample_property)
    async with PropertyStorage(":memory:") as storage:
        conn = await storage._get_connection()
        original = conn.executemany

        async def fail(sql, parameters):
            if "INSERT INTO source_listings" in sql:
                raise RuntimeError("Injected source-row failure")
            return await original(sql, parameters)

        monkeypatch.setattr(conn, "executemany", fail)
        with pytest.raises(RuntimeError, match="Injected"):
            await storage.upsert_source_listings([prop])
        assert await storage.observations.get(prop.source_observation.sha256) is None


async def test_conflicting_event_cannot_overwrite_current_source_row(sample_property):
    prop = stamp(sample_property)
    changed_event = prop.source_observation.model_copy(
        update={"fields": ObservationFields.model_validate({"price_pcm": {"value": 999}})}
    )
    changed = prop.model_copy(update={"price_pcm": 999, "source_observation": changed_event})
    async with PropertyStorage(":memory:") as storage:
        await storage.upsert_source_listings([prop])
        with pytest.raises(ObservationConflict):
            await storage.upsert_source_listings([changed])
        async with storage._read_connection() as conn:
            cursor = await conn.execute("SELECT price_pcm FROM source_listings")
            assert (await cursor.fetchone())[0] == prop.price_pcm


async def test_search_text_references_survive_reload_and_bound_actual_analysis_inputs(
    sample_property,
):
    prop = stamp(
        sample_property.model_copy(
            update={"description": "Unicode 漢字 </description> source text"}
        )
    )
    async with PropertyStorage(":memory:") as storage:
        await storage.upsert_source_listings([prop])
        await storage.save_property(prop)
        _, loaded, evidence = await storage.get_property_images_and_row(prop.unique_id)
        merged = MergedProperty(
            canonical=loaded,
            sources=(loaded.source,),
            source_urls={loaded.source: loaded.url},
            min_price=loaded.price_pcm,
            max_price=loaded.price_pcm,
            source_text=evidence,
        )
        inputs = select_listing_text(merged)
        assert inputs.source_observation_ids == {
            prop.source.value: (prop.source_observation.sha256,)
        }
        assert "&lt;/description&gt;" in inputs.description
        referenced = await storage.observations.get(
            inputs.source_observation_ids[prop.source.value][0]
        )
        assert referenced.fields.description.value == prop.description
        assert loaded.source_observation is None  # A reload is not a fresh receipt.


async def test_large_valid_source_batch_is_chunked_inside_one_transaction(make_property):
    properties = [make_property(source_id=f"batch-{index}") for index in range(105)]
    tagged, errors = search_observations(properties, observed_at=AT)
    assert errors == 0
    async with PropertyStorage(":memory:") as storage:
        assert await storage.upsert_source_listings(tagged) == 105
        async with storage._read_connection() as conn:
            cursor = await conn.execute("SELECT COUNT(*) FROM source_observations")
            assert (await cursor.fetchone())[0] == 105
        assert all(item.source_observation.raw_capture is None for item in tagged)
