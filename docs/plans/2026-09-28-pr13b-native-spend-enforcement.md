# PR13b: Enforce spending at every native analysis attempt

**Date:** 28 September 2026. **State:** implemented and verified.
**Predecessor:** PR13a durable spending ledger, `02093c0`.
**Parent:** [PR13 specification](2026-09-28-research-synthesis-and-delivery-plan.md#pr13--account-for-every-paid-attempt-and-reserve-spend-before-admission-m).

## Behavior and scope

Both visual extraction and evaluation now require a durable ledger and an explicit spending policy before the native Anthropic client can dispatch. This applies to initial processing, queued reanalysis and the standalone reanalysis command. Budget denial leaves work pending, retains an existing assessment and does not increment the initial-analysis failure counter. There is no default financial allowance.

[AnalysisSpendMeter](../../src/home_finder/filters/analysis_spend.py) uses the SDK's public middleware boundary, which runs once for each physical attempt, including SDK retries. A context variable binds the correct property, phase, configuration and output validator to concurrent requests. An operation key includes the property, phase, configuration fingerprint and canonical digest of the actual request, including selected image bytes. An unresolved operation cannot be sent again after a new client or process starts. PR12b will additionally attach durable work/revision IDs. [Anthropic middleware documentation](https://platform.claude.com/docs/en/cli-sdks-libraries/middleware).

Before each send, the meter commits admission and the dispatched transition. The application connection now uses **WAL with `synchronous=FULL`**, including after reconnection. The prior `NORMAL` setting can lose recent commits after a power failure, which is insufficient for a reservation that authorizes an external charge. `FULL` requests a WAL sync at each commit; its guarantee still depends on the filesystem and storage honoring sync requests. The tests check the actual setting and process-crash recovery; they do not simulate physical power loss. [SQLite WAL durability](https://www.sqlite.org/wal.html#performance_considerations).

The client fixes the first-party API base URL and disables redirects, including on an injected HTTP client. A redirected POST cannot create an extra send below the accounting boundary. The meter permits only the currently priced model/request contract, custom visual tool, nonstreaming Messages endpoint and five-minute request cache TTL. It explicitly requests standard service tier and global inference routing. Unknown request fields, billing modes or models defer before dispatch. These two billing fields are the only changes to the recorded successful baseline requests; prompts, selected inputs and reasoning defaults are unchanged.

## Response and failure accounting

| Outcome | Accounting and recovery |
| --- | --- |
| Valid HTTP 200 with complete priced usage | Settle input/output/cache units using the attempt's frozen rate card; retain request ID and stop reason. |
| Refusal, truncation or invalid structured output with valid usage | Record the billed units and typed failed result; PR20c still prevents the assessment from completing. |
| Complete provider error envelope | Record a classified zero-cost provider rejection with no fabricated token counts. The SDK may retry bounded rate-limit/transient errors, with a new reservation for each send. |
| Authentication, quota or unsupported request/schema error | Disable immediate SDK retry even if the provider supplied a retry hint. Persist the rejection class. |
| Timeout, disconnect, cancellation or unreadable response | Retain the reservation; no automatic retry of the uncertain paid operation. |
| Missing/invalid usage, unexpected model/tier/region, charged server tools or redirect | Keep the reservation and request ID where available; stop the batch for review. |
| Ledger failure before dispatch | No HTTP request; stop processing. |
| Ledger failure after dispatch | Keep the previously committed reservation and stop processing. Cancellation remains cancellation even if recording its reason fails. |

The usage contract keeps ordinary input, cache reads, five-minute writes and one-hour writes separate. A reported cache breakdown must equal the total. A missing breakdown can use the five-minute rate only because request TTL is enforced. Output uses the provider's total billed count, including thinking, without charging a separate decomposition again. Unsupported modifiers hold the reservation rather than inventing a price. [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing), [cache accounting](https://platform.claude.com/docs/en/build-with-claude/prompt-caching#tracking-cache-performance).

A completed provider rejection differs from a client timeout: Anthropic documents failed API requests as uncharged, while an otherwise successful request can incur charges despite client disconnection. An intermediary's HTML or status code alone is not sufficient evidence. HTTP 200 refusals and malformed model output are recorded with their usage. [Anthropic API billing](https://support.claude.com/en/articles/8977456-how-do-i-pay-for-my-claude-api-usage).

The SDK follows an exception's cause when deciding whether to retry. The uncertainty exception deliberately does not chain the original transport exception. Tests exercise the actual installed 1.8.0 SDK and `httpx2.MockTransport`; they verify that a timeout causes one physical request even when SDK retries are enabled.

Accounting finishes under the existing cancellation-safe transaction helper before cancellation propagates. Unknown/dispatched reservations remain held across budget windows. A process crash is covered by PR13a's real subprocess interruption test. This segment adds fault injection during native dispatch, response accounting and storage failure. No request/response payload, credentials or private notes are stored in the ledger.

Existing floating-point per-run `TokenUsage` summaries remain compatibility telemetry. They are not the admission authority and cannot establish complete historical costs. The exact attempt ledger and its operator report supply the new financial audit contract.

## Configuration and operator procedure

`HOME_FINDER_ANALYSIS_SPEND_POLICY` accepts a JSON object with exact **USD decimal strings**. The safe disabled example is:

```sh
HOME_FINDER_ANALYSIS_SPEND_POLICY='{"per_call_usd":"0","daily_usd":"0","monthly_usd":"0","max_in_flight":1,"max_attempts_per_operation":2}'
```

Omitting the setting also defers all model requests. Caps must satisfy `per_call <= daily <= monthly`; binary float values are rejected. The current conservative maximum reservations are **$6.315 per visual attempt** and **$6.06144 per evaluation attempt** at default output limits. They bound a full context at the highest supported input rate, not the expected price of an assessment. A cap below the relevant reservation defers that phase even when its typical cost would be much lower. A policy change is explicit operator configuration; no dollar allowance was activated by this implementation.

Inspect held and accounted amounts without loading application settings or credentials:

```sh
uv run python -m home_finder.pipeline.spend_report --database /path/to/properties.db
```

After establishing the remote outcome, record an exact confirmed amount and a non-secret billing reference:

```sh
uv run python -m home_finder.pipeline.spend_admin \
  --database /path/to/properties.db \
  --attempt 0123456789abcdef0123456789abcdef \
  --confirmed-cost-usd '0.125' --evidence-ref 'billing:reference'
```

For a reservation that was **never dispatched**, use `--release-undispatched` instead of the amount/reference. This action rejects dispatched and unknown attempts. Both operator actions require an existing ledger and acquire the application's nonblocking runner lock; an active runner prevents reconciliation. They do not start collection, inference or notifications. Invalid/missing paths do not create a new database. A lock only fences current local cooperating processes; an operator must still establish the remote result and ensure obsolete workers cannot resume.

Daily/monthly exhaustion clears when a new UTC window has capacity, but unresolved holds carry forward. Attempt-limit deferral requires review: caps allow at most three attempts per identical operation, and there is no automatic reset command that erases earlier attempts. PR12b owns durable due times and explicit work retry controls. Later pipeline invocations can retry a settled failure within this lifetime cap; this segment's error-class suppression controls the immediate SDK loop, not the future work scheduler.

## Verification and delivery limits

Final native fault checks pass **46 tests**, including the actual SDK retry loop, concurrent admission, phase-two admission, billing modifiers, unpriced usage, cancellation during settlement, unavailable storage, redirect suppression and all three processing entry points. Existing native/configuration/output/recovery checks and quality/integration suites also pass. The final full suite passes **2,823 tests on both Python 3.11.16 and 3.14.7**, with **88.14% / 88.05%** combined line/branch coverage after the durability change. The 59 focused storage/ledger/ownership checks also pass. Ruff lint/format (204 files), strict mypy (85 source files) and `git diff --check` pass. Evidence is under `/tmp/home-finder-pr13/meter-durable-*`, with focused results in `meter-final314.log` and `meter-durability314.log`. The frozen production copy retains SHA-256 `e50897b46ebefea0119fd62951ac277d764684f9590d52801e66c964b1352c63`.

The request replay checks remove only the asserted standard-tier/global-routing additions before comparing the prior PR20 request digests. The fixture-only allowance is opt-in for four test modules; there is no global test bypass that could mask an unmetered production client. Other unit tests that explicitly replace the whole SDK client remain unit tests rather than evidence for native request admission.

**Remaining boundaries:** this enforces the currently supported Anthropic analysis path. Chargeable route/browser providers require their own usage contract and admission boundary; they cannot claim this model budget. Requests from another application/provider account client or a second database authority are outside these local caps. Invalid usage retains an unresolved cost instead of manufacturing token counts. Valid visual output is not yet a reusable checkpoint: a later retry can repeat a completed first phase within the bounded allowance until PR14 lands. Ordinary paid processing therefore remains gated by PR14 and the operational restart checks.

No paid inference, real notification, production migration, policy activation or collection restart was performed. Historical provenance versions remain readable; new assessments use version 3 to identify admission and billing-scope enforcement. Rollback must keep the ledger, held reservations and metered boundary, or disable analysis completely.

**Next eligible work:** PR19 needs retained evidence and independent human labels before a model comparison. The supplied historical database and single fresh Rightmove capture do not fulfill that reference-set gate. Continue the independent PR05 authority/archive/restore work while it remains open; do not fabricate labels or treat the absent spending policy as permission for calibration.
