# Reading and extending the tests

Start with the contract you want to understand. The [pipeline guide](../docs/pipeline.md)
explains the production sequence and vocabulary.

| Location | What it demonstrates |
| --- | --- |
| `test_scrapers/` | Search-page parsing and transport per portal, the detail fetcher, shared parsing |
| `test_filters/` | Criteria, location, media admission, fit scoring, availability, off-market checks |
| `test_enrichment/` | Detail/media acquisition and advisory commute assessment |
| `test_matching/` | Match scores, grouping invariants, canonical selection, and image evidence |
| `test_analysis/` | Provider requests, response validation, evidence assembly, and paid-attempt behavior |
| `test_db/` | Persistence, transactions, queues, migrations, and durable state transitions |
| `test_pipeline/` | Coordination, consolidation, recovery queues, run accounting, and resource ownership |
| `test_notifiers/` | Message planning, keyboards, transport receipts, and delivery recovery |
| `test_web/` | Dashboard, detail page, status changes, cached images, and owner access; `browser/` holds the Playwright tests |
| `test_utils/`, `test_data/`, `test_scripts/` | Supporting modules, area data, and maintenance scripts |
| `integration/` | Contracts that connect several subsystems; each module states its boundary |
| `support/` | Reusable builders, fake transports, and storage inspection helpers |
| `plugins/` | SQLite leak detection and migrated schema templates, with regression tests |
| `test_test_support/` | Tests of the opt-in profiler in `support/pytest_profile.py` |

`integration/test_run_pipeline.py` calls the run coordinator with external
services replaced. `integration/test_candidate_preparation.py` exercises candidate
preparation and persistence. `integration/test_selection_persistence.py` connects
selection, matching, and storage. The `e2e` marker currently means several pipeline
stages; it does not imply live services or complete run-lifecycle coverage.

## Names describe the observed contract

Include the distinguishing condition and observable outcome when useful:

- `test_new_source_preserves_assessment_and_requests_reanalysis`
- `test_repeat_observations_do_not_request_another_reanalysis`
- `test_disabled_analysis_persists_home_and_suppresses_discovery_delivery`

A test claiming failure recovery must inject a failure. A test claiming a round
trip must perform both directions. Preserve precise compatibility names when
testing existing settings or stored fields, even if newer code uses clearer terms.

Use `batch`, `summary`, and `analyzer` for those objects. Distinguish an initial
assessment from a requested reanalysis. A processed count may include skipped
analysis; it does not establish a provider-call count.

## Keep the important boundary real

Call production consolidation rather than copying its anchor-selection algorithm
into a helper. Where recovery is the contract, assert durable state and reopen
storage when restart behavior matters. Compare the saved assessment before and
after a merge, since persistence can normalize the original input.

For notification lifecycle tests, keep the real planner and delivery repository
and fake the Bot API using `support/delivery.py`. Callback
invocation, selection eligibility, and confirmed delivery are separate events.
Assert suppression reasons or receipts as appropriate. The legacy
`notification_deferred` diagnostic also covers suppressed deliveries; its metadata
and the durable delivery row identify the actual state.

Synthetic stage summaries and callback fakes remain useful for narrow accounting
tests. Name that boundary explicitly. Private access can also be useful when the
contract is ownership, cancellation, or a particular provider wire format.

## Shared setup and organization

Put reusable builders and fakes in `support/`, with dependencies on application
code or other support modules. Test modules should not provide helpers to other
tests. Keep resource fixtures in the nearest relevant `conftest.py`, and keep the
values that distinguish a scenario visible in the test itself.

Split modules by responsibility. Matching tests separate grouping rules, canonical
selection, and image evidence. A multi-step restart scenario can legitimately be
long; snapshot size and assertion count alone are not reasons to split a test.

Comments explain a non-obvious constraint, fixture choice, or ordering requirement.
Keep them synchronized with the assertions. Avoid repeating the next function
call, and replace temporary labels such as “new fields” or numbered plan steps
with the lasting domain concept. Use explicit event coordination in concurrency
tests when ordering matters.

## Keep tests fast

A default run should spend its time on application code. The optional profiler
separates startup, test execution and shutdown; see [Performance evidence](#performance-evidence).

- Never wait out production pacing or politeness delays. Telegram's
  `DELIVERY_PAUSE_SECONDS` is zeroed for every test; set a scraper's
  `PAGE_DELAY_SECONDS` to 0 or record the off-market checker's pauses. When the
  pause is the contract, record the `asyncio.sleep` call and assert its argument.
- A fresh in-memory `PropertyStorage` reuses a schema template per worker, built by
  the real migrations (`plugins/schema_template.py`). File databases, tests
  that patch `MIGRATIONS` or start from an older schema, and direct `run_migrations`
  calls migrate for real. Pass `-p no:tests.plugins.schema_template` to rule the
  template out; `plugins/test_schema_template.py` proves the fast path is taken.
- Use `:memory:` storage unless the test reopens the database; file databases run
  WAL with full fsync.
- Build test images with vectorised PIL operations or cache them; per-pixel Python
  loops cost seconds.
- Autouse fixtures run around every test, so keep them cheap. A heap scan in one of
  them once accounted for most of the suite's run time.
- Shells that set `PYTHONDONTWRITEBYTECODE=1` stop pytest writing its assertion
  rewrite cache (an existing cache is still read). If no run has ever written it,
  every worker recompiles every test module; run once with the variable cleared
  (`PYTHONDONTWRITEBYTECODE= uv run pytest`) to create it. `uv sync` precompiles
  dependencies (`compile-bytecode` in `pyproject.toml`).
- The default is `-n auto`, with no repository worker cap. xdist chooses workers
  from the physical CPU count and distributes pending tests to available workers.
  This is CPU discovery, not dynamic host-load management. Override with `-n 4`
  when sharing a busy host, or `-n 0` for a small selection. `--dist=loadgroup`
  preserves marked groups (such as browser tests) and normally schedules other tests.
- A warning from the aiosqlite leak check (`plugins/aiosqlite_leaks.py`) means a test
  left a connection open; the check closes it so its lock is released.

Processes that test crash recovery or CLI isolation are legitimately slower;
keep them few and focused.

## Performance evidence

```sh
uv run pytest --test-profile=test-results/local.json --junitxml=test-results/local.xml
uv run python scripts/summarize_test_profile.py test-results/local.json
```

`--timing-report` remains an alias for `--test-profile`. Both select the same
profiler; there is no second timing plugin. CI uploads profiles and JUnit results
as `test-observability` and includes a summary in the job report. Playwright owns
only `test-results/browser/`: its startup cleanup must not erase saved profiles
or JUnit files in the parent directory.

The JSON records phase timings, startup/test/shutdown windows, worker balance,
per-process CPU and peak RSS, inclusive fixture-setup timings, collection, outcomes,
Python and pytest versions, coverage and Hypothesis settings, worker counts,
schema-template use, and host load. Fixture timings can overlap and must not be
added together. Worker occupancy is elapsed time inside test phases, not CPU
utilization. Per-process CPU excludes subprocesses started by tests; peak RSS
values are per process, not a simultaneous total. Wall/CPU counters start at
pytest configuration, after early imports; the profile ends in the session-finish
hook, before final reporting and process exit. Use an external process timer when
comparing the complete CPU and elapsed cost of worker counts.

Compare repeated, interleaved runs with the same flags and warm-cache conditions.
Record host load, report the range, and keep coverage/CI-Hypothesis timings
separate from the fast default. Shared sessions can change wall time substantially.
The [integration report](../docs/reviews/2026-11-07-test-suite-integration.md)
records the measured tradeoffs and remaining coverage limits.

## Verification

Run from the repository root:

```sh
uv run ruff check src tests scripts/summarize_test_profile.py scripts/check_doc_links.py
uv run ruff format --check src tests scripts/summarize_test_profile.py scripts/check_doc_links.py
uv run mypy src
uv run pytest --cov --cov-report=term
```

Default tests block Python network sockets and exclude `slow` and `browser` cases.
Native `curl_cffi` transport is outside pytest-socket’s protection: replace it at
the session boundary in offline tests. Tests marked `slow` enable sockets locally
as well as in CI. Browser tests enable sockets for their local server; Chromium
may load CDN assets. Run a focused module while changing its contract, then run
the default suite when shared fixtures or import boundaries change.

Run the optional lanes explicitly:

```sh
uv run pytest -n 0 -m 'browser and not slow'
uv run pytest -n 0 -m 'slow and not browser'
```

Slow tests receive a 120-second timeout unless they specify their own. Browser
tests disable pytest-timeout because its signal handler interferes with Playwright;
they use Playwright's action and assertion timeouts instead. A command-line
`--timeout` does not override that marker. CI also limits each optional job to
15 minutes.

Live tests depend on portal access and supply; they may skip when no suitable
listing exists. Paid analysis probes check request/response structure, not model
accuracy. Browser tests use seeded local data; they do not validate live portals.
