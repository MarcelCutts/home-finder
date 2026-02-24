"""Browser-based E2E tests using Playwright against a live FastAPI server.

These tests spin up a real server with pre-populated data and use Chromium
to verify the dashboard, detail pages, map, and responsive behavior.

Requires: pytest-playwright, `uv run playwright install chromium`
"""

import asyncio
import re
import socket
import threading
import time
from datetime import datetime

import pytest
from playwright.sync_api import expect
from pydantic import HttpUrl, SecretStr

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.models import (
    BedroomAnalysis,
    ConditionAnalysis,
    FlooringNoiseAnalysis,
    KitchenAnalysis,
    LightSpaceAnalysis,
    ListingExtraction,
    MergedProperty,
    OutdoorSpaceAnalysis,
    Property,
    PropertyImage,
    PropertyQualityAnalysis,
    PropertySource,
    PropertyType,
    SpaceAnalysis,
    TransportMode,
    ValueAnalysis,
)

from .conftest import wait_for_htmx_settle


def _find_free_port() -> int:
    """Find an available port by binding to port 0."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


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
        url=HttpUrl(f"https://www.openrent.com/property/{source_id}"),
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
                url=HttpUrl("https://example.com/img1.jpg"),
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
            has_worn_fixtures="no",
            maintenance_concerns=[],
            confidence="high",
        ),
        light_space=LightSpaceAnalysis(
            natural_light="good",
            feels_spacious=True,
            notes="Bright",
            ceiling_height="high",
            floor_level="upper",
        ),
        space=SpaceAnalysis(
            living_room_sqm=20.0,
            is_spacious_enough=True,
            hosting_layout="good",
        ),
        bedroom=BedroomAnalysis(
            primary_is_double="yes",
            can_fit_desk="yes",
            office_separation="separate_area",
        ),
        outdoor_space=OutdoorSpaceAnalysis(
            has_balcony=True,
            has_garden=False,
            has_terrace=False,
            has_shared_garden=False,
        ),
        flooring_noise=FlooringNoiseAnalysis(
            building_construction="solid_brick",
            has_double_glazing="yes",
            hosting_noise_risk="low",
        ),
        condition_concerns=False,
        value=ValueAnalysis(
            area_average=2200,
            difference=price - 2200,
            rating="good",
            note=f"£{abs(price - 2200)} {'below' if price < 2200 else 'above'} average",
        ),
        overall_rating=rating,
        summary=f"Well-maintained {bedrooms}-bed flat with modern kitchen and good light.",
        highlights=["Gas hob", "Solid brick"],
        listing_extraction=ListingExtraction(property_type=PropertyType.VICTORIAN),
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


@pytest.fixture(scope="session")
def server_url(tmp_path_factory):
    """Start a test server with pre-populated data (session-scoped)."""
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
        telegram_bot_token=SecretStr("fake:test-token"),
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

    app = create_app(settings, run_pipeline=False)

    port = _find_free_port()
    base = f"http://127.0.0.1:{port}"

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error",
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to be ready
    import httpx

    for _ in range(50):
        try:
            resp = httpx.get(f"{base}/health", timeout=1)
            if resp.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.2)
    else:
        pytest.fail("Test server did not start")

    yield base

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def base_url(server_url):
    """Override pytest-playwright's base_url so tests can use relative paths."""
    return server_url


@pytest.mark.browser
class TestDashboardBrowser:
    """Test dashboard page in real browser."""

    def test_loads_property_cards(self, page):
        page.goto("/")
        cards = page.locator("article.property-card")
        expect(cards.first).to_be_visible()

    def test_filter_by_bedrooms(self, page):
        # Navigate directly with filter param to test server-side filtering
        page.goto("/?bedrooms=1")
        # Radio button should reflect the filter (auto-retries until checked)
        expect(page.locator("input[name='bedrooms'][value='1']")).to_be_checked()

    def test_filter_by_area(self, page):
        page.goto("/?area=E8")
        expect(page.locator("select[name='area']")).to_have_value("E8")

    def test_sort_options(self, page):
        page.goto("/?sort=price_asc")
        expect(page.locator("select[name='sort']")).to_have_value("price_asc")

    def test_htmx_partial_rendering(self, page):
        page.goto("/")
        # Wait for initial content to render
        expect(page.locator("article.property-card").first).to_be_visible()

        # Trigger HTMX filter — page title should still be present (no full reload)
        page.locator("label[for='beds-1']").click()
        page.click("button[type='submit']")
        wait_for_htmx_settle(page)

        # Page title should still be present (HTMX only replaces #results)
        title = page.title()
        assert "I Hate Moving" in title


@pytest.mark.browser
class TestDialogVisibility:
    """Regression tests for dialog hidden/visible state.

    Catches the CSS ``display: flex`` bug that overrode ``<dialog>`` hidden
    state, and CORS/JS errors that shipped silently.
    """

    def test_filter_dialog_hidden_on_page_load(self, page):
        """Regression: dialog must not be visible on initial load."""
        page.goto("/")
        expect(page.locator("#filter-modal")).not_to_be_visible()

    def test_properties_visible_on_page_load(self, page):
        """Properties should be visible and not obscured by overlays."""
        page.goto("/")
        cards = page.locator("article.property-card")
        expect(cards.first).to_be_visible()

    def test_filter_dialog_full_lifecycle(self, page):
        """Hidden -> open -> close -> hidden (full round-trip)."""
        page.goto("/")
        dialog = page.locator("#filter-modal")
        expect(dialog).not_to_be_visible()

        page.get_by_role("button", name="Filters").click()
        expect(dialog).to_be_visible()

        page.get_by_role("button", name="Close filters").click()
        expect(dialog).not_to_be_visible()

    def test_no_console_errors_on_dashboard(self, page, console_errors):
        """No JS/CORS errors on dashboard load."""
        page.goto("/")
        # Wait for page content to render (catches errors from JS execution)
        expect(page.locator("article.property-card").first).to_be_visible()
        assert not console_errors, f"Console errors: {console_errors}"

    def test_no_console_errors_on_detail(self, page, console_errors):
        """No JS/CORS errors on detail page load."""
        page.goto("/property/openrent:1003")
        expect(page.locator("h1")).to_be_visible()
        assert not console_errors, f"Console errors: {console_errors}"


@pytest.mark.browser
class TestPropertyDetailBrowser:
    """Test property detail page in real browser."""

    def test_detail_page_loads(self, page):
        page.goto("/")
        # Click the first property card link
        first_card_link = page.locator("article.property-card a").first
        first_card_link.click()

        # h1 should be visible on detail page
        expect(page.locator("h1")).to_be_visible()

    def test_gallery_lightbox(self, page):
        # Navigate to a property with images (analyzed property 1003)
        page.goto("/property/openrent:1003")
        expect(page.locator("h1")).to_be_visible()

        # Gallery images depend on disk cache (not populated in test data)
        gallery_imgs = page.locator("[data-lightbox] img, [data-lightbox]")
        if gallery_imgs.count() == 0:
            pytest.skip("No gallery images rendered (images not cached to disk in test)")

        gallery_imgs.first.click()
        gallery_view = page.locator("#gallery-view")
        expect(gallery_view).to_be_visible()
        # Press Escape to close
        page.keyboard.press("Escape")
        expect(gallery_view).not_to_be_visible()

    def test_quality_analysis_section(self, page):
        # Property 1003 has quality analysis
        page.goto("/property/openrent:1003")
        # Quality analysis card should be visible
        quality_section = page.locator(".quality-section")
        expect(quality_section.first).to_be_visible()

    def test_map_rendered(self, page):
        # Property 1003 has coordinates
        page.goto("/property/openrent:1003")
        # Leaflet map container should exist and be visible
        map_container = page.locator(".leaflet-container")
        expect(map_container.first).to_be_visible()


@pytest.mark.browser
class TestMapViewBrowser:
    """Test dashboard map view."""

    def test_map_toggle(self, page):
        page.goto("/")
        # Map toggle button should exist on the dashboard
        map_btn = page.locator("button[data-view='map']")
        expect(map_btn).to_be_visible()
        map_btn.click()
        # Dashboard map should become visible
        expect(page.locator("#dashboard-map")).to_be_visible()

    def test_map_markers(self, page):
        page.goto("/")
        map_btn = page.locator("button[data-view='map']")
        expect(map_btn).to_be_visible()
        map_btn.click()
        markers = page.locator(".leaflet-marker-icon")
        # Wait for map + markers to render
        expect(markers.first).to_be_visible()


@pytest.mark.browser
@pytest.mark.parametrize("width", [375, 768, 1440])
class TestResponsiveBrowser:
    """Test responsive layout at different widths."""

    def test_responsive(self, page, width):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto("/")
        # Wait for layout to render before measuring scroll dimensions
        expect(page.locator("article.property-card").first).to_be_visible()

        # Check for no horizontal overflow
        body_scroll_width = page.evaluate("document.body.scrollWidth")
        viewport_width = page.evaluate("window.innerWidth")
        assert body_scroll_width <= viewport_width + 5  # 5px tolerance


@pytest.mark.browser
class TestFilterBehavior:
    """Test filter interaction: no auto-apply, modal lifecycle, chips."""

    # -- Group 1: No Auto-Apply --

    def test_select_change_does_not_auto_apply(self, page):
        """Changing a select should NOT auto-submit the form."""
        page.goto("/")
        nav_count = page.locator("nav #nav-count")
        expect(nav_count).to_contain_text("5 properties")

        requests: list[str] = []
        page.on("request", lambda req: requests.append(req.url))

        page.get_by_label("Area", exact=True).select_option("E8")
        # Deliberate delay: verify no auto-submit fires within debounce window
        page.wait_for_timeout(800)

        # No main-page HTMX request should have fired
        htmx_requests = [r for r in requests if "127.0.0.1" in r and "/count" not in r]
        assert not htmx_requests, f"Unexpected requests: {htmx_requests}"
        expect(nav_count).to_contain_text("5 properties")

    def test_bedrooms_radio_does_not_auto_apply(self, page):
        """Clicking a bedrooms radio should NOT auto-submit."""
        page.goto("/")
        nav_count = page.locator("nav #nav-count")
        expect(nav_count).to_contain_text("5 properties")

        requests: list[str] = []
        page.on("request", lambda req: requests.append(req.url))

        page.locator("label[for='beds-2']").click()
        # Deliberate delay: verify no auto-submit fires within debounce window
        page.wait_for_timeout(800)

        htmx_requests = [r for r in requests if "127.0.0.1" in r and "/count" not in r]
        assert not htmx_requests, f"Unexpected requests: {htmx_requests}"
        expect(nav_count).to_contain_text("5 properties")

    def test_apply_button_submits_and_updates(self, page):
        """Clicking Apply submits filters and updates results."""
        page.goto("/")
        page.get_by_label("Area", exact=True).select_option("E5")

        with page.expect_response(lambda r: "127.0.0.1" in r.url and "area=E5" in r.url):
            page.get_by_role("button", name="Apply").click()

        expect(page.locator("nav #nav-count")).to_contain_text("1 propert")
        assert "area=E5" in page.url

    # -- Group 2: Filter Modal Lifecycle --

    def test_modal_opens(self, page):
        """Clicking Filters button opens the dialog."""
        page.goto("/")
        dialog = page.locator("#filter-modal")
        expect(dialog).not_to_be_visible()
        page.get_by_role("button", name="Filters").click()
        expect(dialog).to_be_visible()

    def test_modal_stays_open_on_filter_change(self, page):
        """Changing a filter inside the modal should NOT close it."""
        page.goto("/")
        dialog = page.locator("#filter-modal")
        expect(dialog).not_to_be_visible()
        page.get_by_role("button", name="Filters").click()
        expect(dialog).to_be_visible()

        page.get_by_label("Hob type").select_option("gas")
        # Wait for /count HTMX request to settle
        wait_for_htmx_settle(page)
        expect(dialog).to_be_visible()

    def test_modal_count_updates_on_filter_change(self, page):
        """Modal count updates live when filters change."""
        page.goto("/")
        dialog = page.locator("#filter-modal")
        expect(dialog).not_to_be_visible()
        page.get_by_role("button", name="Filters").click()
        expect(page.locator("#modal-count")).to_have_text("5")

        page.get_by_label("Hob type").select_option("gas")
        # Auto-retries until count updates via /count endpoint
        expect(page.locator("#modal-count")).to_have_text("2")

    def test_modal_apply_closes_and_filters(self, page):
        """Modal Apply closes dialog and applies filters."""
        page.goto("/")
        page.get_by_role("button", name="Filters").click()
        page.get_by_label("Hob type").select_option("gas")
        expect(page.locator("#modal-count")).to_have_text("2")

        page.get_by_role("button", name=re.compile(r"Show \d+ properties")).click()

        expect(page.locator("#filter-modal")).not_to_be_visible()
        expect(page.locator("nav #nav-count")).to_contain_text("2 propert")
        assert "hob_type=gas" in page.url

    def test_modal_close_without_applying(self, page):
        """Closing modal without Apply should not change results."""
        page.goto("/")
        nav_count = page.locator("nav #nav-count")
        expect(nav_count).to_contain_text("5 properties")

        page.get_by_role("button", name="Filters").click()
        page.get_by_label("Hob type").select_option("gas")
        expect(page.locator("#modal-count")).to_have_text("2")

        page.get_by_role("button", name="Close filters").click()

        expect(page.locator("#filter-modal")).not_to_be_visible()
        expect(nav_count).to_contain_text("5 properties")

    def test_modal_reset_all(self, page):
        """Reset all clears both modal and primary filters."""
        # Start with an active bedrooms filter
        page.goto("/?bedrooms=1")
        nav_count = page.locator("nav #nav-count")
        expect(nav_count).to_contain_text("3 propert")

        page.get_by_role("button", name="Filters").click()
        page.get_by_label("Hob type").select_option("gas")
        expect(page.locator("#modal-count")).to_have_text("1")

        page.get_by_role("button", name="Reset all").click()

        # Count should reflect all filters cleared (including bedrooms)
        expect(page.locator("#modal-count")).to_have_text("5")
        assert page.get_by_label("Hob type").input_value() == ""

    # -- Group 2b: Modal Count Accuracy --

    def test_modal_count_area_filter(self, page):
        """Modal count is accurate with area filter active."""
        # E8 has properties 1001, 1002
        page.goto("/?area=E8")
        page.get_by_role("button", name="Filters").click()
        expect(page.locator("#modal-count")).to_have_text("2")

    def test_modal_count_bedrooms_filter(self, page):
        """Modal count is accurate with bedrooms filter active."""
        # 2-bed: properties 1002, 1004
        page.goto("/?bedrooms=2")
        page.get_by_role("button", name="Filters").click()
        expect(page.locator("#modal-count")).to_have_text("2")

    def test_modal_count_combined_filters(self, page):
        """Modal count is accurate with primary + modal filters combined."""
        # bedrooms=1 + area=E8 = property 1001 only
        page.goto("/?bedrooms=1&area=E8")
        page.get_by_role("button", name="Filters").click()
        expect(page.locator("#modal-count")).to_have_text("1")

        # Adding hob_type=gas should narrow to 0 (1001 has no quality analysis)
        page.get_by_label("Hob type").select_option("gas")
        expect(page.locator("#modal-count")).to_have_text("0")

    def test_modal_reset_then_apply_delivers_all_results(self, page):
        """Reset all followed by Apply returns the full unfiltered result set."""
        page.goto("/?bedrooms=1&hob_type=gas")
        nav_count = page.locator("nav #nav-count")
        expect(nav_count).to_contain_text("1 propert")

        page.get_by_role("button", name="Filters").click()
        page.get_by_role("button", name="Reset all").click()
        expect(page.locator("#modal-count")).to_have_text("5")

        page.get_by_role("button", name=re.compile(r"Show \d+ properties")).click()

        expect(page.locator("#filter-modal")).not_to_be_visible()
        expect(nav_count).to_contain_text("5 properties")
        assert "bedrooms=" not in page.url
        assert "hob_type=" not in page.url

    def test_modal_reset_clears_primary_field_values(self, page):
        """Reset all clears select/radio values for primary filters."""
        page.goto("/?area=E8&bedrooms=1")
        page.get_by_role("button", name="Filters").click()

        # Verify the primary fields have filter values pre-selected
        assert page.get_by_label("Area", exact=True).input_value() == "E8"
        assert page.locator("input[name='bedrooms'][value='1']").is_checked()

        page.get_by_role("button", name="Reset all").click()

        # Primary fields should be cleared
        assert page.get_by_label("Area", exact=True).input_value() == ""
        assert page.locator("input[name='bedrooms'][value='']").is_checked()

    # -- Group 3: Empty State & Filter Chips --

    def test_empty_state_when_no_results(self, page):
        """Studio filter (0 beds) shows empty state."""
        page.goto("/")
        page.locator("label[for='beds-0']").click()

        with page.expect_response(lambda r: "127.0.0.1" in r.url and "bedrooms=0" in r.url):
            page.get_by_role("button", name="Apply").click()

        expect(page.locator(".empty-state")).to_be_visible()
        expect(page.locator(".empty-state")).to_contain_text("No properties found")
        expect(page.locator(".empty-state a")).to_be_visible()

    def test_empty_state_reset_link_works(self, page):
        """Reset filters link in empty state restores all results."""
        page.goto("/?bedrooms=0")
        expect(page.locator(".empty-state")).to_be_visible()

        page.locator(".empty-state a").click()

        expect(page.locator("nav #nav-count")).to_contain_text("5 properties")
        expect(page.locator(".empty-state")).not_to_be_visible()

    def test_filter_chips_appear_and_removal_works(self, page):
        """Filter chips appear for active filters and can be removed."""
        page.goto("/?area=E8")
        nav_count = page.locator("nav #nav-count")
        expect(nav_count).to_contain_text("2 propert")

        chip = page.locator(".filter-chip")
        expect(chip).to_be_visible()
        expect(chip).to_contain_text("E8")

        with page.expect_response(lambda r: "127.0.0.1" in r.url):
            page.locator(".filter-chip-remove").click()

        expect(nav_count).to_contain_text("5 properties")
        assert "area=E8" not in page.url


@pytest.mark.browser
class TestFitScorePopover:
    """Phase 1: Fit score badge popover and detail breakdown."""

    def test_fit_badge_visible_on_analyzed_cards(self, page):
        """Fit badge appears on cards that have quality analysis."""
        page.goto("/")
        badges = page.locator(".fit-badge")
        expect(badges.first).to_be_visible()

    def test_fit_badge_not_on_unanalyzed_cards(self, page):
        """Unanalyzed cards (1001, 1002, 1005) should not have fit badge."""
        page.goto("/")
        card_1001 = page.locator('article[data-property-id="openrent:1001"]')
        expect(card_1001).to_be_visible()
        expect(card_1001.locator(".fit-badge")).to_have_count(0)

    def test_clicking_badge_opens_popover(self, page):
        """Clicking the fit badge opens a popover with dimension bars."""
        page.goto("/")
        wrap = page.locator(".fit-popover-wrap").first
        expect(wrap.locator(".fit-badge")).to_be_visible()
        wrap.locator(".fit-badge").click()
        popover = wrap.locator(".fit-popover")
        expect(popover).to_be_visible()
        # Should show 6 dimension rows within this popover
        expect(popover.locator(".fit-dim-row")).to_have_count(6)

    def test_clicking_badge_does_not_navigate(self, page):
        """Clicking the fit badge does NOT navigate to the detail page."""
        page.goto("/")
        badge = page.locator(".fit-badge").first
        expect(badge).to_be_visible()
        original_url = page.url
        badge.click()
        # Deliberate delay: verify no navigation occurs within a reasonable window
        page.wait_for_timeout(500)
        expect(page).to_have_url(original_url)

    def test_click_outside_closes_popover(self, page):
        """Clicking outside an open popover closes it (light-dismiss)."""
        page.goto("/")
        badge = page.locator(".fit-badge").first
        expect(badge).to_be_visible()
        badge.click()
        expect(page.locator(".fit-popover").first).to_be_visible()
        # Click on body (outside) — light-dismiss closes native popover
        page.locator("body").click(position={"x": 10, "y": 10})
        expect(page.locator(".fit-popover").first).not_to_be_visible()

    def test_detail_page_shows_fit_breakdown(self, page):
        """Detail page for analyzed property shows fit breakdown card."""
        page.goto("/property/openrent:1003")
        breakdown = page.locator(".fit-breakdown-card")
        expect(breakdown).to_be_visible()
        # Should have 6 dimension rows
        expect(page.locator(".fit-breakdown-row")).to_have_count(6)

    def test_detail_breakdown_labels(self, page):
        """Detail breakdown shows correct dimension labels."""
        page.goto("/property/openrent:1003")
        labels = page.locator(".fit-breakdown-label")
        expect(labels.first).to_be_visible()
        label_texts = [labels.nth(i).text_content().strip() for i in range(labels.count())]
        assert "Workspace" in label_texts
        assert "Kitchen" in label_texts
        assert "Hosting" in label_texts
        assert "Sound" in label_texts
        assert "Vibe" in label_texts
        assert "Condition" in label_texts


@pytest.mark.browser
class TestMobileViewport:
    """Phase 2: Mobile viewport optimizations."""

    def test_filter_bar_not_sticky_on_mobile(self, page):
        """Filter bar should NOT be position:sticky on 375px viewport."""
        page.set_viewport_size({"width": 375, "height": 812})
        page.goto("/")
        filter_bar = page.locator(".filter-bar")
        expect(filter_bar).to_be_visible()
        position = filter_bar.evaluate("el => getComputedStyle(el).position")
        assert position == "static"

    def test_filter_bar_sticky_on_desktop(self, page):
        """Filter bar should be position:sticky on 1440px viewport."""
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto("/")
        filter_bar = page.locator(".filter-bar")
        expect(filter_bar).to_be_visible()
        position = filter_bar.evaluate("el => getComputedStyle(el).position")
        assert position == "sticky"

    def test_animation_delay_capped(self, page):
        """Card animation delay should be capped at index 8."""
        page.goto("/")
        cards = page.locator(".card-entrance")
        expect(cards.first).to_be_visible()
        count = cards.count()
        assert count > 1, "Expected multiple cards for animation delay test"
        for i in range(count):
            style = cards.nth(i).get_attribute("style")
            assert style is not None
            # Parse the --card-index value
            idx = int(style.split("--card-index:")[1].strip().rstrip(";").strip())
            assert idx <= 8

    def test_view_toggle_icons_only_on_mobile(self, page):
        """View toggle shows icons but no text labels on mobile."""
        page.set_viewport_size({"width": 375, "height": 812})
        page.goto("/")
        labels = page.locator(".view-toggle-label")
        # Labels exist in DOM but are hidden via CSS on mobile
        expect(labels.first).to_be_attached()
        visible = labels.first.evaluate("el => getComputedStyle(el).display")
        assert visible == "none"


@pytest.mark.browser
class TestSourceBadgeClickTarget:
    """Phase 3: Source badges inside card link."""

    def test_source_badges_inside_card_link(self, page):
        """Source badge elements are descendants of .card-link."""
        page.goto("/")
        badges_inside_link = page.locator(".card-link .card-sources")
        expect(badges_inside_link.first).to_be_visible()

    def test_clicking_source_badge_area_navigates(self, page):
        """Clicking the source badge area navigates to detail page."""
        page.goto("/")
        badge = page.locator(".card-sources .source-badge").first
        expect(badge).to_be_visible()
        badge.click()
        expect(page).to_have_url(re.compile(r"/property/"))


@pytest.mark.browser
class TestDetailPagePolish:
    """Phase 4: Detail page scroll-to-top link."""

    def test_section_nav_has_top_link(self, page):
        """Section nav contains a 'Top' link."""
        page.goto("/property/openrent:1003")
        top_link = page.locator(".section-nav-top a")
        expect(top_link).to_be_visible()
        expect(top_link).to_have_text("Top")

    def test_top_link_scrolls_to_top(self, page):
        """Clicking 'Top' scrolls page to top."""
        page.goto("/property/openrent:1003")
        top_link = page.locator(".section-nav-top a")
        expect(top_link).to_be_visible()
        # Scroll down first
        page.evaluate("window.scrollTo(0, 1000)")
        page.wait_for_timeout(300)  # Wait for scroll to complete
        scroll_before = page.evaluate("window.scrollY")
        assert scroll_before > 0

        top_link.click()
        # Poll via evaluate (wait_for_function blocked by CSP nonce policy)
        page.wait_for_timeout(800)  # Wait for smooth scroll animation
        scroll_after = page.evaluate("window.scrollY")
        assert scroll_after < scroll_before
