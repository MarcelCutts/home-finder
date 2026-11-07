# Test suite review: speed, structure and intent

> Historical snapshot. The [integration report](2026-11-07-test-suite-integration.md) describes the implemented combined suite. Counts, paths, timings and worker-cap recommendations below describe the earlier branch or verification state.

7 November 2026. Scope: every test under `tests/`, its shared support and plugins, the
CI test job, and the test guidance in `CLAUDE.md`, `tests/README.md` and
`CONTRIBUTING.md`. Production code changed only where a test needed a seam (one named
constant) and where docstrings were wrong.

## Summary

The default suite now takes **about 12 seconds instead of 2.5 minutes** on this
workstation, and **191 CPU-seconds instead of 2,359**, with combined coverage unchanged
(it rounds to 91%). Measured on the same machine, alternating the original (HEAD
51dd9c6) and changed suites with warm bytecode caches:

| Run | Load average | Wall | CPU | Test time (sum of all tests) |
| --- | --- | --- | --- | --- |
| Original | 3.7 | 150.8 s | 2,359 s | 2,280 s, of which teardown 2,118 s |
| Changed | 16.2 | 12.1 s | 191 s | 64 s |
| Original | 18.3 | 193.8 s | 2,885 s | |
| Changed | 33.5 | 17.6 s | 244 s | |
| Changed, `PYTHONDONTWRITEBYTECODE=1` | 31.9 | 25.3 s | 349 s | 83 s |

The first measurement of the day, in an agent shell with no bytecode at all and a load
average near 47, took 252 s. Coverage runs fell from 286 s to 28 s. The timings above
were taken with 4,024 tests; the difference from 4,251 is duplicates, tests that would
miss the regression they named, and tests of code defined inside the test file.

**Corrections after cross-review.** A parallel Codex audit and its review of this
branch found real errors here; see [the
assessment](2026-11-07-codex-audit-assessment.md).

- The schema template described below never ran when these timings were taken. Its
  emptiness check compared an `aiosqlite.Row` with a tuple. The template now works for
  in-memory databases and saves about 2.3 s of 17 s at 4 workers, not the ~30% first
  claimed.
- The leak check stopped threads without closing their databases.
- Live tests ran with sockets blocked.
- Leak warnings were invisible.

Those and smaller points are fixed. The suite now holds 4,049 tests, including 19
adopted from the Codex work, and runs in about 12 s.

Most of the old run time was not spent testing the application:

| Cause | Cost before | Change |
| --- | --- | --- |
| An autouse fixture ran `gc.collect()` and scanned the whole heap after every test | 0.4–3 s per test; most of the suite's run time | Detect leaked aiosqlite connections by enumerating threads instead |
| structlog's default renderer drew rich tracebacks (highlighted source plus the locals of every frame) for each logged exception, because `rich` is installed as a dev dependency | ~8 s of an 11 s analyzer test; seconds across provider-failure tests | Plain tracebacks for the test session |
| Tests waited out real politeness delays: off-market per-source pauses, Telegram's 1 s pause after each send, OnTheMarket page pauses, the analyzer's batch delay, SDK retry backoff | 16 s for one test; ~30 s across the suite; set the critical path | Record or zero the pauses; assert them explicitly where they are the contract |
| Every fresh `PropertyStorage` ran all 15 migrations | 10–35 ms each for about 1,600 databases | A schema template for in-memory databases (saves about 2.3 s of 17 s at 4 workers) |
| Per-pixel PIL image builders and a badge suite that ran 138 full two-phase analyses to test one pure function | several seconds | Vectorised or cached images; direct tests of `reconcile_analysis_badges` |
| This agent shell sets `PYTHONDONTWRITEBYTECODE=1`, so no dependency was ever compiled to bytecode and pytest re-rewrote every test module in every worker | collection roughly doubles (5.5 s → 13–16 s per process) | `compile-bytecode = true` for `uv sync`; documented opt-out of the variable |

The remaining wall time is mostly fixed per-worker cost: Python start-up plus
importing and collecting the suite (about 5 s; `aiogram.types` alone takes 1.5 s and
`anthropic` 1.2 s), then process shutdown (about 2.5 s). The slowest tests left spawn
real processes to prove crash recovery and CLI isolation (about 1 s each); that is a
property of what they verify, not waste.

The restructuring removed duplicates and tests that could not fail, strengthened
dozens of assertions (about 40 in the web and notifier tests alone), closed several
coverage gaps, and renamed or split files so each is named by the behaviour and
boundary it tests. One flaky test turned out to share an image cache directory with
other workers. The cause, a silently ignored `data_dir=` setting, is fixed there and
in seven other fixtures, and a guard now fails any test that writes into the
repository again.

## Method

The workstation has 16 physical / 32 logical cores and was shared with five other
sessions running pytest (load average 8–49), so wall times are noisy. Findings rest on
CPU time, on profiles of individual tests (`cProfile`, `-X importtime`), on
micro-benchmarks of fixture costs, and on runs that alternate the original and changed
suites under the same load. A new timing report (below) gave per-phase, per-file and
per-worker numbers for every run.

## Where the time went

### 1. The heap scan (largest)

`_cleanup_aiosqlite_threads` guarded against leaked aiosqlite connections, whose
non-daemon worker threads block interpreter exit. After every test it called
`gc.collect()` and iterated `gc.get_objects()`: about 300,000 objects in a fresh
worker with the app imported (0.4–0.7 s), and more in long-lived xdist workers (2–3 s,
which is why trivial tests showed 2–3 s *teardown* times). Every open connection owns
exactly one `_connection_worker_thread`, so enumerating threads finds the same leaks
in microseconds. Removing the scan alone took the suite from 252 s to 74 s wall and
from 3,239 to 541 CPU-seconds. The check now lives in
`tests/plugins/aiosqlite_leaks.py`:

- It compares the threads before and after each test, so a longer-lived fixture's
  connection is left alone.
- It waits briefly for threads that are already closing.
- It closes a real leak with `Connection.stop()`, releasing the SQLite handle and its
  lock.
- It warns with `PytestWarning`; the old `ResourceWarning` was hidden by Python's
  default filters.

Self-tests leak a real connection holding a write lock and check that an older
connection stays usable.

### 2. Bytecode and assertion rewriting

This session's agent shell sets `PYTHONDONTWRITEBYTECODE=1` (it is not in your shell
profile; other agent environments may set it too). The venv therefore had no `.pyc`
files at all, so every pytest process compiled every dependency from source, and
pytest's assertion rewriter, which honours the same flag, re-parsed and re-compiled
about 370 test, conftest and plugin modules in every worker. Collection in one process
took 13–16 s cold, 11 s with dependencies precompiled, and 5.5 s with the rewrite
cache warm. With 16 workers that is minutes of CPU per run. `compile-bytecode = true`
now precompiles dependencies at `uv sync` (every `uv run` then spends ~0.1 s
re-checking and prints a "Bytecode compiled" line). The variable stops caches being
*written*; pytest still reads a valid rewrite cache. One run with the variable cleared
creates it, as `CLAUDE.md` and `tests/README.md` now explain.

### 3. Rich tracebacks

structlog's default `ConsoleRenderer` uses `RichTracebackFormatter(show_locals=True)`
whenever `rich` is importable; here it is, through inline-snapshot and textual. Each
logged exception rendered syntax-highlighted source and the locals of up to 100 frames
into output that pytest captures and discards. One circuit-breaker test spent 8 of its
11 seconds in `rich`. The test session now keeps structlog's default console output
with plain tracebacks. Rendering the suite's ~48,000 other log lines costs only 2–3
test-seconds, so log levels were left alone; several tests assert on debug output.

### 4. Real waits

- `OffMarketChecker.check_batch` waits 2 s between Zoopla requests; the
  circuit-breaker tests waited 16 s and 10 s. They now record the pauses, and the old
  "groups by source with delays" test, which counted calls only, asserts the pacing it
  named.
- Telegram pacing (`asyncio.sleep(1)` after each plan `deliver_pending` delivers, and
  after every send attempt in the pipeline's notify callback) set the suite's critical
  path at about 3 s per recovery test. It is now `telegram.DELIVERY_PAUSE_SECONDS`
  (unchanged, 1.0), zeroed by an autouse fixture, and asserted by
  `test_resumed_delivery_pauses_after_each_delivered_plan`. Patching
  `flow.asyncio.sleep`, as some tests did, replaces the global `asyncio.sleep` and
  would also swallow the `sleep(0)` yields the recovery tests use for ordering.
- OnTheMarket's 0.5 s page pause, the analyzer batch helper's 0.2 s per property, and
  SDK retry backoff on 409/504 are now zeroed or recorded where they are not the
  subject of the test.

### 5. Database setup

1,597 storages were initialised per run, each running 15 migrations in separate
transactions: 9.7 ms unloaded and about 36 ms under xdist.
`tests/plugins/schema_template.py` builds a migrated database once per worker with the
real migrations and copies it into each fresh in-memory database with SQLite's backup
API (0.04 ms).

As first written it never ran. `PropertyStorage` installs `aiosqlite.Row`, which never
equals the tuple its emptiness check compared against, and the guard test compared two
normally migrated databases. The storage-setup drop first attributed to it (57 s → 22
s) came from a change in host load. File databases in WAL mode also did not see a
schema copied on the same connection. So the template now applies only to empty
in-memory databases (about 1,100 of the 1,600), while `MIGRATIONS` matches the
production list.

File databases, upgrade tests, tests that patch `MIGRATIONS` and direct
`run_migrations` calls migrate for real. A self-test now fails if a fresh storage does
not take the fast path. Measured interleaved at 4 workers: 17.0 s without the
template, 14.7 s with it. Pass `-p no:tests.plugins.schema_template` to rule it out.

### 6. Worker count

Each xdist worker repeats start-up and collection (about 5 s of CPU), so start-up
grows with the worker count. Interleaved runs on a quiet machine (load 10–12):

| Workers | Wall | CPU |
| --- | --- | --- |
| 4 | 16.0–16.2 s | 69–71 s |
| 8 | 12.7–13.0 s | 104–106 s |
| 16 (`-n auto` here) | 12.5–12.9 s | 178–180 s |

Eight workers finish as fast as sixteen with 41% less CPU, which matters because
several sessions often run tests on this machine at once. The default is now `-n auto
--maxprocesses=8`; use `-n 4` on a busy machine. The Codex audit chose four, which
saves another third of the CPU for about 26% more wall time.

### What was measured and left alone

- Rendering log lines (2–3 test-seconds) and the per-test `.env` isolation fixture
  (negligible).
- Third-party import time at start-up (aiogram's pydantic models, anthropic): a
  property of the application's dependencies.
- Crash-recovery and CLI-isolation tests that start real processes: they test real
  process exit, file locks and isolation.

## Observability

`pytest --timing-report=PATH` (`tests/plugins/timing_report.py`) records every test's
setup, call and teardown time and worker, writes them to PATH as JSON, and prints:

```text
wall 11.7s since pytest configure = startup 4.2s + tests 5.2s + finish (worker shutdown, session hooks) 2.3s
4049 tests on 8 worker(s): 37.5s of test time, workers inside tests for 90% of the test window, ...
worker busy time: min ..., median ..., max ...
phase totals: setup ..., call ..., teardown ...
per-test total: median ..., p95 ...
slowest directories / files (total, tests, mean, setup+teardown share)
slowest tests (setup / call / teardown)
run context: -n 16 (max 8), hypothesis profile fast, coverage off, load average 4.0 -> 5.1
```

The JSON also records the run's context: Python and pytest versions, worker settings,
Hypothesis profile, coverage, whether the schema template was active, load averages at
start and finish, and CPU seconds of the controller and its finished workers. Saved
reports can therefore be compared like for like. The "inside tests" share is
test-phase wall time, not CPU.

Splitting wall time into start-up, test window and finish exposed the collection cost;
phase totals exposed the heap scan (teardown); worker balance shows whether one long
test sets the critical path. CI runs with `--timing-report=test-timings.json` and
uploads it as the `test-timings` artifact, replacing `--durations=20`. The CI job's
25-minute allowance was set when runs took about 14 minutes; revisit it once the
artifact shows current CI times.

## What each suite tests

Counts and test-seconds come from the alternating runs above (original at load 3.7,
changed at load 16); two more plugin self-tests were added afterwards. Counts moved
between directories as unit tests left `integration/` for the module they test.

| Directory | Boundary and behaviour | Default tests before → after | Test-seconds before → after |
| --- | --- | --- | --- |
| `test_analysis/` | Provider requests on the native SDK transport, response validation, spend admission and settlement, visual checkpoints, prompts, assessment models | 923 → 796 | 505 → 12.0 |
| `test_scrapers/` | Search-page parsing and transport per portal from saved pages, detail fetcher, shared parsing | 617 → 612 | 318 → 1.6 |
| `test_web/` | Dashboard, filters, card and status changes, detail page, cached images, area page, owner access, Telegram webhook (TestClient, in-memory SQLite) | 471 → 471 | 257 → 18.3 |
| `test_db/` | Repositories and queues, migrations, transactions, merge safety, backups, spend ledger | 418 → 391 | 218 → 9.9 |
| `test_filters/` | Criteria, location, media gate, fit score, availability pages, off-market checker, shadow rating | 352 → 345 | 204 → 0.4 |
| `test_pipeline/` | Run coordination per stage, consolidation, queued work, recovery, run accounting, run lock | 311 → 326 | 177 → 10.8 |
| `test_utils/` | Image cache, hashing, EPC and floorplan detection, address, costs, circuit breaker | 312 → 346 | 161 → 2.2 |
| `test_notifiers/` | Message formatting, keyboards, delivery selection, receipts and restart recovery | 168 → 175 | 88 → 2.4 |
| `test_matching/` | Match signals and score, grouping invariants, canonical selection, image evidence, merging | 182 → 165 | 87 → 1.8 |
| root files | CLI dispatch, configuration, core models, resource lifecycle | 132 → 122 | 75 → 0.5 |
| `integration/` | Contracts across stages: full run with external services faked, candidate preparation, cross-run reanalysis, selection and storage, off-market command, schema upgrade | 136 → 40 | 72 → 1.6 |
| `test_enrichment/` | Detail and media acquisition, commute assessment | 85 → 74 | 46 → 1.9 |
| `test_data/` | Area context data and location mappings | 79 → 91 | 40 → 0.1 |
| `test_scripts/` | Maintenance and audit scripts | 65 → 68 | 34 → 0.4 |
| `plugins/` | Self-tests of the suite's own plugins (schema template, leak check) | 0 → 2 | 0 → 0.1 |
| **Total** |  | **4,251 → 4,024** | **2,280 → 64** |

Slow (live network) tests: 52 → 51; the removed one asserted a bedroom-count override
that production dropped in bb92b87, so it could only fail. Browser (Playwright) tests:
81, unchanged: 77 now in `tests/test_web/browser/`, plus four live sort checks in
`integration/test_sort_verification.py`.

### Coverage

Under the original configuration, combined statement and branch coverage of
`src/home_finder` rounds to 91% before and after. It had 962 statements missed instead
of 976, and 472 partial branches instead of 485. Compared line by line (by source
text, since a few production files shifted), three things lost coverage:

- `PropertyStorage._upsert_source_listing`'s "no such table" skip, previously reached
  only incidentally by an old one-line-repair test that called `save_property` on a
  pre-migration schema. That test now exercises the migration directly; the skip
  serves `save_property`, which has no production caller.
- Two dry-run log fields (`images`, `has_floorplan`) in `pipeline/flow.py`.
- `_zoopla_size_from_rsc` in the detail fetcher, which has no production caller; its
  tests now go through the live `_zoopla_area_from_rsc` path.

That configuration under-measured. pytest-cov 7 no longer measures child processes, so
CLI and crash-recovery code counted as unexecuted (`pipeline/backup.py` showed 0%),
and `logging.py` and `data/` were omitted. Coverage now uses coverage.py's `patch =
["subprocess", "_exit"]` and omits nothing. With the CI Hypothesis profile and 4,049
tests, statements are 94.00% (12,903 / 13,727), branch exits 87.00% (4,122 / 4,738)
and combined 92.20%. The Codex branch measures 93.87% / 86.79% / 92.05% with 4,274
tests under the same settings.

Coverage rose in `filters/off_market.py` (80.0% → 84.3%), `matching/deduplication.py`
(95.4% → 97.7%), `db/processing_repo.py` (96.8% → 98.6%), `matching/scoring.py`
(100%), `analysis/analyzer.py` and `web/routes.py`. With subprocess coverage and the
adopted admin CLI tests, the admin modules are now 87–97% and `pipeline/backup.py` is
90%. The least covered are `utils/union_find.py` (0%, dead), `scrapers/rightmove.py`
(78%), `scrapers/detail_fetcher.py` (79%: retry paths) and `utils/listing_identity.py`
(80%).

Every pipeline phase in `pipeline/flow.py` has at least one direct test. The CLI tests
check dispatch with the command functions mocked. `run_scrape_only`, the ward lookup,
the admin command wrappers and Telegram webhook start-up and callback acknowledgement
first ran their own code in tests adopted from the Codex branch. Gaps that remain:

- **Dashboard actions tested only in the browser suite**, which the default run
  excludes and CI runs only on manual dispatch: opening, applying and resetting the
  Filters dialog and its live count, the map toggle and markers, the lightbox, the fit
  popover, themes and mobile layout. The status selector and beds radio state are now
  also asserted in TestClient tests.
- **Routes' failure paths:** no test injects a storage failure into `/`, the card, the
  detail page or the map-marker fallback. The detail page's status history, price
  history table, negotiation card and `og:image` are never rendered by a test.
- **DetailFetcher:** every test replaces the retry wrappers, so the 429 retry and
  exhaustion paths and per-host throttling are untested.
- **CLI:** an accepted `--full-scrape`, `--debug`, exit on invalid settings, the
  thumbnail lock-busy branch, a bare `--reanalyze`, `--check-off-market` without
  `--scrapers`.
- **Scripts:** `scripts/audit_merges.py` deletes rows by default with raw SQL on three
  tables and has no test; `backfill_wards.py` writes to the database untested.
- **Utilities:** `utils/union_find.py` is imported nowhere;
  `listing_identity.listing_id` guards (control characters, userinfo, ports) and
  `utils/async_tasks.py` have no direct tests.

Gaps closed in this pass include: `notification_deliveries` and processing-recovery
history blocking a merge; `reset_failed_analyses` at the attempt limit; the circuit
breaker opening between the visual and evaluation phases; the deduplicator's
hero-thumbnail fallback; off-market redirects to a different listing and the
proxy/impersonation settings; `extract_outcode`, which had no direct test; sort orders
on the Dashboard, previously checked only for a 200.

## Tests that would miss the regression they named

Each of these would pass with the regression it names (one, the live evaluation, could
only fail). All now fail for the regression they describe; several were checked by
injecting the fault from a scratch plugin.

- `/images` directory traversal: httpx removes `/../` before sending, so the request
  hit a router 404 and the guard never ran. It now sends names that reach the guard
  and asserts the 400 body.
- Status changes asserted a label that every response renders (all nine are always
  present). They now assert the trigger's label, the active and checked option, and
  the status history.
- `integration/test_run_pipeline.py` (was `test_run_pipeline_e2e.py`):
  `quality_analysis is not None or len(call.args) > 0` is always true. It now asserts
  each send's assessment, the set of homes and the delivery report.
- `collect_listings` ignores `full_scrape`, so the "full scrape skips known IDs" test
  could not fail; it is now parametrised over both values.
- "Reconnect" tests ran on `:memory:` (a reconnect gets an empty database) and only
  ran `SELECT 1`. They now use a file and read a saved home back.
- A "partially migrated" test never built a partial database; it now builds v8 and
  asserts exactly migrations 9–15 run.
- An off-market "404 is removed" test sent a 410; a bare 404 is deliberately
  unconfirmed. Both are now asserted.
- `test_quality_eval.py` (live) could never reach the API: it had no spend ledger, so
  the analyzer deferred with `missing_usable_images`. It now reaches the API, but its
  10×10 solid images make it a structural smoke test, not an evaluation of analysis
  quality.
- Helpers that copied production algorithms (the acoustic-context mapping, the
  analysis merge in the badge tests) were replaced by calls to production.
- Dashboard badge, chip, radio and map-JSON assertions matched text the page renders
  regardless (modal options, labels, the title).
- The media gate's "passes all when all have floorplans" test passed only because its
  OpenRent listings are exempt from the floorplan requirement.

## Structure, naming and terminology

- **Split by responsibility:** `test_routes.py` (3,200 lines) into Dashboard, filters,
  card and status, detail page, costs, floorplans, cached images, area page, template
  filters and diagnostics. `test_analyzer.py` (3,500 lines) into assessment models,
  value assessment, image blocks, response pairs and analyzer behaviour.
  `test_storage.py` into property rows, source membership, anchor records, location
  backfill and the legacy notification status. `test_orchestration.py` by stage. The
  1,900-line cross-run file by scenario, with unit-level cases moved to
  `test_pipeline`.
- **Renamed by behaviour:** model codenames (`test_sonnet55.py`), tools no longer used
  (`test_quality_respx.py`), time words (`*_current_*`), "e2e" files that were single
  components, and vague names (`test_recovery.py` → `test_queued_work_progress.py`,
  `test_storage_quality.py` → `test_web_queries.py`, `test_deduplication_e2e.py` →
  `test_cross_platform_merge.py`).
- **Placed with the module they test:** address and Cloudflare-challenge tests in
  `test_utils`, area mappings in `test_data`, the media gate in `test_filters`,
  Settings bounds in `test_config.py`, the retired sync script in `test_scripts`.
- **Shared setup:** one `storage` fixture per directory conftest replaced about 25
  copies. Notifier fixtures that shadowed root fixtures with different data are now
  `notification_property` and friends. The pipeline Bot API fake moved from the tests
  root into `support/delivery.py`; the duplicated, diverged `_pipeline_mocks` became
  `support/pipeline_run.py`. Playwright-only fixtures live in `test_web/browser/`.
- **Vocabulary:** the analyzer instance is `analyzer` everywhere; "home" means a
  stored properties row, "stored anchor" a home candidates are matched against,
  "staging row" a pending row retired by absorption;
  `with_usable_media`/`without_usable_media` replace "enriched/failed"; ticket
  numbers, plan steps and "new fields" labels are gone; `PropertyQualityFilter`, which
  does not exist, is gone from docs and tests.
- **Configuration:** `--strict-markers`; the unused `unit` marker removed; dead
  Crawlee state fixtures removed (nothing in `src` imports Crawlee); the slow-test
  timeout hook no longer overrides a test's own longer timeout (it had cut the live
  full-pipeline class from 300 s to 120 s); exported `HOME_FINDER_*` variables no
  longer leak into test Settings (function-scoped tests through the autouse fixture,
  the browser session's server through its own isolation); the browser collection
  rules moved to the root conftest, so browser-marked tests outside
  `test_web/browser/` get sockets when run on their own; `slow` tests now get real
  sockets (43 of 51 had run with Python sockets blocked, so Rightmove's live checks
  could never reach the portal); hostile-input parameters have semantic IDs (one ID
  was 4,000,130 characters); the TravelTime SDK is imported at collection, so
  urllib3's IPv6 probe no longer trips pytest-socket.
- **Browser tests:** the racy `wait_for_htmx_settle` (it registered its listener after
  the action) is replaced by auto-retrying checks on the outcome, and filter, sort and
  gallery tests assert which cards appear and in what order, all ported from the Codex
  branch.

## The flaky test

`test_no_usable_images_remain_pending_without_spend_or_failure_penalty[corrupt_floorplan]`
failed once in about 20 full runs under load. Its fixture passed
`Settings(database_path=":memory:", data_dir=str(tmp_path))`, but `data_dir` is a
property derived from `database_path`, and `Settings` ignores unknown keywords. The
image cache therefore resolved to `./image_cache` in the repository, shared by every
xdist worker, and other workers' homes with the same fixture IDs could appear in it.
Six other test files passed the same ignored keyword; three of them also resolved into
the repository. All now set `database_path` under `tmp_path`, as does the web tests'
shared `settings` fixture. `_keep_data_out_of_the_repository` is a cheap top-level
check that names the fix. It fails a test that creates `image_cache/` or `data/` in
the repository root or working directory, or adds a home directory to a leftover
`image_cache/`. It does not see files written inside an existing home's directory, or
under an existing `data/`, where a local pipeline may write legitimately.

## Findings for the owner

Production behaviour was not changed for any of these.

1. **Prompt bug:** the evaluation prompt reads "35–40 dB dB airborne". The f-string in
   `analyzer.py` (about line 1524) appends " dB" to `area_context.json` values that
   already end in "dB". No test asserts that line.
2. **Code with no production caller** (still tested): `send_property_notification`,
   `send_batch_notifications`, `send_status_message`, `send_price_drop_notification`,
   `format_property_message` (exported); the analyzer's `analyze_merged_properties`
   and `DELAY_BETWEEN_CALLS`, `_extract_eval_json`, `_sanitize_eval_enums`;
   `Deduplicator.deduplicate_and_merge_async`, `scoring.coordinates_match`,
   `scoring.prices_match`; storage's `get_pending_notifications`,
   `get_unsent_notifications`, `mark_notified`, `mark_notification_failed`,
   `filter_new`, `is_seen`, `get_all_properties`, `get_all_known_source_ids`,
   `save_property`, `link_source_listings`, `mark_price_drop_notified`,
   `get_unsent_price_drops`; and, with neither caller nor test, `log_enquiry`,
   `has_enquiry`, `get_enquiries_for_property`, `delete_viewing_message`,
   `utils/union_find.py`. Deleting them would retire dozens of tests.
   `OffMarketChecker._fetch_with_httpx` is unreachable because every source uses
   curl_cffi.
3. **Test setup that bypasses deliveries:** 59 calls in 21 test files use
   `mark_notified`, which writes the legacy status without a `notification_deliveries`
   row. "Sent" in those tests means that projection, not a delivery. Where a scenario
   claims a confirmed send, it should deliver through `DeliveryRepository`; tests
   about the legacy projection itself should keep `mark_notified`.
4. **Dependencies:** `crawlee[beautifulsoup]` is unused by `src`, but it is the only
   thing that installs BeautifulSoup, which five modules import directly. Replacing it
   with `beautifulsoup4` would drop a large dependency tree.
5. **`Settings` ignores unknown keywords** (`extra="ignore"`), which hid the misplaced
   `data_dir=` arguments above.
6. **Status wording:** the card button "Archive" and the status label "Archived" are
   an action and its result, but the Telegram button for the same action says "Skip".
7. **Unreachable or unchecked code:** fit score's "no confident dimensions → None"
   cannot happen for a rated assessment under today's input rules. Separately, the
   analyzer does not re-check the circuit breaker after the visual phase's
   image-building awaits; that is a race window, not a reproduced failure, and wants
   an event-controlled test before any change. (An earlier version of this report also
   called `_MAX_GROUP_SIZE` unreachable. It is reachable, executed by the suite, and
   redundant with the same-source check.)

## Remaining suggestions

- The test-support analyzer builders are still called `quality(...)` in two modules
  with different signatures, and `policy()` exists in three support modules with
  different defaults. Consolidating them would touch about 60 call sites in
  `test_analysis`.
- The default run still imports Playwright through the pytest-playwright entry point;
  `-p no:playwright` with a browser-only opt-in would save a little start-up time.
- Four image-noise helpers remain local to their test files; they differ in channels,
  masks and offsets that the detectors under test depend on.
- `respx` is a dev dependency that no test imports; provider tests use
  `httpx2.MockTransport`.
- The badge wiring test now runs the analyzer for one model instead of two; the
  reconciliation rules are model-agnostic and tested directly.
- `CLAUDE.md` now points to `docs/pipeline.md` for the pipeline sequence; dated
  reviews in `docs/reviews/` still name old test paths, as historical records.

## Verification

- `uv run ruff check src tests`, `uv run ruff format --check src tests` and `uv run
  mypy src` pass.
- After the cross-review fixes, the default suite passes 4,049 tests in 11.7 s, in
  repeated runs at the default worker cap, with no warnings even with
  `ResourceWarning`s shown. Earlier runs, with 4,024 and 4,026 tests, passed every
  time as well.
- The coverage run, with the CI Hypothesis profile and subprocess coverage, passes in
  17.6 s.
- All 77 browser tests pass with Chromium (about 37 s), including with hostile
  `HOME_FINDER_*` variables exported. `-m slow` collects 51 live tests, now with real
  sockets; they were not run against the live portals.
- The schema template's self-tests prove that a fresh in-memory storage takes the fast
  path. They also check that its schema matches a really migrated one, and that file
  databases, non-empty databases and edited migration lists migrate for real. (The
  first version's guard was vacuous; see the corrections in the summary.)
- The leak check's self-tests leak a connection holding a write lock and confirm the
  lock is released, and confirm that an older connection stays usable.
- The repository-root guard was checked by reintroducing the old `:memory:` settings,
  with and without a leftover `image_cache/`; both times it failed the test with its
  message.
- Strengthened assertions were spot-checked by injecting faults (status shown as
  "new", secondary filter count 0, sort and bedroom parameters ignored, the seen
  filter disabled, delivery history unprotected, attempt exhaustion removed,
  reconnecting to `:memory:`): the new tests failed and the old assertions would have
  passed.
- An independent read-only review sampled about 25 deleted or merged tests and 10
  rewritten assertions, and found each deletion covered by a test at least as strong.
  The parallel Codex review found the defects listed at the top; both reviews'
  findings are fixed or recorded in [the
  assessment](2026-11-07-codex-audit-assessment.md).
- CI was not run; the first CI run will report its timings in the `test-timings`
  artifact.
