"""Saved crossings and read-only guarantees for the offline rating candidate."""

import json
import sqlite3
from contextlib import closing
from copy import deepcopy
from pathlib import Path

import pytest

from home_finder.filters.fit_score import FIT_SCORE_VERSION, compute_fit_score
from home_finder.filters.notification_policy import select_discovery
from home_finder.filters.shadow_rating import SHADOW_RATING_POLICY_VERSION
from home_finder.models.notifications import NotificationPolicy
from home_finder.models.quality import PropertyQualityAnalysis
from home_finder.pipeline.rating_shadow_report import compare_rating, main, read_report

FIXTURES = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "shadow_rating_crossings.json").read_text()
)["records"]


def compare(analysis, record=None, **overrides):
    record = record or FIXTURES[4]
    context = {
        "postcode": record["outcode"],
        "user_status": "new",
        "is_off_market": False,
        "policy": NotificationPolicy(),
        **overrides,
    }
    return compare_rating(analysis, record["bedrooms"], **context)


@pytest.mark.parametrize("record", FIXTURES, ids=lambda r: f"{r['case_id']}-{r['arm']}-{r['rep']}")
@pytest.mark.parametrize("as_model", [False, True])
def test_retained_crossings_are_counterfactual_and_leave_live_selection_intact(record, as_model):
    data = deepcopy(record["analysis"])
    analysis = PropertyQualityAnalysis.model_validate(data) if as_model else data
    original = deepcopy(analysis)
    result = compare(analysis, record)
    expected = record["expected"]
    assert result["model_rating"] == expected["model_rating"]
    assert result["shadow"]["candidate_rating"] == expected["candidate_rating"]
    assert result["shadow"]["observed_weight_percent"] == expected["observed_weight_percent"]
    assert result["baseline_selection"]["fit_score"] == expected["baseline_fit_score"]
    assert result["candidate_selection"]["fit_score"] == expected["candidate_fit_score"]
    assert (result["baseline_selection"]["disposition"] == "alert") == expected["baseline_alert"]
    assert (result["candidate_selection"]["disposition"] == "alert") == expected["candidate_alert"]
    assert result["threshold_crossed"] == (
        expected["baseline_alert"] != expected["candidate_alert"]
    )
    assert result["selection_changed"] == result["threshold_crossed"]
    assert analysis == original
    live = select_discovery(
        analysis,
        record["bedrooms"],
        postcode=record["outcode"],
        user_status="new",
        is_off_market=False,
        policy=NotificationPolicy(),
    )
    assert live.model_dump() == result["baseline_selection"]


def test_original_two_model_disagreements_survive_rating_replacement():
    cases = [compare(r["analysis"], r) for r in FIXTURES[:4]]
    assert [c["candidate_selection"]["disposition"] for c in cases] == [
        "dashboard_only",
        "alert",
        "alert",
        "dashboard_only",
    ]


@pytest.mark.parametrize("rating", [None, 0, 6, 4.5, True, "4"])
def test_unrated_or_invalid_assessment_cannot_be_rescued_by_the_shadow_default(rating):
    analysis = deepcopy(FIXTURES[4]["analysis"])
    analysis["overall_rating"] = rating
    result = compare(analysis)
    assert result["baseline_selection"]["reason"] == "assessment_unavailable"
    assert result["shadow"] is None and result["candidate_selection"] is None
    assert not result["threshold_crossed"] and not result["selection_changed"]


@pytest.mark.parametrize("analysis", [None, {}, {"overall_rating": 3}])
def test_missing_assessments_stay_unavailable(analysis):
    result = compare(analysis)
    assert result["shadow"] is None
    assert result["candidate_selection"] is None


def test_failed_assessment_stays_unavailable_even_with_complete_good_observations():
    analysis = deepcopy(FIXTURES[0]["analysis"])
    analysis["analysis_failure"] = {"phase": "evaluation", "kind": "malformed_output"}
    assert compare(analysis)["candidate_selection"] is None


@pytest.mark.parametrize(
    "context,reason",
    [
        ({"user_status": "archived"}, "user_archived"),
        ({"user_status": "rejected"}, "user_rejected"),
        ({"is_off_market": True}, "off_market"),
    ],
)
def test_threshold_crossing_does_not_override_owner_or_market_stops(context, reason):
    result = compare(FIXTURES[4]["analysis"], **context)
    assert result["threshold_crossed"] is True
    assert result["selection_changed"] is False
    assert result["baseline_selection"]["reason"] == reason
    assert result["candidate_selection"]["reason"] == reason
    assert result["candidate_selection"]["disposition"] == "dashboard_only"


def test_explicit_notification_threshold_is_used_in_both_comparisons():
    result = compare(FIXTURES[4]["analysis"], policy=NotificationPolicy(min_fit_score=61))
    assert result["threshold_crossed"] is False
    assert result["candidate_selection"]["fit_score"] == 60
    assert result["candidate_selection"]["min_fit_score"] == 61


def make_database(path, *, corrupt=None):
    # Intentionally omit application tables and use an old schema version:
    # opening PropertyStorage or running migrations would invalidate this fixture.
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript("""
            PRAGMA user_version=11;
            CREATE TABLE properties (unique_id TEXT PRIMARY KEY, bedrooms INTEGER,
                postcode TEXT, user_status TEXT, is_off_market INTEGER);
            CREATE TABLE quality_analyses (property_unique_id TEXT PRIMARY KEY,
                analysis_json TEXT, overall_rating INTEGER, fit_score INTEGER);
            CREATE TABLE notification_deliveries (event_key TEXT PRIMARY KEY, state TEXT);
            INSERT INTO notification_deliveries VALUES ('existing-event', 'sent');
        """)
        for i, record in enumerate(FIXTURES):
            uid = f"fixture:{i}"
            conn.execute(
                "INSERT INTO properties VALUES (?,?,?,?,?)",
                (uid, record["bedrooms"], record["outcode"], "new", 0),
            )
            conn.execute(
                "INSERT INTO quality_analyses VALUES (?,?,?,?)",
                (
                    uid,
                    json.dumps(record["analysis"]),
                    record["expected"]["model_rating"],
                    record["expected"]["baseline_fit_score"],
                ),
            )
        if corrupt is not None:
            conn.execute(
                "UPDATE quality_analyses SET analysis_json=? WHERE property_unique_id='fixture:4'",
                (corrupt,),
            )
        conn.commit()


def test_database_report_is_reproducible_and_leaves_every_stored_byte_unchanged(tmp_path):
    path = tmp_path / "retained.db"
    make_database(path)
    before = path.read_bytes()
    report = read_report(path, NotificationPolicy())
    assert report == read_report(path, NotificationPolicy())
    assert report["rating_policy_version"] == SHADOW_RATING_POLICY_VERSION
    assert report["fit_score_version"] == FIT_SCORE_VERSION
    assert report["mode"] == "offline_rating_shadow"
    assert report["changes_stored_ratings"] is False and report["creates_deliveries"] is False
    assert report["total_homes"] == report["compared_homes"] == 7
    assert report["threshold_crossings"] == report["selection_changes"] == 3
    assert report["coverage"] == {"complete": 3, "partial": 4}
    assert len(report["source_sha256"]) == 64
    assert path.read_bytes() == before
    # Reports contain opaque case keys and decisions, not addresses or model prose.
    text = json.dumps(report)
    assert "Retained scoring observations" not in text and "fixture:" not in text


@pytest.mark.parametrize("corrupt", ["{", "null", "[]", "false", '"not an assessment"'])
def test_corrupt_and_non_object_rows_do_not_abort_or_gain_a_candidate(tmp_path, corrupt):
    path = tmp_path / "corrupt.db"
    make_database(path, corrupt=corrupt)
    before = path.read_bytes()
    report = read_report(path, NotificationPolicy())
    assert report["compared_homes"] == 6
    assert report["threshold_crossings"] == 2
    assert path.read_bytes() == before


def test_source_identity_changes_with_owner_context(tmp_path):
    path = tmp_path / "context.db"
    make_database(path)
    first = read_report(path, NotificationPolicy())
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("UPDATE properties SET user_status='archived' WHERE unique_id='fixture:4'")
        conn.commit()
    second = read_report(path, NotificationPolicy())
    assert first["source_sha256"] != second["source_sha256"]
    assert second["threshold_crossings"] == 3
    assert second["selection_changes"] == 2


def test_missing_database_is_never_created(tmp_path):
    path = tmp_path / "does-not-exist.db"
    with pytest.raises(FileNotFoundError):
        read_report(path, NotificationPolicy())
    assert not path.exists()


def test_cli_needs_no_credentials_and_only_prints_json(tmp_path, monkeypatch, capsys):
    path = tmp_path / "cli.db"
    make_database(path)
    monkeypatch.delenv("HOME_FINDER_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("sys.argv", ["rating_shadow_report", "--database", str(path)])
    before = path.read_bytes()
    main()
    assert json.loads(capsys.readouterr().out)["threshold_crossings"] == 3
    assert path.read_bytes() == before


@pytest.mark.parametrize("threshold", ["-1", "101"])
def test_cli_rejects_invalid_policy_without_creating_database(
    tmp_path, monkeypatch, capsys, threshold
):
    path = tmp_path / "absent.db"
    monkeypatch.setattr(
        "sys.argv",
        ["rating_shadow_report", "--database", str(path), "--minimum-fit-score", threshold],
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert "ValidationError" in capsys.readouterr().err
    assert not path.exists()


def test_low_coverage_crossing_keeps_the_known_risk_in_the_report():
    result = compare(FIXTURES[4]["analysis"])
    assert result["shadow"]["observed_components"] == ("condition", "light")
    assert result["shadow"]["missing_components"] == ("kitchen", "bathroom", "space")
    assert result["shadow"]["coverage"] == "partial"
    assert compute_fit_score(FIXTURES[4]["analysis"], 2, postcode="E8") == 55
