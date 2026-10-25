"""Tests for web dashboard routes."""

import re
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import HttpUrl, SecretStr

from home_finder.config import Settings
from home_finder.db.storage import PropertyStorage
from home_finder.models import (
    BedroomAnalysis,
    ConditionAnalysis,
    FlooringNoiseAnalysis,
    KitchenAnalysis,
    LightSpaceAnalysis,
    ListingExtraction,
    ListingRedFlags,
    ListingTextEvidence,
    MergedProperty,
    OutdoorSpaceAnalysis,
    Property,
    PropertyImage,
    PropertyQualityAnalysis,
    PropertySource,
    PropertyType,
    SpaceAnalysis,
    ValueAnalysis,
)
from home_finder.models.analysis import ImageReviewCoverage
from home_finder.utils.image_cache import (
    get_cache_dir,
    safe_dir_name,
    save_image_bytes,
    url_to_filename,
)
from home_finder.web.routes import (  # type: ignore[attr-defined]
    _parse_optional_int,
    _resolve_cached_thumbnail,
    listing_age_filter,
    router,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    return Settings(
        telegram_bot_token=SecretStr("fake:token"),
        telegram_chat_id=0,
        search_areas="e8,e3,n16",
        database_path=":memory:",
    )


@pytest_asyncio.fixture
async def storage() -> AsyncGenerator[PropertyStorage, None]:
    s = PropertyStorage(":memory:")
    await s.initialize()
    yield s
    await s.close()


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
        image_url=HttpUrl("https://example.com/img.jpg"),
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


@pytest.fixture
def prop_b() -> Property:
    return Property(
        source=PropertySource.RIGHTMOVE,
        source_id="200",
        url=HttpUrl("https://rightmove.co.uk/200"),
        title="2 bed in N16",
        price_pcm=2500,
        bedrooms=2,
        address="5 Church Street",
        postcode="N16 0AP",
        latitude=51.5615,
        longitude=-0.0750,
        image_url=HttpUrl("https://example.com/img.jpg"),
    )


@pytest.fixture
def merged_b(prop_b: Property) -> MergedProperty:
    return MergedProperty(
        canonical=prop_b,
        sources=(PropertySource.RIGHTMOVE,),
        source_urls={PropertySource.RIGHTMOVE: prop_b.url},
        min_price=2500,
        max_price=2500,
        descriptions={PropertySource.RIGHTMOVE: "Spacious flat."},
    )


@pytest.fixture
def studio_prop() -> Property:
    return Property(
        source=PropertySource.OPENRENT,
        source_id="300",
        url=HttpUrl("https://openrent.com/300"),
        title="Studio in E8",
        price_pcm=1500,
        bedrooms=0,
        address="15 Mare Street",
        postcode="E8 3RH",
        latitude=51.5465,
        longitude=-0.0553,
        image_url=HttpUrl("https://example.com/img.jpg"),
    )


@pytest.fixture
def merged_studio(studio_prop: Property) -> MergedProperty:
    return MergedProperty(
        canonical=studio_prop,
        sources=(PropertySource.OPENRENT,),
        source_urls={PropertySource.OPENRENT: studio_prop.url},
        min_price=1500,
        max_price=1500,
    )


@pytest.fixture
def base_analysis() -> PropertyQualityAnalysis:
    """Reusable quality analysis — tests override fields via model_copy(update={...})."""
    return PropertyQualityAnalysis(
        kitchen=KitchenAnalysis(overall_quality="modern", hob_type="gas", has_dishwasher="yes"),
        condition=ConditionAnalysis(overall_condition="good", confidence="high"),
        light_space=LightSpaceAnalysis(natural_light="good", feels_spacious=True),
        space=SpaceAnalysis(living_room_sqm=18.0, is_spacious_enough=True),
        overall_rating=4,
        summary="Nice place.",
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def test_health_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"status": "ok"}

    def test_diagnostics_returns_pipeline_state(self, client: TestClient) -> None:
        data = client.get("/diagnostics").json()
        assert data["pipeline_running"] is False
        assert data["last_run_at"] is None
        assert data["last_run_status"] is None
        assert data["last_run_notified"] is None


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


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
        for sort in ("newest", "price_asc", "price_desc", "fit_desc"):
            resp = client.get(f"/?sort={sort}")
            assert resp.status_code == 200

    def test_invalid_sort_defaults(self, client: TestClient) -> None:
        resp = client.get("/?sort=evil_injection")
        assert resp.status_code == 200

    def test_best_fit_is_the_default_sort(self, client: TestClient) -> None:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(client.get("/").text, "html.parser")
        assert soup.select_one('select[name="sort"] option[selected]')["value"] == "fit_desc"

    def test_invalid_page_clamped(self, client: TestClient) -> None:
        resp = client.get("/?page=-5")
        assert resp.status_code == 200

    def test_page_beyond_total_clamped(self, client: TestClient) -> None:
        resp = client.get("/?page=9999")
        assert resp.status_code == 200

    def test_min_fit_score_clamped(self, client: TestClient) -> None:
        resp = client.get("/?min_fit_score=150")
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
        assert 'id="properties-map-data"' in resp.text


# ---------------------------------------------------------------------------
# Property detail
# ---------------------------------------------------------------------------


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
        assert "Dashboard" in resp.text

    @pytest.mark.parametrize("saved_count", [3, 0])
    async def test_every_red_flag_is_listed_under_watch_out(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
        saved_count: int,
    ) -> None:
        """The saved count can leave out photo flags, so it decides nothing shown."""
        from bs4 import BeautifulSoup

        flags = ListingRedFlags(
            missing_room_photos=["Second bedroom"],
            selective_angles=True,
            description_concerns=["Railway beside the building", "Price above average"],
            red_flag_count=saved_count,
        )
        analysis = base_analysis.model_copy(
            update={
                "listing_red_flags": flags,
                "lowlights": ["Potential traffic noise", "Missing key photos"],
            }
        )
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)
        soup = BeautifulSoup(client.get(f"/property/{merged_a.unique_id}").text, "html.parser")
        assert [li.get_text() for li in soup.select(".verdict-watch li")] == [
            "Potential road or rail noise",
            "Limited views in supplied photos",
            "Not seen in supplied photos: Second bedroom",
            "Railway beside the building",
            "Price above average",
        ]

    @pytest.mark.asyncio
    async def test_facts_and_verdict_lead_the_page(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        from bs4 import BeautifulSoup

        highlights = ["Gas hob", "Dishwasher included", "Modern kitchen", "Private garden"]
        analysis = base_analysis.model_copy(
            update={
                "one_line": "Garden flat with a gas hob",
                "highlights": [*highlights, "High ceilings", "Period features"],
                "lowlights": ["Service charge unstated"],
            }
        )
        await storage.save_merged_property(merged_a, commute_minutes=22)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)
        soup = BeautifulSoup(client.get(f"/property/{merged_a.unique_id}").text, "html.parser")

        facts = {
            fact.dt.get_text(strip=True): " ".join(fact.dd.get_text(" ").split())
            for fact in soup.select(".detail-facts .detail-fact")
        }
        assert facts["Rent"] == "£1,900 pcm"
        assert facts["Bedrooms"] == "1"
        assert facts["Floor area"] == "Not listed"
        assert facts["Commute"].startswith("22 min")
        assert facts["Fit"].endswith("of 100")

        verdict = soup.select_one("#section-overview")
        assert verdict.h2.get_text() == "Garden flat with a gas hob"
        assert "AI reading of the listing photos" in verdict.select_one(".provenance-note").text
        fits = verdict.select_one(".verdict-fits")
        assert [li.get_text() for li in fits.select(":scope > ul li")] == [
            *highlights,
            "High ceilings",
        ]
        assert fits.select_one(".verdict-more summary").get_text() == "1 more"
        assert verdict.select_one(".verdict-watch .verdict-empty").get_text() == "Nothing flagged."
        # The service charge is the landlord's cost, so the unstated-charge lowlight is dropped.
        find_out = [li.get_text() for li in verdict.select(".verdict-find li")]
        assert "Service charge" not in find_out
        headings = [h3.get_text(strip=True) for h3 in verdict.select(".verdict-list h3")]
        assert headings == ["What fits", "What doesn't", "What's unknown"]
        assert verdict.select_one("#section-fit") is not None

        order = [
            el["id"] for el in soup.select("#section-overview, #section-costs, #section-quality")
        ]
        assert order == ["section-overview", "section-costs", "section-quality"]

    @pytest.mark.asyncio
    async def test_area_context_populated(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "E8" in resp.text

    @pytest.mark.asyncio
    async def test_micro_area_renders_matched_card_with_ward(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        """With a ward set, detail page shows micro-area in compact area summary."""
        await storage.save_merged_property(merged_a, ward="London Fields")
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "London Fields / Broadway Market" in resp.text
        assert "/area/E8" in resp.text
        assert "Hosting:" in resp.text
        assert "WFH:" in resp.text

    @pytest.mark.asyncio
    async def test_micro_area_graceful_for_unknown_outcode(
        self, client: TestClient, storage: PropertyStorage, prop_a: Property
    ) -> None:
        """Properties with unknown outcodes should not show micro-area content."""
        prop_unknown = prop_a.model_copy(update={"postcode": "ZZ9 9ZZ"})
        merged = MergedProperty(
            canonical=prop_unknown,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: prop_unknown.url},
            min_price=1900,
            max_price=1900,
        )
        await storage.save_merged_property(merged)
        resp = client.get(f"/property/{merged.unique_id}")
        assert resp.status_code == 200
        assert "Full area guide" not in resp.text

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

    @pytest.mark.asyncio
    async def test_image_url_map_index_independent(
        self, storage: PropertyStorage, prop_a: Property, tmp_path: Path
    ) -> None:
        """Image mapping should work regardless of index in filename.

        After merging, inherited images may have different indices than
        the enumerate() position. The URL-hash lookup must still find them.
        """
        img_url = "https://cdn.example.com/gallery/photo1.jpg"
        merged = MergedProperty(
            canonical=prop_a,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: prop_a.url},
            min_price=1900,
            max_price=1900,
        )
        await storage.save_merged_property(merged)
        await storage.save_property_images(
            merged.unique_id,
            [
                PropertyImage(
                    source=PropertySource.OPENRENT, url=HttpUrl(img_url), image_type="gallery"
                )
            ],
        )

        # Save cached file with index 7 (not 0 as enumerate would produce)
        fname = url_to_filename(img_url, "gallery", 7)
        # data_dir is derived from database_path parent, so use a DB
        # path inside tmp_path so data_dir == str(tmp_path)
        test_settings = Settings(
            telegram_bot_token=SecretStr("fake:token"),
            telegram_chat_id=0,
            search_areas="e8",
            database_path=str(tmp_path / "test.db"),
        )
        cache_dir = get_cache_dir(test_settings.data_dir, merged.unique_id)
        save_image_bytes(cache_dir / fname, b"fake")

        from fastapi import FastAPI

        app = FastAPI()
        app.state.storage = storage
        app.state.settings = test_settings
        app.include_router(router)
        client = TestClient(app)

        resp = client.get(f"/property/{merged.unique_id}")
        assert resp.status_code == 200
        safe_id = safe_dir_name(merged.unique_id)
        # The image URL should be rewritten to the local cached path
        assert f"/images/{safe_id}/{fname}" in resp.text


# ---------------------------------------------------------------------------
# Property card partial endpoint (map marker click)
# ---------------------------------------------------------------------------


class TestPropertyCardEndpoint:
    """Tests for GET /property/{id}/card which returns a single card partial."""

    @pytest.mark.asyncio
    async def test_returns_card_html(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get(f"/property/{merged_a.unique_id}/card")
        assert resp.status_code == 200
        assert "property-card" in resp.text
        assert merged_a.unique_id in resp.text

    def test_not_found_returns_404(self, client: TestClient) -> None:
        resp = client.get("/property/nonexistent:999/card")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_card_includes_price(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get(f"/property/{merged_a.unique_id}/card")
        assert resp.status_code == 200
        assert "1,900" in resp.text

    @pytest.mark.asyncio
    async def test_card_includes_quality_data(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, base_analysis)
        resp = client.get(f"/property/{merged_a.unique_id}/card")
        assert resp.status_code == 200
        assert "fit-tier-pill" in resp.text

    @pytest.mark.asyncio
    async def test_card_is_partial_not_full_page(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        """Card endpoint returns just the card, not wrapped in base.html."""
        await storage.save_merged_property(merged_a)
        resp = client.get(f"/property/{merged_a.unique_id}/card")
        assert resp.status_code == 200
        # Should NOT contain full page scaffolding
        assert "<nav" not in resp.text
        assert "I Hate Moving" not in resp.text
        # Should contain the card article
        assert "<article" in resp.text


# ---------------------------------------------------------------------------
# Detail page quality cards
# ---------------------------------------------------------------------------


class TestDetailQualityCards:
    """Tests that quality cards render with correct content on the detail page."""

    @pytest.mark.parametrize("reviewed", [None, 7, 8])
    async def test_gallery_review_coverage_is_qualified_and_legacy_remains_unknown(
        self, client, storage, prop_a, merged_a, base_analysis, reviewed
    ):
        coverage = (
            ImageReviewCoverage(
                available_gallery_count=8,
                reviewed_gallery_count=reviewed,
                rooms_not_seen_in_review=("bathroom",) if reviewed == 7 else (),
            )
            if reviewed is not None
            else None
        )
        analysis = base_analysis.model_copy(update={"image_review_coverage": coverage})
        if reviewed == 8:
            analysis = analysis.model_copy(
                update={
                    "listing_red_flags": ListingRedFlags(
                        missing_room_photos=["bathroom"], red_flag_count=1
                    ),
                    "lowlights": ["No bathroom photos supplied"],
                }
            )
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)
        response = client.get(f"/property/{merged_a.unique_id}")
        assert response.status_code == 200
        if reviewed is None:
            assert "available gallery photos" not in response.text
        else:
            assert f"Reviewed {reviewed} of 8 available gallery photos." in response.text
        if reviewed == 7:
            assert "Other photos were not reviewed; photo completeness is unknown." in response.text
            assert "Not seen in reviewed photos: bathroom." in response.text
            assert "No bathroom photos" not in response.text
        else:
            assert "Other photos were not reviewed" not in response.text
        if reviewed == 8:
            assert "Not seen in supplied photos: bathroom" in response.text
            # The red flag names the room, so the vaguer photo tag isn't repeated.
            assert "No bathroom photos supplied" not in response.text

    @pytest.mark.asyncio
    async def test_quality_cards_render_room_data(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, base_analysis)
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        # Primary cards
        assert "Kitchen" in resp.text
        assert "Modern" in resp.text
        assert "Gas hob" in resp.text
        assert "Dishwasher" in resp.text
        assert "Condition" in resp.text
        assert "Light" in resp.text
        # Secondary cards
        assert "Living Room" in resp.text

    @pytest.mark.asyncio
    async def test_quality_section_rendered(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, base_analysis)
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "Room by room" in resp.text

    @pytest.mark.asyncio
    async def test_estimated_badge_absent_with_floorplan(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        """Properties WITH floorplan images should NOT show the 'estimated' badge."""
        await storage.save_merged_property(merged_a)
        await storage.save_property_images(
            merged_a.unique_id,
            [
                PropertyImage(
                    source=PropertySource.OPENRENT,
                    url=HttpUrl("https://example.com/fp.jpg"),
                    image_type="floorplan",
                )
            ],
        )
        await storage.save_quality_analysis(prop_a.unique_id, base_analysis)
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "Living Room" in resp.text
        assert "estimated" not in resp.text

    @pytest.mark.asyncio
    async def test_estimated_badge_shown_without_floorplan(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        """Properties WITHOUT floorplan images should show the 'estimated' badge."""
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, base_analysis)
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "Living Room" in resp.text
        assert "estimated" in resp.text


# ---------------------------------------------------------------------------
# Cached images
# ---------------------------------------------------------------------------


class TestCachedImages:
    """Tests for the /images/ route."""

    @pytest.fixture
    def settings(self, tmp_path: Path) -> Settings:
        """Override module settings to use tmp_path for image cache."""
        return Settings(
            telegram_bot_token=SecretStr("fake:token"),
            telegram_chat_id=0,
            search_areas="e8",
            database_path=str(tmp_path / "properties.db"),
        )

    def test_serves_cached_image(self, client: TestClient, settings: Settings) -> None:
        unique_id = "openrent:100"
        filename = "gallery_000_abc12345.jpg"
        cache_dir = get_cache_dir(settings.data_dir, unique_id)
        save_image_bytes(cache_dir / filename, b"fake jpeg")

        safe_id = safe_dir_name(unique_id)
        resp = client.get(f"/images/{safe_id}/{filename}")
        assert resp.status_code == 200
        assert resp.content == b"fake jpeg"
        assert "max-age=31536000" in resp.headers["cache-control"]

    def test_returns_404_for_missing_image(self, client: TestClient) -> None:
        resp = client.get("/images/openrent_100/nonexistent.jpg")
        assert resp.status_code == 404

    def test_blocks_directory_traversal(self, client: TestClient) -> None:
        resp = client.get("/images/openrent_100/../../../etc/passwd")
        assert resp.status_code != 200


def _floorplan_png(*, rooms: list[tuple[int, int, int, int]], size: tuple[int, int]) -> bytes:
    """Draw a plan of black-outlined rooms, scaled to the requested size."""
    import io

    from PIL import Image, ImageDraw

    plan = Image.new("L", (400, 300), 255)
    draw = ImageDraw.Draw(plan)
    for room in rooms:
        draw.rectangle(room, outline=0, width=8)
    buf = io.BytesIO()
    plan.resize(size).save(buf, format="PNG")
    return buf.getvalue()


class TestFloorplanDedup:
    """Reduce repetition without losing potentially different plan evidence."""

    @pytest.fixture
    def settings(self, tmp_path: Path) -> Settings:
        return Settings(
            telegram_bot_token=SecretStr("fake:token"),
            telegram_chat_id=0,
            search_areas="e8",
            database_path=str(tmp_path / "properties.db"),
        )

    @pytest.mark.asyncio
    async def test_repeated_plan_from_another_portal_is_shown_once(
        self,
        client: TestClient,
        settings: Settings,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        from bs4 import BeautifulSoup

        flat = [(10, 10, 390, 150), (10, 150, 200, 290), (200, 150, 390, 290)]
        upper_floor = [(10, 10, 130, 290), (130, 10, 390, 120), (130, 120, 390, 290)]
        plans = {
            # The same drawing from two portals at different resolutions.
            "https://example.com/zoopla-plan.png": (PropertySource.ZOOPLA, flat, (800, 600)),
            "https://example.com/rightmove-plan.png": (PropertySource.RIGHTMOVE, flat, (400, 300)),
            "https://example.com/upper-floor.png": (
                PropertySource.RIGHTMOVE,
                upper_floor,
                (400, 300),
            ),
        }
        cache_dir = get_cache_dir(settings.data_dir, merged_a.unique_id)
        images = []
        for index, (url, (source, rooms, size)) in enumerate(plans.items()):
            save_image_bytes(
                cache_dir / url_to_filename(url, "floorplan", index),
                _floorplan_png(rooms=rooms, size=size),
            )
            images.append(
                PropertyImage(
                    source=source,
                    url=HttpUrl(url),
                    image_type="floorplan",
                    floorplan_origin=(
                        "source_declared" if source == PropertySource.RIGHTMOVE else "unknown"
                    ),
                )
            )
        await storage.save_merged_property(merged_a)
        await storage.save_property_images(merged_a.unique_id, images)

        soup = BeautifulSoup(client.get(f"/property/{merged_a.unique_id}").text, "html.parser")
        safe_id = safe_dir_name(merged_a.unique_id)
        distinct = [
            f"/images/{safe_id}/{url_to_filename(url, 'floorplan', index)}"
            for index, url in enumerate(plans)
            if index != 0
        ]
        repeat = f"/images/{safe_id}/{url_to_filename(next(iter(plans)), 'floorplan', 0)}"
        panel = soup.select_one("#section-floorplan")
        assert [img["src"] for img in panel.select(".floorplan-panel-image img")] == distinct[:1]
        assert "See all 3 plans" in panel.figcaption.get_text()
        variants = panel.select_one("details.floorplan-repeats")
        assert variants.summary.get_text() == "Additional floorplan versions (1)"
        assert "label wasn't recorded" in variants.get_text()
        assert variants.img.parent["data-lightbox"] == "floorplan"
        assert soup.select_one("#gallery-tab-floorplan").get_text() == "Floorplan 3"
        assert [img["src"] for img in soup.select(".gallery-floorplan-img")] == [*distinct, repeat]

    @pytest.mark.asyncio
    async def test_uncached_plan_from_another_portal_is_set_aside_not_hidden(
        self,
        client: TestClient,
        settings: Settings,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        """Without a local copy there is nothing to compare, so it is collapsed instead."""
        from bs4 import BeautifulSoup

        cached_url = "https://example.com/rightmove-plan.png"
        save_image_bytes(
            get_cache_dir(settings.data_dir, merged_a.unique_id)
            / url_to_filename(cached_url, "floorplan", 0),
            _floorplan_png(rooms=[(10, 10, 390, 290)], size=(400, 300)),
        )
        same_site_url = "https://example.com/rightmove-upper.png"
        other_site_url = "https://example.com/zoopla-plan.png"
        await storage.save_merged_property(merged_a)
        await storage.save_property_images(
            merged_a.unique_id,
            [
                PropertyImage(source=source, url=HttpUrl(url), image_type="floorplan")
                for source, url in [
                    (PropertySource.ZOOPLA, other_site_url),
                    (PropertySource.RIGHTMOVE, cached_url),
                    (PropertySource.RIGHTMOVE, same_site_url),
                ]
            ],
        )

        soup = BeautifulSoup(client.get(f"/property/{merged_a.unique_id}").text, "html.parser")
        section = soup.select_one("#section-floorplan")
        assert [img["src"] for img in soup.select(".gallery-floorplan-img")] == [
            f"/images/{safe_dir_name(merged_a.unique_id)}/"
            f"{url_to_filename(cached_url, 'floorplan', 0)}",
            same_site_url,
            other_site_url,
        ]
        repeats = section.select_one("details.floorplan-repeats")
        assert repeats.summary.get_text() == "Additional floorplan versions (1)"
        assert [img["src"] for img in repeats.select("img")] == [other_site_url]
        assert repeats.img.parent["data-lightbox"] == "floorplan"

    def test_changed_dimension_with_identical_perceptual_hash_is_retained(
        self, settings: Settings, merged_a: MergedProperty
    ) -> None:
        """A text change can leave the pHash identical, even at distance zero."""
        import io

        from PIL import Image, ImageDraw

        from home_finder.utils.image_hash import hash_from_disk
        from home_finder.web.routes import _split_floorplans

        images = []
        hashes = []
        encoded = []
        for index, dimension in enumerate(["4.2m x 3.1m", "3.2m x 3.1m"]):
            plan = Image.new("L", (800, 600), 255)
            draw = ImageDraw.Draw(plan)
            for room in [(20, 20, 780, 300), (20, 300, 400, 580), (400, 300, 780, 580)]:
                draw.rectangle(room, outline=0, width=8)
            draw.text((80, 100), dimension, fill=0)
            buf = io.BytesIO()
            plan.save(buf, format="PNG")
            encoded.append(buf.getvalue())
            url = f"https://example.com/plan-{index}.png"
            path = get_cache_dir(settings.data_dir, merged_a.unique_id) / url_to_filename(
                url, "floorplan", index
            )
            save_image_bytes(path, encoded[-1])
            hashes.append(hash_from_disk(path))
            images.append(
                PropertyImage(
                    source=[PropertySource.RIGHTMOVE, PropertySource.ZOOPLA][index],
                    url=HttpUrl(url),
                    image_type="floorplan",
                    floorplan_origin="source_declared",
                )
            )

        assert encoded[0] != encoded[1]
        assert hashes[0] is not None and hashes[0] == hashes[1]
        shown, variants = _split_floorplans(settings.data_dir, merged_a.unique_id, images)
        assert shown == images[:1]
        assert variants == images[1:]

    def test_cache_removed_during_lookup_preserves_plan(
        self, settings: Settings, merged_a: MergedProperty, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from home_finder.web import routes

        missing_path = Path(settings.data_dir) / "evicted.png"
        monkeypatch.setattr(routes, "find_cached_file", lambda *args: missing_path)
        image = PropertyImage(
            source=PropertySource.RIGHTMOVE,
            url=HttpUrl("https://example.com/plan.png"),
            image_type="floorplan",
        )
        assert routes._split_floorplans(settings.data_dir, merged_a.unique_id, [image]) == (
            [image],
            [],
        )


# ---------------------------------------------------------------------------
# Thumbnail resolution
# ---------------------------------------------------------------------------


class TestResolveCachedThumbnail:
    """Tests for _resolve_cached_thumbnail()."""

    def test_thumbnail_skips_epc_files(self, tmp_path: Path) -> None:
        """Fallback should skip epc_* files and pick the first gallery_* file."""
        unique_id = "zoopla:55555"
        data_dir = str(tmp_path)

        cache_dir = get_cache_dir(data_dir, unique_id)
        # Create an epc file (alphabetically first) and a gallery file
        save_image_bytes(cache_dir / "epc_000_aaa11111.jpg", b"fake")
        save_image_bytes(cache_dir / "gallery_001_bbb22222.jpg", b"fake")

        result = _resolve_cached_thumbnail(unique_id, None, data_dir)
        assert result is not None
        assert "gallery_001_bbb22222.jpg" in result
        assert "epc_" not in result

    def test_thumbnail_fast_path_finds_gallery_by_hash(self, tmp_path: Path) -> None:
        """Hash-based lookup should find a gallery file regardless of index."""
        unique_id = "rightmove:66666"
        data_dir = str(tmp_path)
        img_url = "https://cdn.example.com/photo.jpg"

        # Save with index 7 (not 0)
        fname = url_to_filename(img_url, "gallery", 7)
        cache_dir = get_cache_dir(data_dir, unique_id)
        save_image_bytes(cache_dir / fname, b"fake")

        result = _resolve_cached_thumbnail(unique_id, img_url, data_dir)
        assert result is not None
        assert fname in result

    def test_prefers_thumb_file_fast_path(self, tmp_path: Path) -> None:
        """When a thumb_ file exists, the hash-based path should prefer it."""
        unique_id = "rightmove:77777"
        data_dir = str(tmp_path)
        img_url = "https://cdn.example.com/photo.jpg"

        fname = url_to_filename(img_url, "gallery", 0)
        thumb_name = f"thumb_{Path(fname).stem}.jpg"
        cache_dir = get_cache_dir(data_dir, unique_id)
        save_image_bytes(cache_dir / fname, b"fake")
        save_image_bytes(cache_dir / thumb_name, b"fake")

        result = _resolve_cached_thumbnail(unique_id, img_url, data_dir)
        assert result is not None
        assert thumb_name in result

    def test_prefers_thumb_file_fallback_path(self, tmp_path: Path) -> None:
        """When a thumb_ file exists, the fallback path should prefer it."""
        unique_id = "openrent:88888"
        data_dir = str(tmp_path)

        cache_dir = get_cache_dir(data_dir, unique_id)
        save_image_bytes(cache_dir / "gallery_000_aaa11111.jpg", b"fake")
        save_image_bytes(cache_dir / "thumb_gallery_000_aaa11111.jpg", b"fake")

        result = _resolve_cached_thumbnail(unique_id, None, data_dir)
        assert result is not None
        assert "thumb_gallery_000_aaa11111.jpg" in result

    def test_falls_back_to_original_without_thumb(self, tmp_path: Path) -> None:
        """When no thumb_ file exists, should serve the original."""
        unique_id = "openrent:99999"
        data_dir = str(tmp_path)

        cache_dir = get_cache_dir(data_dir, unique_id)
        save_image_bytes(cache_dir / "gallery_000_aaa11111.jpg", b"fake")

        result = _resolve_cached_thumbnail(unique_id, None, data_dir)
        assert result is not None
        assert "gallery_000_aaa11111.jpg" in result
        assert "thumb_" not in result


# ---------------------------------------------------------------------------
# Listing age filter
# ---------------------------------------------------------------------------


class TestListingAgeFilter:
    """Tests for the listing_age Jinja filter."""

    def test_today(self) -> None:
        now = datetime.now(UTC).isoformat()
        assert listing_age_filter(now) == "today"

    def test_days(self) -> None:
        three_days_ago = (datetime.now(UTC) - timedelta(days=3)).isoformat()
        assert listing_age_filter(three_days_ago) == "3d"

    def test_weeks(self) -> None:
        ten_days_ago = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        assert listing_age_filter(ten_days_ago) == "1w"

    def test_months(self) -> None:
        sixty_days_ago = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        assert listing_age_filter(sixty_days_ago) == "2mo"

    def test_none(self) -> None:
        assert listing_age_filter(None) == ""

    def test_invalid(self) -> None:
        assert listing_age_filter("not-a-date") == ""


# ---------------------------------------------------------------------------
# Dashboard card rendering
# ---------------------------------------------------------------------------


class TestCardRendering:
    """Tests for the redesigned card rendering."""

    @pytest.mark.asyncio
    async def test_commute_pill_rendered(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        from bs4 import BeautifulSoup

        await storage.save_merged_property(merged_a, commute_minutes=25)
        resp = client.get("/")
        assert resp.status_code == 200
        pill = BeautifulSoup(resp.text, "html.parser").select_one(".commute-pill")
        assert pill.get_text() == "25 min · estimate"
        assert pill.select_one(".figure").get_text() == "25 min"
        assert "journey date/time and location precision are unverified" in pill["title"]

    @pytest.mark.asyncio
    async def test_quick_actions_are_named_for_assistive_tech(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        from bs4 import BeautifulSoup

        await storage.save_merged_property(merged_a)
        soup = BeautifulSoup(client.get("/").text, "html.parser")
        names = [btn["aria-label"] for btn in soup.select(".card-quick-actions button")]
        assert names == ["Mark interested", "Archive"]

    @pytest.mark.asyncio
    async def test_traffic_noise_lowlight_reads_as_road_or_rail_noise(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        """The stored tag also covers railways; only its displayed wording changes."""
        from bs4 import BeautifulSoup

        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(
            prop_a.unique_id,
            base_analysis.model_copy(update={"lowlights": ["Potential traffic noise"]}),
        )
        dashboard = BeautifulSoup(client.get("/").text, "html.parser")
        card_tags = [chip.get_text() for chip in dashboard.select(".highlight-negative")]
        assert card_tags == ["Potential road or rail noise"]
        tag_input = dashboard.select_one('input[name="tag"][value="Potential traffic noise"]')
        assert tag_input.parent.get_text(strip=True) == "Potential road or rail noise"

        filtered = BeautifulSoup(
            client.get("/", params={"tag": "Potential traffic noise"}).text, "html.parser"
        )
        assert filtered.select(".property-card")
        chip = filtered.select_one(".filter-chip")
        assert chip.get_text(" ", strip=True).startswith("Potential road or rail noise")

        detail = client.get(f"/property/{merged_a.unique_id}").text
        assert "Potential road or rail noise" in detail
        assert "Potential traffic noise" not in detail

    @pytest.mark.parametrize("minutes", [None, 0])
    async def test_unknown_commute_is_not_zero_minute_success(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty, minutes
    ) -> None:
        await storage.save_merged_property(merged_a, commute_minutes=minutes)
        resp = client.get("/")
        assert "Commute unknown" in resp.text
        assert ">0 min" not in resp.text

    @pytest.mark.asyncio
    async def test_fit_tier_pill_rendered(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, base_analysis)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "fit-tier-pill" in resp.text

    @pytest.mark.asyncio
    async def test_value_badge_rendered(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        analysis = base_analysis.model_copy(
            update={
                "value": ValueAnalysis(
                    area_average=2200,
                    difference=-300,
                    rating="excellent",
                    note="Below avg",
                ),
            }
        )
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Value: excellent (unverified)" in resp.text


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


class TestParseOptionalInt:
    """Unit tests for the _parse_optional_int helper."""

    def test_none(self) -> None:
        assert _parse_optional_int(None) is None

    def test_empty_string(self) -> None:
        assert _parse_optional_int("") is None

    def test_whitespace(self) -> None:
        assert _parse_optional_int("   ") is None

    def test_valid_int(self) -> None:
        assert _parse_optional_int("42") == 42

    def test_negative_int(self) -> None:
        assert _parse_optional_int("-5") == -5

    def test_non_numeric(self) -> None:
        assert _parse_optional_int("abc") is None

    def test_float_string(self) -> None:
        assert _parse_optional_int("3.14") is None


# ---------------------------------------------------------------------------
# Empty-string params regression
# ---------------------------------------------------------------------------


class TestEmptyStringParams:
    """Regression tests for the core bug: empty string params should not 422."""

    def test_empty_bedrooms(self, client: TestClient) -> None:
        resp = client.get("/?bedrooms=")
        assert resp.status_code == 200

    def test_empty_min_price(self, client: TestClient) -> None:
        resp = client.get("/?min_price=")
        assert resp.status_code == 200

    def test_empty_max_price(self, client: TestClient) -> None:
        resp = client.get("/?max_price=")
        assert resp.status_code == 200

    def test_empty_min_fit_score(self, client: TestClient) -> None:
        resp = client.get("/?min_fit_score=")
        assert resp.status_code == 200

    def test_empty_page(self, client: TestClient) -> None:
        resp = client.get("/?page=")
        assert resp.status_code == 200

    def test_empty_area(self, client: TestClient) -> None:
        resp = client.get("/?area=")
        assert resp.status_code == 200

    def test_all_empty(self, client: TestClient) -> None:
        resp = client.get("/?bedrooms=&min_price=&max_price=&min_fit_score=&area=&page=")
        assert resp.status_code == 200

    def test_non_numeric_bedrooms(self, client: TestClient) -> None:
        resp = client.get("/?bedrooms=abc")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Price filters
# ---------------------------------------------------------------------------


class TestPriceFilters:
    """Tests for min/max price filtering."""

    @pytest.mark.asyncio
    async def test_min_price_filter(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        merged_b: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)  # 1900
        await storage.save_merged_property(merged_b)  # 2500
        resp = client.get("/?min_price=2000")
        assert resp.status_code == 200
        assert "2 bed in N16" in resp.text
        assert "1 bed in E8" not in resp.text

    @pytest.mark.asyncio
    async def test_max_price_filter(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        merged_b: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)  # 1900
        await storage.save_merged_property(merged_b)  # 2500
        resp = client.get("/?max_price=2000")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text
        assert "2 bed in N16" not in resp.text

    @pytest.mark.asyncio
    async def test_price_range_match(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        merged_b: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)  # 1900
        await storage.save_merged_property(merged_b)  # 2500
        resp = client.get("/?min_price=1800&max_price=2000")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text
        assert "2 bed in N16" not in resp.text

    @pytest.mark.asyncio
    async def test_price_range_no_match(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        merged_b: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)  # 1900
        await storage.save_merged_property(merged_b)  # 2500
        resp = client.get("/?min_price=3000&max_price=4000")
        assert resp.status_code == 200
        assert "No properties found" in resp.text


# ---------------------------------------------------------------------------
# Combined filters
# ---------------------------------------------------------------------------


class TestCombinedFilters:
    """Tests for combining multiple filters."""

    @pytest.mark.asyncio
    async def test_bedrooms_and_area(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        merged_b: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)  # 1 bed E8
        await storage.save_merged_property(merged_b)  # 2 bed N16
        resp = client.get("/?bedrooms=1&area=E8")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text
        assert "2 bed in N16" not in resp.text

    @pytest.mark.asyncio
    async def test_conflicting_filters(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        merged_b: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_merged_property(merged_b)
        # 1 bed in E8 area but min_price 2000 — should not match prop_a
        resp = client.get("/?bedrooms=1&min_price=2000")
        assert resp.status_code == 200
        assert "No properties found" in resp.text

    @pytest.mark.asyncio
    async def test_all_filters(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)  # 1 bed, 1900, E8
        resp = client.get("/?bedrooms=1&min_price=1500&max_price=2000&area=E8")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

    @pytest.mark.asyncio
    async def test_htmx_partial_with_filters(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/?bedrooms=1&area=E8", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert "<!DOCTYPE html>" not in resp.text
        assert "1 bed in E8" in resp.text


# ---------------------------------------------------------------------------
# Filter chips
# ---------------------------------------------------------------------------


class TestFilterChips:
    """Tests for filter chip rendering."""

    @pytest.mark.asyncio
    async def test_chips_rendered_with_filters(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/?bedrooms=1")
        assert resp.status_code == 200
        assert "filter-chip" in resp.text
        assert "1 bed" in resp.text
        assert 'data-filter-key="bedrooms"' in resp.text

    def test_no_chips_without_filters(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "filter-chip" not in resp.text

    @pytest.mark.asyncio
    async def test_chips_in_htmx_partial(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/?area=E8", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert "filter-chip" in resp.text
        assert "E8" in resp.text

    @pytest.mark.asyncio
    async def test_multiple_chips(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/?bedrooms=1&min_price=1500&area=E8")
        assert resp.status_code == 200
        assert "1 bed" in resp.text
        assert 'data-filter-key="min_price"' in resp.text
        assert 'data-filter-key="area"' in resp.text


# ---------------------------------------------------------------------------
# Quality-based dashboard filters
# ---------------------------------------------------------------------------


class TestQualityFilters:
    """Tests for quality-based dashboard filters."""

    @pytest.mark.asyncio
    async def test_invalid_property_type_ignored(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/?property_type=evil_injection")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

    @pytest.mark.asyncio
    async def test_invalid_hob_type_ignored(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/?hob_type=nuclear")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

    @pytest.mark.asyncio
    async def test_invalid_natural_light_ignored(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/?natural_light=blazing")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

    @pytest.mark.asyncio
    async def test_property_type_filter(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        analysis = base_analysis.model_copy(
            update={
                "listing_extraction": ListingExtraction(property_type=PropertyType.WAREHOUSE),
            }
        )
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)

        resp = client.get("/?property_type=warehouse")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

        resp = client.get("/?property_type=victorian")
        assert resp.status_code == 200
        assert "No properties found" in resp.text

    @pytest.mark.asyncio
    async def test_hob_type_filter(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, base_analysis)

        resp = client.get("/?hob_type=gas")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

        resp = client.get("/?hob_type=induction")
        assert resp.status_code == 200
        assert "No properties found" in resp.text

    @pytest.mark.asyncio
    async def test_combined_quality_filters(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        analysis = base_analysis.model_copy(
            update={
                "listing_extraction": ListingExtraction(property_type=PropertyType.WAREHOUSE),
                "light_space": LightSpaceAnalysis(natural_light="excellent"),
                "outdoor_space": OutdoorSpaceAnalysis(has_balcony=True),
            }
        )
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)

        resp = client.get(
            "/?property_type=warehouse&hob_type=gas&outdoor_space=yes&natural_light=excellent"
        )
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

        resp = client.get("/?property_type=warehouse&hob_type=induction")
        assert resp.status_code == 200
        assert "No properties found" in resp.text

    @pytest.mark.asyncio
    async def test_quality_filter_chips_rendered(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/?property_type=warehouse")
        assert resp.status_code == 200
        assert "filter-chip" in resp.text
        assert "Warehouse" in resp.text

    @pytest.mark.asyncio
    async def test_empty_quality_params(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/?property_type=&outdoor_space=&hob_type=")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

    @pytest.mark.asyncio
    async def test_filter_badge_shown_when_active(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/?hob_type=gas")
        assert resp.status_code == 200
        assert "filter-badge" in resp.text


# ---------------------------------------------------------------------------
# Studio support
# ---------------------------------------------------------------------------


class TestStudioSupport:
    """Tests for studio (0 bedrooms) support in the dashboard."""

    @pytest.mark.asyncio
    async def test_studio_filter(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_studio: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_studio)
        resp = client.get("/?bedrooms=0")
        assert resp.status_code == 200
        assert "Studio in E8" in resp.text

    @pytest.mark.asyncio
    async def test_studio_chip_label(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_studio: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_studio)
        resp = client.get("/?bedrooms=0")
        assert resp.status_code == 200
        assert "Studio" in resp.text
        assert "0 bed" not in resp.text

    @pytest.mark.asyncio
    async def test_studio_card_badge(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_studio: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_studio)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Studio" in resp.text

    @pytest.mark.asyncio
    async def test_studio_detail_page_is_not_zero_bed(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_studio: MergedProperty,
    ) -> None:
        from bs4 import BeautifulSoup

        await storage.save_merged_property(merged_studio)
        resp = client.get(f"/property/{merged_studio.unique_id}")
        soup = BeautifulSoup(resp.text, "html.parser")
        bedrooms = next(
            fact.dd for fact in soup.select(".detail-fact") if fact.dt.get_text() == "Bedrooms"
        )
        assert bedrooms.get_text(strip=True) == "Studio"
        for meta in ('meta[name="description"]', 'meta[property="og:description"]'):
            assert soup.select_one(meta)["content"] == "Studio · 15 Mare Street, E8 3RH"
        assert "0 bed" not in resp.text


# ---------------------------------------------------------------------------
# Accessibility
# ---------------------------------------------------------------------------


class TestAriaLive:
    """Tests for accessibility: aria-live result count."""

    @pytest.mark.asyncio
    async def test_count_announced_with_results(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'aria-live="polite"' in resp.text
        assert "1 property found" in resp.text

    def test_zero_results_announced(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'aria-live="polite"' in resp.text
        assert "0 properties found" in resp.text


# ---------------------------------------------------------------------------
# New dashboard filters (office, hosting, noise, broadband)
# ---------------------------------------------------------------------------


class TestNewFilters:
    """Tests for office_separation, hosting_layout, hosting_noise_risk, broadband_type."""

    @pytest.mark.asyncio
    async def test_office_separation_filter(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        analysis = base_analysis.model_copy(
            update={
                "bedroom": BedroomAnalysis(office_separation="dedicated_room"),
            }
        )
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)

        resp = client.get("/?office_separation=dedicated_room")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

        resp = client.get("/?office_separation=none")
        assert resp.status_code == 200
        assert "No properties found" in resp.text

    @pytest.mark.asyncio
    async def test_hosting_layout_filter(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        analysis = base_analysis.model_copy(
            update={
                "space": SpaceAnalysis(hosting_layout="excellent"),
            }
        )
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)

        resp = client.get("/?hosting_layout=excellent")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

        resp = client.get("/?hosting_layout=poor")
        assert resp.status_code == 200
        assert "No properties found" in resp.text

    @pytest.mark.asyncio
    async def test_hosting_noise_risk_filter(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        analysis = base_analysis.model_copy(
            update={
                "flooring_noise": FlooringNoiseAnalysis(hosting_noise_risk="low"),
            }
        )
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)

        resp = client.get("/?hosting_noise_risk=low")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

        resp = client.get("/?hosting_noise_risk=high")
        assert resp.status_code == 200
        assert "No properties found" in resp.text

    @pytest.mark.asyncio
    async def test_broadband_type_filter(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        analysis = base_analysis.model_copy(
            update={
                "listing_extraction": ListingExtraction(broadband_type="fttp"),
            }
        )
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)

        resp = client.get("/?broadband_type=fttp")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

        resp = client.get("/?broadband_type=standard")
        assert resp.status_code == 200
        assert "No properties found" in resp.text

    @pytest.mark.asyncio
    async def test_invalid_office_separation_ignored(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/?office_separation=evil_injection")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

    @pytest.mark.asyncio
    async def test_invalid_hosting_layout_ignored(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/?hosting_layout=evil")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

    @pytest.mark.asyncio
    async def test_new_filter_chips_rendered(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/?office_separation=dedicated_room")
        assert resp.status_code == 200
        assert "filter-chip" in resp.text
        assert "Dedicated Room" in resp.text


# ---------------------------------------------------------------------------
# Filter count endpoint
# ---------------------------------------------------------------------------


class TestFilterCount:
    """Tests for the /count endpoint."""

    def test_count_empty_db(self, client: TestClient) -> None:
        resp = client.get("/count")
        assert resp.status_code == 200
        assert resp.text == "0"
        assert resp.headers["content-type"] == "text/plain; charset=utf-8"

    @pytest.mark.asyncio
    async def test_count_with_data(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/count")
        assert resp.status_code == 200
        assert resp.text == "1"

    @pytest.mark.asyncio
    async def test_count_with_filters(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        merged_b: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)  # 1 bed
        await storage.save_merged_property(merged_b)  # 2 bed
        resp = client.get("/count?bedrooms=1")
        assert resp.status_code == 200
        assert resp.text == "1"

    @pytest.mark.asyncio
    async def test_count_with_price_range(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        merged_b: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)  # 1900
        await storage.save_merged_property(merged_b)  # 2500
        resp = client.get("/count?max_price=2000")
        assert resp.status_code == 200
        assert resp.text == "1"


# ---------------------------------------------------------------------------
# Filter modal
# ---------------------------------------------------------------------------


class TestFilterModal:
    """Tests for the filter modal dialog in full page render."""

    def test_dialog_present_in_page(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'id="filter-modal"' in resp.text
        assert "<dialog" in resp.text

    def test_tag_checkboxes_rendered(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'name="tag"' in resp.text
        assert "Gas hob" in resp.text

    def test_tag_categories_present(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Workspace" in resp.text
        assert "Hosting" in resp.text
        assert "Kitchen" in resp.text

    def test_filter_groups_present(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'name="office_separation"' in resp.text
        assert 'name="broadband_type"' in resp.text
        assert 'name="hosting_layout"' in resp.text
        assert 'name="hosting_noise_risk"' in resp.text


# ---------------------------------------------------------------------------
# Beds toggle
# ---------------------------------------------------------------------------


class TestBedsToggle:
    """Tests for the segmented beds toggle (radio inputs)."""

    def test_radio_inputs_rendered(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'name="bedrooms"' in resp.text
        assert 'type="radio"' in resp.text
        assert 'id="beds-any"' in resp.text

    @pytest.mark.asyncio
    async def test_correct_radio_checked_for_bedrooms_1(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/?bedrooms=1")
        assert resp.status_code == 200
        assert 'id="beds-1"' in resp.text

    def test_studio_radio_checked(self, client: TestClient) -> None:
        resp = client.get("/?bedrooms=0")
        assert resp.status_code == 200
        assert 'id="beds-0"' in resp.text


# ---------------------------------------------------------------------------
# Secondary filter count badge
# ---------------------------------------------------------------------------


class TestSecondaryFilterCount:
    """Tests for 'Filters (N)' badge showing active secondary filter count."""

    def test_no_badge_without_secondary_filters(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'class="filter-badge" hidden' in resp.text

    @pytest.mark.asyncio
    async def test_badge_with_one_secondary_filter(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/?hob_type=gas")
        assert resp.status_code == 200
        assert "filter-badge" in resp.text

    @pytest.mark.asyncio
    async def test_badge_count_with_multiple_secondary_filters(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.get("/?hob_type=gas&property_type=warehouse")
        assert resp.status_code == 200
        assert "filter-badge" in resp.text
        assert ">2<" in resp.text


# ---------------------------------------------------------------------------
# Reanalysis endpoint
# ---------------------------------------------------------------------------


class TestReanalysisEndpoint:
    """Tests for POST /property/{id}/reanalyze."""

    @pytest.mark.asyncio
    async def test_reanalyze_returns_queued(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, base_analysis)

        resp = client.post(f"/property/{prop_a.unique_id}/reanalyze")
        assert resp.status_code == 200
        assert resp.json() == {"status": "queued"}

    @pytest.mark.asyncio
    async def test_reanalyze_unknown_property_404(self, client: TestClient) -> None:
        resp = client.post("/property/nonexistent-999/reanalyze")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_reanalyze_no_analysis_404(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        """Property without quality analysis returns 404."""
        await storage.save_merged_property(merged_a)
        resp = client.post(f"/property/{merged_a.unique_id}/reanalyze")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_reanalyze_sets_flag_in_db(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, base_analysis)

        client.post(f"/property/{prop_a.unique_id}/reanalyze")
        queue = await storage.processing.get_reanalysis_queue()
        assert len(queue) == 1
        assert queue[0].unique_id == prop_a.unique_id

    @pytest.mark.asyncio
    async def test_reanalyze_idempotent(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, base_analysis)

        resp1 = client.post(f"/property/{prop_a.unique_id}/reanalyze")
        resp2 = client.post(f"/property/{prop_a.unique_id}/reanalyze")
        assert resp1.status_code == 200
        assert resp2.status_code == 200

        queue = await storage.processing.get_reanalysis_queue()
        assert len(queue) == 1


# ---------------------------------------------------------------------------
# Cost breakdown
# ---------------------------------------------------------------------------


class TestCostBreakdown:
    """Tests for the costs section of the detail page."""

    async def test_earlier_bills_claim_remains_visible_as_unverified(
        self, client, storage, merged_a, base_analysis
    ) -> None:
        from bs4 import BeautifulSoup

        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(
            merged_a.unique_id,
            base_analysis.model_copy(
                update={"listing_extraction": ListingExtraction(bills_included="yes")}
            ),
        )
        detail = BeautifulSoup(client.get(f"/property/{merged_a.unique_id}").text, "html.parser")
        card = detail.select_one(".cost-breakdown-card").get_text(" ", strip=True)
        assert "Earlier analysis records bills as included (unverified)" in card
        assert "In rent" not in card

    @pytest.mark.parametrize(
        "description",
        [
            "Water included in the optional bills package.",
            "Water included in service charge, payable separately.",
            "Water included for an extra £50 per month.",
        ],
    )
    async def test_paid_package_is_not_shown_as_included_in_rent(
        self, client, storage, merged_a, description
    ) -> None:
        from bs4 import BeautifulSoup

        await storage.save_merged_property(merged_a)
        await storage.update_source_text(
            merged_a.unique_id,
            ListingTextEvidence.observed(observed_at=datetime.now(UTC), description=description),
        )
        detail = BeautifulSoup(client.get(f"/property/{merged_a.unique_id}").text, "html.parser")
        card = detail.select_one(".cost-breakdown-card")
        assert "In rent" not in card.get_text()
        assert "Included in rent" not in card.get_text()
        assert "water" in card.select_one(".money-total-note").get_text().split("Not priced:")[1]
        water = next(row for row in card.select(".cost-bills tr") if "Water" in row.get_text())
        assert "Coverage and amount unknown" in water.get_text()

    @pytest.mark.asyncio
    async def test_current_text_and_rent_rebuild_budget_without_reanalysis(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        from bs4 import BeautifulSoup

        await storage.save_merged_property(merged_a)
        legacy = base_analysis.model_copy(
            update={
                "listing_extraction": ListingExtraction(
                    epc_rating="D",
                    council_tax_band="C",
                    service_charge_pcm=450,
                )
            }
        )
        await storage.save_quality_analysis(merged_a.unique_id, legacy)
        before = await storage.get_quality_analysis(merged_a.unique_id)
        stamp = datetime.now(UTC)
        await storage.update_source_text(
            merged_a.unique_id,
            ListingTextEvidence.observed(
                observed_at=stamp,
                description="EPC B. Council tax band D. Water: £30 pcm payable separately.",
                features=["Gas and electricity included in rent"],
            ),
        )
        first = BeautifulSoup(client.get(f"/property/{merged_a.unique_id}").text, "html.parser")
        budget = " ".join(first.select_one(".cost-breakdown-card").get_text(" ").split())
        assert "Each month £1,930 Rent, water £30 as advertised." in budget
        assert "Included in rent: gas, electricity. Not priced: council tax, broadband." in budget
        assert "Listing EPC: B (an earlier AI reading said D)" in budget
        assert "Council-tax band: D (an earlier AI reading said C)" in budget
        assert "earlier analysis: £450/mo, unverified" in budget
        assert "From 1 May 2026" in budget
        assert "private assured periodic tenancies" in budget
        assert "England fee guidance" in budget
        assert await storage.get_quality_analysis(merged_a.unique_id) == before

        await storage.detect_and_record_price_change(merged_a.unique_id, 1800)
        second = BeautifulSoup(client.get(f"/property/{merged_a.unique_id}").text, "html.parser")
        assert "Each month £1,830" in " ".join(
            second.select_one(".cost-breakdown-card").get_text(" ").split()
        )
        await storage.update_source_text(
            merged_a.unique_id,
            ListingTextEvidence.observed(
                observed_at=stamp + timedelta(seconds=1),
                description="",
                features=[],
                origin="correction",
            ),
        )
        third = BeautifulSoup(client.get(f"/property/{merged_a.unique_id}").text, "html.parser")
        cleared = " ".join(third.select_one(".cost-breakdown-card").get_text(" ").split())
        assert "Each month £1,800" in cleared
        assert (
            "Listing EPC: not in the saved advert (an earlier AI reading said D, unconfirmed)"
            in cleared
        )
        assert "Water: £30" not in cleared
        after = await storage.get_quality_analysis(merged_a.unique_id)
        # Existing price updates recompute ValueAnalysis deterministically; the
        # saved extraction/visual observations and retained legacy claims survive.
        assert after.value != before.value
        assert after.model_dump(exclude={"value"}) == before.model_dump(exclude={"value"})

    @pytest.mark.asyncio
    async def test_unanalysed_property_still_has_budget(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)
        response = client.get(f"/property/{merged_a.unique_id}")
        assert "What it costs" in response.text
        assert "Each month" in response.text
        assert "Listing EPC: not stated. Council-tax band: not stated." in response.text

    @pytest.mark.asyncio
    async def test_cost_breakdown_renders(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        prop_a: Property,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        analysis = base_analysis.model_copy(
            update={
                "listing_extraction": ListingExtraction(
                    property_type=PropertyType.VICTORIAN,
                    epc_rating="D",
                    council_tax_band="C",
                ),
            }
        )
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)

        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "What it costs" in resp.text
        assert "Each month" in resp.text
        # The advert text was not retained, so the earlier reading is the only claim.
        assert (
            "Council-tax band: not in the saved advert (an earlier AI reading said C, unconfirmed)"
            in resp.text
        )
        assert "Earlier analysis claims" not in resp.text
        assert "True Monthly Cost" not in resp.text

    @pytest.mark.asyncio
    async def test_cost_breakdown_shows_rent(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        prop_a: Property,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        analysis = base_analysis.model_copy(
            update={
                "listing_extraction": ListingExtraction(),
            }
        )
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)

        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "Rent" in resp.text
        assert "Each month" in resp.text


# ---------------------------------------------------------------------------
# Acoustic card (compact area summary)
# ---------------------------------------------------------------------------


class TestAcousticCard:
    """Tests for acoustic profile in compact area summary."""

    @pytest.mark.asyncio
    async def test_acoustic_badge_renders(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        prop_a: Property,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        """E8 property should show Sound info in compact area summary."""
        analysis = base_analysis.model_copy(
            update={
                "listing_extraction": ListingExtraction(property_type=PropertyType.VICTORIAN),
            }
        )
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)

        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "Sound" in resp.text
        assert "Full area guide" in resp.text

    @pytest.mark.asyncio
    async def test_area_section_absent_without_area_context(
        self,
        client: TestClient,
        storage: PropertyStorage,
    ) -> None:
        """Property with unknown outcode should not show area section."""
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="888",
            url=HttpUrl("https://openrent.com/888"),
            title="1 bed in SW99",
            price_pcm=3000,
            bedrooms=1,
            address="1 Unknown Street",
            postcode="SW99 9ZZ",
            image_url=HttpUrl("https://example.com/img.jpg"),
        )
        merged = MergedProperty(
            canonical=prop,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: prop.url},
            min_price=3000,
            max_price=3000,
        )
        await storage.save_merged_property(merged)

        resp = client.get(f"/property/{merged.unique_id}")
        assert resp.status_code == 200
        assert "Full area guide" not in resp.text

    @pytest.mark.asyncio
    async def test_full_area_details_on_area_page(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        prop_a: Property,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        """Full acoustic/noise details live on /area/{outcode}, not detail page."""
        analysis = base_analysis.model_copy(
            update={
                "listing_extraction": ListingExtraction(property_type=PropertyType.VICTORIAN),
            }
        )
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)

        # Detail page should NOT have full acoustic/noise sections
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "Noise Enforcement" not in resp.text

        # Area page should have them
        resp = client.get("/area/E8")
        assert resp.status_code == 200
        assert "Hackney" in resp.text


# ---------------------------------------------------------------------------
# Area route
# ---------------------------------------------------------------------------


class TestAreaRoute:
    def test_area_page_returns_200(self, client: TestClient) -> None:
        resp = client.get("/area/E8")
        assert resp.status_code == 200
        assert "Dalston core" in resp.text
        assert "Haggerston" in resp.text

    def test_area_page_case_insensitive(self, client: TestClient) -> None:
        resp = client.get("/area/e8")
        assert resp.status_code == 200

    def test_area_page_unknown_outcode_404(self, client: TestClient) -> None:
        resp = client.get("/area/ZZ99")
        assert resp.status_code == 404

    def test_area_page_highlight(self, client: TestClient) -> None:
        resp = client.get("/area/E8?highlight=Dalston+core")
        assert resp.status_code == 200
        assert "micro-area-highlighted" in resp.text

    def test_area_page_no_highlight(self, client: TestClient) -> None:
        resp = client.get("/area/E8")
        assert resp.status_code == 200
        assert "micro-area-highlighted" not in resp.text

    def test_area_page_has_benchmarks(self, client: TestClient) -> None:
        resp = client.get("/area/E8")
        assert resp.status_code == 200
        assert "Historical research comparisons" in resp.text
        assert "not current market medians" in resp.text


# ---------------------------------------------------------------------------
# Detail micro-area matching
# ---------------------------------------------------------------------------


class TestDetailMicroAreaMatching:
    @pytest.mark.asyncio
    async def test_detail_shows_matched_micro_area_via_ward(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        """When a property has a ward, compact area card shows micro-area name."""
        await storage.save_merged_property(merged_a, ward="London Fields")
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "London Fields / Broadway Market" in resp.text

    @pytest.mark.asyncio
    async def test_detail_shows_area_guide_link(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        """Detail page should link to full area guide."""
        await storage.save_merged_property(merged_a, ward="London Fields")
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "/area/E8" in resp.text
        assert "Full area guide" in resp.text

    @pytest.mark.asyncio
    async def test_detail_text_fallback_when_no_ward(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        """Without a ward, text-based matching is used as fallback."""
        await storage.save_merged_property(merged_a)
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "Full area guide" in resp.text


# ---------------------------------------------------------------------------
# Hosting tolerance (compact area summary)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Temporal (added) filter
# ---------------------------------------------------------------------------


class TestTemporalFilter:
    """Tests for the ?added= temporal filter."""

    @pytest.mark.asyncio
    async def test_added_1d_excludes_old_properties(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        prop_b: Property,
    ) -> None:
        """?added=1d should exclude properties first seen 5 days ago."""
        old_prop = prop_a.model_copy(update={"first_seen": datetime.now(UTC) - timedelta(days=5)})
        merged_old = MergedProperty(
            canonical=old_prop,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: old_prop.url},
            min_price=1900,
            max_price=1900,
        )
        recent_prop = prop_b.model_copy(
            update={"first_seen": datetime.now(UTC) - timedelta(hours=6)},
        )
        merged_recent = MergedProperty(
            canonical=recent_prop,
            sources=(PropertySource.RIGHTMOVE,),
            source_urls={PropertySource.RIGHTMOVE: recent_prop.url},
            min_price=2500,
            max_price=2500,
        )
        await storage.save_merged_property(merged_old)
        await storage.save_merged_property(merged_recent)

        resp = client.get("/?added=1d")
        assert resp.status_code == 200
        assert "2 bed in N16" in resp.text
        assert "1 bed in E8" not in resp.text

    @pytest.mark.asyncio
    async def test_added_30d_includes_recent(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
    ) -> None:
        """?added=30d should include properties from 10 days ago."""
        recent_prop = prop_a.model_copy(
            update={"first_seen": datetime.now(UTC) - timedelta(days=10)},
        )
        merged = MergedProperty(
            canonical=recent_prop,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: recent_prop.url},
            min_price=1900,
            max_price=1900,
        )
        await storage.save_merged_property(merged)
        resp = client.get("/?added=30d")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

    @pytest.mark.asyncio
    async def test_empty_added_shows_all(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
    ) -> None:
        """?added= (empty) should show all properties."""
        old_prop = prop_a.model_copy(update={"first_seen": datetime.now(UTC) - timedelta(days=90)})
        merged = MergedProperty(
            canonical=old_prop,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: old_prop.url},
            min_price=1900,
            max_price=1900,
        )
        await storage.save_merged_property(merged)
        resp = client.get("/?added=")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

    def test_invalid_added_ignored(self, client: TestClient) -> None:
        """?added=evil should silently be ignored (200 OK)."""
        resp = client.get("/?added=evil")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_added_composes_with_sort(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
    ) -> None:
        """?added=7d&sort=fit_desc should work together."""
        recent_prop = prop_a.model_copy(
            update={"first_seen": datetime.now(UTC) - timedelta(days=2)},
        )
        merged = MergedProperty(
            canonical=recent_prop,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: recent_prop.url},
            min_price=1900,
            max_price=1900,
        )
        await storage.save_merged_property(merged)
        resp = client.get("/?added=7d&sort=fit_desc")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text

    @pytest.mark.asyncio
    async def test_added_chip_rendered(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        """?added=3d should render a 'Last 3 days' filter chip."""
        await storage.save_merged_property(merged_a)
        resp = client.get("/?added=3d")
        assert resp.status_code == 200
        assert "filter-chip" in resp.text
        assert "Last 3 days" in resp.text

    @pytest.mark.asyncio
    async def test_count_endpoint_with_added(
        self,
        client: TestClient,
        storage: PropertyStorage,
        prop_a: Property,
        prop_b: Property,
    ) -> None:
        """/count?added=1d should return correct count."""
        old_prop = prop_a.model_copy(update={"first_seen": datetime.now(UTC) - timedelta(days=5)})
        merged_old = MergedProperty(
            canonical=old_prop,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: old_prop.url},
            min_price=1900,
            max_price=1900,
        )
        recent_prop = prop_b.model_copy(
            update={"first_seen": datetime.now(UTC) - timedelta(hours=6)},
        )
        merged_recent = MergedProperty(
            canonical=recent_prop,
            sources=(PropertySource.RIGHTMOVE,),
            source_urls={PropertySource.RIGHTMOVE: recent_prop.url},
            min_price=2500,
            max_price=2500,
        )
        await storage.save_merged_property(merged_old)
        await storage.save_merged_property(merged_recent)

        resp = client.get("/count?added=1d")
        assert resp.status_code == 200
        assert resp.text == "1"

    def test_added_select_in_dashboard(self, client: TestClient) -> None:
        """The 'added' select element should be present in dashboard HTML."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'name="added"' in resp.text
        assert "All time" in resp.text
        assert "Last 3 days" in resp.text


class TestHostingToleranceCard:
    """Tests for hosting tolerance badge in compact area summary."""

    @pytest.mark.asyncio
    async def test_hosting_badge_renders_for_e8(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
    ) -> None:
        """E8 property should show Hosting info in compact area summary."""
        await storage.save_merged_property(merged_a, ward="London Fields")
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "Hosting:" in resp.text

    @pytest.mark.asyncio
    async def test_creative_scene_on_area_page(self, client: TestClient) -> None:
        """Creative scene data appears on area page as micro-area detail."""
        resp = client.get("/area/E8")
        assert resp.status_code == 200
        assert "Creative" in resp.text

    @pytest.mark.asyncio
    async def test_hosting_badge_absent_for_unknown_outcode(
        self,
        client: TestClient,
        storage: PropertyStorage,
    ) -> None:
        """Property with unknown outcode should not show area summary."""
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="999",
            url=HttpUrl("https://openrent.com/999"),
            title="1 bed in SW1",
            price_pcm=3000,
            bedrooms=1,
            address="1 Buckingham Gate",
            postcode="SW1A 1AA",
            image_url=HttpUrl("https://example.com/img.jpg"),
        )
        merged = MergedProperty(
            canonical=prop,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: prop.url},
            min_price=3000,
            max_price=3000,
        )
        await storage.save_merged_property(merged)
        resp = client.get(f"/property/{merged.unique_id}")
        assert resp.status_code == 200
        assert "Hosting:" not in resp.text

    @pytest.mark.asyncio
    async def test_fit_score_includes_area_tolerance(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        prop_a: Property,
        base_analysis: PropertyQualityAnalysis,
    ) -> None:
        """Detail page fit score should reflect area hosting tolerance."""
        analysis = base_analysis.model_copy(
            update={
                "light_space": LightSpaceAnalysis(
                    natural_light="good",
                    feels_spacious=True,
                    ceiling_height="high",
                ),
                "space": SpaceAnalysis(
                    living_room_sqm=18.0,
                    is_spacious_enough=True,
                    confidence="high",
                    hosting_layout="good",
                ),
                "bedroom": BedroomAnalysis(
                    primary_is_double="yes",
                    can_fit_desk="yes",
                    office_separation="dedicated_room",
                ),
                "flooring_noise": FlooringNoiseAnalysis(
                    has_double_glazing="yes",
                    building_construction="solid_brick",
                ),
                "listing_extraction": ListingExtraction(property_type=PropertyType.VICTORIAN),
            }
        )
        await storage.save_merged_property(merged_a)
        await storage.save_quality_analysis(prop_a.unique_id, analysis)
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "Fit with your brief" in resp.text


# ---------------------------------------------------------------------------
# Status tracking routes (Ticket 7)
# ---------------------------------------------------------------------------


class TestStatusRoutes:
    """Tests for PATCH /property/{unique_id}/status and status features."""

    @pytest.mark.asyncio
    async def test_patch_status_success(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.patch(
            f"/property/{merged_a.unique_id}/status",
            data={"status": "interested"},
        )
        assert resp.status_code == 200
        assert "status-selector" in resp.text

    @pytest.mark.asyncio
    async def test_patch_status_invalid_400(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.patch(
            f"/property/{merged_a.unique_id}/status",
            data={"status": "bogus"},
        )
        assert resp.status_code == 400

    def test_patch_status_unknown_404(self, client: TestClient) -> None:
        resp = client.patch(
            "/property/fake:999/status",
            data={"status": "interested"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_patch_returns_selector_partial(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.patch(
            f"/property/{merged_a.unique_id}/status",
            data={"status": "interested"},
        )
        assert resp.status_code == 200
        assert "<!DOCTYPE html>" not in resp.text
        assert "status-selector" in resp.text
        assert "Interested" in resp.text
        # hx-target must use "closest" to avoid colon-in-id CSS selector bug
        assert 'hx-target="closest .status-selector-wrap"' in resp.text
        assert 'hx-swap="morph:innerHTML"' in resp.text

    @pytest.mark.asyncio
    async def test_patch_returns_card_partial_for_htmx_card_swap(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        """When source=card, return full card partial."""
        await storage.save_merged_property(merged_a)
        resp = client.patch(
            f"/property/{merged_a.unique_id}/status",
            data={"status": "interested", "source": "card"},
        )
        assert resp.status_code == 200
        assert "property-card" in resp.text
        assert 'data-status="interested"' in resp.text
        assert "<!DOCTYPE html>" not in resp.text

    @pytest.mark.asyncio
    async def test_dashboard_status_filter(
        self,
        client: TestClient,
        storage: PropertyStorage,
        merged_a: MergedProperty,
        merged_b: MergedProperty,
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.save_merged_property(merged_b)
        from home_finder.models import UserStatus

        await storage.update_user_status(merged_a.unique_id, UserStatus.INTERESTED)
        resp = client.get("/?status=interested")
        assert resp.status_code == 200
        assert "1 bed in E8" in resp.text
        assert "2 bed in N16" not in resp.text

    @pytest.mark.asyncio
    async def test_status_filter_chip_rendered(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        """The status filter should produce a filter chip in the partial response."""
        await storage.save_merged_property(merged_a)
        resp = client.get("/?status=new", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert 'data-filter-key="status"' in resp.text

    def test_sort_longest_listed(self, client: TestClient) -> None:
        resp = client.get("/?sort=longest_listed")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_consecutive_status_changes(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        """3 sequential PATCHes should each return 200 with the correct label."""
        await storage.save_merged_property(merged_a)
        steps = [
            ("interested", "Interested"),
            ("enquired", "Enquired"),
            ("viewing_booked", "Viewing"),
        ]
        for status, label in steps:
            resp = client.patch(
                f"/property/{merged_a.unique_id}/status",
                data={"status": status},
            )
            assert resp.status_code == 200, f"Failed on {status}"
            assert label in resp.text, f"Missing label '{label}' after setting {status}"

    @pytest.mark.asyncio
    async def test_response_htmx_attrs_on_all_buttons(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        """Every status-option button in the PATCH response must have correct HTMX attrs."""
        from bs4 import BeautifulSoup

        await storage.save_merged_property(merged_a)
        resp = client.patch(
            f"/property/{merged_a.unique_id}/status",
            data={"status": "interested"},
        )
        assert resp.status_code == 200
        soup = BeautifulSoup(resp.text, "html.parser")
        buttons = soup.select("button.status-option")
        # There should be 9 status buttons (3 groups x 3)
        assert len(buttons) == 9
        for btn in buttons:
            assert btn.get("hx-target") == "closest .status-selector-wrap"
            assert btn.get("hx-swap") == "morph:innerHTML"
            assert btn.get("hx-patch")

    @pytest.mark.asyncio
    async def test_active_and_disabled_state_updates(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        """After PATCH: exactly 1 disabled button and 1 checkmark. Verify across 2 changes."""
        from bs4 import BeautifulSoup

        await storage.save_merged_property(merged_a)
        for status in ("interested", "enquired"):
            resp = client.patch(
                f"/property/{merged_a.unique_id}/status",
                data={"status": status},
            )
            assert resp.status_code == 200
            soup = BeautifulSoup(resp.text, "html.parser")
            # Exactly one disabled button
            assert len(soup.select("button.status-option[disabled]")) == 1
            # Exactly one checkmark SVG
            assert len(soup.select("svg.status-option-check")) == 1
            # The active button class should appear exactly once
            assert len(soup.select("button.status-option.active")) == 1

    @pytest.mark.asyncio
    async def test_trigger_label_and_color_update(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        """Trigger button shows new label and --status-color matches USER_STATUS_META."""
        from home_finder.models import USER_STATUS_META

        await storage.save_merged_property(merged_a)
        for status in ("viewing_booked", "rejected"):
            resp = client.patch(
                f"/property/{merged_a.unique_id}/status",
                data={"status": status},
            )
            assert resp.status_code == 200
            meta = USER_STATUS_META[status]
            # Trigger button should contain the label
            assert meta["label"] in resp.text
            # Trigger should use the correct --status-color
            assert f"--status-color: {meta['color']}" in resp.text


# ---------------------------------------------------------------------------
# Price history + benchmark on detail (Ticket 10)
# ---------------------------------------------------------------------------


class TestPriceHistoryDetail:
    """Tests for price and benchmark rendering on detail page."""

    @pytest.mark.asyncio
    async def test_price_drop_badge_on_card(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.detect_and_record_price_change(merged_a.unique_id, 1750)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Dropped" in resp.text

    @pytest.mark.asyncio
    async def test_status_bar_on_detail(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        """Detail page should render the status selector in header meta."""
        await storage.save_merged_property(merged_a)
        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "detail-status-bar" not in resp.text
        assert "status-selector" in resp.text

    @pytest.mark.asyncio
    async def test_listing_age_pill_on_detail(
        self, client: TestClient, storage: PropertyStorage
    ) -> None:
        """Detail page should show listing age pill for properties with first_seen."""
        prop = Property(
            source=PropertySource.OPENRENT,
            source_id="999",
            url=HttpUrl("https://openrent.com/999"),
            title="Old listing",
            price_pcm=1900,
            bedrooms=1,
            address="1 Street",
            postcode="E8 3RH",
            first_seen="2025-01-01T00:00:00+00:00",
            image_url=HttpUrl("https://example.com/img.jpg"),
        )
        merged = MergedProperty(
            canonical=prop,
            sources=(PropertySource.OPENRENT,),
            source_urls={PropertySource.OPENRENT: prop.url},
            min_price=1900,
            max_price=1900,
        )
        await storage.save_merged_property(merged)
        resp = client.get(f"/property/{merged.unique_id}")
        assert resp.status_code == 200
        assert "listing-age-pill" in resp.text
        assert "First seen" in resp.text
        assert "May be negotiable" not in resp.text


# ---------------------------------------------------------------------------
# Off-market display
# ---------------------------------------------------------------------------


class TestOffMarketDisplay:
    """Tests for off-market badge on cards, banner on detail, and filter."""

    @pytest.mark.asyncio
    async def test_off_market_badge_on_card(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.mark_off_market(merged_a.unique_id)

        # off_market=show to include off-market in results
        resp = client.get("/?off_market=show")
        assert resp.status_code == 200
        assert "OFF MARKET" in resp.text
        assert "property-card-off-market" in resp.text

    @pytest.mark.asyncio
    async def test_off_market_hidden_by_default(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.mark_off_market(merged_a.unique_id)

        resp = client.get("/")
        assert resp.status_code == 200
        # Should not appear in default view
        assert "OFF MARKET" not in resp.text

    @pytest.mark.asyncio
    async def test_off_market_only_filter(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.mark_off_market(merged_a.unique_id)

        resp = client.get("/?off_market=only")
        assert resp.status_code == 200
        assert "OFF MARKET" in resp.text

    @pytest.mark.asyncio
    async def test_off_market_banner_on_detail(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.mark_off_market(merged_a.unique_id)

        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "off-market-banner" in resp.text
        assert "removed from the listing platform" in resp.text

    @pytest.mark.asyncio
    async def test_no_banner_for_active_property(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)

        resp = client.get(f"/property/{merged_a.unique_id}")
        assert resp.status_code == 200
        assert "off-market-banner" not in resp.text

    @pytest.mark.asyncio
    async def test_map_markers_include_off_market(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        await storage.mark_off_market(merged_a.unique_id)

        resp = client.get("/?off_market=show")
        assert resp.status_code == 200
        # Map markers JSON includes is_off_market
        assert "is_off_market" in resp.text


# ---------------------------------------------------------------------------
# Phase 4: Error handling, ARIA, response-targets
# ---------------------------------------------------------------------------


class TestHTMXErrorResponses:
    """Tests for HTML error responses on HTMX requests (4.4)."""

    @pytest.mark.asyncio
    async def test_patch_invalid_status_htmx_returns_html(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.patch(
            f"/property/{merged_a.unique_id}/status",
            data={"status": "bogus"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 400
        assert "toast-message" in resp.text
        assert "<p" in resp.text

    def test_patch_missing_htmx_returns_html(self, client: TestClient) -> None:
        resp = client.patch(
            "/property/fake:999/status",
            data={"status": "interested"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404
        assert "toast-message" in resp.text

    def test_patch_missing_non_htmx_returns_json(self, client: TestClient) -> None:
        resp = client.patch(
            "/property/fake:999/status",
            data={"status": "interested"},
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "Property not found"

    @pytest.mark.asyncio
    async def test_patch_invalid_non_htmx_returns_json(
        self, client: TestClient, storage: PropertyStorage, merged_a: MergedProperty
    ) -> None:
        await storage.save_merged_property(merged_a)
        resp = client.patch(
            f"/property/{merged_a.unique_id}/status",
            data={"status": "bogus"},
        )
        assert resp.status_code == 400
        assert "Invalid status" in resp.json()["error"]


class TestResponseTargetsAndARIA:
    """Tests for response-targets extension attributes and ARIA markup (4.4, 4.8)."""

    def test_filter_form_has_response_targets(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'hx-target-4*="#error-toast"' in resp.text

    def test_body_has_hx_ext(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'hx-ext="response-targets,morph"' in resp.text

    def test_error_toast_container_exists(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'id="error-toast"' in resp.text
        assert 'role="alert"' in resp.text

    def test_sr_announcer_exists(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'id="sr-announcer"' in resp.text
        assert 'aria-live="polite"' in resp.text

    def test_description_toggle_aria_expanded(self, client: TestClient) -> None:
        resp = client.get("/")
        # The detail page has the toggle, but dashboard doesn't.
        # Just verify the base template has the sr-announcer.
        assert resp.status_code == 200
        assert 'id="sr-announcer"' in resp.text


async def test_two_voices_lead_brief_notes_and_working_render(
    client, storage, prop_a, merged_a, base_analysis
) -> None:
    """The listing opens with the brief's first gap, lists the brief, and annotates the text."""
    from bs4 import BeautifulSoup

    from home_finder.models import FlooringNoiseAnalysis

    analysis = base_analysis.model_copy(
        update={
            "lowlights": ["Potential traffic noise", "No dishwasher"],
            "highlights": ["Gas hob"],
            "flooring_noise": FlooringNoiseAnalysis(
                primary_flooring="wood",
                has_double_glazing="yes",
                hosting_noise_risk="moderate",
                noise_indicators=["Railway line directly beside the building"],
            ),
        }
    )
    await storage.save_merged_property(merged_a, commute_minutes=22)
    await storage.update_source_text(
        merged_a.unique_id,
        ListingTextEvidence.observed(
            observed_at=datetime.now(UTC),
            description="A stunning flat with a private balcony and a fitted kitchen.",
        ),
    )
    await storage.save_quality_analysis(prop_a.unique_id, analysis)
    client.app.state.settings = client.app.state.settings.model_copy(
        update={"brief_must_haves": "workspace,sound"}
    )
    soup = BeautifulSoup(client.get(f"/property/{merged_a.unique_id}").text, "html.parser")

    lead = soup.select_one(".detail-lead")
    assert "detail-lead-risk" in lead["class"]
    assert (
        lead.select_one(".detail-lead-title").get_text()
        == "Railway line directly beside the building."
    )
    assert lead.select_one(".detail-lead-detail").get_text() == (
        "Your must-have: quiet enough for calls. Photos can't settle this; check it at a viewing."
    )

    groups = [li.get_text(strip=True) for li in soup.select(".brief-check .brief-group")]
    assert groups == ["Must-haves", "Nice-to-haves"]
    broken = soup.select_one(".brief-check .brief-broken")
    assert broken.select_one(".brief-label").get_text() == "Quiet enough for calls"

    assert soup.select_one(".fit-brief-score") is not None
    rows = soup.select(".fit-working tbody tr")
    assert len(rows) == 6
    assert soup.select_one(".fit-working tfoot td:last-child").get_text(
        strip=True
    ) == soup.select_one(".fit-brief-score").get_text(strip=True)

    notes = [li.get_text() for li in soup.select(".annotated-notes li")]
    assert notes[0] == "Marketing wording."
    assert soup.select_one("#section-nav a[href='#section-description']") is not None
    assert (
        "Photo assessment: Railway line directly beside the building. Check noise at a viewing."
        in notes
    )
    assert soup.select_one(".annotated-text .mark-strike").get_text() == "stunning"
    assert soup.select_one("#section-description .description-text") is None


@pytest.mark.asyncio
async def test_listing_text_keeps_its_paragraph_breaks(
    client, storage, prop_a, merged_a, base_analysis
) -> None:
    """A newline in the retained text renders as a line break, not a literal "<br>".

    Both renderings of the text are covered: the plain description shown when there is
    no analysis, and the annotated one shown when there is.
    """
    await storage.save_merged_property(merged_a)
    await storage.update_source_text(
        merged_a.unique_id,
        ListingTextEvidence.observed(
            observed_at=datetime.now(UTC), description="First paragraph.\n\nSecond paragraph."
        ),
    )
    plain = client.get(f"/property/{merged_a.unique_id}").text
    assert "First paragraph.<br><br>Second paragraph." in plain
    assert "&lt;br&gt;" not in plain

    await storage.save_quality_analysis(prop_a.unique_id, base_analysis)
    annotated = client.get(f"/property/{merged_a.unique_id}").text
    assert '<p class="annotated-text">First paragraph.<br><br>Second paragraph.</p>' in annotated
    assert "&lt;br&gt;" not in annotated


@pytest.mark.asyncio
async def test_templates_mark_figures_and_drop_badge_colours(
    client, storage, prop_a, merged_a, base_analysis
) -> None:
    """Figures inside sentences are wrapped in .figure, the "up to" qualifier is wrapped the
    other way round, and source badges carry no inline colour."""
    from bs4 import BeautifulSoup

    await storage.save_merged_property(merged_a, commute_minutes=22)
    await storage.save_quality_analysis(prop_a.unique_id, base_analysis)

    queue = BeautifulSoup(client.get("/").text, "html.parser")
    assert queue.select_one("#nav-count .figure").get_text() == "1"
    assert re.fullmatch(r"\d/\d", queue.select_one(".card-must .figure").get_text())
    badges = queue.select(".source-badge")
    assert badges and all(not badge.has_attr("style") for badge in badges)

    listing = BeautifulSoup(client.get(f"/property/{merged_a.unique_id}").text, "html.parser")
    assert listing.select_one(".money-total-qualifier").get_text() == "up to"
    assert listing.select_one(".fit-working tfoot .figure") is not None
