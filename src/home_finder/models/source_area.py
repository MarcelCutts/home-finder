"""Source quantities keep units, bounds and unspecified measurement definitions."""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SQUARE_METRES_PER_SQUARE_FOOT = Decimal("0.09290304")


class SourceAreaReading(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_field: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.\[\]]+$")
    unit: Literal["sq_ft", "sq_m"]
    source_unit: Literal["sqft", "sqm"]
    basis: Literal["unspecified"] = "unspecified"
    lower: Decimal | None = Field(
        default=None, gt=0, le=100_000, max_digits=14, decimal_places=8, allow_inf_nan=False
    )
    upper: Decimal | None = Field(
        default=None, gt=0, le=100_000, max_digits=14, decimal_places=8, allow_inf_nan=False
    )

    @field_validator("lower", "upper", mode="before")
    @classmethod
    def decimal_input(cls, value: Any) -> Any:
        if isinstance(value, bool | float):
            raise ValueError("Source quantities require decimal JSON parsing or exact strings")
        return value

    @model_validator(mode="after")
    def coherent_bounds(self) -> "SourceAreaReading":
        if self.lower is None and self.upper is None:
            raise ValueError("An area reading requires at least one bound")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("Area lower bound exceeds upper bound")
        if self.unit != {"sqft": "sq_ft", "sqm": "sq_m"}[self.source_unit]:
            raise ValueError("Normalized area unit differs from the source unit")
        return self

    @property
    def kind(self) -> Literal["point", "range", "minimum", "maximum"]:
        if self.lower is None:
            return "maximum"
        if self.upper is None:
            return "minimum"
        return "point" if self.lower == self.upper else "range"

    @property
    def conversion_factor(self) -> Decimal:
        return SQUARE_METRES_PER_SQUARE_FOOT if self.unit == "sq_ft" else Decimal(1)

    @property
    def lower_square_metres(self) -> Decimal | None:
        return self.lower * self.conversion_factor if self.lower is not None else None

    @property
    def upper_square_metres(self) -> Decimal | None:
        return self.upper * self.conversion_factor if self.upper is not None else None


def display_area_square_metres(readings: tuple[SourceAreaReading, ...] | None) -> float | None:
    """A scalar requires compatible point readings, never a range endpoint.

    Keep the existing 100-5,000 sq ft plausibility gate for the legacy display/
    prompt value. Evidence retains valid out-of-display-range source quantities.
    """
    if not readings or any(item.kind != "point" for item in readings):
        return None
    values: set[Decimal] = set()
    for item in readings:
        area = item.lower_square_metres
        assert area is not None
        if not 100 * SQUARE_METRES_PER_SQUARE_FOOT <= area <= 5000 * SQUARE_METRES_PER_SQUARE_FOOT:
            return None
        values.add(area.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
    return float(values.pop()) if len(values) == 1 else None
