# PR13a: Durable paid-attempt ledger and exact reservations

**Date:** 28 September 2026. **State:** implemented and verified.
**Predecessor:** PR20c output failures, `f632dd4`.
**Parent:** [PR13 specification](2026-09-28-research-synthesis-and-delivery-plan.md#pr13--account-for-every-paid-attempt-and-reserve-spend-before-admission-m).

## Scope and delivery boundary

This segment establishes the durable accounting contract. Migration 009 adds `paid_attempts`; [SpendRepository](../../src/home_finder/db/spend.py) admits, dispatches, settles and reconciles attempts through PR04's owned transactions. [Spending models](../../src/home_finder/models/spend.py) define exact amounts, the supported rate card and bounded metadata. A separate operator command reports spending without initialising storage or running the application.

**PR13b must connect this ledger to every physical analysis request, including SDK retries and reanalysis.** The existing analysis path is not yet metered by this foundation alone. No live policy, paid request, collection restart or notification was activated. Chargeable route/browser services still need their provider-specific units, terms and admission boundary before they can be enabled under a common spending policy.

## Amounts and rates

All persisted amounts are integer **USD nanodollars**: one dollar is 1,000,000,000 units. Configuration and billing confirmation accept exact decimal strings/Decimal values; binary floats, booleans, non-finite values, negative values and sub-nanodollar fractions are rejected. No intermediate float arithmetic or budget rounding occurs. Report strings show nine decimal places. Rental prices remain GBP; there is no implicit currency conversion.

The first immutable card is `anthropic-sonnet-4-6-global-standard-2026-09-28-v1`, scoped to the existing Anthropic/Sonnet 4.6 application boundary. Its per-million-token rates are $3 input, $15 output, $3.75 five-minute cache creation, $6 one-hour cache creation and $0.30 cache reads. The provider documents standard pricing across this model's 1M context. Regional/priority/batch execution and charged server tools are outside this card. [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing).

Input, cache reads and cache creation are separate usage categories, so the calculator does not subtract or double-count cached tokens. Five-minute and one-hour writes have distinct fields. Output means the inclusive billed output count, including billed thinking; a decomposition is not charged again. [Anthropic cache accounting](https://platform.claude.com/docs/en/build-with-claude/prompt-caching#tracking-cache-performance).

Admission conservatively reserves a full 1M input context at the highest supported input rate plus the phase's maximum output. With current defaults this is **$6.315** for visual analysis and **$6.06144** for evaluation. These are deliberately generous reservation bounds, not expected property-assessment costs. This avoids treating an estimated token count, an expected cache hit or typical output length as a spending guarantee. A smaller bound requires a separately enforced and verified input contract. Unsupported billing modes must be rejected by PR13b before dispatch.

`SpendPolicy` requires per-call, UTC-day and UTC-month caps, ordered `per_call <= daily <= monthly`; there is no implicit financial allowance. It also bounds active reservations/dispatches and attempts per logical operation. These are the whole application's and the sole supported provider's limits at this stage. Another provider cannot reuse the Anthropic rate card. Future shared/global budgets must aggregate across providers explicitly.

## Lifecycle and recovery

| State / action | Durable result |
| --- | --- |
| Admit | In one `BEGIN IMMEDIATE` transaction, check caps/concurrency/attempt limit/unresolved operation; insert a reservation and frozen metadata. Rejection inserts nothing. |
| Mark dispatched | Commit the transition before the network attempt. A duplicate/stale dispatch is rejected. |
| Cancel before dispatch | Release only a still-`reserved` attempt. This does not consume a dispatched-attempt allowance. |
| Response with usage | Store units, result, request ID and stop reason. Price from the frozen rate card, release the reservation and retain the cost as an estimate based on provider usage. Refusal/validation failure can still cost money. |
| Complete provider error | Store error class/status/request ID and a distinct `provider_error` basis at zero cost. No token counts are invented. The caller must validate an actual complete provider error envelope. |
| Timeout/disconnect/cancellation after dispatch | Mark unknown and keep the reservation. If the process exits before recording uncertainty, `dispatched` remains held. |
| Billing reconciliation | Require an exact amount and a non-secret evidence reference. Store one confirmation, retaining any original usage/estimate. Identical repeats are harmless; conflicting terminal writes are rejected. |

The distinction between a provider error and an ambiguous transport outcome follows the provider's billing guidance: failed API requests are not charged, but client disconnection or timeout can still incur a charge. HTTP status alone or a proxy's error page is not enough to release a reservation. [Anthropic API billing](https://support.claude.com/en/articles/8977456-how-do-i-pay-for-my-claude-api-usage).

All unresolved reservations carry into subsequent day/month windows. Midnight does not erase uncertain spending. Settled amounts belong to the UTC admission period; reports distinguish currently held reservations, current-period committed amounts, lifetime usage estimates and lifetime confirmed amounts. These local admission windows are not a provider invoice's accounting-period definition.

The ledger records the request digest, operation key, phase, property reference, complete PR20 configuration provenance/fingerprint, frozen rates/policy and timestamps. It contains no API key, prompt, image bytes, full response or provider error message. There is deliberately no cascading property/run foreign key: deleting a listing or pruning ordinary event logs must not erase spend evidence. A unique partial index independently prevents two unresolved attempts for one operation.

If reported usage exceeds its reservation, the ledger records the full amount and exposes `over_reservation_count`; it never clips the charge to make a budget appear satisfied. Subsequent admission includes that cost. Local guarantees require one authoritative ledger and all paid application paths passing through it. Provider account spending by other clients, changed rates or unpriced billing modifiers are not bounded by this table.

## Operator report

```sh
uv run python -m home_finder.pipeline.spend_report --database /path/to/properties.db
```

The command requires an existing explicit path, opens SQLite with `mode=ro` and `query_only`, and reads one transaction snapshot. It does not load `.env`/credentials, migrate the database or invoke a processing command. A legacy database without the ledger returns `ledger_available: false` and `historical_cost: unknown`, not a fabricated zero-cost history. Missing paths fail without creating a database.

Unresolved entries include their attempt IDs, phase, state, timestamps, request ID, reason and held amount, sufficient to identify work requiring review. No expiry releases a dispatched/unknown attempt automatically. The repository exposes reconciliation and undispatched release; PR13b will provide the guarded operator action alongside request enforcement. An operator must fence the old worker and establish the remote outcome before confirming an uncertain charge.

## Verification and next segment

Focused verification passes **64 tests** covering the ledger plus existing migration/transaction ownership. Sixteen concurrent admissions through two independent SQLite connections admit exactly two requests under a $12.63 reservation limit. Duplicate operation admission permits one active attempt. Tests cover exact cache pricing, cap validation, daily/monthly rollover, uncertainty across a month boundary, billing confirmation, unchanged original usage, bounded retries, cancellation before admission commit, stale completion, property deletion and read-only reporting.

A real child process commits a dispatch then exits with `os._exit(23)`, without connection cleanup. Reopening retains its $6.315 reservation and denies the same operation. The report CLI is exercised in another process with synthetic data. All external services are mocked; no paid inference runs. The final full suite passes **2,784 tests on Python 3.11.16 and 3.14.7**, with **88.11% / 88.02%** combined line/branch coverage. A test-only SQLite connection warning on 3.14 was corrected with explicit fixture cleanup; the 33 ledger cases were rerun cleanly. No production code changed after those full runs. Ruff lint/format (200 files), strict mypy (83 source files) and `git diff --check` pass. Evidence is under `/tmp/home-finder-pr13/ledger-final-*`.

The installed Anthropic 1.8.0 middleware boundary was also tested independently with native `httpx2.MockTransport`: a complete 429 response followed by success invoked middleware twice, with retry counters 0 and 1; a timeout converted to an explicit uncertainty exception dispatched once despite a configured retry. The probe is `/tmp/home-finder-pr13/middleware-probe.json`. This confirms the next integration approach without replacing the real transport with a test-only alias. [Anthropic SDK middleware](https://platform.claude.com/docs/en/cli-sdks-libraries/middleware).

**Next: PR13b.** Install admission/settlement at that per-attempt boundary for both phases and all three application entry points. Validate billing scope/usage, preserve cancellation and uncertain costs, classify retryable provider errors, make budget exhaustion a deferral, add explicit opt-in caps and guarded reconciliation. Keep ordinary paid processing gated until the complete admission/recovery contract and PR14 checkpoints are ready. Do not interpret this foundation as permission to activate a default dollar budget.

Rollback preserves the ledger and its unknown reservations. Older application versions intentionally reject a newer schema; use a compatible rollback build with spending disabled instead of downgrading or deleting the audit table. Migration tests use temporary databases; the frozen production copy is unchanged.
