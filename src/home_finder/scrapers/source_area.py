"""Map supported structured portal quantities without guessing an exact total."""

from typing import Any, Literal

from pydantic import ValidationError

from home_finder.models.source_area import SourceAreaReading

AreaResult = tuple[tuple[SourceAreaReading, ...] | None, Literal["unsupported_quantity"] | None]


def rightmove_areas(sizings: Any) -> AreaResult:
    if sizings is None or sizings == []:
        return None, None
    if not isinstance(sizings, list) or len(sizings) > 10:
        return None, "unsupported_quantity"
    readings = []
    for index, sizing in enumerate(sizings):
        if (
            not isinstance(sizing, dict)
            or not isinstance(sizing.get("unit"), str)
            or sizing["unit"] not in {"sqft", "sqm"}
        ):
            return None, "unsupported_quantity"
        try:
            readings.append(
                SourceAreaReading.model_validate(
                    {
                        "source_field": f"sizings[{index}]",
                        "source_unit": sizing["unit"],
                        "unit": {"sqft": "sq_ft", "sqm": "sq_m"}[sizing["unit"]],
                        "lower": sizing.get("minimumSize"),
                        "upper": sizing.get("maximumSize"),
                    }
                )
            )
        except ValidationError:
            return None, "unsupported_quantity"
    return tuple(readings), None


def single_area(raw: Any, *, source_field: str, minimum_only: bool = False) -> AreaResult:
    if raw is None:
        return None, None
    try:
        reading = SourceAreaReading(
            source_field=source_field,
            source_unit="sqft",
            unit="sq_ft",
            lower=raw,
            upper=None if minimum_only else raw,
        )
    except ValidationError:
        return None, "unsupported_quantity"
    return (reading,), None
