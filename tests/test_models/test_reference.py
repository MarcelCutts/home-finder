"""Reference labels never coerce a guess or a model answer into reviewed fact."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from home_finder.models.reference import CaseAnnotation, PrintedArea, ReferenceFact


def annotation(**changes):
    return {
        "case_id": "case-0123456789abcdef",
        "input_sha256": "a" * 64,
        **changes,
    }


def citation(kind="image"):
    return {
        "material_id": f"{kind}:" + "b" * 64,
        "location": "Upper right",
        "quote": "Total area 500 sq ft",
    }


def reviewed(**changes):
    return annotation(
        reviewer_id="fixture-human",
        reviewed_at=datetime(2026, 9, 28, tzinfo=UTC),
        independent_of_candidate=True,
        **changes,
    )


def test_blank_template_has_no_facts_or_personal_scores():
    label = CaseAnnotation.model_validate(annotation())
    assert not label.independent_of_candidate and label.reviewer_id is None
    assert label.owner_judgment.state == "unreviewed"
    label.validate_material(set())
    with pytest.raises(ValueError, match="Unreviewed"):
        label.validate_material(set(), require_reviewed=True)


@pytest.mark.parametrize(
    "label",
    [
        {"state": "supported", "value": 2},
        {"state": "supported", "evidence": [citation()]},
        {"state": "unknown", "value": 2, "reason": "not printed"},
        {"state": "unknown"},
        {"state": "conflicting", "reason": "Different drawings", "evidence": [citation()]},
        {"state": "unreviewed", "value": 2},
        {"state": "unreviewed", "evidence": [citation()]},
    ],
)
def test_incomplete_or_invented_reference_is_rejected(label):
    with pytest.raises(ValidationError):
        ReferenceFact[int].model_validate(label)


@pytest.mark.parametrize("missing", ["reviewer_id", "reviewed_at", "independent_of_candidate"])
def test_review_requires_independent_human_attestation(missing):
    value = reviewed(facts={"printed_total_area": {"state": "unknown", "reason": "Illegible"}})
    del value[missing]
    with pytest.raises(ValidationError, match="independent reviewer"):
        CaseAnnotation.model_validate(value)


def test_printed_unit_definition_and_exact_conversion_survive():
    value = reviewed(
        facts={
            "printed_total_area": {
                "state": "supported",
                "value": {"amount": "500", "unit": "sq_ft", "definition": "unspecified_total"},
                "evidence": [citation()],
            }
        }
    )
    label = CaseAnnotation.model_validate(value)
    area = label.facts.printed_total_area.value
    assert area.square_metres == Decimal("46.45152000")
    assert area.definition == "unspecified_total" and area.amount == Decimal("500")
    label.validate_material({"image:" + "b" * 64})
    with pytest.raises(ValueError, match="outside"):
        label.validate_material({"image:" + "c" * 64})


@pytest.mark.parametrize("amount", [True, 1.5, "NaN", "Infinity", "0", "-5"])
def test_area_cannot_use_an_inexact_or_invalid_reference(amount):
    with pytest.raises(ValidationError):
        PrintedArea(amount=amount, unit="sq_m", definition="gross_internal")


def test_advertised_facts_require_a_transcription_and_strict_types():
    value = reviewed(
        facts={
            "advertised_pets_allowed": {
                "state": "supported",
                "value": "yes",
                "evidence": [citation("text")],
            }
        }
    )
    with pytest.raises(ValidationError):
        CaseAnnotation.model_validate(value)
    value["facts"]["advertised_pets_allowed"]["value"] = True
    del value["facts"]["advertised_pets_allowed"]["evidence"][0]["quote"]
    with pytest.raises(ValidationError, match="exact evidence transcription"):
        CaseAnnotation.model_validate(value)


def test_personal_unknowns_cannot_become_scores():
    value = reviewed(
        owner_judgment={
            "state": "insufficient_evidence",
            "profile_reference": "owner:wfh-v1",
            "workspace_rating": 1,
            "reason": "No usable layout information",
        }
    )
    with pytest.raises(ValidationError, match="cannot become"):
        CaseAnnotation.model_validate(value)
    value["owner_judgment"]["workspace_rating"] = None
    assert CaseAnnotation.model_validate(value).owner_judgment.retain_for_review is None


def test_label_corrections_and_second_checks_are_explicit():
    with pytest.raises(ValidationError, match="preceding"):
        CaseAnnotation.model_validate(reviewed(revision=2))
    label = CaseAnnotation.model_validate(reviewed(revision=2, supersedes_sha256="c" * 64))
    assert label.revision == 2
    with pytest.raises(ValidationError, match="different reviewer"):
        CaseAnnotation.model_validate(reviewed(adjudicator_id="fixture-human"))
