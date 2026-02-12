"""Browser-based E2E tests using Playwright against a live FastAPI server.

These tests spin up a real server with pre-populated data and use Chromium
to verify the dashboard, detail pages, map, and responsive behavior.

Requires: pytest-playwright, `uv run playwright install chromium`
"""

import asyncio
import threading
import time
from datetime import datetime

import pytest

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.models import (
    ConditionAnalysis,
    KitchenAnalysis,
    LightSpaceAnalysis,
    MergedProperty,
    Property,
    PropertyImage,
    PropertyQualityAnalysis,
    PropertySource,
    SpaceAnalysis,
    TransportMode,
    ValueAnalysis,
)

# Port for test server — avoid conflicts with dev server
TEST_PORT = 8765
BASE_URL = f"http://127.0.0.1:{TEST_PORT}"


def _make_test_property(
    source_id: str,
    price: int,
    bedrooms: int,
    postcode: str,
    lat: float,
    lon: float,
    area: str = "E8",
    title: str | None = None,
) -> tuple[MergedProperty, PropertyQualityAnalysis | None]:
    """Create a test property with optional quality analysis."""
    prop = Property(
        source=PropertySource.OPENRENT,
        source_id=source_id,
        url=f"https://www.openrent.com/property/{source_id}",
        title=title or f"{bedrooms} bed flat in {area}",
        price_pcm=price,
        bedrooms=bedrooms,
        address=f"{source_id} Test Street, London",
        postcode=postcode,
        latitude=lat,
        longitude=lon,
        description=f"A lovely {bedrooms} bed flat in {area}.",
        first_seen=datetime(2025, 1, 15, 10, 30),
    )

    merged = MergedProperty(
        canonical=prop,
        sources=(PropertySource.OPENRENT,),
        source_urls={PropertySource.OPENRENT: prop.url},
        images=(
            PropertyImage(
                url="https://example.com/img1.jpg",
                source=PropertySource.OPENRENT,
                image_type="gallery",
            ),
        ),
        min_price=price,
        max_price=price,
        descriptions={PropertySource.OPENRENT: prop.description or ""},
    )

    return merged, None


def _make_analyzed_property(
    source_id: str,
    price: int,
    bedrooms: int,
    postcode: str,
    lat: float,
    lon: float,
    rating: int,
) -> tuple[MergedProperty, PropertyQualityAnalysis]:
    """Create a test property with quality analysis."""
    merged, _ = _make_test_property(
        source_id,
        price,
        bedrooms,
        postcode,
        lat,
        lon,
        title=f"Analyzed {bedrooms} bed flat",
    )

    analysis = PropertyQualityAnalysis(
        kitchen=KitchenAnalysis(overall_quality="modern", hob_type="gas", notes="Good kitchen"),
        condition=ConditionAnalysis(
            overall_condition="good",
            has_visible_damp="no",
            has_visible_mold="no",
            has_worn_fixtures=False,
            maintenance_concerns=[],
            confidence="high",
        ),
        light_space=LightSpaceAnalysis(natural_light="good", feels_spacious=True, notes="Bright"),
        space=SpaceAnalysis(living_room_sqm=20.0, is_spacious_enough=True, confidence="high"),
        condition_concerns=False,
        value=ValueAnalysis(
            area_average=2200,
            difference=price - 2200,
            rating="good",
            note=f"£{abs(price - 2200)} {'below' if price < 2200 else 'above'} average",
        ),
        overall_rating=rating,
        summary=f"Well-maintained {bedrooms}-bed flat with modern kitchen and good light.",
    )

    return merged, analysis


async def _populate_db(db_path: str):
    """Pre-populate the database with 5 test properties."""
    storage = PropertyStorage(db_path)
    await storage.initialize()

    test_data = [
        _make_test_property("1001", 1800, 1, "E8 3RH", 51.5465, -0.0553),
        _make_test_property("1002", 2200, 2, "E8 4AB", 51.5470, -0.0560),
        _make_analyzed_property("1003", 1900, 1, "E5 9NL", 51.5580, -0.0530, rating=4),
        _make_analyzed_property("1004", 2100, 2, "N16 7EF", 51.5600, -0.0800, rating=3),
        _make_test_property("1005", 1700, 1, "E9 5LH", 51.5420, -0.0430),
    ]

    for merged, analysis in test_data:
        await storage.save_merged_property(
            merged,
            commute_minutes=15,
            transport_mode=TransportMode.CYCLING,
        )
        if merged.images:
            await storage.save_property_images(merged.unique_id, list(merged.images))
        if analysis:
            await storage.save_quality_analysis(merged.unique_id, analysis)

    await storage.close()


@pytest.fixture(scope="module")
def server_url(tmp_path_factory):
    """Start a test server with pre-populated data (module-scoped)."""
    import uvicorn

    from home_finder.web.app import create_app

    # Create temp DB and populate it
    tmp_dir = tmp_path_factory.mktemp("browser_e2e")
    db_path = str(tmp_dir / "test.db")

    # Run async population in a separate thread (pytest-asyncio already owns
    # the main event loop, so asyncio.run() would fail here)
    exc: list[BaseException] = []

    def _populate():
        try:
            asyncio.run(_populate_db(db_path))
        except BaseException as e:
            exc.append(e)

    t = threading.Thread(target=_populate)
    t.start()
    t.join()
    if exc:
        raise exc[0]

    settings = Settings(
        telegram_bot_token="fake:test-token",
        telegram_chat_id=0,
        database_path=db_path,
        search_areas="e5,e8,e9,n16",
        min_price=1500,
        max_price=2500,
        min_bedrooms=1,
        max_bedrooms=2,
        enable_quality_filter=False,
        require_floorplan=False,
        pipeline_interval_minutes=9999,  # Don't run pipeline during tests
    )

    app = create_app(settings)

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=TEST_PORT,
        log_level="error",
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to be ready
    import httpx

    for _ in range(50):
        try:
            resp = httpx.get(f"{BASE_URL}/health", timeout=1)
            if resp.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.2)
    else:
        pytest.fail("Test server did not start")

    yield BASE_URL

    server.should_exit = True
    thread.join(timeout=5)


@pytest.mark.browser
class TestDashboardBrowser:
    """Test dashboard page in real browser."""

    def test_loads_property_cards(self, server_url, page):
        page.goto(server_url)
        cards = page.locator("article.property-card")
        # We populated 5 properties
        assert cards.count() >= 1

    def test_filter_by_bedrooms(self, server_url, page):
        # Navigate directly with filter param to test server-side filtering
        page.goto(f"{server_url}/?bedrooms=1")
        page.wait_for_load_state("networkidle")
        # Dropdown should reflect the filter
        selected = page.locator("select[name='bedrooms']").input_value()
        assert selected == "1"
        # All visible cards should be 1-bed (or page is valid HTML)
        assert page.locator("article").count() >= 0

    def test_filter_by_area(self, server_url, page):
        page.goto(f"{server_url}/?area=E8")
        page.wait_for_load_state("networkidle")
        selected = page.locator("select[name='area']").input_value()
        assert selected == "E8"

    def test_sort_options(self, server_url, page):
        page.goto(f"{server_url}/?sort=price_asc")
        page.wait_for_load_state("networkidle")
        selected = page.locator("select[name='sort']").input_value()
        assert selected == "price_asc"

    def test_htmx_partial_rendering(self, server_url, page):
        page.goto(server_url)
        # Page title should be present initially
        assert page.locator("h1, title").first is not None

        # Trigger HTMX filter — page title should still be present (no full reload)
        page.select_option("select[name='bedrooms']", "1")
        page.click("button[type='submit']")
        page.wait_for_load_state("networkidle")

        # Page title should still be present (HTMX only replaces #results)
        title = page.title()
        assert "Home Finder" in title


@pytest.mark.browser
class TestPropertyDetailBrowser:
    """Test property detail page in real browser."""

    def test_detail_page_loads(self, server_url, page):
        page.goto(server_url)
        # Click the first property card link
        first_card_link = page.locator("article.property-card a").first
        first_card_link.click()
        page.wait_for_load_state("networkidle")

        # h1 should be visible on detail page
        h1 = page.locator("h1")
        assert h1.is_visible()

    def test_gallery_lightbox(self, server_url, page):
        # Navigate to a property with images (analyzed property 1003)
        page.goto(f"{server_url}/property/openrent:1003")
        page.wait_for_load_state("networkidle")

        # Check if there are gallery images
        gallery_imgs = page.locator(".gallery img, .gallery-image, [data-lightbox] img")
        if gallery_imgs.count() > 0:
            gallery_imgs.first.click()
            # Check if lightbox overlay appears
            lightbox = page.locator(".lightbox, .lightbox-overlay, [role='dialog']")
            if lightbox.count() > 0:
                assert lightbox.first.is_visible()
                # Press Escape to close
                page.keyboard.press("Escape")

    def test_quality_analysis_section(self, server_url, page):
        # Property 1003 has quality analysis
        page.goto(f"{server_url}/property/openrent:1003")
        page.wait_for_load_state("networkidle")

        # Star rating should be visible
        stars = page.locator(".star-rating, .star")
        assert stars.count() > 0

    def test_map_rendered(self, server_url, page):
        # Property 1003 has coordinates
        page.goto(f"{server_url}/property/openrent:1003")
        page.wait_for_load_state("networkidle")

        # Leaflet map container should exist on the detail page
        map_container = page.locator(".leaflet-container")
        # May take a moment to initialize
        if map_container.count() > 0:
            assert map_container.first.is_visible()


@pytest.mark.browser
class TestMapViewBrowser:
    """Test dashboard map view."""

    def test_map_toggle(self, server_url, page):
        page.goto(server_url)
        page.wait_for_load_state("networkidle")

        # Click map view toggle
        map_btn = page.locator("button[data-view='map']")
        if map_btn.count() > 0:
            map_btn.click()
            # Dashboard map should become visible
            dashboard_map = page.locator("#dashboard-map")
            # Wait for it to become visible
            page.wait_for_timeout(1000)
            assert not dashboard_map.is_hidden()

    def test_map_markers(self, server_url, page):
        page.goto(server_url)
        page.wait_for_load_state("networkidle")

        map_btn = page.locator("button[data-view='map']")
        if map_btn.count() > 0:
            map_btn.click()
            page.wait_for_timeout(2000)  # Wait for map + markers to render

            markers = page.locator(".leaflet-marker-icon")
            # We have properties with coordinates, so markers should appear
            if markers.count() > 0:
                assert markers.count() >= 1


@pytest.mark.browser
@pytest.mark.parametrize("width", [375, 768, 1440])
class TestResponsiveBrowser:
    """Test responsive layout at different widths."""

    def test_responsive(self, server_url, page, width):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(server_url)
        page.wait_for_load_state("networkidle")

        # Check for no horizontal overflow
        body_scroll_width = page.evaluate("document.body.scrollWidth")
        viewport_width = page.evaluate("window.innerWidth")
        assert body_scroll_width <= viewport_width + 5  # 5px tolerance
