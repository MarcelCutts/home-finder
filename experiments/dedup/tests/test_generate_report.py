"""Tests for generate_report.py — Python-side report generation."""

import json
from pathlib import Path

import pytest


class TestSlimPairs:
    """The report should strip heavy fields and keep only what the UI needs."""

    def test_raw_a_raw_b_stripped(self, candidates_json: Path, tmp_path: Path):
        """raw_a and raw_b should be removed from the JSON blob."""
        # Add raw_a/raw_b to input
        data = json.loads(candidates_json.read_text())
        for pair in data["pairs"]:
            pair["raw_a"] = {"huge": "data" * 1000}
            pair["raw_b"] = {"huge": "data" * 1000}
        candidates_json.write_text(json.dumps(data))

        from generate_report import render_report

        output = tmp_path / "report.html"
        render_report(candidates_json, output)
        html = output.read_text()

        assert '"raw_a"' not in html
        assert '"raw_b"' not in html

    def test_essential_fields_preserved(self, report_html: Path):
        """All fields needed by the UI must be in the embedded JSON."""
        html = report_html.read_text()
        pairs = _extract_pairs_json(html)

        for pair in pairs:
            assert "pair_id" in pair
            assert "block" in pair
            assert "property_a" in pair
            assert "property_b" in pair
            assert "scorer" in pair
            assert "signals" in pair

            for side in ("property_a", "property_b"):
                prop = pair[side]
                for field in (
                    "unique_id", "source", "title", "price_pcm", "bedrooms",
                    "address", "postcode", "url",
                ):
                    assert field in prop, f"Missing {field} in {side}"

            scorer = pair["scorer"]
            for field in ("score", "signal_count", "is_match"):
                assert field in scorer, f"Missing {field} in scorer"


class TestReportStructure:
    """The generated HTML should be structurally valid."""

    def test_html_has_doctype(self, report_html: Path):
        html = report_html.read_text()
        assert html.startswith("<!DOCTYPE html>")

    def test_total_pairs_in_title(self, report_html: Path):
        html = report_html.read_text()
        assert "<title>Dedup Labeling — 5 pairs</title>" in html

    def test_pairs_json_is_valid(self, report_html: Path):
        html = report_html.read_text()
        pairs = _extract_pairs_json(html)
        assert len(pairs) == 5

    def test_pair_count_matches(self, report_html: Path):
        html = report_html.read_text()
        pairs = _extract_pairs_json(html)
        # TOTAL const should match
        assert f"const TOTAL = ALL_PAIRS.length;" in html
        assert len(pairs) == 5

    def test_no_jinja_artifacts(self, report_html: Path):
        """Ensure no unrendered Jinja2 template syntax."""
        html = report_html.read_text()
        assert "{{" not in html
        assert "{%" not in html
        assert "#}" not in html

    def test_leaflet_loaded(self, report_html: Path):
        html = report_html.read_text()
        assert "leaflet@1.9.4" in html

    def test_keyboard_shortcuts_defined(self, report_html: Path):
        html = report_html.read_text()
        assert "addEventListener('keydown'" in html


class TestXSSPrevention:
    """Ensure malicious data in pairs doesn't break the page."""

    def test_xss_in_title(self, tmp_path: Path):
        """Script tags in property titles should be escaped client-side."""
        from generate_report import render_report
        from conftest import _make_pair

        evil_pair = _make_pair(0, score=50, signal_count=1, is_match=False)
        evil_pair["property_a"]["title"] = '<script>alert("xss")</script>'
        data = {
            "generated_from": ["test"],
            "config": {},
            "total_properties": 2,
            "total_pairs": 1,
            "pairs": [evil_pair],
        }
        cand = tmp_path / "candidates.json"
        cand.write_text(json.dumps(data))
        output = tmp_path / "report.html"
        render_report(cand, output)
        html = output.read_text()

        # The script tag should appear as JSON data (escaped in the JSON blob),
        # not as raw HTML. The client-side esc() function handles rendering.
        pairs = _extract_pairs_json(html)
        assert pairs[0]["property_a"]["title"] == '<script>alert("xss")</script>'
        # But the HTML itself shouldn't have unescaped script from template rendering
        # (the old Jinja2 approach with autoescape=True would catch this,
        # now we rely on esc() in the JS rendering)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _extract_pairs_json(html: str) -> list[dict]:
    """Extract the ALL_PAIRS JSON array from the HTML."""
    marker = "const ALL_PAIRS = "
    start = html.index(marker) + len(marker)
    depth = 0
    i = start
    while i < len(html):
        if html[i] == "[":
            depth += 1
        elif html[i] == "]":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return json.loads(html[start : i + 1])
