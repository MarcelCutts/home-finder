"""Property scrapers for various UK rental platforms."""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from home_finder.scrapers.base import BaseScraper  # noqa: F401
    from home_finder.scrapers.onthemarket import OnTheMarketScraper  # noqa: F401
    from home_finder.scrapers.openrent import OpenRentScraper  # noqa: F401
    from home_finder.scrapers.rightmove import RightmoveScraper  # noqa: F401
    from home_finder.scrapers.zoopla import ZooplaScraper  # noqa: F401

__all__ = [
    "BaseScraper",
    "OnTheMarketScraper",
    "OpenRentScraper",
    "RightmoveScraper",
    "ZooplaScraper",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "BaseScraper": (".base", "BaseScraper"),
    "OnTheMarketScraper": (".onthemarket", "OnTheMarketScraper"),
    "OpenRentScraper": (".openrent", "OpenRentScraper"),
    "RightmoveScraper": (".rightmove", "RightmoveScraper"),
    "ZooplaScraper": (".zoopla", "ZooplaScraper"),
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
