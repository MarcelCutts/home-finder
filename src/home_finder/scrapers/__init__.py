"""Property scrapers for various UK rental platforms."""

from home_finder.scrapers.base import BaseScraper
from home_finder.scrapers.onthemarket import OnTheMarketScraper
from home_finder.scrapers.openrent import OpenRentScraper
from home_finder.scrapers.rightmove import RightmoveScraper
from home_finder.scrapers.zoopla import ZooplaScraper

__all__ = [
    "BaseScraper",
    "OnTheMarketScraper",
    "OpenRentScraper",
    "RightmoveScraper",
    "ZooplaScraper",
]
