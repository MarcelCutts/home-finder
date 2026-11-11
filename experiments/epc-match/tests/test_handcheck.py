"""Tests for drawing the hand-check sample."""

from handcheck import draw_sample


def record(uid: str, evidence: str, outcome: str = "one") -> dict:
    return {
        "outcome": outcome,
        "evidence": evidence,
        "listing": {"dataset": "fixture", "unique_id": uid},
    }


def test_sample_is_seeded_and_covers_every_stratum() -> None:
    records = [record(f"a{i}", "number") for i in range(40)]
    records += [record("b0", "flat"), record("c0", "street", outcome="ambiguous")]
    first = draw_sample(records, size=10, seed=1)
    assert first == draw_sample(records, size=10, seed=1)
    assert {r["evidence"] for r in first} == {"number", "flat"}  # single matches only
    assert len(first) == 11  # ten in proportion, plus the lone flat match
