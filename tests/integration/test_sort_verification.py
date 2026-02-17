"""Playwright tests verifying scraper sort parameters against real sites.

These tests navigate to each property portal and check the sort UI to confirm
our URL parameters produce the expected ordering. They serve as living
documentation and regression coverage for sort-order assumptions.

The early-stop pagination in base.py assumes results are sorted newest-first.
If a site changes its sort parameter semantics, these tests will catch it.

Run with: uv run pytest -m "browser and slow" tests/integration/test_sort_verification.py -v
"""

import pytest
from playwright.sync_api import Page, expect

pytestmark = [pytest.mark.browser, pytest.mark.slow]

# ---------------------------------------------------------------------------
# Rightmove: sortType=6 → "Newest Listed"
# ---------------------------------------------------------------------------


class TestRightmoveSortVerification:
    """Verify Rightmove sortType=6 selects 'Newest Listed'."""

    URL = (
        "https://www.rightmove.co.uk/property-to-rent/find.html"
        "?locationIdentifier=OUTCODE%5E762"
        "&minBedrooms=1&maxBedrooms=2"
        "&minPrice=1800&maxPrice=2200"
        "&sortType=6"
        "&propertyTypes=flat"
        "&mustHave=&dontShow=retirement"
        "&furnishTypes=&keywords="
    )

    def test_sort_dropdown_shows_newest_listed(self, page: Page) -> None:
        page.goto(self.URL, wait_until="domcontentloaded")

        sort_select = page.locator("select#sortOptions")
        expect(sort_select).to_be_visible(timeout=15_000)
        assert sort_select.input_value() == "6"

        selected_option = sort_select.locator("option[selected], option:checked")
        assert "newest" in selected_option.text_content().lower()


# ---------------------------------------------------------------------------
# OpenRent: no "newest" sort option exists (documents platform limitation)
# ---------------------------------------------------------------------------


class TestOpenRentSortVerification:
    """Verify OpenRent has no 'newest'/'recent' sort option.

    OpenRent only supports sortType 0 (Distance), 1 (Price ↑), 2 (Price ↓).
    There is no newest-first sort. This test documents that limitation —
    if OpenRent adds one in the future, this test will fail, prompting us
    to re-enable early-stop for OpenRent.
    """

    URL = (
        "https://www.openrent.co.uk/properties-to-rent/e8"
        "?prices_min=1800&prices_max=2200"
        "&bedrooms_min=1&bedrooms_max=2"
        "&within=2"
    )

    def test_no_newest_sort_option(self, page: Page) -> None:
        page.goto(self.URL, wait_until="domcontentloaded")

        # OpenRent renders sort options as radio buttons or a dropdown
        # Look for any element containing sort-related text
        sort_container = page.locator("[id*='sort'], [class*='sort']").first
        if sort_container.is_visible(timeout=10_000):
            sort_text = sort_container.text_content().lower()
        else:
            # Fall back to full page text
            sort_text = page.content().lower()

        # There should be no "newest" or "most recent" sort option
        assert "newest" not in sort_text, (
            "OpenRent now has a 'newest' sort option — re-enable early-stop!"
        )
        assert "most recent" not in sort_text, (
            "OpenRent now has a 'most recent' sort option — re-enable early-stop!"
        )


# ---------------------------------------------------------------------------
# OnTheMarket: sort-field=update_date → "Recent"
# ---------------------------------------------------------------------------


class TestOnTheMarketSortVerification:
    """Verify OnTheMarket sort-field=update_date selects 'Recent'."""

    URL = (
        "https://www.onthemarket.com/to-rent/property/E8"
        "?min-bedrooms=1&max-bedrooms=2"
        "&min-price=1800&max-price=2200"
        "&sort-field=update_date"
    )

    def test_sort_shows_recent(self, page: Page) -> None:
        page.goto(self.URL, wait_until="domcontentloaded")

        # OTM shows a sort button/dropdown with current selection
        sort_button = page.locator(
            "[data-testid='sort-button'], .sort-label, button:has-text('Sort')"
        )

        if sort_button.first.is_visible(timeout=15_000):
            sort_text = sort_button.first.text_content().lower()
        else:
            # Fall back: look for any element mentioning the sort state
            sort_text = page.locator("text=/sort/i").first.text_content().lower()

        assert "recent" in sort_text, (
            f"Expected 'Recent' sort selection, got: {sort_text!r}"
        )


# ---------------------------------------------------------------------------
# Zoopla: results_sort=newest_listings → "Most recent"
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=False, reason="Zoopla Cloudflare may block headless browsers")
class TestZooplaSortVerification:
    """Verify Zoopla results_sort=newest_listings selects 'Most recent'."""

    URL = (
        "https://www.zoopla.co.uk/to-rent/property/e8/"
        "?beds_min=1&beds_max=2"
        "&price_min=1800&price_max=2200"
        "&results_sort=newest_listings"
        "&search_source=to-rent"
    )

    def test_sort_dropdown_shows_most_recent(self, page: Page) -> None:
        page.goto(self.URL, wait_until="domcontentloaded")

        sort_select = page.locator("select#results_sort")

        if sort_select.is_visible(timeout=15_000):
            assert sort_select.input_value() == "newest_listings"
            selected_option = sort_select.locator("option[selected], option:checked")
            assert "recent" in selected_option.text_content().lower()
        else:
            # Zoopla may use a custom dropdown instead of <select>
            sort_el = page.locator("[data-testid*='sort'], [class*='sort']")
            sort_text = sort_el.first.text_content().lower()
            assert "recent" in sort_text, (
                f"Expected 'Most recent' sort, got: {sort_text!r}"
            )
