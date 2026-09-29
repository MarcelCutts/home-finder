"""Live output validation, separate from lenient historical record loading."""

import json
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from home_finder.models.analysis import AnalysisFailure, AnalysisFailureKind

Phase = Literal["visual", "evaluation"]
MAX_OUTPUT_CHARACTERS = 500_000


class AnalysisOutputError(Exception):
    def __init__(self, failure: AnalysisFailure, diagnostics: dict[str, Any] | None = None) -> None:
        self.failure = failure
        self.diagnostics = diagnostics or {}
        super().__init__(f"{failure.phase}:{failure.kind}")


def failure(
    phase: Literal["visual", "evaluation", "assembly"],
    kind: AnalysisFailureKind,
    response: Any = None,
    *,
    diagnostics: dict[str, Any] | None = None,
) -> AnalysisOutputError:
    stop = getattr(response, "stop_reason", None)
    request_id = getattr(response, "_request_id", None)
    if not isinstance(request_id, str):
        request_id = getattr(response, "request_id", None)
    return AnalysisOutputError(
        AnalysisFailure(
            phase=phase,
            kind=kind,
            stop_reason=stop[:80] if isinstance(stop, str) else None,
            request_id=request_id[:128] if isinstance(request_id, str) else None,
        ),
        diagnostics,
    )


def check_stop_reason(response: Any, phase: Phase) -> None:
    """A complete-looking partial payload is still an unsuccessful phase."""
    stop = getattr(response, "stop_reason", None)
    if stop == "refusal" or any(
        getattr(block, "type", None) == "refusal" for block in response.content
    ):
        raise failure(phase, "refusal", response)
    if stop in {"max_tokens", "model_context_window_exceeded"}:
        raise failure(phase, "truncated", response)
    expected = {"tool_use", "end_turn"} if phase == "visual" else {"end_turn"}
    if stop not in expected:
        raise failure(phase, "unexpected_stop_reason", response)


def _canonical_literals(value: Any, schema: dict[str, Any], defs: dict[str, Any]) -> Any:
    """Only normalise unambiguous enum casing; never invent a missing value."""
    if "$ref" in schema:
        schema = defs[schema["$ref"].rsplit("/", 1)[-1]]
    # Nullable enums are represented as anyOf(enum, null) by Pydantic.
    if isinstance(value, str) and "anyOf" in schema:
        enum_options = [option for option in schema["anyOf"] if "enum" in option]
        if len(enum_options) == 1:
            return _canonical_literals(value, enum_options[0], defs)
    if isinstance(value, str) and "enum" in schema:
        matches = [
            choice
            for choice in schema["enum"]
            if isinstance(choice, str) and choice.casefold() == value.casefold()
        ]
        return matches[0] if len(matches) == 1 else value
    if isinstance(value, dict):
        fields = schema.get("properties", {})
        return {
            key: _canonical_literals(item, fields.get(key, {}), defs) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_canonical_literals(item, schema.get("items", {}), defs) for item in value]
    return value


def validate_output(
    raw: Any,
    model: type[BaseModel],
    phase: Phase,
    response: Any,
) -> dict[str, Any]:
    """Validate required live fields before any historical coercion can run.

    Non-strict visual tool responses can have unused extension fields. Validate
    the requested fields strictly, without using extensions to fill omissions.
    Evaluation uses its complete native schema, including its extra-field rule.
    """
    if not isinstance(raw, dict):
        raise failure(
            phase, "malformed_output", response, diagnostics={"reason": "expected_object"}
        )
    try:
        if len(json.dumps(raw, ensure_ascii=False, allow_nan=False)) > MAX_OUTPUT_CHARACTERS:
            raise failure(phase, "oversized_output", response)
        schema = model.model_json_schema()
        canonical: dict[str, Any] = _canonical_literals(raw, schema, schema.get("$defs", {}))
        # Metadata describing runtime execution is never accepted from model output.
        for name in ("analysis_provenance", "analysis_failure", "listing_text_inputs"):
            canonical.pop(name, None)
        model.model_validate(
            canonical, strict=True, extra="ignore" if phase == "visual" else "forbid"
        )
        if phase == "visual" and not 1 <= canonical["overall_rating"] <= 5:
            raise failure(
                phase, "malformed_output", response, diagnostics={"reason": "invalid_rating"}
            )
        return canonical
    except ValidationError as exc:
        # Error messages/inputs/context can contain model text. Paths can also
        # contain generated dictionary keys: emit only schema-owned field names
        # and array indices, without any response values.
        known_fields: set[str] = set()

        def collect_fields(node: Any) -> None:
            if isinstance(node, dict):
                known_fields.update(node.get("properties", {}))
                for child in node.values():
                    collect_fields(child)
            elif isinstance(node, list):
                for child in node:
                    collect_fields(child)

        collect_fields(schema)
        issues = [
            {
                "path": [
                    part if isinstance(part, int) or part in known_fields else "[unknown_field]"
                    for part in error["loc"][:8]
                ],
                "type": error["type"],
            }
            for error in exc.errors(include_input=False, include_context=False, include_url=False)[
                :10
            ]
        ]
        raise failure(
            phase,
            "malformed_output",
            response,
            diagnostics={"validation_error_count": exc.error_count(), "validation_errors": issues},
        ) from exc
    except (ValueError, TypeError, RecursionError) as exc:
        raise failure(
            phase, "malformed_output", response, diagnostics={"reason": "invalid_output_encoding"}
        ) from exc


def rejected_request(phase: Phase, error: Any) -> AnalysisOutputError:
    """Distinguish a reported schema rejection from other invalid requests."""
    body = getattr(error, "body", None)
    details = body.get("error", body) if isinstance(body, dict) else {}
    message = details.get("message", "") if isinstance(details, dict) else ""
    schema_error = (
        isinstance(message, str)
        and "schema" in message.lower()
        and any(word in message.lower() for word in ("invalid", "unsupported", "complex", "limit"))
    )
    return failure(phase, "unsupported_schema" if schema_error else "request_rejected", error)


def evaluation_json(response: Any) -> Any:
    blocks = [block for block in response.content if getattr(block, "type", None) == "text"]
    if len(blocks) != 1 or not isinstance(blocks[0].text, str):
        raise failure(
            "evaluation",
            "malformed_output",
            response,
            diagnostics={"reason": "expected_one_text_block"},
        )
    text = blocks[0].text
    if len(text) > MAX_OUTPUT_CHARACTERS:
        raise failure("evaluation", "oversized_output", response)

    def unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_json_field")
            result[key] = value
        return result

    try:
        return json.loads(text, object_pairs_hook=unique_fields)
    except (ValueError, RecursionError) as exc:
        raise failure(
            "evaluation",
            "malformed_output",
            response,
            diagnostics={"reason": "invalid_or_duplicate_json"},
        ) from exc
