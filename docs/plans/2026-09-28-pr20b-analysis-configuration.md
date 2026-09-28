# PR20b: Explicit phase settings and assessment provenance

**Date:** 28 September 2026. **State:** implemented and verified.
**Predecessor:** SDK transport migration, `03e3e4c`.
**Parent:** [PR20 specification](2026-09-28-research-synthesis-and-delivery-plan.md#pr20--isolate-sdk-transport-phase-configuration-and-output-contracts-m).

## Behaviour and configuration

Each quality filter now owns an immutable, validated configuration for its visual and evaluation phases. Initial processing, queued reanalysis and the standalone reanalysis command all receive the same settings contract. Defaults preserve PR20a's request hashes; this segment does not select another model, change prompts, run paid comparisons or activate a new live policy.

`HOME_FINDER_ANALYSIS_CONFIG` accepts a JSON object with `version`, `visual` and `evaluation`. Omitted fields expand to these effective defaults:

| Setting | Visual | Evaluation |
| --- | --- | --- |
| `provider` | `anthropic` | `anthropic` |
| `model` | `claude-sonnet-4-6` | `claude-sonnet-4-6` |
| `reasoning` | `adaptive` | `disabled` |
| `effort` | `null` (omit from request) | `null` (omit from request) |
| `max_output_tokens` | 21,000 | 4,096 |
| `request_timeout_seconds` | 240 | 60 |
| `connect_timeout_seconds` | 5 | 5 |
| `sdk_retries` | 1 | 1 |
| `output_contract` | `visual_tool_v1` | `evaluation_schema_v1` |

For example, `HOME_FINDER_ANALYSIS_CONFIG='{"version":1}'` selects the explicit default configuration. Without this setting, `HOME_FINDER_ENABLE_EXTENDED_THINKING=false` still disables visual thinking as before. **Any supplied configuration takes precedence over that legacy flag**, including when `visual` is omitted; there is no ambiguous partial merge with the old boolean. No local environment configuration is changed by this implementation.

Reasoning accepts `adaptive`/`disabled`; effort accepts `null`/`low`/`medium`/`high`. The request adds `output_config.effort` only for an explicit effort, while retaining evaluation's native JSON schema. Adaptive visual reasoning keeps automatic tool choice; disabled visual reasoning keeps the existing forced tool. Disabled thinking retains the baseline's omitted request field for the supported Sonnet model. Effort is a behavioural control, not a hard budget; the provider documents it separately from output-token limits. [Anthropic effort documentation](https://platform.claude.com/docs/en/build-with-claude/effort).

Output tokens must be integers from 1–21,000. Read/write/pool request timeouts must be finite, positive and at most 600 seconds; connect timeouts are at most 60 seconds and cannot exceed the request timeout. A phase can select zero or one SDK retry. The pipeline's existing property deadline separately bounds both phases and all retry delay. Unknown fields/contracts, unsupported provider/model values, boolean token/retry values and invalid timeouts fail configuration validation at startup. Per-phase retry options share one owned native transport; closing the filter closes it.

Only **Anthropic / Sonnet 4.6** is currently admitted. This is deliberate: the existing cost estimator hard-codes that model's rates. Accepting arbitrary model names now would silently misprice their calls. PR13 must supply versioned model/rate accounting, and PR21 must qualify capabilities/quality, before expanding this model/provider boundary. Effort levels and output contracts likewise expand through a reviewed candidate contract. This is explicit configuration of the supported application, not a general model-provider framework.

## Stored provenance and scope

New assessments retain `analysis_provenance` in the existing quality-analysis JSON alongside the already retained `listing_text_inputs`. It contains the expanded configuration, installed SDK version, image limit, an explicit prompt-policy version, and SHA-256 hashes of both static system prompts and output definitions. Text hashes use UTF-8 text; output definitions use sorted JSON. `AnalysisProvenance.fingerprint` hashes the canonical serialised provenance for future durable work keys.

The application constructs this metadata after validating/merging the model response. Model-supplied `analysis_provenance` or `listing_text_inputs` cannot override it or inject a malformed application-owned object. Provenance contains no API key. The effective configuration cannot be rebound midway through a filter's lifetime, preventing an already-created client from disagreeing with its recorded settings.

Historical rows continue to load with `analysis_provenance=None`; the application does not infer which model or settings produced them. A read-only, immutable SQLite check loaded **all 1,001 retained assessments**, all with unknown provenance preserved. The production-copy SHA-256 remains `e50897b46ebefea0119fd62951ac277d764684f9590d52801e66c964b1352c63`. No storage initialisation or migration ran against that copy. The private aggregate is `/tmp/home-finder-pr20/historical-config-compatibility.json`.

This metadata is **configuration provenance**, not a complete inference-input snapshot or attempt ledger. Dynamic context, image-byte identities, the full composed prompts, raw responses and failed attempt usage still need PR13/14. The composed-prompt policy version must change when prompt-building semantics change. The fingerprint is available for those later extraction/attempt keys; those durable tables are not implemented by this slice. Existing incomplete-output behaviour remains for the next PR20 segment.

## Verification and next segment

Focused checks pass **345 tests**, including strict settings validation, legacy precedence, immutable configuration, both native per-phase request overrides, different phase retry settings, stable/default request hashes, provenance identity, protection against model-supplied provenance, a complete mocked assessment with cached images, SQLite roundtrip and historical compatibility. The full suite passes **2,717 tests on both Python 3.11.16 and 3.14.7**, with **87.96% / 87.86%** combined line/branch coverage and no reported warnings. No network or model access occurs in tests. Ruff lint/format pass (194 files), strict mypy passes (79 source files), and `git diff --check` passes. Logs and coverage are under `/tmp/home-finder-pr20/config-*`. There are no dependency, database-schema or dashboard changes requiring repeated package audits or browser tests.

Next: **PR20c typed output failures**. A refusal, truncation, unsupported schema or malformed required evaluation must not become a completed assessment with invented/defaulted facts. Preserve bounded legacy repair for historical reads, add live failure contracts and keep consumed-usage accounting before output rejection. Then continue PR13 spend admission and its durable per-call ledger. A rollback of PR20b leaves the additional JSON metadata in storage; the earlier reader ignores unknown fields and does not require a schema rollback.
