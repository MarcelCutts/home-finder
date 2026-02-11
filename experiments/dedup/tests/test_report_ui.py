"""Playwright browser tests for the labeling report UI.

Tests cover: rendering, navigation, labeling, keyboard shortcuts, sort, filter,
undo, stats, import/export, list view, and storage.

Run with:
    uv run pytest tests/test_report_ui.py -v
"""

import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _get_displayed_score(page: Page) -> float:
    """Extract the score from the currently visible pair header."""
    text = page.locator(".pair-score").inner_text()
    # e.g. "80.0 pts (3 signals) → MATCH"
    match = re.search(r"([\d.]+)\s*pts", text)
    assert match, f"Could not parse score from: {text}"
    return float(match.group(1))


def _get_displayed_pair_num(page: Page) -> int:
    """Extract the pair number (e.g. #1) from the header."""
    text = page.locator(".pair-num").inner_text()
    return int(text.strip().lstrip("#"))


def _get_nav_info(page: Page) -> tuple[int, int]:
    """Parse '3 / 5' from nav info."""
    text = page.locator("#navInfo").inner_text()
    parts = text.split("/")
    return int(parts[0].strip()), int(parts[1].strip())


def _get_progress(page: Page) -> tuple[int, int]:
    """Parse '2 / 5' from progress text."""
    text = page.locator("#progressText").inner_text()
    parts = text.split("/")
    return int(parts[0].strip()), int(parts[1].strip())


def _active_label_buttons(page: Page) -> list[str]:
    """Return list of currently active label button types."""
    active = []
    for cls, name in [
        (".match-btn", "match"),
        (".nomatch-btn", "no_match"),
        (".uncertain-btn", "uncertain"),
    ]:
        btn = page.locator(f".label-btn{cls}.active")
        if btn.count() > 0:
            active.append(name)
    return active


def _clear_indexeddb(page: Page):
    """Clear IndexedDB to start fresh."""
    page.evaluate("indexedDB.deleteDatabase('dedup-labels')")


# --------------------------------------------------------------------------- #
# Focus View — Rendering
# --------------------------------------------------------------------------- #


class TestFocusViewRendering:
    """Initial focus view renders correctly."""

    def test_initial_load_shows_first_pair(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined' && ALL_PAIRS.length > 0")

        # Should show pair #1 (hardest first sort — closest to threshold 55)
        pos, total = _get_nav_info(page)
        assert pos == 1
        assert total == 5

    def test_pair_header_shows_score(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        score = _get_displayed_score(page)
        assert score > 0

    def test_property_cards_show_data(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        cards = page.locator(".property-card")
        assert cards.count() == 2

        # Both should have source badges
        sources = page.locator(".property-card .source")
        assert sources.count() == 2

    def test_signals_panel_rendered(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        signal_rows = page.locator(".signal-row")
        assert signal_rows.count() == 13  # All 13 signals

    def test_label_buttons_shown(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        assert page.locator(".match-btn").count() == 1
        assert page.locator(".nomatch-btn").count() == 1
        assert page.locator(".uncertain-btn").count() == 1

    def test_no_label_active_initially(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        assert _active_label_buttons(page) == []


# --------------------------------------------------------------------------- #
# Focus View — Navigation
# --------------------------------------------------------------------------- #


class TestFocusNavigation:
    def test_next_button_advances(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        pos1, _ = _get_nav_info(page)
        page.click("#nextBtn")
        pos2, _ = _get_nav_info(page)
        assert pos2 == pos1 + 1

    def test_prev_button_goes_back(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        page.click("#nextBtn")
        pos_after_next, _ = _get_nav_info(page)
        page.click("#prevBtn")
        pos_after_prev, _ = _get_nav_info(page)
        assert pos_after_prev == pos_after_next - 1

    def test_prev_disabled_at_start(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        assert page.locator("#prevBtn").is_disabled()

    def test_next_disabled_at_end(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        # Navigate to last
        for _ in range(4):
            page.click("#nextBtn")
        assert page.locator("#nextBtn").is_disabled()

    def test_arrow_keys_navigate(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        pos1, _ = _get_nav_info(page)
        page.keyboard.press("ArrowRight")
        pos2, _ = _get_nav_info(page)
        assert pos2 == pos1 + 1

        page.keyboard.press("ArrowLeft")
        pos3, _ = _get_nav_info(page)
        assert pos3 == pos1


# --------------------------------------------------------------------------- #
# Focus View — Labeling
# --------------------------------------------------------------------------- #


class TestLabeling:
    def test_click_match_button(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        page.click(".match-btn")
        page.wait_for_timeout(300)

        # Should have auto-advanced — check that we're on pair 2
        pos, _ = _get_nav_info(page)
        assert pos == 2

    def test_click_nomatch_button(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        page.click(".nomatch-btn")
        page.wait_for_timeout(300)

        pos, _ = _get_nav_info(page)
        assert pos == 2

    def test_label_clears_on_next_pair(self, page: Page, report_html: Path):
        """BUG REPORT: After labeling pair 1, pair 2 should show NO active buttons."""
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        # Label pair 1 as match — should auto-advance to pair 2
        page.click(".match-btn")
        page.wait_for_timeout(300)

        # Pair 2 is unlabeled — NO buttons should be active
        active = _active_label_buttons(page)
        assert active == [], f"Expected no active buttons on unlabeled pair, got: {active}"

    def test_keyboard_a_labels_match(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        page.keyboard.press("a")
        page.wait_for_timeout(300)

        # Should auto-advance
        pos, _ = _get_nav_info(page)
        assert pos == 2

    def test_keyboard_d_labels_nomatch(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        page.keyboard.press("d")
        page.wait_for_timeout(300)

        pos, _ = _get_nav_info(page)
        assert pos == 2

    def test_keyboard_s_labels_uncertain(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        page.keyboard.press("s")
        page.wait_for_timeout(300)

        pos, _ = _get_nav_info(page)
        assert pos == 2

    def test_label_persists_after_navigating_back(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        # Label pair 1 as match
        page.click(".match-btn")
        page.wait_for_timeout(300)

        # Now on pair 2 — go back to pair 1
        page.click("#prevBtn")
        page.wait_for_timeout(100)

        # Pair 1 should still show match as active
        active = _active_label_buttons(page)
        assert "match" in active

    def test_relabel_changes_selection(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        # Label as match
        page.click(".match-btn")
        page.wait_for_timeout(300)

        # Go back and relabel as no_match
        page.click("#prevBtn")
        page.wait_for_timeout(100)
        page.click(".nomatch-btn")
        page.wait_for_timeout(300)

        # Go back — should show no_match, NOT match
        page.click("#prevBtn")
        page.wait_for_timeout(100)
        active = _active_label_buttons(page)
        assert active == ["no_match"]

    def test_label_does_not_advance_past_last_pair(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        # Navigate to last pair
        for _ in range(4):
            page.click("#nextBtn")
        pos, total = _get_nav_info(page)
        assert pos == total  # On last pair

        # Label it — should stay on same pair (don't go past end)
        page.click(".match-btn")
        page.wait_for_timeout(300)
        pos2, _ = _get_nav_info(page)
        assert pos2 == total

        # The label should show as active since we're still on this pair
        active = _active_label_buttons(page)
        assert "match" in active

    def test_notes_field_saves(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        # Label first, then add notes
        page.click(".match-btn")
        page.wait_for_timeout(300)

        # Go back to pair 1
        page.click("#prevBtn")
        page.wait_for_timeout(100)

        # Type notes
        page.fill("#notesInput", "test note")
        page.locator("#notesInput").dispatch_event("change")
        page.wait_for_timeout(200)

        # Navigate away and back
        page.click("#nextBtn")
        page.wait_for_timeout(100)
        page.click("#prevBtn")
        page.wait_for_timeout(100)

        assert page.locator("#notesInput").input_value() == "test note"


# --------------------------------------------------------------------------- #
# Focus View — Undo
# --------------------------------------------------------------------------- #


class TestUndo:
    def test_undo_reverts_label(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        # Label pair 1
        page.click(".match-btn")
        page.wait_for_timeout(300)

        # Undo — should go back to pair 1, unlabeled
        page.click(".undo-btn")
        page.wait_for_timeout(300)

        pos, _ = _get_nav_info(page)
        assert pos == 1
        assert _active_label_buttons(page) == []

    def test_ctrl_z_undoes(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        page.keyboard.press("a")  # match
        page.wait_for_timeout(300)

        page.keyboard.press("Meta+z")
        page.wait_for_timeout(300)

        pos, _ = _get_nav_info(page)
        assert pos == 1
        assert _active_label_buttons(page) == []

    def test_undo_button_disabled_when_empty(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        assert page.locator(".undo-btn").is_disabled()

    def test_multiple_undos(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        # Label 3 pairs in a row
        page.keyboard.press("a")  # match pair 1
        page.wait_for_timeout(200)
        page.keyboard.press("d")  # no_match pair 2
        page.wait_for_timeout(200)
        page.keyboard.press("s")  # uncertain pair 3
        page.wait_for_timeout(200)

        # Undo all 3
        for _ in range(3):
            page.keyboard.press("Meta+z")
            page.wait_for_timeout(200)

        # Should be back at pair 1, unlabeled
        pos, _ = _get_nav_info(page)
        assert pos == 1
        assert _active_label_buttons(page) == []


# --------------------------------------------------------------------------- #
# Sort
# --------------------------------------------------------------------------- #


class TestSort:
    """Sort should reorder pairs and show the first pair in the new order."""

    def test_default_sort_is_hardest_first(self, page: Page, report_html: Path):
        """Default sort should show the pair closest to match threshold (55)."""
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        score = _get_displayed_score(page)
        # Pair with score 55.0 is closest to threshold 55
        assert score == 55.0

    def test_sort_score_desc_in_focus(self, page: Page, report_html: Path):
        """BUG REPORT: Changing sort in focus view should show first pair in new order."""
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        page.select_option("#sortSelect", "score_desc")
        page.wait_for_timeout(200)

        score = _get_displayed_score(page)
        assert score == 95.0  # Highest score first
        pos, _ = _get_nav_info(page)
        assert pos == 1  # Should be at position 1

    def test_sort_score_asc_in_focus(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        page.select_option("#sortSelect", "score_asc")
        page.wait_for_timeout(200)

        score = _get_displayed_score(page)
        assert score == 20.0  # Lowest score first

    def test_sort_unlabeled_first(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        # Label the first pair
        page.click(".match-btn")
        page.wait_for_timeout(300)

        # Switch to unlabeled first sort
        page.select_option("#sortSelect", "unlabeled")
        page.wait_for_timeout(200)

        # The first pair shown should be unlabeled
        active = _active_label_buttons(page)
        assert active == []

    def test_sort_changes_navigation_order(self, page: Page, report_html: Path):
        """After sorting score_desc, navigating should go through pairs in score order."""
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        page.select_option("#sortSelect", "score_desc")
        page.wait_for_timeout(200)

        scores = []
        for i in range(5):
            scores.append(_get_displayed_score(page))
            if i < 4:
                page.click("#nextBtn")
                page.wait_for_timeout(100)

        # Scores should be in descending order
        assert scores == sorted(scores, reverse=True), f"Expected descending, got {scores}"


# --------------------------------------------------------------------------- #
# Filter
# --------------------------------------------------------------------------- #


class TestFilter:
    def test_filter_all_shows_all_pairs(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        _, total = _get_nav_info(page)
        assert total == 5

    def test_filter_unlabeled_excludes_labeled(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        # Label 2 pairs
        page.keyboard.press("a")
        page.wait_for_timeout(200)
        page.keyboard.press("d")
        page.wait_for_timeout(200)

        # Filter to unlabeled
        page.click("[data-filter='unlabeled']")
        page.wait_for_timeout(200)

        _, total = _get_nav_info(page)
        assert total == 3

    def test_filter_match_shows_only_matches(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        # Label 1 as match, 1 as no_match
        page.keyboard.press("a")
        page.wait_for_timeout(200)
        page.keyboard.press("d")
        page.wait_for_timeout(200)

        page.click("[data-filter='match']")
        page.wait_for_timeout(200)

        _, total = _get_nav_info(page)
        assert total == 1

    def test_filter_empty_shows_empty_state(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        # No uncertain labels — filter should be empty
        page.click("[data-filter='uncertain']")
        page.wait_for_timeout(200)

        assert page.locator(".empty-state").count() == 1

    def test_labeling_in_unlabeled_filter_removes_from_list(self, page: Page, report_html: Path):
        """When filtering 'unlabeled' and labeling, the pair should vanish from the list."""
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        page.click("[data-filter='unlabeled']")
        page.wait_for_timeout(200)
        _, total_before = _get_nav_info(page)

        page.keyboard.press("a")
        page.wait_for_timeout(300)

        _, total_after = _get_nav_info(page)
        assert total_after == total_before - 1


# --------------------------------------------------------------------------- #
# Stats & Progress
# --------------------------------------------------------------------------- #


class TestStats:
    def test_initial_progress_zero(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        labeled, total = _get_progress(page)
        assert labeled == 0
        assert total == 5

    def test_progress_updates_on_label(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        page.keyboard.press("a")
        page.wait_for_timeout(300)

        labeled, _ = _get_progress(page)
        assert labeled == 1

    def test_stat_chips_update(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        page.keyboard.press("a")  # match
        page.wait_for_timeout(200)
        page.keyboard.press("d")  # no_match
        page.wait_for_timeout(200)

        assert "Match: 1" in page.locator("#stat-match").inner_text()
        assert "Not Match: 1" in page.locator("#stat-no_match").inner_text()
        assert "Unlabeled: 3" in page.locator("#stat-unlabeled").inner_text()

    def test_progress_decreases_on_undo(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        page.keyboard.press("a")
        page.wait_for_timeout(200)

        labeled1, _ = _get_progress(page)
        assert labeled1 == 1

        page.keyboard.press("Meta+z")
        page.wait_for_timeout(200)

        labeled2, _ = _get_progress(page)
        assert labeled2 == 0


# --------------------------------------------------------------------------- #
# List View
# --------------------------------------------------------------------------- #


class TestListView:
    def test_switch_to_list_view(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        page.click("[data-view='list']")
        page.wait_for_timeout(200)

        assert page.locator("#listView").is_visible()
        assert not page.locator("#focusView").is_visible()

    def test_list_view_shows_rows(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        page.click("[data-view='list']")
        page.wait_for_timeout(200)

        rows = page.locator(".list-row")
        assert rows.count() == 5  # All 5 pairs visible (small dataset)

    def test_click_list_row_goes_to_focus(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        page.click("[data-view='list']")
        page.wait_for_timeout(200)

        # Click the 3rd row
        page.locator(".list-row").nth(2).click()
        page.wait_for_timeout(200)

        # Should be in focus view at position 3
        assert page.locator("#focusView").is_visible()
        pos, _ = _get_nav_info(page)
        assert pos == 3

    def test_list_shows_label_status(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        # Label first pair
        page.keyboard.press("a")
        page.wait_for_timeout(200)

        # Switch to list
        page.click("[data-view='list']")
        page.wait_for_timeout(200)

        # First row should show "match" label
        first_label = page.locator(".list-row").first.locator(".row-label")
        assert "match" in first_label.inner_text().lower()

    def test_tab_toggles_view(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        assert page.locator("#focusView").is_visible()

        page.keyboard.press("Tab")
        page.wait_for_timeout(200)
        assert page.locator("#listView").is_visible()

        page.keyboard.press("Tab")
        page.wait_for_timeout(200)
        assert page.locator("#focusView").is_visible()


# --------------------------------------------------------------------------- #
# Import / Export
# --------------------------------------------------------------------------- #


class TestImportExport:
    def test_export_creates_json(self, page: Page, report_html: Path, tmp_path: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        # Label 2 pairs
        page.keyboard.press("a")
        page.wait_for_timeout(200)
        page.keyboard.press("d")
        page.wait_for_timeout(200)

        # Export
        with page.expect_download() as download_info:
            page.click("text=Export")
        download = download_info.value
        dl_path = tmp_path / "exported.json"
        download.save_as(dl_path)

        data = json.loads(dl_path.read_text())
        label_values = [v["label"] for v in data.values()]
        assert "match" in label_values
        assert "no_match" in label_values

    def test_export_contains_all_labeled_pairs(self, page: Page, report_html: Path, tmp_path: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        # Label 3 pairs
        for key in ["a", "d", "s"]:
            page.keyboard.press(key)
            page.wait_for_timeout(200)

        with page.expect_download() as download_info:
            page.click("text=Export")
        download = download_info.value
        dl_path = tmp_path / "exported.json"
        download.save_as(dl_path)

        data = json.loads(dl_path.read_text())
        assert len(data) == 3


# --------------------------------------------------------------------------- #
# Storage persistence
# --------------------------------------------------------------------------- #


class TestPersistence:
    def test_labels_survive_reload(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        # Label pair 1
        page.keyboard.press("a")
        page.wait_for_timeout(300)

        # Reload
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")
        page.wait_for_timeout(500)

        labeled, _ = _get_progress(page)
        assert labeled == 1

    def test_labels_survive_reload_and_navigate_back(self, page: Page, report_html: Path):
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        # Remember which pair is first (hardest sort)
        score_before = _get_displayed_score(page)

        # Label it
        page.keyboard.press("d")
        page.wait_for_timeout(300)

        # Reload and go back to first pair
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")
        page.wait_for_timeout(500)

        score_after = _get_displayed_score(page)
        assert score_after == score_before

        active = _active_label_buttons(page)
        assert "no_match" in active


# --------------------------------------------------------------------------- #
# Edge Cases
# --------------------------------------------------------------------------- #


class TestEdgeCases:
    def test_rapid_keyboard_labeling(self, page: Page, report_html: Path):
        """Rapid keyboard presses should not drop labels."""
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        # Rapidly label all 5 pairs
        for _ in range(5):
            page.keyboard.press("a")
            page.wait_for_timeout(100)

        page.wait_for_timeout(500)
        labeled, _ = _get_progress(page)
        assert labeled == 5

    def test_pair_border_shows_label_color(self, page: Page, report_html: Path):
        """The pair card should get a colored left border matching its label."""
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        page.click(".match-btn")
        page.wait_for_timeout(300)

        # Go back to labeled pair
        page.click("#prevBtn")
        page.wait_for_timeout(100)

        pair_el = page.locator(".pair")
        assert "labeled-match" in (pair_el.get_attribute("class") or "")

    def test_diff_highlighting_on_price(self, page: Page, report_html: Path):
        """Properties with different prices should show diff highlighting."""
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        # Navigate to a pair with different prices (pair index 1 has price_a=1500, price_b=1800)
        # In hardest-first sort, pair with score 55 is first (which IS pair 1)
        diff_elements = page.locator(".field-value.diff")
        assert diff_elements.count() > 0

    def test_map_hidden_when_no_coords(self, page: Page, report_html: Path):
        """Pair without coordinates should not show the map."""
        page.goto(f"file://{report_html}")
        _clear_indexeddb(page)
        page.reload()
        page.wait_for_function("typeof ALL_PAIRS !== 'undefined'")

        # Pair 4 (score 45, no coords) — sort score_asc to make it easy to find
        page.select_option("#sortSelect", "score_asc")
        page.wait_for_timeout(200)

        # score_asc: 20, 45, 55, 80, 95 — pair at index 1 has score 45, no coords
        score = _get_displayed_score(page)
        assert score == 20.0  # First pair in asc order

        page.click("#nextBtn")
        page.wait_for_timeout(200)

        score = _get_displayed_score(page)
        assert score == 45.0

        map_el = page.locator("#signal-map")
        assert "hidden" in (map_el.get_attribute("class") or "")
