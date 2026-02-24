"""Static area context data for London rental market analysis.

This module loads rental benchmarks, area descriptions, council tax rates,
crime statistics, and rent trends from a JSON data file. Used by the quality
analysis filter and web dashboard routes.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, TypedDict


class MicroArea(TypedDict, total=False):
    """Micro-area neighbourhood profile within an outcode."""

    character: str
    transport: str
    creative_scene: str
    broadband: str
    hosting_tolerance: str  # "high" | "moderate" | "low"
    wfh_suitability: str  # "good" | "moderate" | "poor"
    value: str


class AreaContext(TypedDict, total=False):
    """Structured area context for an outcode."""

    overview: str
    micro_areas: dict[str, MicroArea]


class CrimeInfo(TypedDict, total=False):
    """Crime rate data for an outcode."""

    rate: int
    vs_london: str
    risk: str
    note: str


class RentTrend(TypedDict):
    """Year-over-year rent trend data for a borough."""

    yoy_pct: float
    direction: str


class AcousticProfile(TypedDict):
    """Acoustic performance profile for a building/property type."""

    label: str
    airborne_insulation_db: str
    hosting_safety: str  # "good" | "moderate" | "poor"
    summary: str
    viewing_checks: list[str]


class NoiseEnforcement(TypedDict):
    """Borough-specific noise enforcement data."""

    process: str
    threshold_info: str
    response_time: str


class ServiceChargeRange(TypedDict):
    """Service charge range for a property type."""

    typical_low: int
    typical_high: int


class HostingTolerance(TypedDict, total=False):
    """Area-level hosting tolerance rating for an outcode."""

    rating: str  # "high" | "moderate" | "low"
    notes: str
    known_friendly_areas: list[str]
    known_sensitive_areas: list[str]


class CreativeScene(TypedDict, total=False):
    """Creative infrastructure data for an outcode."""

    rehearsal_spaces: list[str]
    venues: list[str]
    creative_hubs: list[str]
    summary: str


_DATA_PATH = Path(__file__).resolve().parent / "area_context.json"
try:
    _DATA = json.loads(_DATA_PATH.read_text())
except (FileNotFoundError, json.JSONDecodeError) as e:
    raise RuntimeError(f"Failed to load {_DATA_PATH}: {e}") from e

RENTAL_BENCHMARKS: Final[dict[str, dict[int, int]]] = {
    outcode: {int(k): v for k, v in beds.items()}
    for outcode, beds in _DATA["rental_benchmarks"].items()
}

DEFAULT_BENCHMARK: Final[dict[int, int]] = {
    int(k): v for k, v in _DATA["default_benchmark"].items()
}

AREA_CONTEXT: Final[dict[str, AreaContext]] = _DATA["area_context"]

OUTCODE_BOROUGH: Final[dict[str, str]] = _DATA["outcode_borough"]

COUNCIL_TAX_MONTHLY: Final[dict[str, dict[str, int]]] = _DATA["council_tax_monthly"]

CRIME_RATES: Final[dict[str, CrimeInfo]] = _DATA["crime_rates"]

RENT_TRENDS: Final[dict[str, RentTrend]] = _DATA["rent_trends"]

ACOUSTIC_PROFILES: Final[dict[str, AcousticProfile]] = _DATA.get("acoustic_profiles", {})

NOISE_ENFORCEMENT: Final[dict[str, NoiseEnforcement]] = _DATA.get("noise_enforcement", {})

ENERGY_COSTS_MONTHLY: Final[dict[str, dict[str, int]]] = _DATA.get("energy_costs_monthly", {})

WATER_COSTS_MONTHLY: Final[dict[str, int]] = _DATA.get("water_costs_monthly", {})

BROADBAND_COSTS_MONTHLY: Final[dict[str, int]] = _DATA.get("broadband_costs_monthly", {})

SERVICE_CHARGE_RANGES: Final[dict[str, ServiceChargeRange]] = _DATA.get("service_charge_ranges", {})

HOSTING_TOLERANCE: Final[dict[str, HostingTolerance]] = _DATA.get("hosting_tolerance", {})
CREATIVE_SCENE: Final[dict[str, CreativeScene]] = _DATA.get("creative_scene", {})


def build_area_context(outcode: str) -> dict[str, Any]:
    """Build common area context dict for an outcode.

    Returns the shared subset of area data used by both the property detail
    and area detail routes: overview description, rental benchmarks,
    borough info (council tax, rent trends), and crime rates.
    """
    ctx: dict[str, Any] = {
        "description": get_area_overview(outcode),
        "benchmarks": RENTAL_BENCHMARKS.get(outcode),
        "crime": CRIME_RATES.get(outcode),
    }
    borough = OUTCODE_BOROUGH.get(outcode)
    if borough:
        ctx["borough"] = borough
        ctx["council_tax"] = COUNCIL_TAX_MONTHLY.get(borough)
        ctx["rent_trend"] = RENT_TRENDS.get(borough)
    return ctx


@dataclass(frozen=True)
class PropertyContext:
    """Pre-computed area context for quality analysis prompts."""

    outcode: str | None
    area_overview: str | None
    borough: str | None
    council_tax_band_c: float | None
    energy_estimate: float | None
    crime_summary: str | None
    rent_trend: str | None
    hosting_tolerance: str | None


def build_property_context(postcode: str | None, bedrooms: int) -> PropertyContext:
    """Build property-specific area context for quality analysis.

    Unlike build_area_context() (which returns raw dicts for web routes),
    this returns pre-formatted strings ready for LLM prompts.
    """
    if not postcode:
        return PropertyContext(
            outcode=None,
            area_overview=None,
            borough=None,
            council_tax_band_c=None,
            energy_estimate=None,
            crime_summary=None,
            rent_trend=None,
            hosting_tolerance=None,
        )

    outcode = postcode.split()[0].upper() if " " in postcode else postcode.upper()
    area_overview = get_area_overview(outcode)

    borough = OUTCODE_BOROUGH.get(outcode)
    council_tax_c = COUNCIL_TAX_MONTHLY.get(borough, {}).get("C") if borough else None

    bed_key = f"{min(max(bedrooms, 1), 2)}_bed"
    energy_estimate = ENERGY_COSTS_MONTHLY.get("D", {}).get(bed_key)

    crime = CRIME_RATES.get(outcode)
    crime_summary: str | None = None
    if crime:
        crime_summary = f"{crime['rate']}/1,000 ({crime['vs_london']} vs London avg)"
        if crime.get("note"):
            crime_summary += f". {crime['note']}"

    trend = RENT_TRENDS.get(borough) if borough else None
    rent_trend = f"+{trend['yoy_pct']}% YoY ({trend['direction']})" if trend else None

    hosting_data = HOSTING_TOLERANCE.get(outcode)
    hosting_str: str | None = None
    if hosting_data:
        rating = hosting_data.get("rating", "unknown")
        notes = hosting_data.get("notes", "")
        hosting_str = f"{rating} — {notes}" if notes else rating

    return PropertyContext(
        outcode=outcode,
        area_overview=area_overview,
        borough=borough,
        council_tax_band_c=council_tax_c,
        energy_estimate=energy_estimate,
        crime_summary=crime_summary,
        rent_trend=rent_trend,
        hosting_tolerance=hosting_str,
    )


# Maps (outcode, official ward name) → our custom micro-area name.
# Keyed by tuple to disambiguate wards like "Lea Bridge" that exist in
# multiple boroughs (Hackney for E5, Waltham Forest for E10).
# Sourced from postcodes.io admin_ward lookups.
WARD_TO_MICRO_AREA: Final[dict[tuple[str, str], str]] = {
    # E2 — Tower Hamlets + Hackney edge
    ("E2", "Bethnal Green West"): "Bethnal Green / Cambridge Heath",
    ("E2", "Bethnal Green East"): "Bethnal Green / Cambridge Heath",
    ("E2", "Weavers"): "Weavers / Brick Lane Fringe",
    ("E2", "Haggerston"): "Haggerston / Queensbridge",
    ("E2", "Hoxton East & Shoreditch"): "Shoreditch / Hoxton",
    # E3 — Tower Hamlets
    ("E3", "Bow West"): "Roman Road / Old Ford",
    ("E3", "Bow East"): "Fish Island",
    ("E3", "Bromley North"): "Mile End",
    ("E3", "Mile End"): "Mile End",
    ("E3", "St Dunstan's"): "Mile End",
    ("E3", "Bromley South"): "Devons Road / Langdon Park",
    ("E3", "Lansbury"): "Poplar / Chrisp Street",
    # E5 — Hackney
    ("E5", "Hackney Downs"): "Hackney Downs fringe",
    ("E5", "Lea Bridge"): "Lea Bridge fringe",
    ("E5", "Cazenove"): "Chatsworth Road / Millfields",
    ("E5", "King's Park"): "Lower Clapton Road",
    ("E5", "Homerton"): "Chatsworth Road / Millfields",
    ("E5", "Springfield"): "Upper Clapton / Springfield Park",
    # E8 — Hackney
    ("E8", "Dalston"): "Dalston core",
    ("E8", "Haggerston"): "Haggerston",
    ("E8", "London Fields"): "London Fields / Broadway Market",
    ("E8", "Hackney Central"): "Hackney Central / Mare Street",
    # E9 — Hackney / Tower Hamlets
    ("E9", "Hackney Wick"): "Hackney Wick core",
    ("E9", "Homerton"): "Homerton / Chatsworth Road",
    ("E9", "Victoria"): "Victoria Park Village",
    ("E9", "King's Park"): "Homerton / Chatsworth Road",
    ("E9", "Bow East"): "Hackney Wick core",
    # E10 — Waltham Forest
    ("E10", "Lea Bridge"): "Lea Bridge Road",
    ("E10", "Leyton"): "Francis Road / Leyton Village",
    ("E10", "Grove Green"): "Central Leyton",
    ("E10", "Forest"): "Upper Leytonstone / Whipps Cross Fringe",
    # E15 — Newham
    ("E15", "Stratford"): "Stratford Village / The Grove",
    ("E15", "West Ham"): "West Ham / Plaistow Fringe",
    ("E15", "Maryland"): "Maryland / Forest Gate Border",
    ("E15", "Forest Gate South"): "Maryland / Forest Gate Border",
    ("E15", "Canning Town North"): "West Ham / Plaistow Fringe",
    ("E15", "Stratford Olympic Park"): "Olympic Park / East Village",
    # E17 — Waltham Forest
    ("E17", "High Street"): "St James Street",
    ("E17", "St James"): "St James Street",
    ("E17", "Markhouse"): "St James Street",
    ("E17", "William Morris"): "Walthamstow Village",
    ("E17", "Higham Hill"): "Blackhorse Road",
    ("E17", "Lea Bridge"): "Blackhorse Road",
    ("E17", "Hoe Street"): "Central Walthamstow / Hoe Street",
    ("E17", "Chapel End"): "Chapel End / West Walthamstow",
    ("E17", "Wood Street"): "Wood Street",
    ("E17", "Upper Walthamstow"): "Wood Street",
    # N15 — Haringey
    ("N15", "West Green"): "West Green Road / Clyde Circus",
    ("N15", "Tottenham Central"): "Markfield Road corridor",
    ("N15", "Seven Sisters"): "West Green Road / Clyde Circus",
    ("N15", "South Tottenham"): "West Green Road / Clyde Circus",
    ("N15", "St Ann's"): "West Green Road / Clyde Circus",
    ("N15", "Harringay"): "Harringay Ladder",
    ("N15", "Northumberland Park"): "Northumberland Park / Stadium Quarter",
    # N16 — Hackney (+ Islington for Mildmay)
    ("N16", "Stoke Newington"): "Church Street Village",
    ("N16", "Clissold"): "Church Street Village",
    ("N16", "Shacklewell"): "Shacklewell / Rectory Road",
    ("N16", "Dalston"): "Shacklewell / Rectory Road",
    ("N16", "Hackney Downs"): "Shacklewell / Rectory Road",
    ("N16", "Stamford Hill West"): "Stamford Hill",
    ("N16", "Cazenove"): "Stamford Hill",
    ("N16", "Springfield"): "Stamford Hill",
    ("N16", "Woodberry Down"): "Woodberry Down / Manor House",
    ("N16", "Mildmay"): "Newington Green / Mildmay",
    # N17 — Haringey
    ("N17", "Tottenham Central"): "Markfield Road (N17 side)",
    ("N17", "Northumberland Park"): "Tottenham Hale",
    ("N17", "South Tottenham"): "Markfield Road (N17 side)",
    ("N17", "Tottenham Hale"): "Tottenham Hale",
    ("N17", "Bruce Castle"): "Bruce Grove / High Road N17",
    ("N17", "West Green"): "Bruce Grove / High Road N17",
    # N1 — Islington (+ Hackney for De Beauvoir, Camden for King's Cross)
    ("N1", "Caledonian"): "King's Cross / Caledonian Road",
    ("N1", "St Peter's & Canalside"): "Angel / Upper Street",
    ("N1", "St Mary's & St James'"): "Angel / Upper Street",
    ("N1", "Barnsbury"): "Barnsbury / Thornhill Square",
    ("N1", "Laycock"): "Canonbury",
    ("N1", "De Beauvoir"): "De Beauvoir Town",
    ("N1", "King's Cross"): "Pentonville / Claremont",
    # N5 — Islington
    ("N5", "Highbury"): "Highbury Fields / Highbury Corner",
    ("N5", "Arsenal"): "Arsenal / Drayton Park",
    ("N5", "Laycock"): "Highbury Fields / Highbury Corner",
    ("N5", "Mildmay"): "Highbury Barn / Highbury Grove",
}


def get_area_overview(outcode: str) -> str | None:
    """Return the overview string for an outcode, or None if not found."""
    entry = AREA_CONTEXT.get(outcode)
    if entry is None:
        return None
    # Defensive: handle legacy string format
    if isinstance(entry, str):
        return entry
    return entry.get("overview")


def get_micro_areas(outcode: str) -> dict[str, MicroArea] | None:
    """Return micro-areas dict for an outcode, or None if unavailable."""
    entry = AREA_CONTEXT.get(outcode)
    if entry is None or isinstance(entry, str):
        return None
    return entry.get("micro_areas")


def get_micro_area_for_ward(ward: str, outcode: str) -> str | None:
    """Map an official ward name to our custom micro-area name.

    Uses the WARD_TO_MICRO_AREA lookup keyed by (outcode, ward) to handle
    wards that exist in multiple boroughs. Returns None if no mapping exists
    or if the mapped micro-area doesn't exist for this outcode.
    """
    micro_area_name = WARD_TO_MICRO_AREA.get((outcode, ward))
    if micro_area_name is None:
        return None
    # Verify the mapped name actually exists in this outcode's micro-areas
    micro_areas = get_micro_areas(outcode)
    if micro_areas is None or micro_area_name not in micro_areas:
        return None
    return micro_area_name


# Words that appear in micro-area names but don't help distinguish them
_MA_STOP_WORDS = frozenset(
    {
        "core",
        "fringe",
        "corridor",
        "side",
        "border",
        "the",
        "and",
        "of",
        "in",
        "a",
    }
)

# Generic words that appear in many addresses and shouldn't score individually
_MA_GENERIC_WORDS = frozenset(
    {
        "road",
        "street",
        "lane",
        "drive",
        "avenue",
        "place",
        "close",
        "way",
        "walk",
        "hill",
        "square",
        "terrace",
        "mews",
        "crescent",
        "gardens",
        "rise",
        "grove",
        "park",
        "london",
    }
)

# Pattern to extract street names from prose (e.g. "Mare Street", "Kingsland Road")
_STREET_NAME_RE = re.compile(
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)"
    r"\s+(?:Road|Street|Lane|Drive|Square|Walk|Way|Terrace|Place|Close"
    r"|Grove|Avenue|Mews|Crescent|Hill|Rise|Green|Gardens)",
)


def match_micro_area(address: str, outcode: str) -> str | None:
    """Match a property address to its most likely micro-area.

    Scores each micro-area by:
    - Full sub-area name appearing in the address (10 pts)
    - Individual distinctive words from the name (3 pts each)
    - Street names from character/value prose appearing in the address (5 pts)

    Returns the name of the best-matching micro-area, or None if no match.
    """
    if not address:
        return None

    micro_areas = get_micro_areas(outcode)
    if not micro_areas:
        return None

    addr_lower = address.lower()

    best_name: str | None = None
    best_score = 0

    for name, data in micro_areas.items():
        score = 0

        # Split "London Fields / Broadway Market" into sub-areas
        sub_areas = [s.strip() for s in name.split("/")]

        for sub_area in sub_areas:
            sub_lower = sub_area.lower()
            # Remove parenthetical notes like "(N17 side)"
            sub_lower = re.sub(r"\(.*?\)", "", sub_lower).strip()

            # Clean version with stop words removed
            words = sub_lower.split()
            cleaned = " ".join(w for w in words if w not in _MA_STOP_WORDS)

            # Full phrase match (strongest signal)
            if sub_lower in addr_lower or (cleaned != sub_lower and cleaned in addr_lower):
                score += 10
                continue

            # Individual distinctive word matches
            significant = [w for w in words if w not in _MA_STOP_WORDS | _MA_GENERIC_WORDS]
            for word in significant:
                if re.search(r"\b" + re.escape(word) + r"\b", addr_lower):
                    score += 3

        # Check street names mentioned in character/value prose
        for field in ("character", "value"):
            text = str(data.get(field, ""))
            if not text:
                continue
            for street_match in _STREET_NAME_RE.finditer(text):
                street_name = street_match.group(1).lower()
                if street_name in addr_lower:
                    score += 5

        if score > best_score:
            best_score = score
            best_name = name

    return best_name if best_score > 0 else None
