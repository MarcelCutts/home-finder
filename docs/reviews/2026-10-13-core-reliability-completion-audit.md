# Core reliability repair audit

13 October 2026. This audits native goal G1 against T02–T09 in the
[architecture assessment](2026-10-13-architecture-assessment-and-tasks.md).
The [execution record](../plans/2026-10-13-goal-driven-implementation.md) contains
the research, decisions and intermediate test results. The original assessment
remains the explanation of what is valuable and what should be deferred.

**Original G1 offline completion checkpoint:** 4,100 default tests passed with
90.94% branch-enabled total coverage; all 63 dashboard browser tests passed.
Lint, formatting, strict typing, release-artifact builds and historical-copy
upgrade/restart checks passed. Protected deployment and live qualification remain
the separate work described below.

These are the original G1 **workspace** totals, which include the pre-existing
offline evaluation prototype. The [PR series record](2026-10-13-reliability-pr-series.md)
distinguishes validation of the isolated published tree and documents the
pre-existing work intentionally excluded from its commits. Subsequent independent
merge review found and corrected two regressions: promotion of queued homes lost
enriched evidence, and alert suppression erased a recipient's saved cooldown.
The series record separates those fixes and their final validation from the
historical checkpoint results retained here.

## Architectural outcome

The changes repair the existing owner application on Python, FastAPI, SQLite,
Jinja/HTMX and Telegram. They do not introduce a second scheduler, queue service,
ORM, distributed lease, frontend framework or parallel persistence model.
Domain-specific decisions are separated where that prevents inconsistent
behaviour: scoring inputs, notification selection, bounded processing recovery
and safe delivery presentation. Transactions still belong to the database
boundary; filesystem recovery uses already-committed source membership.

The most useful improvements are observable behaviour: a valid paid result stays
valid, a failed search cannot look like a healthy empty result, deferred work
survives restart, a refused merge does not damage neighboring homes, uncertain
charges remain counted, a declined alert does not retry forever, and a saved
dashboard query keeps its meaning. File-size reduction was not an acceptance
condition. The obsolete recursive live-output repair path and its implementation
tests were removed because they contradicted validated prose; historical tolerant
decoding and its meaningful regression cases remain.

## Requirement-by-requirement evidence

### T02: paid output and effective configuration

- Strict live phase results reach assembly without recursively interpreting prose
  as JSON. Bracketed strings and nested caller inputs are preserved.
  [Assembly](../../src/home_finder/filters/quality.py) and
  [paid-output regressions](../../tests/test_filters/test_analysis_output_failures.py)
  cover valid prose, failed required phases and retained usage without completed facts.
- New runtime settings and historical model decoding are independent. Empty and
  partial dictionaries merge onto the bounded Sonnet 5.5 profile; a top-level
  null is rejected; an explicit per-phase null input bound retains its deliberate
  full-context meaning. Effective phase configuration is logged at filter setup.
  [Configuration](../../src/home_finder/config.py),
  [runtime tests](../../tests/test_filters/test_runtime_analysis_defaults.py) and
  [operations](../operations.md#runtime-model-defaults) record exact semantics.
- Recognized billing/conflict/timeout envelopes and HTTP 402 quota rejection are
  understood without treating an unknown response as free. Usage, malformed
  output, interrupted transport and unknown charges retain separate outcomes.
  [Request boundary](../../src/home_finder/filters/analysis_spend.py) and
  [transport/accounting tests](../../tests/test_filters/test_analysis_spend.py).
- Historical provenance, model identities and saved rate cards remain readable.
  There was no bulk model-name, assessment JSON or charge rewrite.

**Disposition:** offline requirement met. No claim of current provider billing
accuracy or successful live paid analysis follows from these fixtures.

### T03: startup intent before effects

- Modes are mutually exclusive and modifiers are checked before `Settings`,
  storage initialization, provider work or notifier construction. The
  [dispatch matrix](../../tests/test_cli.py) covers every pair of modes, orphan
  modifiers, accepted commands, help and thumbnail maintenance.
- `--serve` and the application factory default to dashboard-only;
  `--serve --with-pipeline` explicitly enables collection. Existing
  `--serve --no-pipeline` remains valid.
- Actual installed entry-point smoke checks ran from an isolated `/tmp` directory:
  `--help` exited 0; `--serve --dry-run`, `--no-pipeline` and `--all` exited 2 with
  usage. None created the nominated database.

**Disposition:** offline requirement met. Deployment must choose active versus
dormant operation explicitly; the old deployment's startup settings are not
evidence that a new release would run scheduled collection.

### T04: collection health and durable admission

- All four portals return a page envelope containing valid properties plus
  explicit errors. Recognized empty, supported results, challenge, unknown layout,
  invalid records, exhausted transport and cancellation are distinguished.
  [Page contracts](../../tests/test_scrapers/test_search_page_health.py),
  [pagination](../../tests/test_scrapers/test_pagination.py) and persisted scraper
  metrics verify the boundary. A source's exhausted page request stops pagination;
  its existing bounded transport retry policy remains in force.
- Configuration, direct collection and filtering share area preflight.
  Misspelled names fail before scraper construction; supported aliases agree;
  explicitly empty areas disable collection. Postal syntax validation does not
  assert postal existence or exact borough boundaries.
  [Area preflight](../../tests/test_scrapers/test_area_preflight.py).
- Detail work is capped independently of paid analysis: 75 homes by default,
  alternating oldest backlog and fresh candidates. Candidates commit before
  enrichment. Waiting retains cache, attempts and a `run_limit` event; reopening
  storage can drain the backlog without fresh discovery.
  [Admission/restart tests](../../tests/test_pipeline/test_enrichment_admission.py).
- Merge review added [promotion regressions](../../tests/test_pipeline/test_enrichment_persistence.py):
  enriched measurements and their provenance survive prequeueing, promotion and
  restart, including floorplan-dropped multi-source homes. Sparse observations do
  not erase retained evidence, owner history or spend records.

**Disposition:** offline requirement met. This bounds homes, not every HTTP
request. Live source access and parser support still need source-specific
qualification before collection is expanded; fixtures establish neither.

### T05: lifecycle and merge safety

- Processing, personal choices, availability and delivery have separate transition
  rules in [operations](../operations.md#collection-and-delivery). Exhausted initial
  failures and paid-operation ceilings have named persisted reasons; budget
  deferral is not mislabeled as a permanent analysis failure.
- A newly accepted source membership can grant a stranded unassessed anchor one
  additional detail outcome without resetting cumulative attempts, identity,
  personal choices or paid history. The local recovery command is runner-fenced,
  explicit and limited to at most 20 selected homes. Replay cannot renew a grant.
  [Recovery tests](../../tests/test_pipeline/test_processing_recovery.py).
- Each proposed group commits atomically. A refused middle group retains all its
  original rows/memberships while neighboring groups progress. Only accepted
  groups reach analysis; a stale proposal cannot steal a canonical source.
  [Group isolation](../../tests/test_db/test_pipeline_group_isolation.py).
- Proposal construction no longer copies files. After membership commits,
  consolidation publishes each missing file atomically without replacing existing
  destination bytes. Interrupted completion can replay from retained source
  directories without repeating a merge. Temporary copy files are not evidence.
  [Cache recovery](../../src/home_finder/pipeline/cache_recovery.py),
  [merge safety tests](../../tests/test_db/test_merge_safety.py) and
  [atomic-copy tests](../../tests/test_utils/test_image_cache.py).

**Disposition:** offline requirement met. Original source caches are recovery
inputs and must not be deleted casually. Existing corrupt destination files are
not silently overwritten or certified by this repair mechanism.

### T06: execution recovery with retained liability

- Startup recovery requires the current task to own the matching database runner
  fence. It runs once before paid work. Another task, database or live owner cannot
  recover somebody else's request. Process-exit, restart, cancellation and replay
  are exercised in [spend-recovery tests](../../tests/test_pipeline/test_spend_recovery.py).
- Undispatched reservations release both execution occupancy and their hold;
  dispatched work becomes unknown and retains the entire financial reservation.
  The two transitions share a transaction. Unresolved exposure spans accounting
  periods and its oldest age is reported.
- New v2 reservations assume five-minute cache writes and enforce compatible native
  request controls before dispatch. Historical v1 cards remain frozen. The coupled
  request tests reject one-hour/unsupported cache modes. All 206 saved rate cards
  and reservations in the October historical snapshot validated without changing
  its hash.

**Disposition:** offline requirement met. This is a single-host filesystem lock,
not a distributed lease. No assumed maximum-charge settlement was introduced;
uncertain liability still needs evidence-based reconciliation.

### T07: consistent fit and explicit selection

- A common, immutable normalization boundary requires a valid rated, nonfailed
  assessment and derives context from the property postcode. Raw historical JSON
  passes through the same decoder as model objects. Failed/unrated/invalid shapes
  do not receive a fallback recommendation. Noise prose and empty lists do not
  supply acoustic evidence; typed observations still do. This is fit version 3.
- [Cross-consumer tests](../../tests/test_web/test_score_consistency.py) compare
  persistence, grid, map, detail, all three Telegram formatters and new durable
  plans. Backfill preserves original assessment content, personal/financial rows
  and old frozen delivery plans. Raw/model parity also held across all 1,001
  September and 91 October historical assessments.
- The [selection review](2026-10-13-alert-selection-review.md) examines 24 retained
  cases and the provisional inclusive cutoff of 60. All seven historically
  pursued sampled homes remain eligible. The actual production decision function
  replayed read-only over both snapshots: 446/1,016 September homes and 4/93 October
  homes were eligible after saved exclusions. These populations and counts are
  counterfactual history, not an accuracy estimate or a live-volume forecast.
- Assessment completion, first discovery decision and transition commit together.
  Decision version, fit version, score, threshold and reason persist. Suppressed
  homes remain visible as `dashboard_only`; replay, reanalysis and threshold
  changes cannot reopen that first event. Price updates use announced or explicitly
  tracked interest independently of discovery fit. Archive/rejection/off-market
  stops known unsent work while preserving uncertain parts and prior receipts.
  [Selection/rollback/restart tests](../../tests/test_notifiers/test_notification_selection.py).

**Disposition:** offline requirement met. The cutoff is provisional and reversible
for future decisions, not a retroactive rewrite of past decisions. Quality and
relevance calibration remain subsequent evidence work.

### T08: operable durable delivery

- New dashboard links are ordinary URL buttons. Owner-only `/deliveries` presents
  up to 100 oldest unfinished records, with state filtering, part progress,
  saved message IDs, retry timing and fixed safe explanations. It excludes raw
  exceptions, payloads, recipient IDs and operator evidence references.
  [Access/presentation tests](../../tests/test_web/test_delivery_view.py) use the
  actual application boundary and verify the page does not mutate delivery state.
- [Delivery recovery tests](../../tests/test_notifiers/test_delivery_recovery.py)
  cover 429 delay, definitive rejection, response loss, changed assets, missing
  assets, cancellation, restart, incomplete receipts and recipient changes.
  Acknowledged parts and plan assets remain authoritative. The subsequent
  [selection regressions](../../tests/test_notifiers/test_notification_selection.py)
  verify that suppressing a rate-limited alert preserves the recipient's original
  delay across restart, without blocking other recipients or extending its expiry.
- The chosen uncertainty policy remains conservative: no automatic retry allowance
  exists to reset after restart. Local fenced reconciliation can acknowledge actual
  observed IDs, retry a confirmed-undelivered part, or suppress remaining parts.
  No fallback payload revision mechanism was added. An eventual revision must
  retain earlier receipts and explain changes.
- Authenticated local CLI browser evidence covers desktop/mobile recovery views
  with zero console errors and no horizontal mobile overflow. Captures are linked
  in the execution record.

**Disposition:** G1's offline delivery requirement met. The wider T08 live
transport qualification is explicitly outstanding. No message was sent by this
goal's verification. The old sender was retained pending a separately authorized,
bounded real bot/chat qualification; its removal is not silently counted as done.

### T09: dashboard query meaning

- Exact JSON array-element membership is shared by grid, live count and map.
  `Washing machine` does not match `No washing machine`; multiple selected tags
  are conjunctive.
- Pagination serializes the validated object instead of maintaining a template
  list of parameters. A real 28-home database scenario exercises all filter
  fields together, repeated tags, zero values, off-market-only, both size limits
  and last-page clamping. A history-restore request receives a complete page even
  if both HTMX headers are present.
  [Database/HTTP regressions](../../tests/test_web/test_filter_navigation.py).
- Applied server state updates controls, counts and badges. Tested bedroom and
  fit-score bookmark values outside compact menu options survive Apply. Reset,
  history restoration, native numeric validation and correct `morph` registration
  are verified in the actual [browser suite](../../tests/test_web/test_browser_e2e.py),
  including card DOM identity and a working score popover after status mutation.
- The independent CLI flow followed page two, Back, Forward and mobile Reset all
  with 29 synthetic homes. It retained all tested controls, reported zero console
  errors/warnings and fit a 390 px viewport. Screenshots:
  [page two](assets/reliability/filtered-page-two-desktop.png),
  [mobile filters](assets/reliability/filters-mobile.png),
  [mobile reset](assets/reliability/reset-mobile.png).

**Disposition:** requirement met locally. Back/Forward deliberately requests a
fresh full page instead of reusing a DOM snapshot with stale control/map state.
This adds a server round trip and preserves the current application stack.

## Integration gate and preservation audit

- Local verification runtime: Python 3.14.7 and SQLite 3.53.4. Type checking targets
  Python 3.11. The assembled deployment image is a separate G2 qualification.
- Ruff: passed. Ruff formatting: 270 files passed. Strict mypy: 109 source files
  passed. `git diff --check`: passed at the M4 checkpoint.
- Browser suite: **63 passed**, two upstream WebSocket deprecation warnings,
  25.96 seconds. Log: `/tmp/home-finder-goal-m4-browser-final.log`.
- First full default offline suite: **4,088 passed, 12 failed**, with **90.89%**
  branch-enabled total coverage. Failures were stale runtime-default and unrated
  outdoor-icon test assumptions, now corrected without changing application code.
  All **66 tests** in the three affected modules pass, including the correction of
  a test-only SQLite connection leak. The final full confirmation passed **all
  4,100 tests in 472.07 seconds**, with **90.94% branch-enabled total coverage**,
  above the 75% gate. Its five warnings are blocked socket attempts in existing
  commute/cross-run integration cases; no provider result is inferred from them.
  Browser and slow markers remain excluded from this default run; network sockets
  remain blocked. Log: `/tmp/home-finder-goal-full-coverage-final.log`.
- Standard `uv build` succeeded in an isolated build environment, producing a
  wheel and source distribution under `/tmp/home-finder-goal-build`. All ten
  inspected new modules/assets are present, including recovery/selection commands,
  the delivery template and updated JavaScript. The sdist contains source/tests,
  declared project metadata, license/readme and `.gitignore`; it contains no root
  runtime data, screenshots or `.env`. An initial `--no-build-isolation` attempt
  failed because Hatchling was absent from the application environment; using the
  declared build isolation resolved that without changing project dependencies.
- All 33 original untracked roots still exist. The baseline README evaluation
  paragraph, operations delivery-count explanation and `main.py` final-drain
  accounting remain. Compared with the saved starting test file, observability
  tests only gained an explicit zero-threshold fixture policy and its enqueue
  argument; all original behavioural assertions remain.
- Historical schema 12 keeps its original review classification. New migrations
  13–15 add bounded processing/recovery/selection state; they do not reset old
  personal state, charge evidence or delivery receipts. Migration/restart tests
  use temporary databases. Historical replay used read-only connections and
  verified unchanged snapshot hashes. An additional actual upgrade/restart audit
  on disposable export copies established:

  | Export | Upgrade | Original rows preserved, excluding only derived score columns | Repeat initialization |
  | --- | --- | --- | --- |
  | September | Schema 6 → 15 | 65,152 rows in 13 tables | No row changes |
  | October | Schema 12 → 15 | 2,392 rows in 16 tables | No row changes |

  Both upgraded copies passed integrity checks with zero foreign-key violations.
  New columns and tables were included in the repeat-initialization comparison.
  Both original file hashes remained unchanged. Aggregate evidence:
  `/tmp/home-finder-goal-upgrade-audit.json`.
- No deployment, production migration, live portal run, paid analysis or Telegram
  message is established by this verification. Temporary browser/server processes
  were stopped. At this G1 checkpoint no commit had been made; the subsequent
  draft PR series preserves the original workspace and excludes unrelated WIP.

At the original G1 checkpoint, the recorded local evidence satisfied its milestones
without waived failures. The later merge review exposed the two missing regression
cases described above; their fixes and corrected-candidate validation are recorded
in the [PR series](2026-10-13-reliability-pr-series.md#merge-review-corrections-and-final-validation).
The threshold, history-reload and conservative uncertainty decisions remain explicit.
The original application stack, historical evidence and user work remain intact.

## Remaining qualifications and subsequent goals

G1's code is not a production-readiness certificate. The next bounded goal should
cover protected release and operation (G2, T01/T10/T11): inspect the assembled
runtime, restore an isolated backup with ledger/delivery/assets intact, choose
active or dormant scheduling explicitly, deploy only the intended artifacts,
verify anonymous and authenticated access on the deployed service, and establish
useful terminal/absent/stuck-run signals. Current public exposure was contained
by cordoning the old Fly release; do not uncordon that exposed release to restore
availability. The dashboard and webhook remain unavailable through that cordon
until the protected release is qualified.

The checked-in Fly configuration selects the Docker dashboard target, whose command
is `home-finder --serve --no-pipeline`, while keeping one machine running and
autostop disabled. That is an always-on dashboard configuration, not an active
collector. G2 must choose its operating mode explicitly rather than assume the
repository's current deployment settings implement scheduled collection.

Review retained delivery intent before activation. The October export upgrade
correctly preserves 91 queued intents, whereas the new-policy shadow review admits
four of 93 homes. An existing queue is not retroactively selected by the new
cutoff. This is preserved decision history, and a material release consideration:
inspect the actual deployment's queue and reconcile exact events with retained
receipts instead of resetting or silently rewriting it.

Live source and Telegram qualification need bounded execution plans with the
appropriate authorization. Subsequent G3 evidence/availability/geography work
should be driven by actual reviewed failures. G4 multi-user ownership and reusable
observations remain a separate product expansion. These are recorded backlog,
not hidden completion requirements for G1 or reasons to introduce a distributed
architecture into this owner application now.
