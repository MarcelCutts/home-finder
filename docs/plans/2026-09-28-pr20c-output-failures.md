# PR20c: Required analysis output and explicit failure outcomes

**Date:** 28 September 2026. **State:** implemented and verified.
**Predecessor:** phase configuration/provenance, `fee3448`.
**Parent:** [PR20 specification](2026-09-28-research-synthesis-and-delivery-plan.md#pr20--isolate-sdk-transport-phase-configuration-and-output-contracts-m).

## Problem and resulting behaviour

The previous evaluation path could catch a malformed response or an exception, merge an empty or partially repaired evaluation with the visual result, and produce a rated assessment. The pipeline then treated the property as analysed and eligible for notification. A provider refusal or output-token limit was not checked before consuming otherwise plausible output. This made incomplete work look successful.

Both required phases must now produce acceptable output before a new assessment is usable. Failure returns an unrated outcome with a typed `analysis_failure`; it cannot turn visual facts alone into a completed assessment. Initial processing retains `pending_analysis`, increments the existing bounded failure counter, records the structured reason in its existing property event, and does not call the notification callback. Existing reanalysis handling keeps the previous usable assessment and pending reanalysis request when the new attempt is incomplete.

The output boundary is intentionally small: [analysis_contracts.py](../../src/home_finder/filters/analysis_contracts.py) validates responses before the historical storage reader's repairs can run. Both phases still use the same SDK, model, prompts, request bodies, reasoning settings and output schemas selected in PR20a/b. This is not a strict-tool-schema experiment or a model promotion.

## Contracts

| Condition | Outcome |
| --- | --- |
| Provider `refusal` stop reason or refusal block | `refusal` |
| `max_tokens` or `model_context_window_exceeded` | `truncated`, even if the partial payload looks complete |
| Unexpected stop reason | `unexpected_stop_reason` |
| Missing/duplicate/wrong-name visual tool; missing required field; invalid type/enum/rating | `malformed_output` |
| Invalid evaluation JSON, duplicate JSON keys, zero/multiple text blocks, wrapped required values | `malformed_output` |
| Phase payload exceeds 500,000 characters | `oversized_output` |
| Provider reports an invalid/unsupported/over-complex schema | `unsupported_schema` |
| Other invalid or forbidden request | `request_rejected` |
| Unexpected application exception | `internal_error` |

Visual output requires exactly one `property_visual_analysis` tool. The established `tool_use` and `end_turn` compatibility remains, but the tool and its required fields must be present in either case. Evaluation requires `end_turn` and one JSON text block. Non-finite numbers, boolean strings, missing required values and invalid categories are rejected. Unused visual extension fields remain tolerated; they cannot fill required omissions. Evaluation enforces its complete schema's extra-field rule.

Unambiguous enum casing is normalised, including nullable enums. No invalid categories are silently removed, object-wrapped required strings unwrapped, or missing booleans replaced. This separates a lossless spelling normalisation from an invented or discarded value. The provider explicitly documents that refusals can return HTTP 200 and bill tokens, output limits can leave incomplete structured output, and enum capitalisation may vary. [Anthropic structured-output documentation](https://platform.claude.com/docs/en/build-with-claude/structured-outputs#invalid-outputs).

`analysis_failure` contains only phase, kind, bounded stop reason and bounded provider request ID. It excludes raw model text and listing content. Runtime-owned provenance, input metadata and failure fields are stripped from model output. Authentication and transient provider failures continue through the existing circuit-breaker path; cancellation still propagates. This slice does not add automatic retries, increase output limits on failure or make another provider call to repair output.

## Usage, compatibility and limits

Provider-reported token and cache usage is accumulated **before** checking the stop reason or validating output. A refusal or malformed billed response therefore remains in the current run's usage totals. HTTP errors without usage do not manufacture token counts. Durable accounting across process failure, every physical retry and ambiguous timeouts is still **PR13**; these in-memory totals are not a spending guarantee.

New provenance uses version **2** to identify required live-output validation. Version 1 remains supported with its previous serialisation and fingerprint; old records without provenance retain `None`. Historical readers retain their bounded repairs. A read-only immutable check loaded all **1,001** assessments from the production copy, leaving both absent metadata fields unknown and preserving SHA-256 `e50897b46ebefea0119fd62951ac277d764684f9590d52801e66c964b1352c63`. No initialisation or migration ran on that database.

The recorded successful two-phase replay matches PR20a's baseline exactly for both request bodies, both parsed responses and all usage counts. Request SHA-256 values remain:

- Visual: `05007bbf2d261d843c31919c2a52daba72e6f3fb6ec202379413aca80323788b`.
- Evaluation: `1be5ce938e29adea05be23dc2e1609b528301e535ef2b267af7e91709fc37511`.

Rejections may increase the pending queue relative to the older lenient behaviour. This is intentional and measurable, but output-shape validity alone does not establish factual accuracy. PR19/21 must qualify actual model quality. PR14 will persist a valid visual checkpoint so an evaluation failure can retain useful visual work without representing the whole assessment as complete or paying for unchanged images again. Until then, ordinary paid processing remains subject to the existing restart gates.

## Verification and handoff

Native HTTP transport tests cover both phases, refusals, truncation, unexpected stops, malformed/missing/duplicate/oversized output, non-finite numbers, schema rejection, permission rejection, case normalisation, nullable values, request IDs and consumed usage. Real SQLite pipeline tests prove that three evaluation failure classes remain pending, consume one failed attempt, preserve usage and emit no notification callback. Older synthetic integration fixtures now satisfy the API schema; tests of historical coercion explicitly exercise historical loading instead of pretending those responses are valid fresh output.

The Python 3.14.7 full suite passes **2,751 tests**, with **87.92%** coverage. Python 3.11.16 passes **2,748** in its full run with **88.03%** coverage; three new tests initially failed because their function-based constructor double was incompatible with eagerly evaluated union annotations. Replacing that double with a subclass fixes all three, and the complete **34-case** new failure suite then passes on both interpreters. No production code changed after the full runs. Ruff lint and formatting (196 files), strict mypy (80 source files), and `git diff --check` pass. Private replay, historical compatibility, logs and coverage are under `/tmp/home-finder-pr20/output-*`. Tests use synthetic fixtures and blocked/mock network access; no paid inference ran.

Next is **PR13 spend accounting/admission**: first the small durable ledger and exact rate/reservation contract, then its enforcement across both native analysis phases and all application entry points. Retain unresolved reservations through crashes and ambiguous outcomes. Do not treat the existing item-count cap as a monetary limit or turn PR20's usage totals into a purported durable ledger.

Rollback should preserve failure/provenance metadata. Do not re-enable the prior partial-assessment completion behaviour as a workaround for a larger pending queue; pause paid work and inspect the typed failures instead. No schema or dependency migration is introduced by this segment.
