"""Reported units, ranges and decimal precision must survive parsing."""

import json
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from home_finder.models import PropertySource
from home_finder.models.source_area import SourceAreaReading, display_area_square_metres
from home_finder.scrapers.detail_fetcher import DetailFetcher, _zoopla_area_from_rsc
from home_finder.scrapers.detail_identity import DetailIdentityError
from home_finder.scrapers.source_area import rightmove_areas, single_area


def reading(**changes):
    return SourceAreaReading.model_validate(
        {
            "source_field": "sizings[0]",
            "unit": "sq_ft",
            "source_unit": "sqft",
            "lower": "500",
            "upper": "500",
            **changes,
        }
    )


def rightmove_html(raw_sizings, *, with_image=False):
    return (
        '<html><script>window.PAGE_MODEL = {"propertyData":{"id":100001,'
        + ('"images":[{"url":"https://example.com/area.jpg"}],' if with_image else "")
        + '"text":{"description":"Retained source description"},"sizings":'
        + raw_sizings
        + "}};</script></html>"
    )


def zoopla_html(raw_quantity):
    chunk = '1:{"taxonomy":{"sizeSqft":' + raw_quantity + "}}"
    return (
        '<html><head><link rel="canonical" '
        'href="https://www.zoopla.co.uk/to-rent/details/100001/"></head>'
        "<script>self.__next_f.push([1," + json.dumps(chunk) + "]);</script></html>"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"lower": True},
        {"lower": 500.25},
        {"lower": "NaN"},
        {"lower": "Infinity"},
        {"lower": "0"},
        {"lower": "100001"},
        {"lower": "500.000000001"},
        {"lower": "501", "upper": "500"},
        {"lower": None, "upper": None},
        {"unit": "sq_m", "source_unit": "sqft"},
        {"basis": "gross_internal"},
    ],
)
def test_quantity_contract_rejects_ambiguous_or_unsupported_input(changes):
    with pytest.raises(ValidationError):
        reading(**changes)


def test_exact_conversion_preserves_the_reported_quantity():
    reported = reading()
    assert reported.lower_square_metres == Decimal("46.45152000")
    assert reported.lower == Decimal("500") and reported.basis == "unspecified"
    assert display_area_square_metres((reported,)) == 46.5
    assert reading(unit="sq_m", source_unit="sqm", lower="46.45152", upper="46.45152").kind == (
        "point"
    )


@pytest.mark.parametrize(
    "lower,upper,kind",
    [(None, "500", "maximum"), ("500", None, "minimum"), ("500", "600", "range")],
)
def test_bound_or_range_never_becomes_a_scalar_total(lower, upper, kind):
    area = reading(lower=lower, upper=upper)
    assert area.kind == kind and display_area_square_metres((area,)) is None


def test_disagreeing_units_or_a_mixed_bound_are_not_arbitrarily_selected():
    point = reading()
    compatible = reading(unit="sq_m", source_unit="sqm", lower="46.45", upper="46.45")
    disagreement = reading(unit="sq_m", source_unit="sqm", lower="55", upper="55")
    assert display_area_square_metres((point, compatible)) == 46.5
    assert display_area_square_metres((point, disagreement)) is None
    assert display_area_square_metres((point, reading(upper=None))) is None
    assert display_area_square_metres((point, reading(lower="501", upper="501"))) is None


@pytest.mark.parametrize(
    ("square_feet", "square_metres", "expected"),
    [
        ("650", "60", 60.4),
        ("650", "60.4", 60.4),
        ("650", "60.0", None),
        ("650", "61", None),
        ("750", "70", 69.7),
        ("650.125", "60.3986", 60.4),
        ("650.125", "60.40", 60.4),
        ("650.125", "60.4000", None),
    ],
)
def test_dual_units_allow_reported_rounding_but_not_real_disagreement(
    square_feet, square_metres, expected
):
    feet = reading(lower=square_feet, upper=square_feet)
    metres = reading(unit="sq_m", source_unit="sqm", lower=square_metres, upper=square_metres)
    assert display_area_square_metres((feet, metres)) == expected
    assert display_area_square_metres((metres, feet)) == expected
    assert feet.lower == Decimal(square_feet) and metres.lower == Decimal(square_metres)


@pytest.mark.parametrize(
    "bad",
    [
        {"unit": []},
        {"unit": "acres"},
        {"unit": "sqft", "minimumSize": True},
        {"unit": "sqft", "minimumSize": 0},
        {"unit": "sqft"},
    ],
)
def test_invalid_optional_sizing_marks_the_whole_set_unavailable(bad):
    valid = {"unit": "sqft", "minimumSize": 500, "maximumSize": 500}
    assert rightmove_areas([valid, bad]) == (None, "unsupported_quantity")
    assert rightmove_areas([valid] * 11) == (None, "unsupported_quantity")


async def test_rightmove_fractional_json_preserves_range(make_property, monkeypatch):
    prop = make_property(
        source=PropertySource.RIGHTMOVE,
        source_id="100001",
        url="https://www.rightmove.co.uk/properties/100001",
    )
    html = rightmove_html('[{"unit":"sqft","minimumSize":650.1250,"maximumSize":700.8750}]')
    response = httpx.Response(200, text=html, request=httpx.Request("GET", str(prop.url)))
    async with DetailFetcher() as fetcher:
        monkeypatch.setattr(fetcher, "_httpx_get_with_retry", AsyncMock(return_value=response))
        detail = await fetcher.fetch_detail_page(prop)
    assert detail is not None and detail.floor_area_sqm is None
    area = detail.floor_area_readings[0]
    assert str(area.lower) == "650.1250" and str(area.upper) == "700.8750"
    assert area.lower_square_metres == Decimal("60.398588880000")


def test_zoopla_fraction_is_preserved_and_conflicting_identity_refused():
    areas, omission = _zoopla_area_from_rsc(zoopla_html("650.1250"), expected_id="100001")
    assert omission is None and areas is not None
    assert str(areas[0].lower) == "650.1250"
    assert display_area_square_metres(areas) == 60.4
    html = zoopla_html("650").replace("sizeSqft", 'listingId\\":\\"100002\\",\\"sizeSqft')
    with pytest.raises(DetailIdentityError):
        _zoopla_area_from_rsc(html, expected_id="100001")


def test_minimum_only_quantity_never_becomes_an_exact_total():
    areas, omission = single_area(
        Decimal("650.1250"), source_field="minimumAreaSqFt", minimum_only=True
    )
    assert omission is None and areas is not None
    assert areas[0].kind == "minimum" and str(areas[0].lower) == "650.1250"
    assert display_area_square_metres(areas) is None
