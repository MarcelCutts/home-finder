"""Tests for shared location mappings."""

from home_finder.data.location_mappings import (
    AREA_MAPPINGS,
    BOROUGH_AREAS,
    RIGHTMOVE_LOCATIONS,
    RIGHTMOVE_OUTCODES,
)


# ---------------------------------------------------------------------------
# Old inline dicts (copied from the deleted code) for regression comparison
# ---------------------------------------------------------------------------

_OLD_RIGHTMOVE_LOCATIONS = {
    "city-of-london": "REGION%5E61224",
    "westminster": "REGION%5E93980",
    "camden": "REGION%5E93941",
    "islington": "REGION%5E93965",
    "hackney": "REGION%5E93953",
    "tower-hamlets": "REGION%5E61417",
    "tower hamlets": "REGION%5E61417",
    "newham": "REGION%5E61231",
    "waltham-forest": "REGION%5E61232",
    "waltham forest": "REGION%5E61232",
    "barking-dagenham": "REGION%5E61400",
    "barking and dagenham": "REGION%5E61400",
    "havering": "REGION%5E61228",
    "redbridge": "REGION%5E61537",
    "haringey": "REGION%5E61227",
    "enfield": "REGION%5E93950",
    "barnet": "REGION%5E93929",
    "kensington-chelsea": "REGION%5E61229",
    "kensington and chelsea": "REGION%5E61229",
    "hammersmith-fulham": "REGION%5E61407",
    "hammersmith and fulham": "REGION%5E61407",
    "brent": "REGION%5E93935",
    "ealing": "REGION%5E93947",
    "hounslow": "REGION%5E93962",
    "hillingdon": "REGION%5E93959",
    "harrow": "REGION%5E93956",
    "lambeth": "REGION%5E93971",
    "southwark": "REGION%5E61518",
    "lewisham": "REGION%5E61413",
    "greenwich": "REGION%5E61226",
    "bromley": "REGION%5E93938",
    "bexley": "REGION%5E93932",
    "croydon": "REGION%5E93944",
    "sutton": "REGION%5E93974",
    "merton": "REGION%5E61414",
    "wandsworth": "REGION%5E93977",
    "kingston-thames": "REGION%5E93968",
    "kingston upon thames": "REGION%5E93968",
    "richmond-thames": "REGION%5E61415",
    "richmond upon thames": "REGION%5E61415",
}

_OLD_RIGHTMOVE_OUTCODES = {
    "E1": "OUTCODE%5E743",
    "E2": "OUTCODE%5E755",
    "E3": "OUTCODE%5E756",
    "E4": "OUTCODE%5E757",
    "E5": "OUTCODE%5E758",
    "E6": "OUTCODE%5E759",
    "E7": "OUTCODE%5E760",
    "E8": "OUTCODE%5E762",
    "E9": "OUTCODE%5E763",
    "E10": "OUTCODE%5E745",
    "E11": "OUTCODE%5E746",
    "E14": "OUTCODE%5E749",
    "E15": "OUTCODE%5E750",
    "E17": "OUTCODE%5E752",
    "N1": "OUTCODE%5E1666",
    "N4": "OUTCODE%5E1682",
    "N5": "OUTCODE%5E1683",
    "N7": "OUTCODE%5E1685",
    "N8": "OUTCODE%5E1686",
    "N15": "OUTCODE%5E1672",
    "N16": "OUTCODE%5E1673",
    "N17": "OUTCODE%5E1674",
}

_OLD_BOROUGH_AREAS: dict[str, tuple[str, str]] = {
    "hackney": ("hackney-london-borough", "Hackney (London Borough), London"),
    "islington": ("islington-london-borough", "Islington (London Borough), London"),
    "tower-hamlets": (
        "tower-hamlets-london-borough",
        "Tower Hamlets (London Borough), London",
    ),
    "camden": ("camden-london-borough", "Camden (London Borough), London"),
    "lambeth": ("lambeth-london-borough", "Lambeth (London Borough), London"),
    "southwark": ("southwark-london-borough", "Southwark (London Borough), London"),
    "haringey": ("haringey-london-borough", "Haringey (London Borough), London"),
    "lewisham": ("lewisham-london-borough", "Lewisham (London Borough), London"),
    "newham": ("newham-london-borough", "Newham (London Borough), London"),
    "waltham-forest": (
        "waltham-forest-london-borough",
        "Waltham Forest (London Borough), London",
    ),
    "greenwich": ("greenwich-london-borough", "Greenwich (London Borough), London"),
    "barnet": ("barnet-london-borough", "Barnet (London Borough), London"),
    "brent": ("brent-london-borough", "Brent (London Borough), London"),
    "ealing": ("ealing-london-borough", "Ealing (London Borough), London"),
    "enfield": ("enfield-london-borough", "Enfield (London Borough), London"),
    "westminster": (
        "city-of-westminster-london-borough",
        "City of Westminster (London Borough), London",
    ),
    "kensington": (
        "kensington-and-chelsea-london-borough",
        "Kensington and Chelsea (London Borough), London",
    ),
    "hammersmith": (
        "hammersmith-and-fulham-london-borough",
        "Hammersmith and Fulham (London Borough), London",
    ),
    "wandsworth": ("wandsworth-london-borough", "Wandsworth (London Borough), London"),
}


# ---------------------------------------------------------------------------
# Regression: derived dicts must be supersets of the old inline values
# ---------------------------------------------------------------------------


class TestRightmoveLocationsRegression:
    def test_all_old_keys_present(self) -> None:
        for key, value in _OLD_RIGHTMOVE_LOCATIONS.items():
            assert key in RIGHTMOVE_LOCATIONS, f"Missing key: {key}"
            assert RIGHTMOVE_LOCATIONS[key] == value, (
                f"Value mismatch for {key}: {RIGHTMOVE_LOCATIONS[key]} != {value}"
            )

    def test_old_dict_is_subset(self) -> None:
        assert _OLD_RIGHTMOVE_LOCATIONS.items() <= RIGHTMOVE_LOCATIONS.items()


class TestRightmoveOutcodesRegression:
    def test_all_old_keys_present(self) -> None:
        for key, value in _OLD_RIGHTMOVE_OUTCODES.items():
            assert key in RIGHTMOVE_OUTCODES, f"Missing key: {key}"
            assert RIGHTMOVE_OUTCODES[key] == value

    def test_exact_match(self) -> None:
        assert RIGHTMOVE_OUTCODES == _OLD_RIGHTMOVE_OUTCODES


class TestBoroughAreasRegression:
    def test_all_old_keys_present(self) -> None:
        for key, value in _OLD_BOROUGH_AREAS.items():
            assert key in BOROUGH_AREAS, f"Missing key: {key}"
            assert BOROUGH_AREAS[key] == value, (
                f"Value mismatch for {key}: {BOROUGH_AREAS[key]} != {value}"
            )

    def test_old_dict_is_subset(self) -> None:
        assert _OLD_BOROUGH_AREAS.items() <= BOROUGH_AREAS.items()


# ---------------------------------------------------------------------------
# Spot-check specific keys including aliases
# ---------------------------------------------------------------------------


class TestSpotChecks:
    def test_hackney_rightmove(self) -> None:
        assert RIGHTMOVE_LOCATIONS["hackney"] == "REGION%5E93953"

    def test_tower_hamlets_alias(self) -> None:
        assert RIGHTMOVE_LOCATIONS["tower hamlets"] == "REGION%5E61417"
        assert RIGHTMOVE_LOCATIONS["tower-hamlets"] == "REGION%5E61417"

    def test_kensington_zoopla_alias(self) -> None:
        assert BOROUGH_AREAS["kensington"] == (
            "kensington-and-chelsea-london-borough",
            "Kensington and Chelsea (London Borough), London",
        )
        assert BOROUGH_AREAS["kensington-chelsea"] == BOROUGH_AREAS["kensington"]

    def test_hammersmith_zoopla_alias(self) -> None:
        assert BOROUGH_AREAS["hammersmith"] == (
            "hammersmith-and-fulham-london-borough",
            "Hammersmith and Fulham (London Borough), London",
        )

    def test_e8_outcode(self) -> None:
        assert RIGHTMOVE_OUTCODES["E8"] == "OUTCODE%5E762"

    def test_n16_outcode(self) -> None:
        assert RIGHTMOVE_OUTCODES["N16"] == "OUTCODE%5E1673"


# ---------------------------------------------------------------------------
# No alias collides with a different area's canonical name
# ---------------------------------------------------------------------------


class TestAliasCollisions:
    def test_no_alias_collides_with_canonical_name(self) -> None:
        canonical_names = {a.name for a in AREA_MAPPINGS}
        for area in AREA_MAPPINGS:
            for alias in area.aliases:
                if alias in canonical_names:
                    # The alias must point to the same area
                    conflicting = next(a for a in AREA_MAPPINGS if a.name == alias)
                    assert False, (
                        f"Alias '{alias}' of '{area.name}' collides with "
                        f"canonical name of '{conflicting.name}'"
                    )
