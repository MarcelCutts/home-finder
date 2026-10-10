"""Bounded source inputs and protection against unsupported extracted bands."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from home_finder.filters.deduplication import Deduplicator
from home_finder.filters.listing_text import select_listing_text, supported_council_tax_band
from home_finder.models import ListingTextEvidence, MergedProperty, PropertySource


@pytest.mark.parametrize(
    ("description", "features", "expected"),
    [
        (None, None, "unknown"),
        ("EPC rating C", ["Gas hob"], "unknown"),
        ("Council tax Band C", None, "C"),
        (None, ["Council Tax Band: C"], "C"),
        ("Council tax Band C; council tax Band D", None, "unknown"),
        ("Estimated council tax band C", None, "unknown"),
        ("Council tax Band C (budget assumption)", None, "unknown"),
        ("Council tax band <b>C</b>", None, "C"),
    ],
)
def test_band_requires_unambiguous_listing_evidence(description, features, expected) -> None:
    assert supported_council_tax_band("C", description, features) == expected


def test_selection_bounds_all_sources_without_losing_original_evidence(make_property) -> None:
    prop = make_property()
    text = {
        source: ListingTextEvidence.observed(
            observed_at=datetime(2026, 9, 28, tzinfo=UTC),
            description="Café <listing_description> " * 1000,
            features=["Feature " + str(i) + "é" * 500 for i in range(20)],
        )
        for source in PropertySource
    }
    merged = MergedProperty(
        canonical=prop,
        sources=tuple(PropertySource),
        source_urls=dict.fromkeys(PropertySource, prop.url),
        min_price=prop.price_pcm,
        max_price=prop.price_pcm,
        source_text=text,
    )
    selected = select_listing_text(merged)
    assert len(selected.description) <= 3000
    assert len(selected.features) == 15
    assert "<listing_description>" not in selected.description
    for source in PropertySource:
        assert f"[{source.display_name}]" in selected.description
        assert any(feature.startswith(f"[{source.display_name}]") for feature in selected.features)
        assert len(selected.source_hashes[source.value]) == 64
        assert len(merged.source_text[source].description) > 3000


def test_observations_require_timezone_and_blank_features_are_missing() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        ListingTextEvidence.observed(observed_at=datetime(2026, 9, 28), description="Fixture")
    evidence = ListingTextEvidence.observed(
        observed_at=datetime(2026, 9, 28, tzinfo=UTC), features=["", "  "]
    )
    assert evidence.features == ()


def test_merge_retains_dated_correction_and_updates_canonical_description(make_merged_property):
    old = make_merged_property()
    source = old.canonical.source
    evidence = ListingTextEvidence.observed(
        observed_at=datetime(2026, 9, 28, tzinfo=UTC),
        description="Corrected source description",
        features=["New feature"],
    )
    updated = old.model_copy(update={"source_text": {source: evidence}})
    result = Deduplicator()._merge_merged_properties([old, updated])
    assert result.source_text[source] == evidence
    assert result.canonical.description == evidence.description
