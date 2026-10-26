# ANALYSIS area findings (analysis/, models/, data/)

(Recorded from the auditor's returned report; the auditor could not write the file itself.)

Prior items: A8 DONE (03f6942). A9 OUTSTANDING (ANALYSIS-4/5, removable with no prompt-byte change). B1 OUTSTANDING (ANALYSIS-9); the "15 validators" from the Feb A1 review are DONE (Annotated aliases quality.py:57-60). B2 PARTIAL (ANALYSIS-6). A3 mostly KEEP (models/__init__ is the main import path). A17/T14 OUTSTANDING (T14 needs product decision: undated RENTAL_BENCHMARKS still drive value.rating).

### ANALYSIS-1: CLAUDE.md "Quality Analysis" section and architecture pointers contradict the code
- Category: STALE-DOC | Confidence: CERTAIN | Action: REWRITE-DOC | Risk: low | Effort: S | Size: ~15 lines
- Location: CLAUDE.md:136-146, 157, 159
- Evidence: model is claude-sonnet-5-5 (config.py:31,39), not 4.6; max_tokens 8192 effort=low sdk_retries=0 (config.py:33-36), not 21000/4096; Phase 2 uses output_config json_schema, not a forced strict tool (analyzer.py:1562-1579); files are analysis/analyzer.py + analysis/prompts.py (filters/quality.py removed 41f499e); failures raise AnalysisOutputError → _create_failed_analysis, no partial visual-only analysis; `{"..."}` stripping removed in 03f6942; 0.2s delay only used by test-only code (prod uses semaphore assessment.py:328); $0.05-0.07 unsupported; PropertyQualityFilter is now PropertyQualityAnalyzer; models.py is now models/ package.
- Recommendation: Rewrite from code; link docs/operations.md "Runtime model defaults".
- Prior item: review line 401 | Owner decision needed: no

### ANALYSIS-2: analyze_merged_properties and DELAY_BETWEEN_CALLS are test-only
- Category: TEST-ONLY | Confidence: HIGH | Action: DELETE | Risk: low | Effort: S | Size: ~30 src + 33 test call sites
- Location: analyzer.py:95-97, 1188-1213
- Evidence: production uses analyze_single_merged (assessment.py:476); 33 call sites all tests (test_analyzer 20, test_quality_respx 6, integration 7); experiments/layout-inference already broken; PR #77 uses analyze_single_merged.
- Recommendation: Migrate tests to analyze_single_merged, delete method, constant, stale "SDK handles retry" comment.
- Prior item: A15 | Owner decision needed: no

### ANALYSIS-3: _extract_eval_json / _sanitize_eval_enums superseded by strict evaluation parsing
- Category: SUPERSEDED | Confidence: HIGH | Action: DELETE | Risk: low | Effort: S | Size: ~25 src + ~55 test
- Location: analyzer.py:730-753; test_analyzer.py:20-22, 3431-3485
- Evidence: 3570e26 replaced calls with contracts.evaluation_json + validate_output(extra=forbid). Only own tests reference them; none on PR #77.
- Recommendation: Delete helpers + their tests (test_analysis_output_failures.py covers the behaviour).
- Prior item: A15 | Owner decision needed: no

### ANALYSIS-4: Always-None council_tax_band_c / energy_estimate plumbing through 4 layers
- Category: DEAD | Confidence: HIGH | Action: SIMPLIFY | Risk: low | Effort: S | Size: ~45 src, ~60 test
- Location: area_context.py:171-239; analyzer.py:1095-1096,1464-1467,1551-1554,1717-1718,1825-1828,1882-1885; prompts.py:330-335,379-381,438,444,524-527
- Evidence: build_property_context hard-codes both None since 3570e26; they only toggle a "withheld" prompt line production never emits ⇒ no prompt-byte / visual_input_key change; pinned request-hash snapshot test_quality_transport.py:28-60 already passes None. PropertyContext.borough read only by tests; `bedrooms` arg unused (ARG001).
- Recommendation: Drop params, borough, bedrooms arg, withheld branch, TestEnergyEstimateInPrompts; convert positional callers of build_user_prompt to keywords first; re-run test_quality_transport + test_runtime_analysis_defaults.
- Prior item: A9 | Owner decision needed: no

### ANALYSIS-5: _build_tool_schema(strict=...) branch never runs
- Category: DEAD | Confidence: HIGH | Action: SIMPLIFY | Risk: low | Effort: S | Size: ~5
- Location: analyzer.py:680-704
- Evidence: one call (707) without strict; line 703 uncovered; VISUAL_ANALYSIS_TOOL bytes unaffected.
- Prior item: A9 | Owner decision needed: no

### ANALYSIS-6: Legacy Sonnet-4.6 defaults remain in runtime signatures (B2 residue)
- Category: COMPAT | Confidence: MEDIUM | Action: SIMPLIFY | Risk: medium (paid request config) | Effort: M | Size: ~20 src, ~55 test constructions
- Location: analyzer.py:779-807, 135; spend.py:88-89; assessment.py:322
- Evidence: only prod constructor create_quality_analyzer always passes settings.analysis_config (never None); enable_extended_thinking ignored when config given; no-config fallback = Sonnet 4.6/21k/sdk_retries=1; ~57/106 test constructions rely on fallback; PR #77 judge.py:105 constructs without config.
- Recommendation: Make analysis_config required (or default to new_runtime_analysis_config()), drop enable_extended_thinking param, make model kwargs required; keep historical-decoding test.
- Prior item: B2 | Owner decision needed: yes

### ANALYSIS-7: Bundled energy/water/broadband/service-charge tables have no consumer
- Category: DEAD | Confidence: HIGH | Action: DELETE | Risk: low | Effort: S | Size: ~29 JSON + ~20 py + ~40 test
- Location: area_context.py:69-74,123-129; area_context.json:771-799
- Evidence: no src/scripts/experiments/PR#77 consumer; test_cost_calculator.py:116-117 patches them only to prove they're ignored; code labels them undated & withheld (area_context.py:207-208).
- Recommendation: Delete constants, ServiceChargeRange, JSON sections, TestCostData; keep negative test patching COUNCIL_TAX_MONTHLY only.
- Prior item: A17/T14 | Owner decision needed: no

### ANALYSIS-8: Area data passed to detail page but never rendered (creative_scene venues etc.)
- Category: DEAD | Confidence: MEDIUM | Action: ASK-OWNER | Risk: low | Effort: S | Size: ~80 JSON + ~6 route lines
- Location: web/routes.py:685-686,698-703; area_context.py:76-91,132; area_context.json:912-992
- Evidence: detail.html never references hosting_tolerance/creative_scene/noise_enforcement/micro_area_count from area_context; top-level CREATIVE_SCENE (~8.5KB venues/rehearsal spaces) reaches no page; HOSTING_TOLERANCE rating/notes live, known_*_areas/label/viewing_checks unread.
- Recommendation: Ask Marcel whether venue data should be shown (music events matter); else drop. Coordinate with WEB-4.
- Prior item: A17 | Owner decision needed: yes

### ANALYSIS-9: Stored-row JSON repair (coerce_json_strings + json-repair dep) may guard impossible rows
- Category: COMPAT | Confidence: MEDIUM | Action: DATA-CHECK then DELETE | Risk: medium (stored assessments) | Effort: S | Size: ~40 src, 1 dep, ~120 test
- Location: models/quality.py:558-621; pyproject.toml:25, 95-97
- Evidence: only writer storage.py:1334 json.dumps(model_dump) emits objects; live path decodes in contracts._canonical_values; quality.py 568, 617-620 uncovered; but migrations.py:172-189 shows one odd shape did reach the DB, pre-63f4271 history unknown.
- Recommendation: Read-only count on real DB: SELECT count(*) FROM quality_analyses WHERE json_valid(analysis_json) AND (json_type(analysis_json,'$.kitchen')='text' OR ... OR json_type(analysis_json,'$.one_line')='object'). If 0, delete string repair + json-repair dep + mypy override; keep tri-state coercions.
- Prior item: B1 | Owner decision needed: yes

### ANALYSIS-10: SQM_PER_SQFT dead; three unused re-exports in models/__init__
- Category: DEAD | Confidence: CERTAIN | Action: DELETE | Risk: low | Effort: S | Size: ~8
- Location: models/core.py:37-38; models/__init__.py:7,26,41,49,53,82
- Evidence: SQM_PER_SQFT zero refs (tree + PR #77), superseded by source_area.SQUARE_METRES_PER_SQUARE_FOOT; CoercedBool/TriStateBool never imported via home_finder.models.
- Prior item: A3/A5 | Owner decision needed: no

### ANALYSIS-11: unused `bedrooms` param of _merge_analysis_results; docstring describes removed override
- Category: DEAD | Confidence: HIGH | Action: SIMPLIFY | Risk: low | Effort: S | Size: ~3 src + 16 test call sites
- Location: analyzer.py:1647-1668, 1894
- Prior item: A9 | Owner decision needed: no

### ANALYSIS-12: Guards for impossible states and stale docstrings in the analysis call chain
- Category: OVERBUILT | Confidence: HIGH | Action: SIMPLIFY | Risk: low | Effort: S | Size: ~15
- Location: analyzer.py:1277, 1851-1852, 1890-1891, 1146, 1688, 1754-1755, 1215-1236
- Evidence: _run_visual_analysis returns dict or raises (NoReturn deferral) → None check at 1852 uncovered; `if not eval_data` at 1891 cannot fire; docstring "None if Phase 1 failed" false; _create_minimal_analysis single caller overwrites its notes.
- Prior item: none | Owner decision needed: no

### ANALYSIS-13: "legacy string format" branches in area_context unreachable
- Category: OVERBUILT | Confidence: HIGH | Action: DELETE | Risk: low | Effort: S | Size: ~4
- Location: area_context.py:350-352, 359
- Evidence: mypy --warn-unreachable flags 352; all 13 bundled JSON entries are dicts; packaged file, not stored data.
- Prior item: A17 | Owner decision needed: no

### ANALYSIS-14: zero-reference members: SourceAreaReading.upper_square_metres, CommuteState.NOT_CONFIGURED
- Category: DEAD | Confidence: CERTAIN | Action: DELETE | Risk: low | Effort: S | Size: ~4
- Location: models/source_area.py:58-60; models/commute.py:21
- Evidence: no refs (tree + PR #77); git log -S"CommuteState.NOT_CONFIGURED" empty → never produced; no DB CHECK constraint.
- Prior item: A5 | Owner decision needed: no

### ANALYSIS-15: TokenUsage is a second lenient usage parser/cost estimate beside the spend ledger
- Category: DUP | Confidence: MEDIUM | Action: ASK-OWNER | Risk: medium (run cost reporting) | Effort: M | Size: ~50
- Location: analyzer.py:111-158; spend.py:88-151; pipeline/flow.py:202-209
- Evidence: add_from_response uses getattr(...) or 0 and prices itself; spend.reported_usage validates strictly for the authoritative ledger (analyzer.py:157 says so); TokenUsage only feeds pipeline_runs.estimated_cost_usd; __post_init__ pricing used only by tests.
- Prior item: A9 | Owner decision needed: yes

### ANALYSIS-16: Mid-file imports left from the split; APIUnavailableError re-exported via analyzer
- Category: HYGIENE | Confidence: HIGH | Action: SIMPLIFY | Risk: low | Effort: S | Size: ~10
- Location: analyzer.py:103-105, 217-222 (noqa E402), 824
- Evidence: no import cycle; APIUnavailableError defined in utils/circuit_breaker but imported from analyzer by assessment.py:9 and ~12 tests.
- Prior item: A3 | Owner decision needed: no

### ANALYSIS-17: Agent residue in comments/docstrings
- Category: AI-ISM | Confidence: HIGH | Action: REWRITE-DOC | Risk: low | Effort: S | Size: ~12
- Location: models/analysis.py:129 ("for PR13/14 to include in their durable work keys"); analyzer.py:224-226 ("models.py"); models/quality.py:321 ("replaces FloorplanFilter logic"), :534 ("New analysis dimensions"), :35-39 & 127-130 (non-strict tool_use narrative), :88-92 ("kept for robustness"); test_token_usage.py:1, test_analyzer.py:2593 ("quality.py")
- Prior item: none | Owner decision needed: no

### ANALYSIS-18: Alias vocabulary SourceListing = Property, PropertyAggregate = MergedProperty
- Category: COMPAT | Confidence: LOW | Action: ASK-OWNER | Risk: low | Effort: M | Size: 2 alias lines, ~61 usages in pipeline/*
- Location: models/core.py:288-291
- Evidence: used only in pipeline/* since 41f499e; everything else uses Property/MergedProperty.
- Prior item: A3 | Owner decision needed: yes

### ANALYSIS-19: analyze_single_merged rebuilds the analysis field-by-field to replace `value`
- Category: OVERBUILT | Confidence: MEDIUM | Action: SIMPLIFY | Risk: low | Effort: S | Size: ~25
- Location: analyzer.py:1130-1168
- Evidence: lists 21 fields; a new field would be silently dropped; model_copy(update=...) equivalent.
- Prior item: none | Owner decision needed: no

## Verified keep (looks dead, isn't)
- `import httpx2` (analyzer.py:88): TYPE_CHECKING annotation at 785; tests use httpx2.MockTransport (~12 files); anthropic 1.11 hard-requires httpx2 and uses httpx2.AsyncClient. Keep declared.
- `follow_redirects` (analyzer.py:847): deliberate, blocks unmetered redirected POSTs.
- Visual/Evaluation response schema fields vulture flags (has_worn_fixtures, area_basis, storage_rating, noise_indicators, parking, furnished_status, ...): sent to model, part of output sha256, all have readers (templates, space_evidence, fit_score, shadow_rating, annotate, brief, telegram).
- floorplan_detected_in_gallery: only logged, but part of prompt contract + schema hash → owner decision.
- Storage mirrors in models/quality.py, ~60 model_config hits and all Pydantic validators: live.
- Persisted provenance fields (prompt_version, *_sha256, input_budget_policy): feed fingerprint & spend keys.
- PropertyType/UserStatus/NotificationStatus.DASHBOARD_ONLY members: stored values / template pills / JS.
- area_context TypedDict fields rendered in area.html/detail.html or read by analyzer.py:1521-1526.
- Three listing_text and three spend modules: legitimate contract / use / persistence layering; no duplicated logic.
- location_mappings.py, borough_outcodes.json, rental_guidance GUIDANCE: live.
- Historical rate cards and claude-sonnet-4-6 AnalysisModel literal: needed to decode history (B2).

## Notes
- experiments/layout-inference/run_inference.py:33,164 and PR #77 (run.py, judge.py, probe_grammar*.py) import home_finder.filters.quality / filters.quality_prompts / filters.listing_text, moved in 41f499e → broken on main.
- test_async_context_managers.py:43 still names TestPropertyQualityFilterContextManager; test_quality_transport.py:46-50 narrates snapshot history.
- Minor: supported_council_tax_band one-line wrapper; contracts.py:108-111 enum-in-anyOf branch never runs; _analysis_provenance() rehashes prompts 4+ times per property.
- Checkpoint identity: changing VISUAL/EVALUATION system prompts, VISUAL_ANALYSIS_TOOL, _eval_json_schema() or visual request content invalidates checkpoints. ANALYSIS-4,5,11,12,19 touch none of these.
