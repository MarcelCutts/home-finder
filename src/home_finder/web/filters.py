"""PropertyFilter model and FastAPI dependency for dashboard filter parsing."""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import Depends, Query
from pydantic import BaseModel, field_validator

from home_finder.models import PropertyHighlight, PropertyLowlight

# ---------------------------------------------------------------------------
# Valid option sets (moved from routes.py)
# ---------------------------------------------------------------------------

VALID_SORT_OPTIONS: Final = {"newest", "price_asc", "price_desc", "rating_desc", "fit_desc"}
VALID_PROPERTY_TYPES: Final = {
    "victorian",
    "edwardian",
    "georgian",
    "new_build",
    "purpose_built",
    "warehouse",
    "ex_council",
    "period_conversion",
}
VALID_NATURAL_LIGHT: Final = {"excellent", "good", "fair", "poor"}
VALID_HOB_TYPES: Final = {"gas", "induction", "electric"}
VALID_VALUE_RATINGS: Final = {"excellent", "good", "fair", "poor"}
VALID_FLOOR_LEVELS: Final = {"basement", "ground", "lower", "upper", "top"}
VALID_BUILDING_CONSTRUCTION: Final = {"solid_brick", "concrete", "timber_frame", "mixed"}
VALID_OFFICE_SEPARATION: Final = {"dedicated_room", "separate_area", "shared_space", "none"}
VALID_HOSTING_LAYOUT: Final = {"excellent", "good", "awkward", "poor"}
VALID_HOSTING_NOISE_RISK: Final = {"low", "moderate", "high"}
VALID_BROADBAND_TYPE: Final = {"fttp", "fttc", "cable", "standard"}

VALID_TAGS: Final = {v.value for v in PropertyHighlight} | {v.value for v in PropertyLowlight}


def _parse_optional_int(value: str | None) -> int | None:
    """Parse a string to int, returning None for empty/whitespace/non-numeric values."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _validate_enum_field(value: object, valid_set: set[str]) -> str | None:
    """Strip, lowercase, and validate against a set. Returns None if invalid."""
    if not value:
        return None
    cleaned = str(value).strip().lower()
    return cleaned if cleaned in valid_set else None


# ---------------------------------------------------------------------------
# PropertyFilter model
# ---------------------------------------------------------------------------


class PropertyFilter(BaseModel):
    """Validated dashboard filter parameters.

    All fields default to None/[] (no filter). Validators coerce strings
    to the correct type and silently discard invalid values.
    """

    min_price: int | None = None
    max_price: int | None = None
    bedrooms: int | None = None
    min_rating: int | None = None
    area: str | None = None
    property_type: str | None = None
    outdoor_space: str | None = None
    natural_light: str | None = None
    pets: str | None = None
    value_rating: str | None = None
    hob_type: str | None = None
    floor_level: str | None = None
    building_construction: str | None = None
    office_separation: str | None = None
    hosting_layout: str | None = None
    hosting_noise_risk: str | None = None
    broadband_type: str | None = None
    tags: list[str] = []

    # --- validators ---

    @field_validator("min_price", "max_price", mode="before")
    @classmethod
    def coerce_price(cls, v: object) -> int | None:
        if v is None:
            return None
        if isinstance(v, int):
            return v
        return _parse_optional_int(str(v))

    @field_validator("bedrooms", mode="before")
    @classmethod
    def coerce_bedrooms(cls, v: object) -> int | None:
        if v is None:
            return None
        parsed = v if isinstance(v, int) else _parse_optional_int(str(v))
        if parsed is None:
            return None
        return max(0, min(10, parsed))

    @field_validator("min_rating", mode="before")
    @classmethod
    def coerce_min_rating(cls, v: object) -> int | None:
        if v is None:
            return None
        parsed = v if isinstance(v, int) else _parse_optional_int(str(v))
        if parsed is None:
            return None
        return max(1, min(5, parsed))

    @field_validator("area", mode="before")
    @classmethod
    def clean_area(cls, v: object) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None

    @field_validator("property_type", mode="before")
    @classmethod
    def validate_property_type(cls, v: object) -> str | None:
        return _validate_enum_field(v, VALID_PROPERTY_TYPES)

    @field_validator("outdoor_space", mode="before")
    @classmethod
    def validate_outdoor_space(cls, v: object) -> str | None:
        if not v:
            return None
        cleaned = str(v).strip().lower()
        return cleaned if cleaned in ("yes", "no") else None

    @field_validator("natural_light", mode="before")
    @classmethod
    def validate_natural_light(cls, v: object) -> str | None:
        return _validate_enum_field(v, VALID_NATURAL_LIGHT)

    @field_validator("pets", mode="before")
    @classmethod
    def validate_pets(cls, v: object) -> str | None:
        if not v:
            return None
        cleaned = str(v).strip().lower()
        return "yes" if cleaned == "yes" else None

    @field_validator("value_rating", mode="before")
    @classmethod
    def validate_value_rating(cls, v: object) -> str | None:
        return _validate_enum_field(v, VALID_VALUE_RATINGS)

    @field_validator("hob_type", mode="before")
    @classmethod
    def validate_hob_type(cls, v: object) -> str | None:
        return _validate_enum_field(v, VALID_HOB_TYPES)

    @field_validator("floor_level", mode="before")
    @classmethod
    def validate_floor_level(cls, v: object) -> str | None:
        return _validate_enum_field(v, VALID_FLOOR_LEVELS)

    @field_validator("building_construction", mode="before")
    @classmethod
    def validate_building_construction(cls, v: object) -> str | None:
        return _validate_enum_field(v, VALID_BUILDING_CONSTRUCTION)

    @field_validator("office_separation", mode="before")
    @classmethod
    def validate_office_separation(cls, v: object) -> str | None:
        return _validate_enum_field(v, VALID_OFFICE_SEPARATION)

    @field_validator("hosting_layout", mode="before")
    @classmethod
    def validate_hosting_layout(cls, v: object) -> str | None:
        return _validate_enum_field(v, VALID_HOSTING_LAYOUT)

    @field_validator("hosting_noise_risk", mode="before")
    @classmethod
    def validate_hosting_noise_risk(cls, v: object) -> str | None:
        return _validate_enum_field(v, VALID_HOSTING_NOISE_RISK)

    @field_validator("broadband_type", mode="before")
    @classmethod
    def validate_broadband_type(cls, v: object) -> str | None:
        return _validate_enum_field(v, VALID_BROADBAND_TYPE)

    @field_validator("tags", mode="before")
    @classmethod
    def filter_tags(cls, v: object) -> list[str]:
        if not v:
            return []
        if isinstance(v, list):
            return [t for t in v if isinstance(t, str) and t in VALID_TAGS]
        return []

    # --- convenience methods ---

    def active_filter_chips(self) -> list[dict[str, str]]:
        """Build filter chip descriptors for template rendering."""
        chips: list[dict[str, str]] = []
        if self.bedrooms is not None:
            label = "Studio" if self.bedrooms == 0 else f"{self.bedrooms} bed"
            chips.append({"key": "bedrooms", "label": label})
        if self.min_price is not None:
            chips.append({"key": "min_price", "label": f"Min \u00a3{self.min_price:,}"})
        if self.max_price is not None:
            chips.append({"key": "max_price", "label": f"Max \u00a3{self.max_price:,}"})
        if self.min_rating is not None:
            chips.append({"key": "min_rating", "label": f"{self.min_rating}+ stars"})
        if self.area:
            chips.append({"key": "area", "label": self.area})
        if self.property_type:
            chips.append(
                {
                    "key": "property_type",
                    "label": self.property_type.replace("_", " ").title(),
                }
            )
        if self.outdoor_space:
            chips.append({"key": "outdoor_space", "label": f"Outdoor: {self.outdoor_space}"})
        if self.natural_light:
            chips.append(
                {
                    "key": "natural_light",
                    "label": f"{self.natural_light.title()} light",
                }
            )
        if self.pets:
            chips.append({"key": "pets", "label": "Pets allowed"})
        if self.value_rating:
            chips.append(
                {
                    "key": "value_rating",
                    "label": f"{self.value_rating.title()} value",
                }
            )
        if self.hob_type:
            chips.append({"key": "hob_type", "label": f"{self.hob_type.title()} hob"})
        if self.floor_level:
            chips.append(
                {
                    "key": "floor_level",
                    "label": f"{self.floor_level.title()} floor",
                }
            )
        if self.building_construction:
            chips.append(
                {
                    "key": "building_construction",
                    "label": self.building_construction.replace("_", " ").title(),
                }
            )
        if self.office_separation:
            chips.append(
                {
                    "key": "office_separation",
                    "label": self.office_separation.replace("_", " ").title(),
                }
            )
        if self.hosting_layout:
            chips.append(
                {
                    "key": "hosting_layout",
                    "label": f"{self.hosting_layout.title()} hosting",
                }
            )
        if self.hosting_noise_risk:
            chips.append(
                {
                    "key": "hosting_noise_risk",
                    "label": f"{self.hosting_noise_risk.title()} noise risk",
                }
            )
        if self.broadband_type:
            chips.append(
                {
                    "key": "broadband_type",
                    "label": f"{self.broadband_type.upper()} broadband",
                }
            )
        for t in self.tags:
            chips.append({"key": "tag", "label": t, "value": t})
        return chips

    @property
    def quality_fields_active(self) -> bool:
        """Whether any quality-analysis filter is set."""
        return any(
            [
                self.property_type,
                self.outdoor_space,
                self.natural_light,
                self.pets,
                self.value_rating,
                self.hob_type,
                self.floor_level,
                self.building_construction,
                self.office_separation,
                self.hosting_layout,
                self.hosting_noise_risk,
                self.broadband_type,
                self.tags,
            ]
        )

    @property
    def secondary_filter_count(self) -> int:
        """Count of active secondary (modal) filters for badge display."""
        return sum(
            1
            for v in [
                self.property_type,
                self.outdoor_space,
                self.natural_light,
                self.pets,
                self.value_rating,
                self.hob_type,
                self.floor_level,
                self.building_construction,
                self.office_separation,
                self.hosting_layout,
                self.hosting_noise_risk,
                self.broadband_type,
            ]
            if v is not None
        ) + len(self.tags)


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


def parse_filters(
    min_price: str | None = None,
    max_price: str | None = None,
    bedrooms: str | None = None,
    min_rating: str | None = None,
    area: str | None = None,
    property_type: str | None = None,
    outdoor_space: str | None = None,
    natural_light: str | None = None,
    pets: str | None = None,
    value_rating: str | None = None,
    hob_type: str | None = None,
    floor_level: str | None = None,
    building_construction: str | None = None,
    office_separation: str | None = None,
    hosting_layout: str | None = None,
    hosting_noise_risk: str | None = None,
    broadband_type: str | None = None,
    tag: list[str] = Query(default=[]),
) -> PropertyFilter:
    """FastAPI dependency that parses query params into a PropertyFilter."""
    return PropertyFilter.model_validate(
        {
            "min_price": min_price,
            "max_price": max_price,
            "bedrooms": bedrooms,
            "min_rating": min_rating,
            "area": area,
            "property_type": property_type,
            "outdoor_space": outdoor_space,
            "natural_light": natural_light,
            "pets": pets,
            "value_rating": value_rating,
            "hob_type": hob_type,
            "floor_level": floor_level,
            "building_construction": building_construction,
            "office_separation": office_separation,
            "hosting_layout": hosting_layout,
            "hosting_noise_risk": hosting_noise_risk,
            "broadband_type": broadband_type,
            "tags": tag,
        }
    )


FilterDep = Annotated[PropertyFilter, Depends(parse_filters)]
