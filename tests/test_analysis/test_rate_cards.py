"""Rate cards: exact per-model prices and reservations, frozen with each paid attempt."""

import json

import pytest
from pydantic import ValidationError

from home_finder.analysis.analyzer import PropertyQualityAnalyzer
from home_finder.analysis.spend import reported_usage
from home_finder.db import PropertyStorage
from home_finder.models.analysis import AnalysisConfig
from home_finder.models.spend import (
    SONNET_46_FIVE_MINUTE_RATES,
    SONNET_55_FIVE_MINUTE_RATES,
    SONNET_55_STANDARD_RATES,
    SONNET_STANDARD_RATES,
    AttemptSpec,
    RateCard,
    UsageSettlement,
    rate_card_for,
)
from tests.support.analysis_config import configuration, policy
from tests.support.analysis_responses import SONNET55, response
from tests.support.analysis_spend import rows

SONNET46 = "claude-sonnet-4-6"
ALL_CARDS = [
    SONNET_STANDARD_RATES,
    SONNET_55_STANDARD_RATES,
    SONNET_46_FIVE_MINUTE_RATES,
    SONNET_55_FIVE_MINUTE_RATES,
]


def test_rate_cards_price_supported_models_and_reject_unpriced_ones():
    billed = reported_usage(response(), expected_model=SONNET55)
    assert SONNET_55_STANDARD_RATES.cost(billed) == 2_335_000
    assert SONNET_STANDARD_RATES.cost(billed) == 3_547_500
    # The original cards reserve input at the one-hour cache-write rate.
    assert SONNET_STANDARD_RATES.reservation(configuration(SONNET46, SONNET46).visual) == (
        962_880_000
    )
    assert SONNET_55_STANDARD_RATES.reservation(configuration().visual) == 641_920_000
    full = AnalysisConfig.model_validate(
        {"visual": {"model": SONNET55}, "evaluation": {"model": SONNET55}}
    )
    assert SONNET_55_STANDARD_RATES.reservation(full.visual) == 4_210_000_000
    assert SONNET_55_STANDARD_RATES.reservation(full.evaluation) == 4_040_960_000
    for card in ALL_CARDS:
        assert RateCard.model_validate_json(card.model_dump_json()) == card
        active = rate_card_for(card.model)
        assert active.cost(billed) == card.cost(billed)
        assert active.reservation_cache_ttl == "5m"
    assert SONNET_55_FIVE_MINUTE_RATES.reservation(configuration().visual) == 431_920_000
    with pytest.raises(ValueError, match="Unpriced model"):
        rate_card_for("claude-sonnet-5")
    with pytest.raises(ValueError, match="Unpriced model"):
        reported_usage({**response(), "model": "unknown"}, expected_model="unknown")
    with pytest.raises(ValueError, match="Unpriced provider/model"):
        SONNET_STANDARD_RATES.reservation(configuration().visual)


@pytest.mark.parametrize("card", ALL_CARDS)
@pytest.mark.parametrize(
    "field",
    [
        "version",
        "model",
        "input_nanousd",
        "output_nanousd",
        "cache_write_5m_nanousd",
        "cache_write_1h_nanousd",
        "cache_read_nanousd",
    ],
)
def test_a_frozen_card_cannot_mix_models_or_rates(card, field):
    other = SONNET_55_STANDARD_RATES if card.model == SONNET46 else SONNET_STANDARD_RATES
    hybrid = {**card.model_dump(), field: getattr(other, field)}
    with pytest.raises(ValidationError):
        RateCard.model_validate_json(json.dumps(hybrid))


async def test_rate_cards_settle_after_restart_without_using_current_rates(tmp_path, monkeypatch):
    database = str(tmp_path / "rates.db")
    attempts = []
    async with PropertyStorage(database) as storage:
        for model, key in ((SONNET46, "a"), (SONNET55, "b")):
            provenance = PropertyQualityAnalyzer(
                "fixture", analysis_config=configuration(model, model)
            )._analysis_provenance()
            spec = AttemptSpec(
                operation_key=key * 64,
                request_sha256=key * 64,
                property_id=f"fixture:{key}",
                phase="visual",
                provenance=provenance,
            )
            attempt = await storage.spend.admit(spec, policy())
            await storage.spend.mark_dispatched(attempt)
            attempts.append(attempt)
    monkeypatch.setattr(
        "home_finder.db.spend.rate_card_for", lambda _: pytest.fail("Frozen rates required")
    )
    async with PropertyStorage(database) as storage:
        for attempt in attempts:
            await storage.spend.settle_usage(
                attempt,
                UsageSettlement(
                    usage=reported_usage(response(), expected_model=SONNET55), result="success"
                ),
            )
        assert [row["accounted_nanousd"] for row in await rows(storage)] == [3_547_500, 2_335_000]


@pytest.mark.parametrize("field", ["model", "rate_version", "rates_json"])
async def test_frozen_card_must_match_admitted_model_and_version(field):
    async with PropertyStorage(":memory:") as storage:
        spec = AttemptSpec(
            operation_key="a" * 64,
            request_sha256="b" * 64,
            property_id="fixture:rate-identity",
            phase="visual",
            provenance=PropertyQualityAnalyzer(
                "fixture", analysis_config=configuration()
            )._analysis_provenance(),
        )
        attempt = await storage.spend.admit(spec, policy())
        await storage.spend.mark_dispatched(attempt)
        replacement = {
            "model": SONNET46,
            "rate_version": SONNET_STANDARD_RATES.version,
            "rates_json": SONNET_STANDARD_RATES.model_dump_json(),
        }[field]
        async with storage._transaction() as conn:
            await conn.execute(
                f"UPDATE paid_attempts SET {field}=? WHERE attempt_id=?", (replacement, attempt)
            )
        with pytest.raises(ValueError, match="Frozen rate card does not match"):
            await storage.spend.settle_usage(
                attempt,
                UsageSettlement(
                    usage=reported_usage(response(), expected_model=SONNET55), result="success"
                ),
            )
        assert (await rows(storage))[0]["state"] == "dispatched"
        assert (await storage.spend.report())["held_nanousd"] == 431_920_000
