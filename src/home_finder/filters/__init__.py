"""Filters for property search criteria and commute times."""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from home_finder.filters.commute import CommuteFilter, CommuteResult  # noqa: F401
    from home_finder.filters.criteria import CriteriaFilter  # noqa: F401
    from home_finder.filters.deduplication import Deduplicator  # noqa: F401
    from home_finder.filters.detail_enrichment import (  # noqa: F401
        enrich_merged_properties,
        filter_by_floorplan,
    )
    from home_finder.filters.floorplan import FloorplanAnalysis, FloorplanFilter  # noqa: F401
    from home_finder.filters.location import LocationFilter  # noqa: F401
    from home_finder.filters.quality import (  # noqa: F401
        PropertyQualityAnalysis,
        PropertyQualityFilter,
        ValueAnalysis,
    )

__all__ = [
    "CommuteFilter",
    "CommuteResult",
    "CriteriaFilter",
    "Deduplicator",
    "enrich_merged_properties",
    "filter_by_floorplan",
    "FloorplanAnalysis",
    "FloorplanFilter",
    "LocationFilter",
    "PropertyQualityAnalysis",
    "PropertyQualityFilter",
    "ValueAnalysis",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "CommuteFilter": (".commute", "CommuteFilter"),
    "CommuteResult": (".commute", "CommuteResult"),
    "CriteriaFilter": (".criteria", "CriteriaFilter"),
    "Deduplicator": (".deduplication", "Deduplicator"),
    "enrich_merged_properties": (".detail_enrichment", "enrich_merged_properties"),
    "filter_by_floorplan": (".detail_enrichment", "filter_by_floorplan"),
    "FloorplanAnalysis": (".floorplan", "FloorplanAnalysis"),
    "FloorplanFilter": (".floorplan", "FloorplanFilter"),
    "LocationFilter": (".location", "LocationFilter"),
    "PropertyQualityAnalysis": (".quality", "PropertyQualityAnalysis"),
    "PropertyQualityFilter": (".quality", "PropertyQualityFilter"),
    "ValueAnalysis": (".quality", "ValueAnalysis"),
}


def __getattr__(name: str) -> type:
    if name in _LAZY_IMPORTS:
        module_path, attr = _LAZY_IMPORTS[name]
        mod = importlib.import_module(module_path, __name__)
        val = getattr(mod, attr)
        globals()[name] = val  # Cache so __getattr__ is only called once
        return val  # type: ignore[no-any-return]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return __all__
