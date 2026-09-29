"""Exact provider identity, separate from an advertisement's home membership."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, ConfigDict

from home_finder.models.sources import PropertySource
from home_finder.utils.listing_identity import listing_id

IDENTITY_POLICY_VERSION = "provider-path-v1"
IdentityReason = Literal[
    "provider_id_and_url_agree",
    "legacy_url_and_url_agree",
    "unsupported_source",
    "unrecognized_listing_url",
    "provider_id_mismatch",
    "unresolved_declared_id",
    "inconsistent_unique_id",
]


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


class SourceIdentityInput(BaseModel):
    """Retain the representation, including genuine historical URL-shaped IDs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unique_id: str
    source: str
    source_id: str
    url: str
    is_backfilled: bool = False
    merged_id: str | None = None

    @property
    def claim_id(self) -> str:
        # Ownership changes are separate from provider identity. A merge/split
        # must not create a new provider entity or replay its identity claim.
        return _digest([IDENTITY_POLICY_VERSION, self.model_dump(exclude={"merged_id"})])


class SourceIdentityResolution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    representation: SourceIdentityInput
    provider_id: str | None
    listing_key: str | None
    reason: IdentityReason


def resolve_source_identity(row: SourceIdentityInput) -> SourceIdentityResolution:
    """Resolve only exact ID/path agreement; never use address, price or photos."""
    provider_id = None
    reason: IdentityReason
    if row.source not in {source.value for source in PropertySource}:
        reason = "unsupported_source"
    elif row.unique_id != f"{row.source}:{row.source_id}":
        reason = "inconsistent_unique_id"
    elif (url_id := listing_id(row.source, row.url)) is None:
        reason = "unrecognized_listing_url"
    elif re.fullmatch(r"[0-9]+", row.source_id):
        if row.source_id == url_id:
            provider_id, reason = url_id, "provider_id_and_url_agree"
        else:
            reason = "provider_id_mismatch"
    elif (legacy_id := listing_id(row.source, row.source_id)) is not None:
        if legacy_id == url_id:
            provider_id, reason = url_id, "legacy_url_and_url_agree"
        else:
            reason = "provider_id_mismatch"
    else:
        # Backfill provenance is not permission to turn an arbitrary ID into
        # the number from a URL. Historical aliases can point to a wrong home.
        reason = "unresolved_declared_id"
    key = _digest(["provider-listing-v1", row.source, provider_id]) if provider_id else None
    return SourceIdentityResolution(
        representation=row, provider_id=provider_id, listing_key=key, reason=reason
    )


class ProviderIdentityGroup(BaseModel):
    model_config = ConfigDict(frozen=True)

    listing_key: str
    source: str
    provider_id: str
    source_unique_ids: tuple[str, ...]
    current_home_ids: tuple[str, ...]
    outcome: Literal["unassigned", "one_home", "owner_conflict"]


class IdentityAudit(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_version: str = IDENTITY_POLICY_VERSION
    snapshot_sha256: str
    source_rows: int
    resolved_rows: int
    unresolved_rows: int
    provider_entities: int
    duplicate_representation_groups: int
    owner_conflict_groups: int
    reason_counts: dict[str, int]
    rows: tuple[SourceIdentityResolution, ...]
    groups: tuple[ProviderIdentityGroup, ...]


def audit_source_identities(rows: list[SourceIdentityInput]) -> IdentityAudit:
    """Return a deterministic dry-run map; shared provider IDs never merge homes."""
    ordered = sorted(rows, key=lambda row: row.unique_id)
    if len({row.unique_id for row in ordered}) != len(ordered):
        raise ValueError("Identity audit requires distinct source row IDs")
    resolutions = tuple(resolve_source_identity(row) for row in ordered)
    grouped: dict[str, list[SourceIdentityResolution]] = defaultdict(list)
    reasons: dict[str, int] = defaultdict(int)
    for result in resolutions:
        reasons[result.reason] += 1
        if result.listing_key is not None:
            grouped[result.listing_key].append(result)
    groups = []
    for key, members in sorted(grouped.items()):
        first = members[0]
        assert first.provider_id is not None
        homes = tuple(
            sorted({m.representation.merged_id for m in members if m.representation.merged_id})
        )
        groups.append(
            ProviderIdentityGroup(
                listing_key=key,
                source=first.representation.source,
                provider_id=first.provider_id,
                source_unique_ids=tuple(m.representation.unique_id for m in members),
                current_home_ids=homes,
                outcome="owner_conflict"
                if len(homes) > 1
                else "one_home"
                if homes
                else "unassigned",
            )
        )
    resolved = sum(r.listing_key is not None for r in resolutions)
    return IdentityAudit(
        snapshot_sha256=_digest([row.model_dump() for row in ordered]),
        source_rows=len(ordered),
        resolved_rows=resolved,
        unresolved_rows=len(ordered) - resolved,
        provider_entities=len(groups),
        duplicate_representation_groups=sum(len(g.source_unique_ids) > 1 for g in groups),
        owner_conflict_groups=sum(g.outcome == "owner_conflict" for g in groups),
        reason_counts=dict(sorted(reasons.items())),
        rows=resolutions,
        groups=tuple(groups),
    )
