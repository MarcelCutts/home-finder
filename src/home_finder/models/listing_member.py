"""One retained listing representation, independent of its display-home identity."""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from home_finder.models.identity import SourceIdentityInput, resolve_source_identity
from home_finder.models.listing_text import ListingTextEvidence, merge_listing_text
from home_finder.models.sources import PropertySource

if TYPE_CHECKING:
    from home_finder.models.core import Property


class ListingMember(BaseModel):
    """Keep repeated portals separate; URL-only legacy evidence has no invented ID."""

    model_config = ConfigDict(frozen=True)

    source: PropertySource
    source_unique_id: str | None = None
    source_id: str | None = None
    url: HttpUrl
    price_pcm: int | None = Field(default=None, ge=0)
    text: ListingTextEvidence = Field(default_factory=ListingTextEvidence)
    is_backfilled: bool = False
    is_off_market: bool | None = None

    @classmethod
    def from_property(cls, prop: Property) -> ListingMember:
        return cls(
            source=prop.source,
            source_unique_id=prop.unique_id,
            source_id=prop.source_id,
            url=prop.url,
            price_pcm=prop.price_pcm,
            text=merge_listing_text(
                ListingTextEvidence(description=prop.description),
                ListingTextEvidence.from_observation(prop.source_observation)
                if prop.source_observation is not None
                else ListingTextEvidence(),
            ),
        )

    @model_validator(mode="after")
    def exact_representation(self) -> ListingMember:
        if (self.source_id is None) != (self.source_unique_id is None):
            raise ValueError("A retained listing needs both its declared and stored identity")
        if self.source_id == "" or self.source_unique_id == "":
            raise ValueError("Listing identity cannot be empty")
        # Historical mismatches remain evidence for the provider-identity audit.
        # A retained row ID is not itself a verified provider-ID claim.
        return self

    @property
    def evidence_key(self) -> str:
        """A stable evidence key, explicitly distinct from a provider-ID claim."""
        if self.source_unique_id is not None:
            return self.source_unique_id
        digest = hashlib.sha256(str(self.url).encode()).hexdigest()
        return f"legacy:{self.source.value}:{digest}"

    def has_equivalent_url(self, url: str) -> bool:
        """Accept exact retained URLs or verified provider-path aliases, never guesses."""
        if str(self.url) == url:
            return True
        if self.source_unique_id is None or self.source_id is None:
            return False
        keys = [
            resolve_source_identity(
                SourceIdentityInput(
                    unique_id=self.source_unique_id,
                    source=self.source.value,
                    source_id=self.source_id,
                    url=value,
                    is_backfilled=self.is_backfilled,
                )
            ).listing_key
            for value in (str(self.url), url)
        ]
        return keys[0] is not None and keys[0] == keys[1]


def labelled_members(
    members: tuple[ListingMember, ...],
) -> tuple[tuple[ListingMember, str], ...]:
    """Number distinct advertisements on a portal without exposing long legacy IDs."""
    totals = Counter(member.source for member in members)
    positions: Counter[PropertySource] = Counter()
    result = []
    for member in sorted(members, key=lambda m: (m.source.value, m.evidence_key)):
        positions[member.source] += 1
        label = member.source.display_name
        if totals[member.source] > 1:
            label += f" {positions[member.source]}"
        result.append((member, label))
    return tuple(result)
