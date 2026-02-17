# A1: Models & Config Review

**Scope:** `models.py` (624L), `config.py` (169L), `data/area_context.py` (305L)
**Total:** 1,098 LOC | **Date:** 2026-02-16

## Executive Summary

The model layer is **well-designed overall** — frozen Pydantic models, clean enums, good validation. The main issues are:
1. **Repetitive backward-compat validators** — 15 identical validator methods across 9 models (boilerplate explosion)
2. **Quality analysis model sprawl** — 13 sub-models in a single file that's really two concerns (core property models + AI analysis schema)
3. **Config string-parsing** — CSV strings parsed at call sites instead of validated on load
4. **Area context module coupling** — tight coupling to JSON structure with no validation on load

No critical issues. The models are the strongest part of the codebase.

---

## Findings

### [MAJOR] models.py:337-555 — Backward-compat validator boilerplate explosion
**Theme:** Duplication | **Effort:** M

There are **15 identical validator methods** across 9 quality analysis models, all delegating to the same 3 module-level functions (`_coerce_bool_to_tristate`, `_coerce_none_to_false`, `_coerce_none_to_unknown`). Each is a 4-line classmethod that just calls the helper:

```python
@field_validator("has_dishwasher", "has_washing_machine", mode="before")
@classmethod
def coerce_bool_to_tristate(cls, v: Any) -> Any:
    return _coerce_bool_to_tristate(v)
```

This pattern repeats at lines: 348-351, 366-369, 384-387, 429-432, 448-451, 465-468, 480-483, 502-505, 507-510, 529-532, 534-537, 551-554, 611-615.

**Recommendation:** Use Pydantic's `Annotated` types to define coercion once:
```python
TriState = Annotated[Literal["yes", "no", "unknown"], BeforeValidator(_coerce_bool_to_tristate)]
OptionalUnknown = Annotated[..., BeforeValidator(_coerce_none_to_unknown)]
```
Then fields become `has_dishwasher: TriState = "unknown"` with zero per-model boilerplate. This eliminates ~90 lines of repetitive code and makes the tri-state pattern self-documenting.

**Migration risk:** Low — the validators are `mode="before"` so `Annotated` + `BeforeValidator` is the direct Pydantic v2 equivalent. Tests already cover the coercion behavior.

---

### [MAJOR] models.py — Quality analysis models should be a separate module
**Theme:** Abstraction / Separation of concerns | **Effort:** M

`models.py` contains two distinct concerns:
1. **Core domain models** (lines 1-232): `Property`, `SearchCriteria`, `MergedProperty`, `TrackedProperty`, `PropertyImage`, enums — used everywhere
2. **Quality analysis schema** (lines 235-625): 13 models (`KitchenAnalysis`, `ConditionAnalysis`, `BathroomAnalysis`, `PropertyQualityAnalysis`, etc.) — used only by `quality.py`, `storage.py`, `telegram.py`, and `routes.py`

The quality analysis models are 63% of the file (390 of 624 lines) and form a cohesive unit with their own enums (`PropertyHighlight`, `PropertyLowlight`, `PropertyType`).

**Recommendation:** Extract to `models/quality.py` (or `quality_models.py` at the same level) with a re-export from the package `__init__` for backward compatibility. The core `models.py` drops to ~230 lines.

---

### [MINOR] models.py:50-58 — SOURCE_BADGES alias adds indirection without value
**Theme:** Naming / Abstraction | **Effort:** S

`SOURCE_BADGES` is just `_SOURCE_META` re-exported under a different name:
```python
SOURCE_BADGES: Final[dict[str, dict[str, str]]] = _SOURCE_META
```
It's only used in `routes.py` (2 call sites). The name "badges" is less descriptive than the actual data structure which contains `name`, `abbr`, and `color`.

**Recommendation:** Either inline `_SOURCE_META` usage at the template level, or better: add `abbr` and `color` as properties on `PropertySource` (like `display_name` already is) and remove the module-level dict exports entirely. The enum becomes the single source of truth:
```python
class PropertySource(StrEnum):
    @property
    def abbr(self) -> str:
        return _SOURCE_META[self.value]["abbr"]

    @property
    def color(self) -> str:
        return _SOURCE_META[self.value]["color"]
```

---

### [MINOR] models.py:57 — SOURCE_NAMES is a derived dict that could be a property
**Theme:** Duplication | **Effort:** S

`SOURCE_NAMES` is `{k: v["name"] for k, v in _SOURCE_META.items()}` — a derived mapping used in 4+ files. Since `PropertySource.display_name` already exists, callers could use `source.display_name` directly instead of `SOURCE_NAMES[source_value]`. The dict exists because templates and storage use string keys, but this could be handled at the boundary.

**Recommendation:** Deprecate `SOURCE_NAMES` dict in favor of `PropertySource.display_name`. Low priority since it works fine, but it's one more thing to keep in sync.

---

### [MINOR] config.py:166-169 — transport_modes hardcoded in get_search_criteria()
**Theme:** Coupling | **Effort:** S

`get_search_criteria()` hardcodes `transport_modes=(TransportMode.CYCLING, TransportMode.PUBLIC_TRANSPORT)` rather than reading from config. There's no `transport_modes` setting on `Settings`.

```python
def get_search_criteria(self) -> SearchCriteria:
    return SearchCriteria(
        ...
        transport_modes=(TransportMode.CYCLING, TransportMode.PUBLIC_TRANSPORT),  # hardcoded
    )
```

**Recommendation:** Either add a `transport_modes` CSV field to `Settings` (like `furnish_types`), or add a comment explaining why it's intentionally hardcoded (Marcel's preference that won't change). Currently it's unclear whether this is an oversight or deliberate.

---

### [MINOR] config.py:96-99,111-114 — CSV string fields parsed at call time
**Theme:** Ergonomics / Validation | **Effort:** S

`furnish_types` and `search_areas` are stored as comma-separated strings and parsed by `get_furnish_types()` / `get_search_areas()`. Invalid `furnish_types` values only fail when `get_furnish_types()` is called, not at config load time.

```python
furnish_types: str = Field(default="unfurnished,part_furnished", ...)
search_areas: str = Field(default="e2,e3,e5,...", ...)
```

**Recommendation:** Add a `@model_validator(mode="after")` to `Settings` that calls these methods eagerly, so invalid config is caught at startup instead of mid-pipeline. This is a 5-line change.

---

### [MINOR] config.py:52-57 — quality_filter_max_images default disagrees with CLAUDE.md
**Theme:** Documentation drift | **Effort:** S

CLAUDE.md says `quality_filter_max_images` defaults to `10`, but `config.py:53` sets `default=20`:
```python
quality_filter_max_images: int = Field(default=20, ge=1, le=20, ...)
```

**Recommendation:** Update CLAUDE.md to say 20 (since code is the source of truth).

---

### [SUGGESTION] models.py:379 — feels_spacious uses bool|None instead of tri-state
**Theme:** Consistency | **Effort:** S

`LightSpaceAnalysis.feels_spacious` and `SpaceAnalysis.is_spacious_enough` use `bool | None` where `None` means "unknown". Every other uncertain field in the quality models uses the tri-state `Literal["yes", "no", "unknown"]` pattern with coercion validators.

```python
feels_spacious: bool | None = None  # None = unknown
is_spacious_enough: bool | None = None  # None = unknown
```

**Recommendation:** Convert to tri-state for consistency. This would also benefit from the `Annotated[TriState]` pattern recommended in the first finding. Low priority but would make the schema uniform.

---

### [SUGGESTION] models.py:593-605 — unwrap_one_line validator compensates for LLM quirks
**Theme:** Complexity / Coupling | **Effort:** S

The `unwrap_one_line` validator on `PropertyQualityAnalysis.one_line` handles the case where Claude wraps text in JSON-like `{"one_line": "text"}` format. This is a workaround for LLM output inconsistency.

**Recommendation:** This is fine as-is for robustness, but consider whether the post-processing in `quality.py` (which already strips `{"..."}` artifacts) should handle this instead of the model. Putting LLM-specific workarounds in the model layer couples models to the AI provider's quirks. Low priority.

---

### [SUGGESTION] models.py:173-180 — TrackedProperty is a thin wrapper with limited value
**Theme:** Abstraction | **Effort:** L

`TrackedProperty` wraps `Property` + commute info + notification status. It's only constructed in `storage.py:_row_to_tracked_property()` and consumed in `telegram.py` for notifications. The class adds a layer of indirection — callers need `tracked.property.price_pcm` instead of `tracked.price_pcm`.

**Recommendation:** Not urgent since it's used consistently, but if `storage.py` gets refactored (A4 session), consider whether a flatter structure (or just returning `Property` + metadata dict) would be simpler. This is a "keep an eye on it" finding, not an action item.

---

### [MINOR] area_context.py:92-93 — JSON loaded at import time with no error handling
**Theme:** Resilience | **Effort:** S

```python
_DATA_PATH = Path(__file__).resolve().parent / "area_context.json"
_DATA = json.loads(_DATA_PATH.read_text())
```

If the JSON file is missing, corrupt, or has unexpected structure, this crashes at import time with an opaque error. Since this module is imported transitively by most of the codebase, a bad JSON file would prevent the entire application from starting.

**Recommendation:** Add a `try/except` around the load with a clear error message:
```python
try:
    _DATA = json.loads(_DATA_PATH.read_text())
except (FileNotFoundError, json.JSONDecodeError) as e:
    raise RuntimeError(f"Failed to load area context data from {_DATA_PATH}: {e}") from e
```

---

### [SUGGESTION] area_context.py:14-89 — Many TypedDicts with no runtime validation
**Theme:** Abstraction | **Effort:** M

There are 9 `TypedDict` classes (`MicroArea`, `AreaContext`, `CrimeInfo`, `RentTrend`, `AcousticProfile`, `NoiseEnforcement`, `ServiceChargeRange`, `HostingTolerance`, `CreativeScene`) that describe the JSON structure, but the JSON is loaded with plain `json.loads()` — no validation that the data matches these types.

The TypedDicts help with IDE autocompletion and type checking, but a typo in the JSON (e.g., `"rtae": 5` instead of `"rate": 5`) would silently produce bad data.

**Recommendation:** Either:
- Keep as-is (TypedDicts are for documentation/IDE support, and the data is hand-curated)
- Add a lightweight startup check: `assert all(key in _DATA for key in ("rental_benchmarks", "area_context", ...))` for the top-level keys
- Switch to Pydantic models if this data grows further (probably overkill for now)

---

### [SUGGESTION] area_context.py:243-305 — match_micro_area scoring could be fragile
**Theme:** Complexity | **Effort:** M

The `match_micro_area()` function uses a multi-signal scoring system (full phrase match: 10 pts, word match: 3 pts, street name from prose: 5 pts) to fuzzy-match addresses to micro-areas. The scoring weights are magic numbers with no tests for edge cases like:
- Address contains words from multiple micro-areas
- Two micro-areas with equal scores (first one wins silently)

**Recommendation:** This is a heuristic and will always be approximate. But adding a few targeted test cases (especially for the ambiguous/tie-breaking scenarios) would increase confidence. The existing test file at `tests/test_data/test_area_context.py` should cover these.

---

### [MINOR] area_context.py:135-183 — WARD_TO_MICRO_AREA is a large hardcoded mapping
**Theme:** Coupling / Maintainability | **Effort:** M

The ward-to-micro-area mapping is a 48-entry hardcoded dict that will need manual updates whenever:
- Search areas change (new outcodes added)
- Ward boundaries are redrawn (happens periodically in London)
- New micro-area definitions are added to the JSON

**Recommendation:** Consider moving this mapping into `area_context.json` alongside the micro-area definitions. This keeps all area data in one place and makes it editable without code changes. The function signatures stay the same — only the data source changes.

---

## Summary by Severity

| Severity | Count | Key themes |
|----------|-------|-----------|
| Critical | 0 | — |
| Major | 2 | Validator boilerplate, file splitting |
| Minor | 6 | Config validation, naming, consistency, JSON loading, data location, doc drift |
| Suggestion | 5 | Tri-state consistency, LLM workaround location, TrackedProperty wrapper, TypedDict validation, scoring tests |

## Top 3 Takeaways

1. **Quick win: Annotated types for tri-state validators** — Eliminates ~90 lines of boilerplate, makes the pattern self-documenting, and reduces the chance of forgetting a validator when adding new fields. Estimated 30-45 min.

2. **Split models.py** — The quality analysis models are a cohesive unit that should live in their own file. This will make both files easier to navigate and reduce cognitive load when working on core property logic vs. AI analysis schema.

3. **Validate config eagerly** — CSV string parsing should fail at startup, not mid-pipeline. A 5-line model validator on `Settings` catches misconfigurations early.
