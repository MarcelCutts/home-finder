"""Jinja filters registered by the web routes."""

from datetime import UTC, datetime, timedelta

from home_finder.web.routes import listing_age_filter


class TestListingAgeFilter:
    """listing_age: how long ago Home Finder first saw a listing, as on the cards."""

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
