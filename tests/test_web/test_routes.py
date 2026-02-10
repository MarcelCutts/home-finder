"""Tests for web dashboard routes."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import HttpUrl

from home_finder.config import Settings
from home_finder.db.storage import PropertyStorage
from home_finder.models import MergedProperty, Property, PropertySource
from home_finder.web.routes import router


@pytest.fixture
def settings() -> Settings:
    return Settings(
        telegram_bot_token="fake:token",
        telegram_chat_id=0,
        search_areas="e8,e3,n16",
        database_path=":memory:",
    )


@pytest_asyncio.fixture
async def storage() -> AsyncGenerator[PropertyStorage, None]:
    s = PropertyStorage(":memory:")
    await s.initialize()
    yield s


@pytest.fixture
def app(storage: PropertyStorage, settings: Settings) -> FastAPI:
    app = FastAPI()
    app.state.storage = storage
    app.state.settings = settings
    app.include_router(router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def prop_a() -> Property:
    return Property(
        source=PropertySource.OPENRENT,
        source_id="100",
        url=HttpUrl("https://openrent.com/100"),
        title="1 bed in E8",
        price_pcm=1900,
        bedrooms=1,
        address="10 Mare Street",
        postcode="E8 3RH",
        latitude=51.5465,
        longitude=-0.0553,
    )


@pytest.fixture
def merged_a(prop_a: Property) -> MergedProperty:
    return MergedProperty(
        canonical=prop_a,
        sources=(PropertySource.OPENRENT,),
        source_urls={PropertySource.OPENRENT: prop_a.url},
        min_price=1900,
        max_price=1900,
        descriptions={PropertySource.OPENRENT: "A nice flat."},
    )


class TestHealthCheck:
    def test_health_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestDashboard:
    def test_empty_dashboard(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "No properties found" in resp.text

    @pytest.mark.asyncio
    async def test_with_properties(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

    @pytest.mark.asyncio
    async def test_filter_by_bedrooms(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/?bedrooms=1")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

        resp = client.get("/?bedrooms=3")
        assert resp.status_code == 200
        assert "No properties found" in resp.text

    @pytest.mark.asyncio
    async def test_filter_by_area(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/?area=E8")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

        resp = client.get("/?area=N16")
        assert resp.status_code == 200
        assert "No properties found" in resp.text

    def test_sort_options(self, client: TestClient) -> None:
        for sort in ("newest", "price_asc", "price_desc", "rating_desc"):
            resp = client.get(f"/?sort={sort}")
            assert resp.status_code == 200

    def test_invalid_sort_defaults(self, client: TestClient) -> None:
        resp = client.get("/?sort=evil_injection")
        assert resp.status_code == 200

    def test_invalid_page_clamped(self, client: TestClient) -> None:
        resp = client.get("/?page=-5")
        assert resp.status_code == 200

    def test_page_beyond_total_clamped(self, client: TestClient) -> None:
        resp = client.get("/?page=9999")
        assert resp.status_code == 200

    def test_min_rating_clamped(self, client: TestClient) -> None:
        resp = client.get("/?min_rating=99")
        assert resp.status_code == 200

    def test_bedrooms_clamped(self, client: TestClient) -> None:
        resp = client.get("/?bedrooms=999")
        assert resp.status_code == 200

    def test_dynamic_search_areas(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "E8" in resp.text
        assert "E3" in resp.text
        assert "N16" in resp.text

    def test_htmx_request_returns_partial(self, client: TestClient) -> None:
        resp = client.get("/", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        # Partial should NOT contain the full page base layout
        assert "<!DOCTYPE html>" not in resp.text

    def test_non_htmx_returns_full_page(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "<!DOCTYPE html>" in resp.text

    @pytest.mark.asyncio
    async def test_properties_json_in_page(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/")
        assert "propertiesMapData" in resp.text


class TestPropertyDetail:
    @pytest.mark.asyncio
    async def test_found(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

    def test_not_found(self, client: TestClient) -> None:
        resp = client.get("/property/nonexistent:999")
        assert resp.status_code == 404
        assert "Property not found" in resp.text
        # Should have navigation back to dashboard
        assert "Dashboard" in resp.text

    @pytest.mark.asyncio
    async def test_area_context_populated(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        # E8 should have area context data
        assert "E8" in resp.text

    @pytest.mark.asyncio
    async def test_description_rendered(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "A nice flat." in resp.text

    @pytest.mark.asyncio
    async def test_xss_in_description_escaped(
        self, client: TestClient, storage: PropertyStorage, prop_a: Property
    ) -> None:
        merged = MergedProperty(
            canonical=prop_a,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: prop_a.url},
            min_price=1900,
            max_price=1900,
            descriptions={PropertySource.OPENRENT: '<script>alert("xss")</script>'},
        )
        await storage.save_merged_property(merged)
        resp = client.get(f"/property/{merged.unique_id}")
        assert resp.status_code == 200
        assert "<script>" not in resp.text
        assert "&lt;script&gt;" in resp.text
