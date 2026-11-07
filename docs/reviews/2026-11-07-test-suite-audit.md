# Test-suite audit — 7 November 2026

> Historical snapshot. The [integration report](2026-11-07-test-suite-integration.md) describes the implemented combined suite. Counts, paths, timings and worker-cap recommendations below describe the earlier branch or verification state.

The suite has strong offline coverage of durable processing, spending, consolidation, and delivery. Its original runtime was dominated by the test harness. Removing unconditional heap collection reduced a four-worker run from **710.15 seconds to 71.66 seconds**, while retaining 4,251 passing cases. Plain exception rendering and explicit pacing fakes reduced the next run to **26.03 seconds** with 4,255 passing cases. The expanded no-coverage run passed 4,273 cases in **45.80 seconds** under higher host load. Final verification, including the additional worker-crash probe, passed **4,274 cases in 46.86 seconds with coverage**. Final verification and scope are recorded below; these are shared-host observations, not CI performance guarantees.

Not every code path or user action is covered. The largest remaining gaps are live model evaluation, some detail-fetch transport/retry paths, optional browser/portal validation in routine CI, and research/maintenance tools outside the application gate. A green default run does not establish live portal compatibility, model judgment quality, or every browser interaction.

## Scope and evidence

This audit started at commit `51dd9c6e05cd36d4fe4a7cf7ea8966023d63cc16` and describes the local changes accompanying this report. It applies the quality-pass criteria: meaningful boundaries, truthful names, observable outcomes, durable state, and complementary layers. It inventories every collected test module, maps every application Python file to measured coverage, and inspects high-risk and suspicious assertion bodies. It does not claim that coverage proves every assertion is adequate.

- [Complete suite inventory](2026-11-07-test-suite-inventory.md): **4,396 cases in 162 modules**, including optional lanes.
- [Source coverage map](2026-11-07-test-suite-coverage.md): every file under `src/home_finder`, with statement/branch results and exercised test domains.
- [Machine-readable measurements](2026-11-07-test-suite-measurements.json): run settings, host load, timing totals, memory, and exact uncovered lines/branches.
- [Maintainer guide](../../tests/README.md): test selection, naming, boundaries, fixtures, and profiling commands.

| Lane | Cases | Meaning |
| --- | ---: | --- |
| Default offline gate | 4,274 | Domain rules, real SQLite/files/processes, HTTP contracts and multi-stage workflows with external acquisition/sends replaced |
| Local application browser | 77 | Chromium against a seeded local server; real JavaScript and CSS; fake map/image responses; CDN frontend dependencies |
| Live services without browser | 41 | Portal/detail probes, geographic diagnostics, and five legacy analysis cases |
| Live portal browser | 4 | Sort-control probes, including two expected failures |
| Research suites, separate configuration | 83 | 21 layout-evaluation, 10 report-generation, 52 labeling-browser cases; outside the counts above |

`unit`, `integration`, and `e2e` are not exhaustive partitions: only 39 collected cases carry `integration`, 10 carry `e2e`, and none carry `unit`. Paths are the useful selector. `slow` means live-service access, not observed duration. The default gate is therefore much broader than unit tests and narrower than all tests.

## How the suites complement one another

| Contract | Narrow evidence | Connected or user-visible evidence | Limit |
| --- | --- | --- | --- |
| Collection and source identity | `test_scrapers/`: retained HTML/JSON envelopes, URLs, pagination, IDs, transport and health | Pipeline collection/observation accounting; new scrape-only entry-point test; Rightmove localhost HTTP contracts | Retained fixtures cannot prove current portal behavior |
| Criteria, location and media admission | `test_filters/`, source-area contracts | Candidate preparation, enrichment admission/persistence, selection/persistence scenarios | Live postcode diagnostics have no acceptance threshold |
| Enrichment and commute | Detail parsing, image validation, `test_enrichment/` | Real persistence of acquired evidence, advisory commute outcomes/backfill; new durable ward lookup tests | Commute providers and geocoders are replaced; out-of-limit is an annotation, not exclusion |
| Matching and consolidation | Score components, grouping, image evidence, property-based invariants | Cross-run consolidation, merge rollback and history preservation | Heuristic tests do not establish false-match rates on representative real homes |
| Assessment and evidence | Request schemas, prompt snapshots, strict fresh-output validation, historical model compatibility | Native SDK transport, checkpoints, image/text provenance, queue recovery and failure disposition | Structure/prompt tests do not measure model accuracy |
| Spending | Exact money/rate cards, admission and ledger transitions | Native request middleware, ambiguous outcomes, concurrent limits and process-exit recovery | Real provider billing and pricing changes need separate qualification |
| Notifications | Selection policy, message composition, Bot method choice | Durable planner, parts, receipts, duplicate prevention, restart recovery and dry-run suppression | No real Telegram send was made |
| Processing and ownership | Queue states, caps, locks, timeouts, event recording | Full run, empty discovery, retries, requested reanalysis, cancellation and crash/restart | Long-running production scheduling under real load is not simulated exhaustively |
| Storage and operations | Models, transactions, migration rules, source membership | Backup/restore subprocess round trips, exclusive administration, off-market aggregation, dedup maintenance, new CLI argument contracts | Some CLI error combinations and external maintenance scripts remain uncovered |
| Dashboard and owner access | HTTP validation, auth/origin/body limits, templates, filters, images, status/reanalysis persistence, delivery view | Browser filter application/reset/history, modal counts, gallery/keyboard, maps/fallbacks, status reload/morph, themes/contrast/overflow | Chromium only; not every route has a browser journey; no JavaScript line/branch metric |
| Telegram owner callbacks | Secret, sender/chat, payload and duplicate-update validation with durable status | New real startup registration and callback-acknowledgement helpers with only Bot API replaced | Real webhook registration/delivery remains external |

The same scenario appearing at several layers is often valuable: a parser proves extraction, a repository proves persistence, and a run scenario proves scheduling/recovery. These should not be combined merely because they touch the same field. The detailed inventory identifies the ownership of all 162 modules.

## Changes made

**Harness and measurement.** Added opt-in JSON phase profiling and a summary command, including xdist worker collection/CPU/wall time, fixture setup, peak process RSS, load averages, outcomes, and worker failures. Tests exercise real serial and distributed sessions with a pass, a skip, a teardown failure, and an abruptly exited worker. The crash probe verifies completed phase retention even when worker timing metadata is unavailable. CI retains the profile and JUnit output and prints its summary even after a non-cancelled failure. Unknown markers now fail collection; automatic worker count is capped at four, with documented overrides. Browser/live jobs use one worker; the explicitly requested live job now enables the sockets its Python transports require.

**SQLite ownership.** The old autouse fixture forced `gc.collect()` and a whole-heap scan after every test, including pure model tests. The replacement snapshots aiosqlite workers, allows completed close operations to settle, and scans only when a newly created worker survives. It preserves connections owned by longer-lived fixtures. Real-leak and healthy-close tests verify containment and preservation. A hidden leak in the full-run fixture was exposed: its purported cleanup only looked up the event loop, and a close call occurred while close was still patched to a no-op. The fixture now owns cleanup in an async context manager's `finally` block. Leak warnings are visible.

**Logging and pacing.** Expected exceptions keep their events and plain tracebacks. Profiling showed rich rendering recursively formatting mock locals dominated analyzer error tests. Off-market circuit scenarios no longer wait 10 and 16 real seconds. The pacing contract now records and asserts the 0.5-second requested wait between same-source checks. The Telegram batch test verifies two waits for three messages and no trailing wait. Other concurrency/recovery tests retain real coordination; these changes introduce no global replacement of `asyncio.sleep`.

**Structure and test intent.** Split the 3,551-line analyzer module into orchestration, provider-schema, and assessment-model/compatibility suites, with shared response fixtures in the nearest `conftest.py`. The original tests were preserved apart from a deliberate replacement: eight acoustic tests exercised a copied mapping helper; seven new native-transport cases inspect the actual production evaluation request. A mutation that removed the production mapping caused all three supported-construction cases to fail, while the four absence/unsupported cases passed. Renamed the mocked Telegram “E2E” module to a notification contract. Corrected “quality filter,” “enriched/failed,” advisory commute, strict-mode, and assembly-only descriptions where they misstated behavior.

**Assertions and gaps.** Cloudflare challenge cases now call the real off-market checker rather than only its predicate. Browser filters assert returned identities, sorting asserts actual order, HTMX retains a document identity sentinel, the gallery must exist, and modal/count waits observe outcomes instead of registering an event listener after the event may have fired. Added durable ward-lookup, administrative argument, scrape-only, Telegram startup, and acknowledgement contracts. Live enrichment checks now assert actual gallery/description evidence, preserve both readiness partitions, and replace a no-op image check with a media-readiness assertion. Empty live collection is an explicit skip, not a passing shape check; scraper cleanup runs in `finally`. Seven duplicate offline location-map cases were removed from the live suite because dedicated data/URL tests already cover them.

**Reporting hygiene.** A 4,000,001-space malformed payload had generated a test ID of roughly four million characters. It and other long hostile inputs now have semantic IDs while retaining the original payloads. The largest collected ID is now 893 characters. Historical review B4 is labelled as historical, and its obsolete counts/architecture claims are not presented as the current state.

## Performance experiments

Linux, Python 3.14.7, 32 visible CPUs/affinity slots, locked dependencies. Host load varied substantially throughout. The baseline and cleanup runs used four workers, the fast Hypothesis profile, no coverage, and excluded the new harness self-tests. They both selected 4,251 cases, although the assertion improvements and analyzer split changed some identities. Brief focused probes also ran during the original baseline. The controlled cleanup comparison below uses exactly the same 26 model cases.

| Run | Passed | xdist workers | Profile | Pytest elapsed | Worker/main CPU | Summed teardown |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| Original harness | 4,251 | 4 | fast | 710.15 s | 2718.13 s | 2608.29 s |
| Guarded SQLite cleanup | 4,251 | 4 | fast | 71.66 s | 158.75 s | 5.01 s |
| Plain logs + pacing assertions | 4,255 | 4 | fast | 26.03 s | 65.72 s | 1.45 s |
| Serial comparison, prior to 18 added cases | 4,255 | 0 | fast | 88.05 s | 52.98 s | 1.66 s |
| Expanded selection, two workers | 4,273 | 2 | fast | 49.72 s | 57.73 s | 1.63 s |
| Expanded selection, four workers | 4,273 | 4 | fast | 45.80 s | 94.08 s | 4.08 s |
| Final coverage + contexts | 4,274 | 4 | ci + coverage | 46.86 s | 109.65 s | 4.03 s |
| Final application browser | 77 | 0 | fast | 48.34 s | 9.74 s | 3.18 s |

The table uses pytest’s terminal elapsed time; the JSON also records the slightly shorter configure-to-session-finish profiling clock. The `ci` profile defaults to 200 Hypothesis examples versus 10 locally; explicit per-test settings can override those defaults. The audit coverage run additionally records test contexts and JSON/XML reports.

“Summed teardown” adds test-phase time across workers; it is not elapsed wall time. Fixture setup totals are inclusive and overlap setup phases. CPU totals sum pytest workers, or the main process for serial runs; they exclude the xdist controller and spawned child processes. The first two runs had similarly high one-minute load (about 43→35 and 41→45), while some later runs had lower load. Do not interpret the complete before/after ratio as a portable CI speedup or a statistically isolated effect of every change.

The stronger causal evidence is:

- Three alternating legacy/current runs of the same 26 serial model cases: legacy profile elapsed **1.06–1.69 s**, median **1.36 s**; guarded cleanup **0.071–0.080 s**, median **0.079 s**. Teardown fell from **0.96–1.60 s** to **0.0016–0.0019 s**. The legacy behavior was replayed from the starting commit in an uncommitted probe plugin.
- Two alternating runs of the same analyzer failure-path case with rich/plain rendering: rich call phases **6.712–6.717 s**; plain call phases **0.020–0.028 s**. Profile elapsed was **7.59–7.67 s** versus **0.65–0.96 s**, including test collection/setup. A separate cProfile run located the cost in rich panel/table/locals rendering; profiler-instrumented duration is not used as an ordinary benchmark.
- Real pacing accounted for 27 seconds of off-market call-phase time in the baseline. Recorded waits preserve the scheduling assertions without that elapsed delay.
- Worker comparisons show the tradeoff between startup/import/memory cost and parallel execution. Four workers are a measured practical cap, not a proven optimum on all machines. Use `-n 0` for small selections and repeat representative runs before raising the cap. Observed worker peak RSS was roughly 0.4–0.6 GiB; the long serial run peaked near 0.8 GiB. Baseline RSS was not recorded, so this is not evidence of a before/after memory improvement.

One preliminary two-worker run briefly overlapped an accidentally started serial run; both measurements were excluded and replaced. Sandbox-only SQLite timeouts were also excluded: a minimal connection probe and identical tests showed that sandbox restrictions prevented thread wakeups, while execution outside that sandbox worked. No production database or external provider was used for those experiments.

The remaining cost is a mix of useful real SQLite/file/process/SDK work, collection repeated per worker, coverage/property generation, and some explicit application pacing in full recovery scenarios. Those residual sleeps could be replaced with narrowly scoped recorded waits if their contract is separately retained. Reusing one database across unrelated tests or removing recovery scenarios would trade away valuable isolation or behavior coverage. No wall-time threshold is introduced on this volatile host.

## Coverage and remaining work

Final offline coverage is **93.87% of statements** (12,884/13,726) and **86.79% of branch exits** (4,112/4,738). Coverage.py’s combined metric is **92.05%**. There are **842 unexecuted statements** and **626 missing branch exits**; 492 are reported as partial branches. The source map shows every module rather than hiding the low-coverage areas behind the aggregate.

The source metric now includes logging and reference-data helpers; the previous module omissions were removed. The existing `TYPE_CHECKING`, `__main__`, and `pragma: no cover` rules still exclude 50 lines from the metric. Python child-process and `os._exit` instrumentation is enabled so CLI/crash contracts contribute evidence; forced process kills can still lose unflushed coverage. This is necessary with current pytest-cov, which delegates subprocess measurement to coverage.py. See the [pytest-cov subprocess documentation](https://pytest-cov.readthedocs.io/en/latest/subprocess-support.html) and [coverage.py patch reference](https://coverage.readthedocs.io/en/latest/config.html#run-patch). The 75% gate is unchanged. This expanded scope means percentages from earlier revisions are not directly comparable.

| Priority | Remaining finding | Next useful evidence |
| --- | --- | --- |
| High | `test_quality_eval.py` is not a working live evaluation gate: placeholder images are not cached, no spend ledger/policy is supplied, and the two-bedroom spaciousness override expectation is obsolete | Replace with curated/licensed cached evidence, explicit small budgets and ledger, actual inference assertions, and human-labelled quality criteria. Structural checks alone are insufficient |
| High | Detail fetcher retry adapters are bypassed by parsing tests; `_httpx_get_with_retry` and `_curl_get_with_retry` have no observed execution in the final offline coverage | Native/mock transport cases for transient recovery, exhausted attempts, redirect/challenge handling, proxy/session ownership, and cancellation; keep parser tests separate |
| Medium | Live portal probes can skip, geographic probes are diagnostic-only, and two sort checks are expected failures | Qualify access, retain current envelopes, define geographic acceptance criteria, and publish pass/skip/xfail separately. No live portal or paid-model run was performed in this audit |
| Medium | Browser checks are optional CI and do not cover every action | Consider a routinely run representative browser lane after making CDN dependencies reproducible; add explicit reanalysis, delivery-inspection and area-navigation journeys. Current HTTP tests cover these server contracts |
| Medium | Some administration error/empty-state, scheduler-lock/error-loop, and provider fallback branches remain unexecuted | Use the source map's exact missing lines/branches to choose failure-injection contracts; avoid padding coverage with import-only assertions |
| Medium | Scripts such as ward backfill, merge auditing, notification smoke tools, icon generation and experiment model/scoring runners sit outside the production coverage metric | Decide which are supported operations; add focused dry-run/fixture contracts for those, and label one-off diagnostics accordingly |
| Low | `utils/union_find.py` is unexecuted and has no repository callers | Confirm external compatibility needs, then remove it or identify its intended supported caller; do not add tests solely to improve the percentage |

Coverage records execution, including imports. Test-domain attribution in the source map is a navigation aid, not proof of a contract. JavaScript/CSS, accessibility beyond the specific checks, live network compatibility, real model quality, and deployment configuration are not represented by the Python percentage. Tests alone cannot establish that all conceivable user or pipeline actions are covered.

## Verification and use

- **4,274 offline cases passed** with the CI Hypothesis profile, branch/subprocess coverage, test contexts, JUnit and timing artifacts: **46.86 s**. The final no-coverage four-worker run passed in **45.80 s**. No leak or socket warnings remained in these offline runs.
- **77 application browser cases passed** after the strengthened identity/order/gallery assertions: **48.34 s**. There were 27 dependency deprecation warnings from websockets/uvicorn and `asyncio.iscoroutinefunction`; no failed or skipped application browser cases.
- **83 research cases passed** in their own configurations: 21 layout-evaluation, 10 report-generation, and 52 labeling UI. They validate the research harnesses, not their underlying real-world accuracy.
- Ruff lint/format passed for source, tests and the new summary script; mypy passed for all 122 source files; `git diff --check` passed. `uv build --offline` produced a wheel and source distribution. A final four-case Telegram lifecycle rerun passed after a context-manager-only cleanup; all five harness self-tests passed, including the worker-crash probe.
- The acoustic mutation probe failed the intended three positive cases. The controlled timing probes all passed. Optional live portal/paid-model checks were inspected/collected, not executed. GitHub Actions itself was not run; the local build used cached dependencies rather than the CI cold-cache download check. Other Python versions, operating systems and browser engines were not exercised.

The previous socket warnings were traced to urllib3’s import-time local IPv6 capability probe while loading the mocked TravelTime SDK. Importing that SDK during collection removes the noise while test requests remain socket-blocked. Native curl and Chromium are outside pytest-socket’s Python-socket guard, so their explicit fakes and separate lanes still matter.

The report, inventory, measurements, and test guide are the durable audit outputs. Raw logs/profiles/coverage are also retained locally under `test-results/audit/`; they are ignored by Git. Subsequent CI runs retain profiles/JUnit for seven days. Run from the repository root:

```sh
uv run pytest -n 4 --test-profile=test-results/local.json --durations=20
uv run python scripts/summarize_test_profile.py test-results/local.json
HYPOTHESIS_PROFILE=ci uv run pytest --cov --cov-report=term \
  --test-profile=test-results/ci.json --junitxml=test-results/ci.xml
uv run pytest -n 0 -m 'browser and not slow' --tracing=retain-on-failure
```

For an incident, compare selection, profile, coverage mode, workers, host load, and phase totals before diagnosing application slowness. For a new contract, keep the production decision real, fake only its external boundary, and assert the saved or user-visible outcome that would fail for the regression.
