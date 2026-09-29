"""Receipt provenance survives image failure, cancellation and cross-source enrichment."""

import asyncio
import hashlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import HttpUrl, ValidationError

from home_finder.db import PropertyStorage
from home_finder.filters.detail_enrichment import DetailEvidenceError, enrich_merged_properties
from home_finder.models import MergedProperty, PropertySource
from home_finder.models.observations import SourceObservation
from home_finder.scrapers.detail_fetcher import DetailFetcher, DetailPageData
from home_finder.scrapers.observations import detail_observation
from home_finder.utils.listing_identity import listing_id

AT = datetime(2026, 9, 1, tzinfo=UTC)
URLS = {
    PropertySource.RIGHTMOVE: "https://www.rightmove.co.uk/properties/100001",
    PropertySource.ZOOPLA: "https://www.zoopla.co.uk/to-rent/details/100001/",
    PropertySource.OPENRENT: "https://www.openrent.co.uk/property-to-rent/london/flat/100001",
    PropertySource.ONTHEMARKET: "https://www.onthemarket.com/details/100001/",
}


def captured(prop):
    data = DetailPageData(
        description="Received detail with café kitchen <tag>",
        features=["Gas hob"],
        gallery_urls=["https://example.com/received.jpg"],
    )
    data.source_observation = detail_observation(prop, data, observed_at=AT)
    return data


@pytest.mark.parametrize("source", list(PropertySource))
async def test_all_detail_boundaries_record_parsed_values_and_actual_receipt_time(
    source, make_property, fixtures_path, monkeypatch
):
    prop = make_property(source=source, source_id="100001", url=HttpUrl(URLS[source]))
    body = (fixtures_path / f"{source.value}_detail_with_floorplan.html").read_text()
    response = httpx.Response(200, text=body, request=httpx.Request("GET", str(prop.url)))
    async with DetailFetcher() as fetcher:
        monkeypatch.setattr(fetcher, "_httpx_get_with_retry", AsyncMock(return_value=response))
        monkeypatch.setattr(fetcher, "_curl_get_with_retry", AsyncMock(return_value=response))
        before = datetime.now(UTC)
        detail = await fetcher.fetch_detail_page(prop)
    event = detail.source_observation
    assert before <= event.observed_at <= datetime.now(UTC)
    assert event.source_unique_id == prop.unique_id and event.origin == "detail"
    assert event.parser.name == f"{source.value}.detail" and event.parser.code_sha256
    assert event.raw_capture is None
    assert event.request.requested_url_sha256 == hashlib.sha256(str(prop.url).encode()).hexdigest()
    assert event.request.query is None and event.request.page_index is None
    assert (
        event.fields.description.value if event.fields.description else None
    ) == detail.description
    assert (event.fields.features.value if event.fields.features else None) == (
        tuple(detail.features) if detail.features else None
    )
    assert event.fields.gallery_urls.value == tuple(HttpUrl(url) for url in detail.gallery_urls)
    assert event.fields.title is event.fields.price_pcm is event.fields.bedrooms is None


def test_unsupported_media_set_is_explicitly_unavailable_without_leaking_query(sample_property):
    data = DetailPageData(
        description="Retained text",
        gallery_urls=["https://example.com/public.jpg", "https://example.com/signed?token=private"],
        floorplan_url="https://example.com/plan.jpg",
    )
    event = detail_observation(sample_property, data, observed_at=AT)
    assert event.fields.description.value == "Retained text"
    assert event.fields.gallery_urls is None
    assert event.normalization_omissions == {"gallery_urls": "unsupported_url"}
    assert "token" not in event.payload_json() and "private" not in event.payload_json()
    assert event.fields.floorplan_urls.value == (HttpUrl(data.floorplan_url),)
    invalid = event.model_dump(mode="json")
    invalid["fields"]["gallery_urls"] = {"value": ["https://example.com/public.jpg"]}
    with pytest.raises(ValidationError, match="omitted field"):
        SourceObservation.model_validate(invalid)


@pytest.mark.parametrize("source", list(PropertySource))
@pytest.mark.parametrize("status,challenge", [(429, False), (200, True)])
async def test_failed_or_challenged_responses_never_become_positive_observations(
    source, status, challenge, make_property, fixtures_path, monkeypatch
):
    prop = make_property(source=source, source_id="100001", url=HttpUrl(URLS[source]))
    body = (fixtures_path / f"{source.value}_detail_with_floorplan.html").read_text()
    response = httpx.Response(
        status,
        text=body,
        request=httpx.Request("GET", str(prop.url)),
        headers={"cf-mitigated": "challenge"} if challenge else {},
    )
    async with DetailFetcher() as fetcher:
        monkeypatch.setattr(fetcher, "_httpx_get_with_retry", AsyncMock(return_value=response))
        monkeypatch.setattr(fetcher, "_curl_get_with_retry", AsyncMock(return_value=response))
        assert await fetcher.fetch_detail_page(prop) is None


def test_empty_detail_is_not_positive_evidence_and_omissions_do_not_rewrite_old_payloads(
    sample_property,
):
    assert detail_observation(sample_property, DetailPageData(), observed_at=AT) is None
    event = captured(sample_property).source_observation
    assert "normalization_omissions" not in event.payload_json()


@pytest.mark.parametrize(
    "url",
    [
        "https://www.rightmove.co.uk.evil.example/properties/100001",
        "https://user:password@www.rightmove.co.uk/properties/100001",
        "https://www.rightmove.co.uk:8443/properties/100001",
        "https://www.rightmove.co.uk/properties/100001/nearby",
    ],
)
def test_provider_identity_requires_own_host_and_exact_path(url):
    assert listing_id("rightmove", url) is None


async def test_cross_source_detail_uses_its_own_id_and_survives_save_reload(
    make_property, fixtures_path, monkeypatch
):
    canonical = make_property(
        source=PropertySource.OPENRENT,
        source_id="200002",
        url=HttpUrl("https://www.openrent.co.uk/200002"),
    )
    rightmove = make_property(
        source=PropertySource.RIGHTMOVE,
        source_id="100001",
        url=HttpUrl(URLS[PropertySource.RIGHTMOVE]),
    )
    merged = MergedProperty(
        canonical=canonical,
        sources=(canonical.source, rightmove.source),
        source_urls={canonical.source: canonical.url, rightmove.source: rightmove.url},
        min_price=canonical.price_pcm,
        max_price=canonical.price_pcm,
    )
    body = (fixtures_path / "rightmove_indexed_detail.html").read_text()
    response = httpx.Response(200, text=body, request=httpx.Request("GET", str(rightmove.url)))
    async with PropertyStorage(":memory:") as storage, DetailFetcher() as fetcher:
        await storage.upsert_source_listings([canonical, rightmove])
        monkeypatch.setattr(fetcher, "_httpx_get_with_retry", AsyncMock(return_value=response))
        monkeypatch.setattr(fetcher, "_fetch_openrent", AsyncMock(return_value=None))
        result = await enrich_merged_properties([merged], fetcher, storage=storage)
        enriched = result.enriched[0]
        history = await storage.observations.history(rightmove.unique_id)
        assert len(history) == 1 and history[0].source_id == "100001"
        assert history[0].fields.observed_floorplan_count.value == 0
        assert await storage.observations.history("rightmove:200002") == []
        await storage.pipeline.save_pre_analysis_properties([enriched], {})
        loaded = (await storage.pipeline.get_pending_analysis_properties())[0]
        text = loaded.source_text[rightmove.source]
        assert text.description == history[0].fields.description.value
        assert (
            text.description_observation_ids
            == text.features_observation_ids
            == (history[0].sha256,)
        )


async def test_image_failure_cannot_discard_received_detail_or_its_current_text(
    make_merged_property, tmp_path, monkeypatch
):
    merged = make_merged_property()
    detail = captured(merged.canonical)
    event = detail.source_observation
    async with PropertyStorage(":memory:") as storage, DetailFetcher() as fetcher:
        await storage.upsert_source_listings([merged.canonical])
        monkeypatch.setattr(fetcher, "fetch_detail_page", AsyncMock(return_value=detail))
        monkeypatch.setattr(
            fetcher, "download_image_bytes", AsyncMock(side_effect=RuntimeError("image failed"))
        )
        result = await enrich_merged_properties(
            [merged], fetcher, data_dir=str(tmp_path), storage=storage
        )
        assert len(result.failed) == 1
        assert await storage.observations.get(event.sha256) == event
        async with storage._read_connection() as conn:
            row = await (await conn.execute("SELECT description FROM source_listings")).fetchone()
            assert row[0] == detail.description
        await storage.retain_detail_observation(event)
        assert len(await storage.observations.history(merged.unique_id)) == 1


async def test_detail_projection_failure_rolls_back_receipt(sample_property, monkeypatch):
    from home_finder.db import storage as storage_module

    event = captured(sample_property).source_observation
    async with PropertyStorage(":memory:") as storage:
        await storage.upsert_source_listings([sample_property])
        monkeypatch.setattr(
            storage_module, "apply_source_text", AsyncMock(side_effect=RuntimeError("injected"))
        )
        with pytest.raises(RuntimeError, match="injected"):
            await storage.retain_detail_observation(event)
        assert await storage.observations.get(event.sha256) is None


async def test_evidence_write_failure_stops_enrichment_before_image_work(
    make_merged_property, tmp_path, monkeypatch
):
    merged = make_merged_property()
    async with PropertyStorage(":memory:") as storage, DetailFetcher() as fetcher:
        monkeypatch.setattr(
            fetcher, "fetch_detail_page", AsyncMock(return_value=captured(merged.canonical))
        )
        download = AsyncMock()
        monkeypatch.setattr(fetcher, "download_image_bytes", download)
        monkeypatch.setattr(
            storage, "retain_detail_observation", AsyncMock(side_effect=RuntimeError("disk failed"))
        )
        with pytest.raises(DetailEvidenceError, match="write failed"):
            await enrich_merged_properties(
                [merged], fetcher, data_dir=str(tmp_path), storage=storage
            )
        download.assert_not_awaited()


async def test_captured_detail_requires_a_durable_sink(make_merged_property, monkeypatch):
    merged = make_merged_property()
    async with DetailFetcher() as fetcher:
        monkeypatch.setattr(
            fetcher, "fetch_detail_page", AsyncMock(return_value=captured(merged.canonical))
        )
        with pytest.raises(DetailEvidenceError, match="requires storage"):
            await enrich_merged_properties([merged], fetcher)


async def test_unresolved_other_portal_does_not_borrow_canonical_id(
    make_merged_property, monkeypatch
):
    original = make_merged_property(sources=(PropertySource.OPENRENT,))
    merged = original.model_copy(
        update={
            "sources": (PropertySource.OPENRENT, PropertySource.ZOOPLA),
            "source_urls": {
                **original.source_urls,
                PropertySource.ZOOPLA: HttpUrl("https://example.com/unresolved"),
            },
        }
    )
    async with DetailFetcher() as fetcher:
        fetch = AsyncMock(return_value=None)
        monkeypatch.setattr(fetcher, "fetch_detail_page", fetch)
        result = await enrich_merged_properties([merged], fetcher)
    assert len(result.failed) == 1
    fetch.assert_awaited_once()
    assert fetch.call_args.args[0].source == PropertySource.OPENRENT


async def test_repeated_cancellation_settles_received_detail_before_propagating(
    make_merged_property, tmp_path, monkeypatch
):
    merged = make_merged_property()
    detail = captured(merged.canonical)
    started, release = asyncio.Event(), asyncio.Event()
    async with PropertyStorage(":memory:") as storage, DetailFetcher() as fetcher:
        original = storage.retain_detail_observation

        async def delayed(event):
            started.set()
            await release.wait()
            await original(event)

        monkeypatch.setattr(storage, "retain_detail_observation", delayed)
        monkeypatch.setattr(fetcher, "fetch_detail_page", AsyncMock(return_value=detail))
        download = AsyncMock()
        monkeypatch.setattr(fetcher, "download_image_bytes", download)
        task = asyncio.create_task(
            enrich_merged_properties([merged], fetcher, data_dir=str(tmp_path), storage=storage)
        )
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        assert (
            await storage.observations.get(detail.source_observation.sha256)
            == detail.source_observation
        )
        download.assert_not_awaited()


async def test_download_cancellation_leaves_detail_durable(
    make_merged_property, tmp_path, monkeypatch
):
    merged = make_merged_property()
    detail = captured(merged.canonical)
    entered = asyncio.Event()
    async with PropertyStorage(":memory:") as storage, DetailFetcher() as fetcher:

        async def download(_url):
            assert await storage.observations.get(detail.source_observation.sha256) is not None
            entered.set()
            await asyncio.Future()

        monkeypatch.setattr(fetcher, "fetch_detail_page", AsyncMock(return_value=detail))
        monkeypatch.setattr(fetcher, "download_image_bytes", download)
        task = asyncio.create_task(
            enrich_merged_properties([merged], fetcher, data_dir=str(tmp_path), storage=storage)
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(await storage.observations.history(merged.unique_id)) == 1
