"""Rejected model output exposes useful field paths without exposing its contents."""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

from home_finder.filters.analysis_contracts import AnalysisOutputError, validate_output


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required_flag: bool
    values: list[int]


def test_validation_diagnostics_redact_values_messages_and_unknown_keys():
    with pytest.raises(AnalysisOutputError) as raised:
        validate_output(
            {
                "required_flag": "private-value",
                "values": ["private-list-value"],
                "private-key": "private-extra-value",
            },
            Contract,
            "evaluation",
            SimpleNamespace(_request_id="req_fixture", stop_reason="end_turn"),
        )
    error = raised.value
    assert error.failure.request_id == "req_fixture"
    assert error.failure.kind == "malformed_output"
    assert error.diagnostics == {
        "validation_error_count": 3,
        "validation_errors": [
            {"path": ["required_flag"], "type": "bool_type"},
            {"path": ["values", 0], "type": "int_type"},
            {"path": ["[unknown_field]"], "type": "extra_forbidden"},
        ],
    }
    assert "private" not in str(error.diagnostics)
    assert "private" not in str(error)


def test_validation_diagnostics_are_bounded_but_keep_total_count():
    with pytest.raises(AnalysisOutputError) as raised:
        validate_output(
            {"required_flag": True, "values": ["private"] * 50},
            Contract,
            "evaluation",
            None,
        )
    assert raised.value.diagnostics["validation_error_count"] == 50
    assert len(raised.value.diagnostics["validation_errors"]) == 10


def test_non_object_shape_is_distinguished_without_logging_input():
    with pytest.raises(AnalysisOutputError) as raised:
        validate_output("private", Contract, "evaluation", None)
    assert raised.value.diagnostics == {"reason": "expected_object"}
