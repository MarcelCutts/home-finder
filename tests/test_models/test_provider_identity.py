"""Provider IDs are exact advertisement identities, never inferred home matches."""

import pytest

from home_finder.models.identity import (
    SourceIdentityInput,
    audit_source_identities,
    resolve_source_identity,
)


def representation(source_id="123", *, source="rightmove", url=None, **kwargs):
    return SourceIdentityInput(
        unique_id=f"{source}:{source_id}",
        source=source,
        source_id=source_id,
        url=url or "https://www.rightmove.co.uk/properties/123",
        **kwargs,
    )


@pytest.mark.parametrize(
    ("source", "url"),
    [
        ("rightmove", "https://www.rightmove.co.uk/properties/123/"),
        ("rightmove", "http://rightmove.co.uk:80/properties/123?channel=RES_LET#view"),
        ("zoopla", "https://www.zoopla.co.uk/to-rent/details/123/"),
        ("zoopla", "https://zoopla.co.uk/details/123?search=abc"),
        ("onthemarket", "https://www.onthemarket.com/details/123/"),
        ("openrent", "https://www.openrent.com/property-to-rent/london/2-bed-flat/123"),
        ("openrent", "https://www.openrent.co.uk/property/123"),
        ("openrent", "https://openrent.com/123"),
    ],
)
def test_numeric_and_legacy_url_ids_resolve_to_same_provider_key(source, url):
    numeric = representation(source=source, url=url)
    legacy = representation(url, source=source, url=url, is_backfilled=True)
    resolved = resolve_source_identity(numeric)
    alias = resolve_source_identity(legacy)
    assert resolved.provider_id == alias.provider_id == "123"
    assert resolved.listing_key == alias.listing_key
    assert resolved.reason == "provider_id_and_url_agree"
    assert alias.reason == "legacy_url_and_url_agree"
    assert resolved.representation == numeric
    assert alias.representation == legacy
    assert numeric.claim_id != legacy.claim_id


@pytest.mark.parametrize(
    "url",
    [
        "https://rightmove.co.uk.evil.test/properties/123",
        "https://user:secret@www.rightmove.co.uk/properties/123",
        "https://rightmove.co.uk:8443/properties/123",
        "https://rightmove.co.uk:invalid/properties/123",
        "https://[invalid/properties/123",
        "https://rightmove.co.uk/property-to-rent/find.html?id=123",
        "https://rightmove.co.uk/properties/%31%32%33",
        "https://rightmove.co.uk/properties/123/photos",
        "https://rightmove.co.uk/properties/１２３",  # noqa: RUF001 — non-ASCII digits
        "https://www.zoopla.co.uk/to-rent/details/123",
        "//rightmove.co.uk/properties/123",
        "ftp://rightmove.co.uk/properties/123",
        " https://rightmove.co.uk/properties/123",
        "https://rightmove.co.uk/properties/1\n23",
        "https://rightmove.co.uk/properties/123\x00",
    ],
)
def test_unrecognized_urls_remain_unresolved(url):
    result = resolve_source_identity(representation(url=url))
    assert result.reason == "unrecognized_listing_url"
    assert result.provider_id is result.listing_key is None


@pytest.mark.parametrize("backfilled", [False, True])
@pytest.mark.parametrize(
    ("source_id", "reason"),
    [
        ("456", "provider_id_mismatch"),
        ("https://www.rightmove.co.uk/properties/456", "provider_id_mismatch"),
        ("opaque-backfill-hash", "unresolved_declared_id"),
        ("１２３", "unresolved_declared_id"),  # noqa: RUF001 — non-ASCII digits
        ("123 ", "unresolved_declared_id"),
    ],
)
def test_backfill_flag_never_authorizes_guessing_an_id(source_id, reason, backfilled):
    result = resolve_source_identity(representation(source_id, is_backfilled=backfilled))
    assert result.reason == reason
    assert result.provider_id is result.listing_key is None


def test_provider_namespace_and_literal_id_are_part_of_identity():
    rows = [
        representation(),
        representation("0123", url="https://rightmove.co.uk/properties/0123"),
        representation(source="onthemarket", url="https://onthemarket.com/details/123/"),
        representation("456", url="https://rightmove.co.uk/properties/456", merged_id="same-home"),
    ]
    assert len({resolve_source_identity(row).listing_key for row in rows}) == 4
    assert resolve_source_identity(rows[1]).provider_id == "0123"


def test_invalid_source_or_raw_unique_id_is_explicitly_unresolved():
    result = resolve_source_identity(representation(source="unknown"))
    assert result.reason == "unsupported_source"
    result = resolve_source_identity(representation().model_copy(update={"unique_id": "wrong"}))
    assert result.reason == "inconsistent_unique_id"


def test_claim_ignores_home_membership_but_audit_detects_ownership_conflicts():
    real = representation(merged_id="saved-home-a")
    moved = real.model_copy(update={"merged_id": "saved-home-b"})
    assert real.claim_id == moved.claim_id
    alias = representation(
        "https://rightmove.co.uk/properties/123?old=1",
        is_backfilled=True,
        merged_id="saved-home-b",
    )
    missing = representation("opaque")
    audit = audit_source_identities([real, alias, missing])
    assert audit == audit_source_identities([missing, alias, real])
    assert (audit.source_rows, audit.resolved_rows, audit.provider_entities) == (3, 2, 1)
    assert audit.unresolved_rows == 1
    assert audit.duplicate_representation_groups == audit.owner_conflict_groups == 1
    assert audit.groups[0].outcome == "owner_conflict"
    assert audit.groups[0].current_home_ids == ("saved-home-a", "saved-home-b")
    assert len(audit.groups[0].source_unique_ids) == 2
    assert audit.snapshot_sha256 != audit_source_identities([moved, alias, missing]).snapshot_sha256


def test_unassigned_and_one_home_are_distinct_and_repeated_rows_are_rejected():
    assert audit_source_identities([representation()]).groups[0].outcome == "unassigned"
    assert (
        audit_source_identities([representation(merged_id="home")]).groups[0].outcome == "one_home"
    )
    with pytest.raises(ValueError, match="distinct source row IDs"):
        audit_source_identities([representation(), representation()])
