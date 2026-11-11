"""Merging listings keeps the floor area of the highest-ranked portal that reports one.

Floor-area rank: Zoopla, then OnTheMarket, then Rightmove.
"""

import pytest

from home_finder.matching.deduplication import Deduplicator
from home_finder.models import PropertySource


@pytest.mark.parametrize(
    ("first", "second", "kept"),
    [
        ((PropertySource.RIGHTMOVE, 63.2), (PropertySource.ZOOPLA, 65.0), (65.0, "zoopla")),
        (
            (PropertySource.RIGHTMOVE, 63.2),
            (PropertySource.ONTHEMARKET, 64.1),
            (64.1, "onthemarket"),
        ),
        ((PropertySource.OPENRENT, None), (PropertySource.ZOOPLA, 51.1), (51.1, "zoopla")),
        ((PropertySource.OPENRENT, None), (PropertySource.RIGHTMOVE, None), (None, None)),
    ],
    ids=["zoopla-over-rightmove", "onthemarket-over-rightmove", "only-one-reported", "neither"],
)
def test_merged_floor_area_comes_from_the_preferred_reporting_portal(
    make_merged_property, first, second, kept
):
    homes = [
        make_merged_property(
            sources=(source,),
            floor_area_sqm=area,
            floor_area_source=source.value if area is not None else None,
        )
        for source, area in (first, second)
    ]

    merged = Deduplicator(enable_cross_platform=True)._merge_merged_properties(homes)

    assert (merged.floor_area_sqm, merged.floor_area_source) == kept
