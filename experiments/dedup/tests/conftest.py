"""Shared fixtures for dedup experiment tests."""

import json
import sys
from pathlib import Path

import pytest

# Add the experiment root to sys.path so we can import generate_report, etc.
EXPERIMENT_ROOT = Path(__file__).resolve().parent.parent
if str(EXPERIMENT_ROOT) not in sys.path:
    sys.path.insert(0, str(EXPERIMENT_ROOT))

# --------------------------------------------------------------------------- #
# Test pair data — 5 pairs with known scores, varying signals
# --------------------------------------------------------------------------- #

PAIR_TEMPLATE = {
    "pair_id": "",
    "block": "",
    "property_a": {
        "unique_id": "",
        "source": "zoopla",
        "title": "",
        "price_pcm": 0,
        "bedrooms": 1,
        "address": "",
        "postcode": "",
        "latitude": None,
        "longitude": None,
        "url": "https://example.com/a",
        "image_url": None,
        "description_preview": "",
        "has_description": False,
        "gallery_count": 0,
        "features": [],
    },
    "property_b": {
        "unique_id": "",
        "source": "onthemarket",
        "title": "",
        "price_pcm": 0,
        "bedrooms": 1,
        "address": "",
        "postcode": "",
        "latitude": None,
        "longitude": None,
        "url": "https://example.com/b",
        "image_url": None,
        "description_preview": "",
        "has_description": False,
        "gallery_count": 0,
        "features": [],
    },
    "scorer": {"score": 0, "signal_count": 0, "is_match": False, "breakdown": {}},
    "signals": {},
}


def _make_signal(fired: bool, value: float, detail: str = "") -> dict:
    return {"fired": fired, "value": value, "detail": detail}


def _make_pair(
    idx: int,
    score: float,
    signal_count: int,
    is_match: bool,
    *,
    source_a: str = "zoopla",
    source_b: str = "onthemarket",
    postcode_a: str = "E3 4AA",
    postcode_b: str = "E3 4AA",
    price_a: int = 1500,
    price_b: int = 1500,
    address_a: str = "1 Test Road",
    address_b: str = "1 Test Road",
    lat_a: float | None = 51.5,
    lon_a: float | None = -0.05,
    lat_b: float | None = 51.5,
    lon_b: float | None = -0.05,
) -> dict:
    pair = json.loads(json.dumps(PAIR_TEMPLATE))  # deep copy
    pair["pair_id"] = f"{source_a}:a{idx}||{source_b}:b{idx}"
    pair["block"] = f"E3:{pair['property_a']['bedrooms']}"
    pair["property_a"]["unique_id"] = f"{source_a}:a{idx}"
    pair["property_a"]["source"] = source_a
    pair["property_a"]["title"] = f"Property A{idx}"
    pair["property_a"]["price_pcm"] = price_a
    pair["property_a"]["address"] = address_a
    pair["property_a"]["postcode"] = postcode_a
    pair["property_a"]["latitude"] = lat_a
    pair["property_a"]["longitude"] = lon_a
    pair["property_a"]["description_preview"] = f"Description for property A{idx}"
    pair["property_a"]["has_description"] = True
    pair["property_b"]["unique_id"] = f"{source_b}:b{idx}"
    pair["property_b"]["source"] = source_b
    pair["property_b"]["title"] = f"Property B{idx}"
    pair["property_b"]["price_pcm"] = price_b
    pair["property_b"]["address"] = address_b
    pair["property_b"]["postcode"] = postcode_b
    pair["property_b"]["latitude"] = lat_b
    pair["property_b"]["longitude"] = lon_b
    pair["property_b"]["description_preview"] = f"Description for property B{idx}"
    pair["property_b"]["has_description"] = True
    pair["scorer"] = {
        "score": score,
        "signal_count": signal_count,
        "is_match": is_match,
        "breakdown": {"full_postcode": score / 2, "coordinates": score / 2},
    }
    pair["signals"] = {
        "full_postcode": _make_signal(True, 1.0, f"{postcode_a} / {postcode_b}"),
        "outcode": _make_signal(True, 1.0, "E3 vs E3"),
        "coordinates": _make_signal(lat_a is not None, 1.0, "0m"),
        "street_name": _make_signal(address_a == address_b, 1.0 if address_a == address_b else 0.0),
        "price": _make_signal(True, 1.0 if price_a == price_b else 0.5, f"£{price_a} vs £{price_b}"),
        "fuzzy_address": _make_signal(False, 0.0),
        "address_number": _make_signal(False, 0.0),
        "title_similarity": _make_signal(False, 0.0),
        "feature_overlap": _make_signal(False, 0.0),
        "gallery_images": _make_signal(False, 0.0),
        "gallery_embeddings": _make_signal(False, 0.0),
        "description_tfidf": _make_signal(False, 0.0),
        "description_semantic": _make_signal(False, 0.0),
    }
    return pair


# Five pairs with distinct scores for sort testing
TEST_PAIRS = [
    _make_pair(0, score=80.0, signal_count=3, is_match=True),           # high match
    _make_pair(1, score=55.0, signal_count=2, is_match=False,           # near threshold
               price_a=1500, price_b=1800),
    _make_pair(2, score=20.0, signal_count=1, is_match=False,           # low score
               postcode_b="E3 4BB", address_b="2 Other St"),
    _make_pair(3, score=95.0, signal_count=4, is_match=True,            # highest match
               source_a="rightmove"),
    _make_pair(4, score=45.0, signal_count=2, is_match=False,           # mid-low
               lat_a=None, lon_a=None, lat_b=None, lon_b=None),         # no coords
]


@pytest.fixture
def test_pairs():
    """Return the list of test pairs."""
    return TEST_PAIRS


@pytest.fixture
def candidates_json(tmp_path: Path) -> Path:
    """Write test pairs to a candidates.json file and return the path."""
    data = {
        "generated_from": ["test"],
        "config": {"weights": {}, "thresholds": {"match_threshold": 70, "min_signals": 2}},
        "total_properties": 10,
        "total_pairs": len(TEST_PAIRS),
        "pairs": TEST_PAIRS,
    }
    p = tmp_path / "candidates.json"
    p.write_text(json.dumps(data))
    return p


@pytest.fixture
def report_html(tmp_path: Path, candidates_json: Path) -> Path:
    """Generate the HTML report and return the path."""
    from generate_report import render_report

    output = tmp_path / "report.html"
    render_report(candidates_json, output)
    return output
