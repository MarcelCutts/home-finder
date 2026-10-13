"""Score eligibility and postcode context agree across actual consumers."""

import copy
import json

import pytest
from bs4 import BeautifulSoup
from fastapi import FastAPI
from fastapi.testclient import TestClient

from home_finder.config import Settings
from home_finder.db import PropertyStorage
from home_finder.filters.fit_score import (
    FIT_SCORE_VERSION,
    compute_fit_breakdown,
    compute_fit_score,
    compute_fit_score_and_breakdown,
    compute_lifestyle_icons,
    normalized_fit_inputs,
)
from home_finder.models.analysis import AnalysisFailure
from home_finder.models.delivery import DiscoverySnapshot
from home_finder.models.quality import PropertyQualityAnalysis
from home_finder.notifiers.delivery import discovery_plan
from home_finder.notifiers.telegram import (
    TelegramNotifier,
    format_merged_property_caption,
    format_merged_property_message,
    format_property_message,
)
from home_finder.web.filters import PropertyFilter
from home_finder.web.routes import router
from tests.test_filters.test_fit_score import _full_analysis


def web_client(storage):
    app = FastAPI()
    app.state.storage = storage
    app.state.settings = Settings(_env_file=None, database_path=storage.db_path)
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize("postcode", ["N15 3RH", " n16 3rh ", "E8 3RH", None])
@pytest.mark.parametrize("assessment", ["rated", "unrated", "failed"])
async def test_saved_score_matches_card_detail_map_messages_and_plan(
    make_merged_property, make_quality_analysis, postcode, assessment
):
    merged = make_merged_property(bedrooms=2, postcode=postcode)
    analysis = make_quality_analysis(**_full_analysis())
    if assessment == "unrated":
        analysis = analysis.model_copy(update={"overall_rating": None})
    elif assessment == "failed":
        analysis = analysis.model_copy(
            update={
                "analysis_failure": AnalysisFailure(phase="evaluation", kind="malformed_output")
            }
        )
    original = analysis.model_dump_json()
    score = compute_fit_score(analysis, 2, postcode=postcode)
    assert (score is not None) == (assessment == "rated")
    if postcode and postcode.strip().upper().startswith("N15") and score is not None:
        assert score > compute_fit_score(analysis, 2)
    async with PropertyStorage(":memory:") as storage:
        await storage.save_merged_property(merged)
        await storage.save_quality_analysis(merged.unique_id, analysis)
        async with storage._read_connection() as conn:
            row = await (
                await conn.execute("SELECT fit_score, fit_score_version FROM quality_analyses")
            ).fetchone()
        assert tuple(row) == (score, FIT_SCORE_VERSION)
        markers = await storage.web.get_map_markers(PropertyFilter())
        assert markers[0]["fit_score"] == score
        assert markers[0]["is_assessed"] == (assessment == "rated")

        client = web_client(storage)
        card = client.get(f"/property/{merged.unique_id}/card")
        detail = client.get(f"/property/{merged.unique_id}")
        assert card.status_code == detail.status_code == 200
        card_score = BeautifulSoup(card.text, "html.parser").select_one(".fit-badge")
        detail_score = BeautifulSoup(detail.text, "html.parser").select_one(
            ".fit-breakdown-donut-value"
        )
        if score is None:
            assert card_score is detail_score is None
        else:
            assert (
                card_score.get_text(strip=True) == detail_score.get_text(strip=True) == str(score)
            )

    messages = [
        format_property_message(merged.canonical, quality_analysis=analysis),
        format_merged_property_message(merged, quality_analysis=analysis),
        format_merged_property_caption(merged, quality_analysis=analysis),
    ]
    sender = TelegramNotifier(bot_token="123456:fixture", chat_id=1)
    plan = discovery_plan(sender, DiscoverySnapshot(merged=merged, quality_analysis=analysis))
    messages.append(plan.parts[0].payload["text"])
    for message in messages:
        if score is None:
            assert "Fit " not in message
        else:
            assert f"Fit {score} (" in message
    assert analysis.model_dump_json() == original


@pytest.mark.parametrize("rating", [None, True, False, 0, 6, "4", 4.5])
def test_unrated_and_invalid_ratings_cannot_acquire_default_recommendations(rating):
    analysis = _full_analysis(overall_rating=rating)
    assert normalized_fit_inputs(analysis, postcode="N15 3RH") is None
    assert compute_fit_score_and_breakdown(analysis, 2, postcode="N15 3RH") == (None, None)
    assert compute_fit_breakdown(analysis, 2) is None
    assert compute_lifestyle_icons(analysis, 2) is None


@pytest.mark.parametrize(
    "notes",
    [[], ["No obvious noise concerns"], ["Busy road visible"], ["Insulation cannot be assessed"]],
)
def test_prose_or_its_absence_does_not_change_score_or_claim_acoustic_evidence(notes):
    baseline = _full_analysis(
        space={"is_spacious_enough": False},
        flooring_noise={"hosting_noise_risk": "unknown"},
    )
    changed = copy.deepcopy(baseline)
    changed["flooring_noise"]["noise_indicators"] = notes
    assert compute_fit_score_and_breakdown(changed, 2) == compute_fit_score_and_breakdown(
        baseline, 2
    )
    assert compute_lifestyle_icons(changed, 2) == compute_lifestyle_icons(baseline, 2)
    sound = next(d for d in compute_fit_breakdown(changed, 2) if d["key"] == "sound")
    assert {"label": "Hosting noise risk", "state": "unknown"} in sound["factors"]
    high_risk = copy.deepcopy(changed)
    high_risk["flooring_noise"]["hosting_noise_risk"] = "high"
    assert compute_lifestyle_icons(high_risk, 2)["hosting"]["state"] == "concern"


def test_context_is_derived_from_the_property_without_mutating_evidence():
    analysis = _full_analysis(_area_hosting_tolerance="high")
    before = copy.deepcopy(analysis)
    assert normalized_fit_inputs(analysis)["_area_hosting_tolerance"] is None
    assert normalized_fit_inputs(analysis, postcode=" n16 3rh ")["_area_hosting_tolerance"] == "low"
    assert analysis == before


def test_historical_literal_coercions_are_shared_without_mutating_original_json():
    raw = _full_analysis(flooring_noise={"hosting_noise_risk": "unsure"})
    before = copy.deepcopy(raw)
    decoded = PropertyQualityAnalysis.model_validate(copy.deepcopy(raw))
    assert decoded.flooring_noise.hosting_noise_risk == "unknown"
    assert compute_fit_score_and_breakdown(raw, 2) == compute_fit_score_and_breakdown(decoded, 2)
    assert raw == before
    # A nominal rating cannot make an invalid required assessment shape valid.
    assert compute_fit_score({"overall_rating": 4, "kitchen": "broken"}, 2) is None


async def test_v3_backfill_replaces_only_derived_scores_and_does_not_create_delivery(
    tmp_path, make_merged_property, make_quality_analysis
):
    path = str(tmp_path / "historical.db")
    async with PropertyStorage(path) as storage:
        for name, rating in (("rated", 4), ("fallback", None)):
            merged = make_merged_property(source_id=name, bedrooms=2, postcode="N15 3RH")
            analysis = make_quality_analysis(overall_rating=rating)
            await storage.save_merged_property(merged)
            await storage.save_quality_analysis(merged.unique_id, analysis)
        async with storage._transaction() as conn:
            await conn.execute("UPDATE quality_analyses SET fit_score=99, fit_score_version=2")
            await conn.execute(
                "UPDATE properties SET user_status='archived', notification_status='sent'"
            )
            before_quality = [
                dict(r)
                for r in await (await conn.execute("SELECT * FROM quality_analyses")).fetchall()
            ]
            before_properties = [
                dict(r) for r in await (await conn.execute("SELECT * FROM properties")).fetchall()
            ]
    async with PropertyStorage(path) as storage:
        async with storage._read_connection() as conn:
            after = [
                dict(r)
                for r in await (await conn.execute("SELECT * FROM quality_analyses")).fetchall()
            ]
            properties = [
                dict(r) for r in await (await conn.execute("SELECT * FROM properties")).fetchall()
            ]
            deliveries = await (
                await conn.execute("SELECT * FROM notification_deliveries")
            ).fetchall()
        assert properties == before_properties
        assert deliveries == []
        for old, new in zip(before_quality, after, strict=True):
            assert {
                k: v for k, v in old.items() if k not in {"fit_score", "fit_score_version"}
            } == {k: v for k, v in new.items() if k not in {"fit_score", "fit_score_version"}}
            assert new["fit_score_version"] == 3
            assert new["fit_score"] == compute_fit_score(
                json.loads(new["analysis_json"]), 2, postcode="N15 3RH"
            )
