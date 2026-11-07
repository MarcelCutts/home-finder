# Integrated test-suite review and implementation

7 November 2026. This is the current account of the combined implementation in the
Codex worktree, based on `51dd9c6e05cd36d4fe4a7cf7ea8966023d63cc16`. It supersedes the
implementation recommendations in the earlier Codex and Claude reports. Those
reports remain historical evidence; their counts, paths and worker caps describe
their respective snapshots.

The corrected Claude suite layout and performance fixes are integrated with the
Codex profiler, CI artifacts, additional execution tests and stronger live/browser
assertions. **The default is `-n auto`, without a repository worker cap.** The final
default run passed **4,081 tests in 10.70 seconds** as reported by pytest. The final
CI-profile coverage run passed the same tests in **18.74 seconds**, without
warnings, including with `ResourceWarning` made visible. All **77 local Chromium
tests** passed in **32.30 seconds** with conflicting application environment
variables exported; they emitted 27 dependency deprecation warnings.

Coverage is **94.40% of statements** (12,958/13,727) and **87.48% of branch exits**
(4,145/4,738). Coverage.py's combined score is 92.62%; it is a different metric.
These figures do not establish complete user-action coverage.

## What was combined

| Area | Integrated decision |
| --- | --- |
| Test layout | Claude's responsibility-based splits of routes, storage, analysis, matching and pipeline tests; shared resource fixtures and builders; clearer contract names |
| Repeated work | Direct badge-reconciliation grids plus three complete analyzer wiring cases; consolidated repeated selection and matching examples; diagnostic-helper tests retired |
| Expensive setup | Thread-based leak detection, plain test tracebacks, recorded or bypassed production waits, faster image builders, and migrated templates for fresh in-memory databases |
| Correctness safeguards | Real migration fallbacks; schema-path activation and parity checks; leaked SQLite handles actually closed; pre-existing connections preserved; closing threads allowed to finish; visible leak warnings |
| Isolation | Temporary database/cache paths, exported-settings isolation including the browser server, and a deliberately limited repository-directory guard |
| Execution coverage | The 19 Codex cases adopted by Claude for scrape-only, ward lookup, admin entry points and Telegram webhook setup/acknowledgement |
| Browser and live probes | Outcome-based HTMX assertions, stronger filter/order/gallery checks, meaningful enrichment assertions, client cleanup and locally enabled sockets for `slow` tests |
| Observability | One opt-in profiler, its subprocess/worker-crash regression tests, JUnit, CI artifact retention and a readable job summary |
| Measurement | All package modules included; child-process and abrupt-exit coverage enabled; bounded parameter IDs; worker/load context recorded |

The suite now collects **4,202 cases in 193 modules**: 4,081 offline, 77 local browser,
40 live non-browser and four live browser cases. The longest node ID is 893
characters. Seven duplicated constant-mapping cases were removed from the live
lane; offline location-mapping tests own those constants.

Use the [current module inventory](2026-11-07-integrated-test-inventory.md) to find a
suite's purpose and lane, the [package coverage table](2026-11-07-integrated-test-coverage.md)
for measured execution, and the [consolidation ledger](2026-11-07-test-suite-consolidation.md)
for previous locations. The ledger accounts for all 1,369 absent baseline test
function identifiers: 474 unchanged-body moves, 172 same-name moves/revisions,
719 contract-family consolidations and four retired local-helper checks. It is
**not an assertion-by-assertion or parameter-by-parameter equivalence proof**.
That limitation remains explicit; higher aggregate coverage cannot supply that proof.

## Additional findings fixed during integration

**Prompt units.** The acoustic data already includes `dB`; the evaluation prompt
added it again. The prompt now has one unit. A test checks the actual evaluation
request, including the absence of `dB dB`; the request-body fingerprint was updated
for this deliberate prompt change. Two complementary native-request cases retain
coverage for absent and legacy unstructured acoustic evidence.

**Detail retry exhaustion.** The HTTPX path made a third request after Tenacity had
exhausted its two configured attempts, while trying to construct the final error.
It now raises from the last response. `_MAX_FETCH_ATTEMPTS` names the actual unit.
Twenty-three cases exercise HTTPX and native-curl boundaries, successful recovery,
exhaustion, non-retryable failures, cancellation, request options and separate
throttle buckets. They run the real retry policy and replace sleep/clock and I/O
boundaries. Restoring the original HTTPX method through a temporary plugin makes
the exhaustion case fail on its extra request; the other four HTTP status cases
still pass. This matches Tenacity's documented interpretation of
[`stop_after_attempt`](https://tenacity.readthedocs.io/en/latest/): attempts include
the initial call.

**Schema-template ownership.** Concurrent first callers could each create a native
SQLite template and overwrite the cached handle. A migration exception or
cancellation could also leave its candidate open. The plugin now retains one
completed template, closes unused or failed candidates, and closes the retained
template at session teardown. Event-coordinated concurrency and failure tests
exercise these paths. Another regression check proves an ordinarily closed
connection never triggers the expensive heap scan.

**Timing artifact deletion.** Playwright's session fixture deleted its output
directory even in ordinary offline runs. With the default `test-results` location,
this erased profiles from earlier runs. Its output is now
`test-results/browser`, leaving sibling profiles and JUnit files intact. Repeated
full runs and the browser run preserved those siblings. The behavior was verified
in the installed plugin and its [upstream implementation](https://github.com/microsoft/playwright-pytest/blob/main/pytest-playwright/pytest_playwright/pytest_playwright.py).
Some earlier local raw artifacts had already been erased; their written summaries
remain, and the fresh integration evidence is retained separately.

The remaining production change is a named Telegram pause constant, still 1.0
seconds in production, so tests can bypass pacing without replacing global
`asyncio.sleep`. Documentation corrections do not alter runtime behavior.

## Performance experiments

Eight interleaved full offline runs used warm bytecode, the fast Hypothesis profile,
and no coverage. These runs had 4,078 cases, before the three final template cleanup
cases were added. All succeeded; disabling the template skipped its five then-existing
self-tests. Whole-process time includes startup and exit; CPU is the accumulated
CPU of the pytest subprocess and its waited-for descendants.

| Workers / template | Whole-process wall, two runs | Whole-process CPU, two runs |
| --- | ---: | ---: |
| Automatic: 16 workers, template on | 11.63–12.42 s | 172.52–174.55 s |
| 8 workers, template on | 12.07–12.12 s | 101.75–101.80 s |
| 4 workers, template on | 15.06–15.07 s | 67.02–68.03 s |
| 4 workers, template off | 17.14–17.38 s | 75.30–75.89 s |

The observed template saving is about **2.2 seconds at four workers**, consistent
with the corrected earlier experiments. Automatic and eight-worker elapsed times
are too close to rank confidently here. Automatic used about 71% more CPU than
eight workers, while four workers took about three seconds longer.

The default follows the user's preference to use the computer's available cores.
[`-n auto`](https://pytest-xdist.readthedocs.io/en/stable/distribution.html) chooses
from the physical-core count; it is not a continuously load-adaptive scheduler.
`--dist=loadgroup` distributes ordinary tests to available workers while preserving
explicit groups such as browser tests. A caller can still use `-n 4`, `-n 8` or
`-n 0`; the repository does not impose that choice.

Load at the experiment boundaries ranged from 0.76 to 8.76 on the one-minute
average. Other sessions can change that. These ranges describe this machine and
this measurement window, not a universal throughput guarantee. Earlier multi-minute
runs occurred under different host load, cache conditions and suite contents, so
this report does not derive an exact overall speedup ratio from them.

The final fast profile partitions 10.68 seconds after pytest configuration into
4.54 seconds of startup, a 3.92-second test window and 2.22 seconds through the
session-finish hook. Workers occupied test phases for 78% of that window. Startup
and shutdown now dominate wall time; remaining slow cases include actual child
processes proving crash recovery, backup/CLI isolation, and the profiler's own
failure-handling tests. Those boundaries are valuable. No further grouping or
worker cap was added merely to improve this machine's benchmark.

## Observability and interpretation

```sh
uv run pytest --test-profile=test-results/local.json --junitxml=test-results/local.xml
uv run python scripts/summarize_test_profile.py test-results/local.json
```

`--timing-report` is a compatibility alias, not another plugin. Profiles include
phase outcomes and durations, inclusive fixture setup, collection, process CPU and
peak RSS, worker balance, load, Python/pytest versions, Hypothesis/coverage settings
and schema-template status. A dead worker's completed reports survive, with its
missing process timings explicitly marked. Occupancy includes allocated workers
that receive no tests, rather than reporting only the active workers; a real
two-worker, single-group regression case checks this. CI retains profiles/JUnit under
`test-observability`, coverage separately, and prints a Markdown job summary.

Phase totals overlap across workers. Fixture timings are inclusive and not additive
to phase timings. Occupancy measures wall time inside phases, not CPU utilization.
Profiler wall/CPU counters start at pytest configuration, after early imports, and
end before final reporting/process exit; CPU excludes child processes launched by
tests. RSS is each process's lifetime peak, not simultaneous memory usage. The
worker comparison above uses an external whole-process timer instead.

Coverage uses `patch = ["subprocess", "_exit"]`, as supported by
[pytest-cov's migration guidance](https://pytest-cov.readthedocs.io/en/latest/subprocess-support.html)
and [coverage.py's patch documentation](https://coverage.readthedocs.io/en/latest/config.html#run-patch).
The denominator includes all 122 Python package files, with the existing 50 line
exclusions. It does not include templates, JavaScript, maintenance scripts or
experimental projects. The detail-fetcher tests raise its statement coverage to
91.68% and branch coverage to 79.44%; not every parsing/error path is covered.

[Compact evidence](2026-11-07-test-suite-integration-evidence.json) preserves all
experiment results, profile summaries, source hashes and missing coverage locations.
Detailed local artifacts are in `test-results/integration/`; source snapshots used
for the comparison are under `/tmp/test-suite-integration/` and are temporary.

## User and pipeline action coverage

| Action or boundary | What is exercised | Limit |
| --- | --- | --- |
| Collection and scrape-only | Portal parsing/transport contracts, limits and failures; coordinator and command execution | Live access and current portal markup require the optional probes |
| Selection, matching and consolidation | Criteria, location/media gates, scoring, grouping, source membership, price/cache preservation and protected durable records | Assertions cover represented scenarios, not all possible property evidence |
| Enrichment and advisory commute | Detail/media boundaries, result partitions, source area and commute failures/cache behavior | External provider correctness is faked in the offline lane |
| Assessment and spending | Actual SDK request serialization with fake responses; admission, settlement, retries, model pairs, checkpoints, badge wiring and reanalysis | Structural tests do not measure model accuracy or real billing |
| Run lifecycle and recovery | Full coordinator with external boundaries replaced, dry run, persistent queues, child-process crash recovery and backups | Some CLI flag combinations and lock-busy branches remain unchecked |
| Delivery and Telegram callbacks | Planner/repository receipts, recovery, messages/keyboards, webhook setup and acknowledgement | Real Telegram delivery is not established by these tests; some legacy-status fixtures intentionally bypass receipts |
| Dashboard and owner actions | HTTP/rendered state, filters/order/status/history persistence, cached-image guard, access, seeded browser interactions | Browser lane is manual/default-off; several rendering and failure paths remain missing |
| Administrative/maintenance operations | Admin entry-point dispatch plus separate durable reconciliation tests; selected cache and retired-sync scripts | `audit_merges.py` and `backfill_wards.py` still lack dedicated execution tests |

There is useful coverage at every principal pipeline stage, but **all code and all
user actions are not covered**. Prioritized follow-up findings are:

1. Test the destructive merge-audit and ward-backfill scripts against disposable
   databases, including history/receipt preservation. The merge audit currently
   deletes through raw SQL; it was not run against user data during this work.
2. Exercise storage failures on dashboard/card/detail/map fallback routes, and
   rendering of detail status/price histories, negotiation content and `og:image`.
3. Complete remaining CLI variants, including accepted full-scrape/debug settings,
   invalid-settings exit, thumbnail lock contention and omitted optional flags.
4. Establish a separate, representative assessment-quality evaluation. The live
   analysis smoke probe's synthetic images only establish request/response structure.
5. Review compatibility exports and unused dependencies before removal. This pass
   retains them; absence of an internal caller is insufficient evidence to delete
   an exported interface. Crawlee's transitive BeautifulSoup dependency needs an
   explicit replacement if Crawlee is removed.

The repository cache guard detects creation/top-level membership changes; it is
not a recursive write monitor. Python socket blocking does not constrain native
curl transport, which must be faked explicitly. Browser tests enable networking
and may load CDN assets. Live tests are collected but were not run against portals
or paid APIs in this integration. Experimental harnesses were not rerun.

## Verification and review

- Final default: 4,081 passed; final CI Hypothesis/coverage: 4,081 passed, no warnings.
- Local Chromium: 77 passed, with hostile exported settings; 27 third-party
  deprecations from websockets/uvicorn/asyncio integration remain visible.
- Focused infrastructure and transport checks: 38 passed with `ResourceWarning`
  treated as an error. The restored extra-request mutation was rejected.
- Eight interleaved worker/template experiments succeeded.
- Ruff checks/formatting, mypy over 122 source files, wheel/source build and
  `git diff --check` passed. Hosted CI itself has not been dispatched.
- The quality-pass reviewed production changes, fixture ownership, real decision
  boundaries, terminology, profile units, historical/current report separation and
  coverage claims. The gaps above remain explicit rather than inferred away from
  a green suite. Nothing was committed or published.
