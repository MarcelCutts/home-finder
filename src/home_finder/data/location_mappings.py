"""Shared London borough and outcode mappings for all scrapers.

Consolidates geographic identifiers that were previously duplicated across
rightmove.py and zoopla.py into a single source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class AreaMapping:
    """Mapping for a London borough across scraper platforms."""

    name: str  # canonical hyphenated slug, e.g. "hackney"
    rightmove_region_id: str | None  # e.g. "REGION%5E93953"
    zoopla_path: str | None  # e.g. "hackney-london-borough"
    zoopla_q_param: str | None  # e.g. "Hackney (London Borough), London"
    aliases: tuple[str, ...] = ()  # variant lookup keys


@dataclass(frozen=True, slots=True)
class OutcodeMapping:
    """Mapping for a UK outcode to Rightmove identifier."""

    outcode: str  # e.g. "E8"
    rightmove_outcode_id: str  # e.g. "OUTCODE%5E762"


# ---------------------------------------------------------------------------
# Canonical area data — all 32+ London boroughs
# ---------------------------------------------------------------------------

AREA_MAPPINGS: Final[tuple[AreaMapping, ...]] = (
    # Central London
    AreaMapping(
        name="city-of-london",
        rightmove_region_id="REGION%5E61224",
        zoopla_path=None,
        zoopla_q_param=None,
    ),
    AreaMapping(
        name="westminster",
        rightmove_region_id="REGION%5E93980",
        zoopla_path="city-of-westminster-london-borough",
        zoopla_q_param="City of Westminster (London Borough), London",
    ),
    AreaMapping(
        name="camden",
        rightmove_region_id="REGION%5E93941",
        zoopla_path="camden-london-borough",
        zoopla_q_param="Camden (London Borough), London",
    ),
    AreaMapping(
        name="islington",
        rightmove_region_id="REGION%5E93965",
        zoopla_path="islington-london-borough",
        zoopla_q_param="Islington (London Borough), London",
    ),
    # East London
    AreaMapping(
        name="hackney",
        rightmove_region_id="REGION%5E93953",
        zoopla_path="hackney-london-borough",
        zoopla_q_param="Hackney (London Borough), London",
    ),
    AreaMapping(
        name="tower-hamlets",
        rightmove_region_id="REGION%5E61417",
        zoopla_path="tower-hamlets-london-borough",
        zoopla_q_param="Tower Hamlets (London Borough), London",
        aliases=("tower hamlets",),
    ),
    AreaMapping(
        name="newham",
        rightmove_region_id="REGION%5E61231",
        zoopla_path="newham-london-borough",
        zoopla_q_param="Newham (London Borough), London",
    ),
    AreaMapping(
        name="waltham-forest",
        rightmove_region_id="REGION%5E61232",
        zoopla_path="waltham-forest-london-borough",
        zoopla_q_param="Waltham Forest (London Borough), London",
        aliases=("waltham forest",),
    ),
    AreaMapping(
        name="barking-dagenham",
        rightmove_region_id="REGION%5E61400",
        zoopla_path=None,
        zoopla_q_param=None,
        aliases=("barking and dagenham",),
    ),
    AreaMapping(
        name="havering",
        rightmove_region_id="REGION%5E61228",
        zoopla_path=None,
        zoopla_q_param=None,
    ),
    AreaMapping(
        name="redbridge",
        rightmove_region_id="REGION%5E61537",
        zoopla_path=None,
        zoopla_q_param=None,
    ),
    # North London
    AreaMapping(
        name="haringey",
        rightmove_region_id="REGION%5E61227",
        zoopla_path="haringey-london-borough",
        zoopla_q_param="Haringey (London Borough), London",
    ),
    AreaMapping(
        name="enfield",
        rightmove_region_id="REGION%5E93950",
        zoopla_path="enfield-london-borough",
        zoopla_q_param="Enfield (London Borough), London",
    ),
    AreaMapping(
        name="barnet",
        rightmove_region_id="REGION%5E93929",
        zoopla_path="barnet-london-borough",
        zoopla_q_param="Barnet (London Borough), London",
    ),
    # West London
    AreaMapping(
        name="kensington-chelsea",
        rightmove_region_id="REGION%5E61229",
        zoopla_path="kensington-and-chelsea-london-borough",
        zoopla_q_param="Kensington and Chelsea (London Borough), London",
        aliases=("kensington and chelsea", "kensington"),
    ),
    AreaMapping(
        name="hammersmith-fulham",
        rightmove_region_id="REGION%5E61407",
        zoopla_path="hammersmith-and-fulham-london-borough",
        zoopla_q_param="Hammersmith and Fulham (London Borough), London",
        aliases=("hammersmith and fulham", "hammersmith"),
    ),
    AreaMapping(
        name="brent",
        rightmove_region_id="REGION%5E93935",
        zoopla_path="brent-london-borough",
        zoopla_q_param="Brent (London Borough), London",
    ),
    AreaMapping(
        name="ealing",
        rightmove_region_id="REGION%5E93947",
        zoopla_path="ealing-london-borough",
        zoopla_q_param="Ealing (London Borough), London",
    ),
    AreaMapping(
        name="hounslow",
        rightmove_region_id="REGION%5E93962",
        zoopla_path=None,
        zoopla_q_param=None,
    ),
    AreaMapping(
        name="hillingdon",
        rightmove_region_id="REGION%5E93959",
        zoopla_path=None,
        zoopla_q_param=None,
    ),
    AreaMapping(
        name="harrow",
        rightmove_region_id="REGION%5E93956",
        zoopla_path=None,
        zoopla_q_param=None,
    ),
    # South London
    AreaMapping(
        name="lambeth",
        rightmove_region_id="REGION%5E93971",
        zoopla_path="lambeth-london-borough",
        zoopla_q_param="Lambeth (London Borough), London",
    ),
    AreaMapping(
        name="southwark",
        rightmove_region_id="REGION%5E61518",
        zoopla_path="southwark-london-borough",
        zoopla_q_param="Southwark (London Borough), London",
    ),
    AreaMapping(
        name="lewisham",
        rightmove_region_id="REGION%5E61413",
        zoopla_path="lewisham-london-borough",
        zoopla_q_param="Lewisham (London Borough), London",
    ),
    AreaMapping(
        name="greenwich",
        rightmove_region_id="REGION%5E61226",
        zoopla_path="greenwich-london-borough",
        zoopla_q_param="Greenwich (London Borough), London",
    ),
    AreaMapping(
        name="bromley",
        rightmove_region_id="REGION%5E93938",
        zoopla_path=None,
        zoopla_q_param=None,
    ),
    AreaMapping(
        name="bexley",
        rightmove_region_id="REGION%5E93932",
        zoopla_path=None,
        zoopla_q_param=None,
    ),
    AreaMapping(
        name="croydon",
        rightmove_region_id="REGION%5E93944",
        zoopla_path=None,
        zoopla_q_param=None,
    ),
    AreaMapping(
        name="sutton",
        rightmove_region_id="REGION%5E93974",
        zoopla_path=None,
        zoopla_q_param=None,
    ),
    AreaMapping(
        name="merton",
        rightmove_region_id="REGION%5E61414",
        zoopla_path=None,
        zoopla_q_param=None,
    ),
    AreaMapping(
        name="wandsworth",
        rightmove_region_id="REGION%5E93977",
        zoopla_path="wandsworth-london-borough",
        zoopla_q_param="Wandsworth (London Borough), London",
    ),
    AreaMapping(
        name="kingston-thames",
        rightmove_region_id="REGION%5E93968",
        zoopla_path=None,
        zoopla_q_param=None,
        aliases=("kingston upon thames",),
    ),
    AreaMapping(
        name="richmond-thames",
        rightmove_region_id="REGION%5E61415",
        zoopla_path=None,
        zoopla_q_param=None,
        aliases=("richmond upon thames",),
    ),
)

# ---------------------------------------------------------------------------
# Outcode mappings (pre-discovered Rightmove identifiers)
# ---------------------------------------------------------------------------

OUTCODE_MAPPINGS: Final[tuple[OutcodeMapping, ...]] = (
    # East London
    OutcodeMapping("E1", "OUTCODE%5E743"),
    OutcodeMapping("E2", "OUTCODE%5E755"),
    OutcodeMapping("E3", "OUTCODE%5E756"),
    OutcodeMapping("E4", "OUTCODE%5E757"),
    OutcodeMapping("E5", "OUTCODE%5E758"),
    OutcodeMapping("E6", "OUTCODE%5E759"),
    OutcodeMapping("E7", "OUTCODE%5E760"),
    OutcodeMapping("E8", "OUTCODE%5E762"),
    OutcodeMapping("E9", "OUTCODE%5E763"),
    OutcodeMapping("E10", "OUTCODE%5E745"),
    OutcodeMapping("E11", "OUTCODE%5E746"),
    OutcodeMapping("E14", "OUTCODE%5E749"),
    OutcodeMapping("E15", "OUTCODE%5E750"),
    OutcodeMapping("E17", "OUTCODE%5E752"),
    # North London
    OutcodeMapping("N1", "OUTCODE%5E1666"),
    OutcodeMapping("N4", "OUTCODE%5E1682"),
    OutcodeMapping("N5", "OUTCODE%5E1683"),
    OutcodeMapping("N7", "OUTCODE%5E1685"),
    OutcodeMapping("N8", "OUTCODE%5E1686"),
    OutcodeMapping("N15", "OUTCODE%5E1672"),
    OutcodeMapping("N16", "OUTCODE%5E1673"),
    OutcodeMapping("N17", "OUTCODE%5E1674"),
)

# ---------------------------------------------------------------------------
# Derived lookup dicts — computed once at import time
# ---------------------------------------------------------------------------


def _build_rightmove_locations() -> dict[str, str]:
    """Build area name → Rightmove region ID mapping."""
    result: dict[str, str] = {}
    for area in AREA_MAPPINGS:
        if area.rightmove_region_id is None:
            continue
        result[area.name] = area.rightmove_region_id
        for alias in area.aliases:
            result[alias] = area.rightmove_region_id
    return result


def _build_rightmove_outcodes() -> dict[str, str]:
    """Build outcode → Rightmove outcode ID mapping."""
    return {m.outcode: m.rightmove_outcode_id for m in OUTCODE_MAPPINGS}


def _build_borough_areas() -> dict[str, tuple[str, str]]:
    """Build area name → (zoopla_path, zoopla_q_param) mapping."""
    result: dict[str, tuple[str, str]] = {}
    for area in AREA_MAPPINGS:
        if area.zoopla_path is None or area.zoopla_q_param is None:
            continue
        result[area.name] = (area.zoopla_path, area.zoopla_q_param)
        for alias in area.aliases:
            result[alias] = (area.zoopla_path, area.zoopla_q_param)
    return result


RIGHTMOVE_LOCATIONS: Final[dict[str, str]] = _build_rightmove_locations()
RIGHTMOVE_OUTCODES: Final[dict[str, str]] = _build_rightmove_outcodes()
BOROUGH_AREAS: Final[dict[str, tuple[str, str]]] = _build_borough_areas()
