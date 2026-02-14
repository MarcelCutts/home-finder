"""Tests for the true monthly cost calculator."""

import pytest

from home_finder.utils.cost_calculator import estimate_true_monthly_cost

# Reference values from area_context.json — pinned here so tests break
# loudly if someone changes the source data without updating expectations.
HACKNEY_BAND_C = 146
HACKNEY_BAND_D = 164
TOWER_HAMLETS_BAND_A = 97
ENERGY_D_1BED = 106
ENERGY_D_2BED = 132
ENERGY_B_1BED = 70
ENERGY_C_2BED = 110
ENERGY_G_1BED = 169
WATER_1BED = 40
WATER_2BED = 50
BROADBAND = 25  # All types are £25


def _get_item(result: dict, label: str) -> dict:
    """Extract a single line item by label, asserting exactly one exists."""
    items = [i for i in result["line_items"] if i["label"] == label]
    assert len(items) == 1, f"Expected 1 '{label}' item, got {len(items)}"
    return items[0]


def _get_labels(result: dict) -> list[str]:
    return [i["label"] for i in result["line_items"]]


class TestRentOnly:
    """Baseline: rent with no extras."""

    def test_total_includes_defaults(self) -> None:
        """Default call adds energy (D), water, broadband to rent."""
        result = estimate_true_monthly_cost(rent_pcm=1800)
        # Defaults: no council tax (no borough), EPC D energy, water, broadband
        assert result["total"] == 1800 + ENERGY_D_1BED + WATER_1BED + BROADBAND

    def test_rent_line_item(self) -> None:
        result = estimate_true_monthly_cost(rent_pcm=1800)
        rent = _get_item(result, "Rent")
        assert rent["amount"] == 1800
        assert rent["note"] is None

    def test_metadata(self) -> None:
        result = estimate_true_monthly_cost(rent_pcm=1800)
        assert result["is_estimate"] is True
        assert result["bills_included"] is False
        assert result["total_high"] is None

    def test_items_have_pct(self) -> None:
        result = estimate_true_monthly_cost(rent_pcm=1800)
        for item in result["line_items"]:
            assert "pct" in item
        rent = _get_item(result, "Rent")
        assert rent["pct"] > 90  # rent dominates


class TestCouncilTax:
    def test_hackney_band_c(self) -> None:
        result = estimate_true_monthly_cost(
            rent_pcm=1800, borough="Hackney", council_tax_band="C"
        )
        ct = _get_item(result, "Council tax")
        assert ct["amount"] == HACKNEY_BAND_C
        assert ct["note"] == "Band C, Hackney"

    def test_tower_hamlets_band_a(self) -> None:
        result = estimate_true_monthly_cost(
            rent_pcm=1500, borough="Tower Hamlets", council_tax_band="A"
        )
        ct = _get_item(result, "Council tax")
        assert ct["amount"] == TOWER_HAMLETS_BAND_A

    def test_added_to_total(self) -> None:
        result = estimate_true_monthly_cost(
            rent_pcm=1800, borough="Hackney", council_tax_band="D"
        )
        assert result["total"] == (
            1800 + HACKNEY_BAND_D + ENERGY_D_1BED + WATER_1BED + BROADBAND
        )

    def test_missing_borough_skips(self) -> None:
        result = estimate_true_monthly_cost(rent_pcm=1800, council_tax_band="C")
        assert "Council tax" not in _get_labels(result)

    def test_missing_band_skips(self) -> None:
        result = estimate_true_monthly_cost(rent_pcm=1800, borough="Hackney")
        assert "Council tax" not in _get_labels(result)

    def test_unknown_band_skips(self) -> None:
        result = estimate_true_monthly_cost(
            rent_pcm=1800, borough="Hackney", council_tax_band="unknown"
        )
        assert "Council tax" not in _get_labels(result)

    def test_bogus_borough_skips(self) -> None:
        result = estimate_true_monthly_cost(
            rent_pcm=1800, borough="Narnia", council_tax_band="C"
        )
        assert "Council tax" not in _get_labels(result)

    def test_case_insensitive_band(self) -> None:
        result = estimate_true_monthly_cost(
            rent_pcm=1800, borough="Hackney", council_tax_band="c"
        )
        ct = _get_item(result, "Council tax")
        assert ct["amount"] == HACKNEY_BAND_C


class TestEnergy:
    def test_epc_b_1bed(self) -> None:
        result = estimate_true_monthly_cost(rent_pcm=1800, epc_rating="B")
        energy = _get_item(result, "Energy")
        assert energy["amount"] == ENERGY_B_1BED
        assert energy["note"] == "EPC B est."

    def test_epc_c_2bed(self) -> None:
        result = estimate_true_monthly_cost(
            rent_pcm=1800, epc_rating="C", bedrooms=2
        )
        energy = _get_item(result, "Energy")
        assert energy["amount"] == ENERGY_C_2BED
        assert energy["note"] == "EPC C est."

    def test_unknown_defaults_to_d(self) -> None:
        result = estimate_true_monthly_cost(rent_pcm=1800, epc_rating="unknown")
        energy = _get_item(result, "Energy")
        assert energy["amount"] == ENERGY_D_1BED
        assert energy["note"] == "EPC D default"

    def test_none_defaults_to_d(self) -> None:
        result = estimate_true_monthly_cost(rent_pcm=1800, epc_rating=None)
        energy = _get_item(result, "Energy")
        assert energy["amount"] == ENERGY_D_1BED
        assert energy["note"] == "EPC D default"

    def test_empty_string_defaults_to_d(self) -> None:
        result = estimate_true_monthly_cost(rent_pcm=1800, epc_rating="")
        energy = _get_item(result, "Energy")
        assert energy["amount"] == ENERGY_D_1BED

    def test_case_insensitive(self) -> None:
        result = estimate_true_monthly_cost(rent_pcm=1800, epc_rating="g")
        energy = _get_item(result, "Energy")
        assert energy["amount"] == ENERGY_G_1BED


class TestWater:
    def test_1bed(self) -> None:
        result = estimate_true_monthly_cost(rent_pcm=1800, bedrooms=1)
        water = _get_item(result, "Water")
        assert water["amount"] == WATER_1BED

    def test_2bed(self) -> None:
        result = estimate_true_monthly_cost(rent_pcm=1800, bedrooms=2)
        water = _get_item(result, "Water")
        assert water["amount"] == WATER_2BED


class TestBroadband:
    def test_default_fttp(self) -> None:
        result = estimate_true_monthly_cost(rent_pcm=1800)
        bb = _get_item(result, "Broadband")
        assert bb["amount"] == BROADBAND

    def test_explicit_type(self) -> None:
        result = estimate_true_monthly_cost(rent_pcm=1800, broadband_type="cable")
        bb = _get_item(result, "Broadband")
        assert bb["amount"] == BROADBAND

    def test_unknown_defaults_to_fttp(self) -> None:
        result = estimate_true_monthly_cost(rent_pcm=1800, broadband_type="unknown")
        bb = _get_item(result, "Broadband")
        assert bb["amount"] == BROADBAND


class TestBillsIncluded:
    def test_skips_energy_water_broadband(self) -> None:
        result = estimate_true_monthly_cost(rent_pcm=1800, bills_included=True)
        labels = _get_labels(result)
        assert "Energy" not in labels
        assert "Water" not in labels
        assert "Broadband" not in labels

    def test_bills_item_shows_zero(self) -> None:
        result = estimate_true_monthly_cost(rent_pcm=1800, bills_included=True)
        bills = _get_item(result, "Bills")
        assert bills["amount"] == 0
        assert "included" in bills["note"]

    def test_total_equals_rent_only(self) -> None:
        """With bills included, total is just rent (no utility costs)."""
        result = estimate_true_monthly_cost(rent_pcm=1800, bills_included=True)
        assert result["total"] == 1800

    def test_council_tax_still_added(self) -> None:
        """Council tax is NOT included in 'bills' — always separate."""
        result = estimate_true_monthly_cost(
            rent_pcm=1800,
            bills_included=True,
            borough="Hackney",
            council_tax_band="C",
        )
        assert "Council tax" in _get_labels(result)
        assert result["total"] == 1800 + HACKNEY_BAND_C


class TestServiceCharge:
    def test_known_amount(self) -> None:
        result = estimate_true_monthly_cost(
            rent_pcm=1800, service_charge_pcm=200
        )
        sc = _get_item(result, "Service charge")
        assert sc["amount"] == 200
        assert sc["note"] == "from listing"

    def test_known_zero(self) -> None:
        """Explicitly zero service charge should appear with £0."""
        result = estimate_true_monthly_cost(
            rent_pcm=1800, service_charge_pcm=0
        )
        sc = _get_item(result, "Service charge")
        assert sc["amount"] == 0

    def test_known_overrides_estimate(self) -> None:
        """Known service charge wins over property_type range estimate."""
        result = estimate_true_monthly_cost(
            rent_pcm=1800,
            service_charge_pcm=100,
            property_type="new_build",
        )
        sc = _get_item(result, "Service charge")
        assert sc["amount"] == 100  # Not the new_build range

    def test_range_estimate_new_build(self) -> None:
        result = estimate_true_monthly_cost(
            rent_pcm=1800, property_type="new_build"
        )
        sc = _get_item(result, "Service charge")
        assert sc["amount"] is None
        assert sc["range_low"] == 200
        assert sc["range_high"] == 450

    def test_range_adds_low_end_to_total(self) -> None:
        """Range estimate adds low-end to total for conservative estimate."""
        base = estimate_true_monthly_cost(rent_pcm=1800)
        with_range = estimate_true_monthly_cost(
            rent_pcm=1800, property_type="new_build"
        )
        assert with_range["total"] == base["total"] + 200  # range_low
        assert with_range["total_high"] == base["total"] + 450  # range_high

    def test_unknown_type_skips(self) -> None:
        result = estimate_true_monthly_cost(
            rent_pcm=1800, property_type="unknown"
        )
        assert "Service charge" not in _get_labels(result)

    def test_bogus_type_skips(self) -> None:
        result = estimate_true_monthly_cost(
            rent_pcm=1800, property_type="spaceship"
        )
        assert "Service charge" not in _get_labels(result)


class TestBedroomClamping:
    """Bedrooms are clamped to 1-2 for cost lookup."""

    def test_zero_bedrooms_uses_1bed(self) -> None:
        result = estimate_true_monthly_cost(rent_pcm=1800, bedrooms=0)
        energy = _get_item(result, "Energy")
        assert energy["amount"] == ENERGY_D_1BED
        water = _get_item(result, "Water")
        assert water["amount"] == WATER_1BED

    def test_five_bedrooms_uses_2bed(self) -> None:
        result = estimate_true_monthly_cost(rent_pcm=1800, bedrooms=5)
        energy = _get_item(result, "Energy")
        assert energy["amount"] == ENERGY_D_2BED
        water = _get_item(result, "Water")
        assert water["amount"] == WATER_2BED


class TestFullBreakdown:
    """End-to-end: verify exact total with all components."""

    def test_exact_total(self) -> None:
        """Hackney Band D + EPC C + 2-bed water + broadband + service charge."""
        result = estimate_true_monthly_cost(
            rent_pcm=1800,
            borough="Hackney",
            council_tax_band="D",
            epc_rating="C",
            bedrooms=2,
            broadband_type="fttp",
            service_charge_pcm=150,
        )
        expected = (
            1800          # rent
            + HACKNEY_BAND_D   # council tax
            + ENERGY_C_2BED    # energy
            + WATER_2BED       # water
            + BROADBAND        # broadband
            + 150              # service charge
        )
        assert result["total"] == expected
        # Also verify: 1800 + 164 + 110 + 50 + 25 + 150 = 2299
        assert result["total"] == 2299

    def test_all_labels_present(self) -> None:
        result = estimate_true_monthly_cost(
            rent_pcm=1800,
            borough="Hackney",
            council_tax_band="D",
            epc_rating="C",
            bedrooms=2,
            service_charge_pcm=150,
        )
        labels = _get_labels(result)
        assert labels == [
            "Rent",
            "Council tax",
            "Energy",
            "Water",
            "Broadband",
            "Service charge",
        ]

    @pytest.mark.parametrize(
        ("borough", "band", "expected"),
        [
            ("Hackney", "C", HACKNEY_BAND_C),
            ("Hackney", "D", HACKNEY_BAND_D),
            ("Tower Hamlets", "A", TOWER_HAMLETS_BAND_A),
        ],
    )
    def test_council_tax_lookup_matrix(
        self, borough: str, band: str, expected: int
    ) -> None:
        result = estimate_true_monthly_cost(
            rent_pcm=1800, borough=borough, council_tax_band=band
        )
        ct = _get_item(result, "Council tax")
        assert ct["amount"] == expected

    @pytest.mark.parametrize(
        ("epc", "bedrooms", "expected"),
        [
            ("B", 1, ENERGY_B_1BED),
            ("C", 2, ENERGY_C_2BED),
            ("D", 1, ENERGY_D_1BED),
            ("D", 2, ENERGY_D_2BED),
            ("G", 1, ENERGY_G_1BED),
        ],
    )
    def test_energy_lookup_matrix(
        self, epc: str, bedrooms: int, expected: int
    ) -> None:
        result = estimate_true_monthly_cost(
            rent_pcm=1800, epc_rating=epc, bedrooms=bedrooms
        )
        energy = _get_item(result, "Energy")
        assert energy["amount"] == expected
