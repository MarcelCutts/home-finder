"""Area page (GET /area/{outcode}): the full area guide linked from each detail page."""

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from home_finder.data.area_context import get_micro_areas


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def test_area_page_lists_the_neighbourhoods(client: TestClient) -> None:
    resp = client.get("/area/E8")
    assert resp.status_code == 200
    assert "Dalston core" in resp.text
    assert "Haggerston" in resp.text


@pytest.mark.parametrize("outcode", ["E8", "e8"])
def test_area_page_outcode_is_case_insensitive(client: TestClient, outcode: str) -> None:
    resp = client.get(f"/area/{outcode}")
    assert resp.status_code == 200
    assert _soup(resp.text).select_one("h3").get_text() == "Area Guide: E8"


def test_area_page_unknown_outcode_404(client: TestClient) -> None:
    resp = client.get("/area/ZZ99")
    assert resp.status_code == 404


def test_area_page_highlights_the_requested_neighbourhood(client: TestClient) -> None:
    soup = _soup(client.get("/area/E8?highlight=Dalston+core").text)
    highlighted = soup.select(".micro-area-highlighted")
    assert [card.strong.get_text() for card in highlighted] == ["Dalston core"]


def test_area_page_no_highlight(client: TestClient) -> None:
    resp = client.get("/area/E8")
    assert resp.status_code == 200
    assert "micro-area-highlighted" not in resp.text


def test_area_page_has_benchmarks(client: TestClient) -> None:
    resp = client.get("/area/E8")
    assert resp.status_code == 200
    assert "Historical research comparisons" in resp.text
    assert "not current market medians" in resp.text


def test_area_page_shows_each_neighbourhoods_creative_scene(client: TestClient) -> None:
    soup = _soup(client.get("/area/E8").text)
    with_creative_line = {
        card.strong.get_text()
        for card in soup.select(".micro-area-card")
        if "Creative" in [label.get_text() for label in card.select(".micro-area-detail-label")]
    }
    expected = {name for name, area in get_micro_areas("E8").items() if area.get("creative_scene")}
    assert expected
    assert with_creative_line == expected
