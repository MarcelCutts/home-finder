"""Jinja filters registered by the web routes."""

from datetime import UTC, datetime, timedelta

import pytest

from home_finder.web.routes import listing_age_filter, short_date_filter


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


class TestShortDateFilter:
    """short_date: a stored UTC timestamp as the date it was in London."""

    @pytest.mark.parametrize(
        ("stored", "shown"),
        [
            ("2026-02-13T09:00:00+00:00", "13 Feb 2026"),
            # British Summer Time: 23:30 UTC is 00:30 the next day in London.
            ("2026-06-13T23:30:00+00:00", "14 Jun 2026"),
            ("2026-06-13T22:59:00+00:00", "13 Jun 2026"),
            # No offset is read as UTC, as listing_age reads it.
            ("2026-06-13T23:30:00", "14 Jun 2026"),
            # Winter: London is on UTC, so the day doesn't move.
            ("2026-12-31T23:30:00+00:00", "31 Dec 2026"),
        ],
    )
    def test_date_is_londons(self, stored: str, shown: str) -> None:
        assert short_date_filter(stored) == shown

    @pytest.mark.parametrize("stored", [None, "", "not-a-date"])
    def test_missing_or_invalid_is_empty(self, stored: str | None) -> None:
        assert short_date_filter(stored) == ""
