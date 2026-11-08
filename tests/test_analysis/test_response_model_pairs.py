"""Provider response models stay paired with the storage models they validate into.

``_MODEL_PAIRS`` in analysis/analyzer.py maps each sub-model of the tool and output
schemas (``_VisualAnalysisResponse``, ``_EvaluationResponse``) to its counterpart in
models/quality.py.
"""

from typing import Any

import pytest
from pydantic import BaseModel as _BaseModel

from home_finder.analysis.analyzer import (  # type: ignore[attr-defined]
    _MODEL_PAIRS,
    _EvaluationResponse,
    _VisualAnalysisResponse,
)


def _extract_enum(prop: dict[str, Any], defs: dict[str, Any] | None = None) -> list[str] | None:
    """Extract enum values from a JSON schema property.

    Handles plain ``{"enum": [...]}`` as well as ``anyOf`` wrappers that
    Pydantic generates for ``Literal[...] | None``, top-level ``$ref``
    pointers (e.g. ``PropertyType`` StrEnum), and ``$ref`` inside
    ``anyOf`` variants in the storage models.
    """
    if "enum" in prop:
        return sorted(str(v) for v in prop["enum"] if v is not None)

    # Top-level $ref (e.g. StrEnum field without | None)
    if "$ref" in prop and defs:
        ref_name = prop["$ref"].rsplit("/", 1)[-1]
        ref_schema = defs.get(ref_name, {})
        if "enum" in ref_schema:
            return sorted(str(v) for v in ref_schema["enum"] if v is not None)

    for variant in prop.get("anyOf", []):
        if "$ref" in variant and defs:
            ref_name = variant["$ref"].rsplit("/", 1)[-1]
            ref_schema = defs.get(ref_name, {})
            if "enum" in ref_schema:
                return sorted(str(v) for v in ref_schema["enum"] if v is not None)
        if "enum" in variant:
            vals = [str(v) for v in variant["enum"] if v is not None]
            if vals:
                return sorted(vals)

    return None


_PAIR_IDS = [f"{api.__qualname__}-{storage.__name__}" for api, storage in _MODEL_PAIRS]


class TestModelPairConsistency:
    """Verify API response models and storage models stay in sync.

    These tests mechanically check that each pair has matching fields and
    compatible enum values, and that API output validates against the storage model.
    """

    @pytest.mark.parametrize("api_model,storage_model", _MODEL_PAIRS, ids=_PAIR_IDS)
    def test_field_names_match(
        self,
        api_model: type[_BaseModel],
        storage_model: type[_BaseModel],
    ) -> None:
        """API and storage models must have identical field names."""
        api_fields = set(api_model.model_fields.keys())
        storage_fields = set(storage_model.model_fields.keys())
        assert api_fields == storage_fields, (
            f"{api_model.__qualname__} vs {storage_model.__name__}: "
            f"API-only={api_fields - storage_fields}, "
            f"storage-only={storage_fields - api_fields}"
        )

    @pytest.mark.parametrize("api_model,storage_model", _MODEL_PAIRS, ids=_PAIR_IDS)
    def test_literal_enum_values_match(
        self,
        api_model: type[_BaseModel],
        storage_model: type[_BaseModel],
    ) -> None:
        """Literal/enum values must match for every shared field."""
        api_schema = api_model.model_json_schema()
        storage_schema = storage_model.model_json_schema()
        storage_defs = storage_schema.get("$defs", {})

        for field_name in api_model.model_fields:
            api_prop = api_schema.get("properties", {}).get(field_name, {})
            storage_prop = storage_schema.get("properties", {}).get(field_name, {})

            api_enum = _extract_enum(api_prop)
            storage_enum = _extract_enum(storage_prop, defs=storage_defs)

            if api_enum is None and storage_enum is None:
                continue
            if api_enum is None or storage_enum is None:
                # One has enums, the other doesn't — only flag if API has
                # enums that storage doesn't (the reverse is OK since storage
                # may use plain types with coercion).
                if api_enum is not None and storage_enum is None:
                    pytest.fail(
                        f"{api_model.__qualname__}.{field_name}: "
                        f"API has enum {api_enum} but storage has none"
                    )
                continue
            assert api_enum == storage_enum, (
                f"{api_model.__qualname__}.{field_name}: "
                f"API enum {api_enum} != storage enum {storage_enum}"
            )

    def test_all_api_submodels_are_paired(self) -> None:
        """Every nested BaseModel in the API responses must appear in _MODEL_PAIRS."""
        paired_api_models = {pair[0] for pair in _MODEL_PAIRS}
        unpaired_by_design = {_EvaluationResponse.ValueForQuality}

        missing: list[str] = []
        for parent in (_VisualAnalysisResponse, _EvaluationResponse):
            for attr_name in dir(parent):
                attr = getattr(parent, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, _BaseModel)
                    and attr is not parent
                    and attr not in paired_api_models
                    and attr not in unpaired_by_design
                ):
                    missing.append(f"{parent.__name__}.{attr_name}")

        assert not missing, (
            f"API sub-models not in _MODEL_PAIRS (add them or document as "
            f"intentionally unpaired): {missing}"
        )

    @pytest.mark.parametrize("api_model,storage_model", _MODEL_PAIRS, ids=_PAIR_IDS)
    def test_api_data_validates_in_storage(
        self,
        api_model: type[_BaseModel],
        storage_model: type[_BaseModel],
    ) -> None:
        """A valid API model instance must validate in the storage model."""
        # Build a minimal valid instance with all required fields populated
        schema = api_model.model_json_schema()
        props = schema.get("properties", {})
        data: dict[str, Any] = {}

        for field_name, prop in props.items():
            data[field_name] = _minimal_value(prop)

        api_instance = api_model.model_validate(data)
        dumped = api_instance.model_dump()

        # This must not raise — storage model should accept API output
        storage_model.model_validate(dumped)


def _minimal_value(prop: dict[str, Any]) -> Any:
    """Produce a minimal valid value for a JSON schema property."""
    if "enum" in prop:
        return prop["enum"][0]

    # anyOf: pick first variant that isn't null
    if "anyOf" in prop:
        for variant in prop["anyOf"]:
            if variant.get("type") == "null":
                continue
            return _minimal_value(variant)
        return None

    typ = prop.get("type")
    if typ == "string":
        return ""
    if typ == "integer":
        return 0
    if typ == "number":
        return 0.0
    if typ == "boolean":
        return False
    if typ == "array":
        return []
    if typ == "null":
        return None
    return ""
