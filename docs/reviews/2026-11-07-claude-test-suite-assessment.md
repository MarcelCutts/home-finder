# Assessment of Claude's test-suite investigation

> Historical snapshot. The [integration report](2026-11-07-test-suite-integration.md) describes the implemented combined suite. Counts, paths, timings and worker-cap recommendations below describe the earlier branch or verification state.

7 November 2026. Reviewed the supplied report, the corresponding changes against
`51dd9c6e05cd36d4fe4a7cf7ea8966023d63cc16`, relevant upstream documentation, and a
disposable snapshot of the implementation. Applied the quality-pass criteria:
truthful boundaries, names and claims; production decisions exercised by tests;
and evidence for performance changes.

**The investigation is useful and its main performance diagnosis holds. Most of the
structural and assertion improvements are worth keeping. The implementation needs
corrections before adopting it wholesale:** the schema template is bypassed,
thread cleanup does not close SQLite connections, and two isolation guarantees
are broader than their implementation. Several coverage and reachability claims
also need narrower wording.

This is a review, not an integration of the two parallel changes. Neither source
worktree was modified by the verification experiments.

## Verification and scope

The reviewed report is
[Claude's report](/home/marcel/projects/lab/home-finder/.claude/worktrees/test-suite-quality-perf-b4e787/docs/reviews/2026-11-07-test-suite-review.md).
The source snapshot was captured at **18:16:11 UTC**. Its lockfile matches this
worktree's: Python 3.14.7, pytest 9.1.1, xdist 3.8.0, pytest-cov 7.1.0,
coverage 7.13.2, aiosqlite 0.22.1 and structlog 26.1.0.

| Independent check | Result |
| --- | --- |
| Default offline suite, four workers | **4,026 passed**, 21.37 s, three warnings |
| Default suite with coverage and the CI Hypothesis profile, four workers | **4,026 passed**, 25.51 s, four warnings |
| Local Chromium browser suite | **77 passed**, 37.10 s, 27 dependency deprecation warnings |
| Template guards after an experimental correction in `/tmp` | Five passed; the fast path was demonstrably entered |
| Timing report with an intentionally crashed worker | Correct failure exit; JSON artifact retained |
| Focused counterexamples | Reproduced the template, SQLite lifetime, cache guard and session-settings problems below |

The coverage run used 108.10 process-tree CPU-seconds (103.98 user + 4.12 system),
measured by `/usr/bin/time`; total process wall time was 27.61 s. Pytest's own
reported duration excludes some process startup/shutdown. These are local
observations, not a stable performance target on this shared host.

Machine-readable totals and source hashes are in
[assessment evidence](/home/marcel/.codex/worktrees/7e10/home-finder/docs/reviews/2026-11-07-claude-test-suite-assessment-evidence.json).
Raw logs, coverage, timing reports and executable probes are retained locally in
[test-results/claude-assessment](/home/marcel/.codex/worktrees/7e10/home-finder/test-results/claude-assessment).
An earlier coverage run collected a temporary settings probe, giving 4,027 cases;
that run was replaced by the clean 4,026-case run above.

Claude updated the leak checker during the review. The later version adds database
names and visible warnings. I separately reproduced both SQLite lifetime problems
against that updated file; they remain. The supplied report, template plugin,
root fixtures and browser settings were unchanged at that comparison.

## Corrections needed before adoption

### 1. The schema-template optimization never runs through normal storage

At
[schema_template.py:45](/home/marcel/projects/lab/home-finder/.claude/worktrees/test-suite-quality-perf-b4e787/tests/plugins/schema_template.py:45),
`_is_empty()` compares `await cursor.fetchone()` with `(0, 0)`.
`PropertyStorage._get_connection()` has already installed `aiosqlite.Row` as its
row factory. A `sqlite3.Row` containing those values does not equal that tuple.
Every ordinary storage initialization therefore falls back to the real migrations.
Python documents the distinction between tuple rows and the `Row` factory, including
the latter's equality semantics. [Python sqlite3 reference](https://docs.python.org/3.14/library/sqlite3.html#sqlite3.Row).

Two independent probes confirmed this: an empty connection with the production
row factory failed `_is_empty()`, and a spy on `_migrated_template()` recorded no
calls while opening a fresh `PropertyStorage`. All 15 migrations ran instead.

The existing equivalence test does not catch this: its purported template database
and its reference database both migrate normally. This is the same class of
false assurance that the investigation correctly criticizes elsewhere.

**Recommendation:** convert the fetched row to a tuple, close the cursor, and assert
that the template path actually runs. A temporary correction passed both new probes
and all three existing schema guard tests, including WAL/schema equivalence.
Then compare full runs with the template enabled and disabled, and explicitly close
the cached connection. The proposed optimization is plausible, but the report cannot
attribute the reviewed suite's speedup to it. Its quoted backup microbenchmark does
not establish an end-to-end saving in the current implementation.

### 2. Stopping a worker is not closing its database

The
[leak checker](/home/marcel/projects/lab/home-finder/.claude/worktrees/test-suite-quality-perf-b4e787/tests/plugins/aiosqlite_leaks.py)
puts a sentinel directly onto each thread's queue. It neither closes the underlying
SQLite connection nor updates the owning `aiosqlite.Connection`'s running state.
It also treats every live non-browser connection as a leak, including one that
existed before the test.

I reproduced two consequences with both the captured and updated checker:

- A leaked file connection holding `BEGIN IMMEDIATE` retained its lock after the
  worker stopped. Another connection received `sqlite3.OperationalError: database is locked`.
- A previously owned connection was stopped too. Its next awaited `SELECT 1`
  timed out because work was queued to a dead thread.

The cleanup self-test only asserts that the thread stopped; it never verifies the
database closed or another owner's connection remained usable. The coverage run
also emitted an unclosed-SQLite `ResourceWarning`, although its delayed reporting
location does not identify the allocation site.

**Recommendation:** keep the cheap thread check, track connections/threads owned
before the test, and invoke the connection's actual close/stop behavior for leaks.
Add lock-release and ownership checks. The updated diagnostic warning is useful,
but does not fix resource lifetime. The installed aiosqlite implementation closes
SQLite inside `Connection.stop()`; `close()` also completes queued work.
[aiosqlite API](https://aiosqlite.omnilib.dev/en/stable/api.html#aiosqlite.Connection.close),
[aiosqlite 0.22.1 source](https://github.com/omnilib/aiosqlite/blob/v0.22.1/aiosqlite/core.py).

### 3. The repository guard misses ordinary nested image writes

The shared-cache diagnosis is correct. `Settings.data_dir` is derived from
`database_path`; `extra="ignore"` silently discards a `data_dir=` constructor
argument. Using `database_path=":memory:"` consequently puts the cache under the
working directory. Correcting those fixtures is a valuable fix.

However,
[the new guard](/home/marcel/projects/lab/home-finder/.claude/worktrees/test-suite-quality-perf-b4e787/tests/conftest.py:125)
only compares the root directories' modification times. Writing
`image_cache/existing-home/new-image.jpg` does not change `image_cache/`'s mtime.
An actual invocation of the fixture completed without failing after that write.
Overwriting an existing image is similarly outside its check.

**Recommendation:** describe this as a top-level creation/change detector, or make
the guarantee stronger by checking write destinations in image-cache helpers or
using an isolated working directory. Avoid restoring an expensive recursive scan
after every test. The fixture corrections should be retained regardless.

### 4. Browser session settings still read local configuration

The root environment-isolation fixture is function-scoped, whereas
[browser server settings](/home/marcel/projects/lab/home-finder/.claude/worktrees/test-suite-quality-perf-b4e787/tests/test_web/browser/test_browser_e2e.py:234)
are session-scoped. The latter execute first and construct `Settings` without
`_env_file=None`. A harmless exported `HOME_FINDER_WEB_HOST=review.invalid` was
present in the constructed settings despite the autouse isolation fixture.
This ordering is documented pytest behavior.
[Fixture execution order](https://docs.pytest.org/en/stable/reference/fixtures.html#higher-scoped-fixtures-are-executed-first).

**Recommendation:** explicitly isolate the browser session's settings before their
construction, including exported variables and dotenv loading. The function-scoped
fixture is useful for ordinary tests, but the report's suite-wide claim is false.
The successful browser run above used the available environment; it does not prove
independence from arbitrary developer configuration.

## Performance findings: what holds

| Claim | Assessment |
| --- | --- |
| Full garbage collection and heap scanning dominate teardown | **Confirmed.** Our independent investigation found 2,608 summed teardown seconds in the original four-worker run. Controlled small-suite comparisons also isolated the effect. Remove this unconditional work. |
| Rich traceback rendering is expensive | **Confirmed.** Our controlled circuit-breaker test fell from about 6.7 call-seconds to 0.02–0.03 with plain tracebacks. Structlog selects rich formatting when installed. Keep events and assertions; simplify formatting. [Structlog source](https://www.structlog.org/en/stable/_modules/structlog/dev.html). |
| Production pacing should be recorded or bypassed in offline contract tests | **Confirmed.** Off-market, Telegram and provider delays are avoidable test cost. The named Telegram pause preserves `sleep(0)` scheduling opportunities and is a reasonable seam. Keep direct pacing assertions at both delivery and pipeline boundaries. |
| Badge rules need not run every model's full two-phase transport | **Supported.** The new direct reconciliation tests call production assembly/reconciliation, with separate native-transport wiring tests. This is a good complementary split. Retain model-specific provider coverage elsewhere. |
| Image generation can be cheaper | **Supported.** Vectorized/cached fixtures are appropriate where the detector-relevant pixels remain unchanged. Do not consolidate the different noise fixtures merely because they look similar. |
| Bytecode compilation helps cold collection | **Supported, with a wording correction.** `uv` can precompile dependencies, trading installation work for startup cost. `PYTHONDONTWRITEBYTECODE` prevents writing caches; it does **not** prevent reading valid existing caches. Pytest 9.1.1 reads its rewrite cache before deciding to rewrite. The report's claim of recompilation in every worker applies to missing/stale caches, not all runs with that variable set. [uv setting](https://docs.astral.sh/uv/reference/settings/#compile-bytecode), [Python flag](https://docs.python.org/3.14/using/cmdline.html#envvar-PYTHONDONTWRITEBYTECODE), [pytest implementation](https://github.com/pytest-dev/pytest/blob/9.1.1/src/_pytest/assertion/rewrite.py). |
| More workers eventually cost more startup than they save | **Supported.** Each xdist worker collects the suite. `-n auto` uses physical cores, which is not inherently the best choice when several sessions share the machine. The reported 8/12/16-worker results overlap; they establish no unique optimum. [xdist distribution](https://pytest-xdist.readthedocs.io/en/stable/distribution.html), [worker lifecycle](https://pytest-xdist.readthedocs.io/en/stable/how-it-works.html). |
| Remaining startup and subprocess costs are application/test-boundary costs | **Mostly supported.** Preserve tests of actual process exit and durable recovery. Some import/plugin overhead may still be reducible; it is not automatically irreducible application cost. |

The 12-second result is credible as a measured warm-cache, high-worker-count run.
It is not directly comparable to our four-worker 4,274-test CI-profile coverage run:
worker count, test population, instrumentation, bytecode state and host load differ.
Neither result should become a hard threshold without a stable measurement setting.

The timing plugin usefully separates setup, call, teardown, worker balance and the
overall test window. Its crash probe also behaved correctly. For the user's stated
contention concern, it should additionally retain worker count/configuration,
Hypothesis profile, coverage mode, Python/plugin versions, CPU time, memory and host
load. Currently most of that context exists only in the prose report. Its
"worker utilisation" is occupied test-phase wall time, including waits, not CPU
utilization; label it accordingly. The finishing interval can include report writing
and other session hooks, not just interpreter shutdown.

There is also an easy remaining observability improvement: the Rightmove whitespace
case still generates a **4,000,130-character node ID**. It inflates JSON, collection
messages and failure output. Give it a semantic parameter ID while preserving its
payload, as our parallel change already does.

## Coverage and completeness claims

The reproduced coverage result is:

| Metric | Covered / total | Percentage |
| --- | --- | --- |
| Statements | 12,554 / 13,515 | **92.89%** |
| Branch exits | 4,029 / 4,670 | **86.27%** |
| Combined coverage.py total | 16,583 / 18,185 | **91.19%** |

Thus "line and branch coverage is 91%" should say **combined statement/branch
coverage rounds to 91%**. There are 961 missing statements, 641 missing branch exits
and 471 partially covered branches in this run. The one-statement difference from
the report is small, but demonstrates why exact artifacts and run settings matter.
The CI Hypothesis profile also reached more grouping branches than the reported run.

Claude's configuration omits logging/data execution and does not enable subprocess
coverage. The backup CLI's 0% is therefore a measurement gap, as the report correctly
recognizes. It is fixable: pytest-cov 7 delegates subprocess collection to coverage's
`patch=["subprocess"]`; abrupt `_exit` tests need the corresponding additional patch.
Our parallel configuration already records these paths. Percentages from the two
configurations should not be ranked directly.
[pytest-cov subprocess support](https://pytest-cov.readthedocs.io/en/latest/subprocess-support.html).

"Every CLI command has at least one direct test" conflates dispatch with execution.
The CLI tests replace command functions with mocks. The final coverage artifact shows
**zero executed statements in `run_scrape_only` (7 statements)** and
**`_lookup_wards` (26 statements)**. Command routing is covered; those behaviors are
not. Our parallel work added direct tests for both and for the admin command wrappers.

Other reported gaps are well founded: detail-fetcher retry wrappers remain unexecuted;
Telegram callback acknowledgement and webhook registration have zero coverage in
Claude's snapshot; browser-only interactions are outside the normal CI gate; and
destructive maintenance scripts deserve focused tests. Our separate changes cover
the Telegram setup/acknowledgement gap but not the detail-fetcher retries or destructive
script workflows. Preserve those additions when combining the investigations.

The suite split and stronger HTML assertions are useful. In particular, actual card
order, selected/checked status, status history, traversal guards reached through valid
route shapes, persisted data after reconnect, and a real partial migration all test
more than the previous assertions. However, the browser suite retains weak checks:
the HTMX test accepts an unchanged page title as proof of no full reload, and the
bedroom/sort tests only inspect selected controls. A gallery regression can still
cause a skip. Our browser changes strengthen those exact cases and should survive
the file moves.

I sampled important rewrites and deletions, including badge reconciliation/wiring,
reconnect durability, migration progression, cached-image guards, pipeline support
and browser assertions. I did **not** establish behavioral equivalence for every
removed parameterization. Unchanged aggregate coverage and a sample of deletions
cannot prove that all 225 net removed cases were redundant. Maintain an explicit
old-contract-to-new-test mapping for further large reductions.

## Assessment of the remaining owner findings

| Finding | Assessment and action |
| --- | --- |
| Duplicated `dB` in the evaluation prompt | **Confirmed.** The data already contain the unit and the f-string appends it again. Fix the formatting and assert the actual outgoing prompt. |
| Unused helpers and legacy APIs | **Largely supported by repository call-site inspection.** Some call one another but have no application entry path; some are exported. Treat removal as a separate compatibility decision, not an automatic consequence of missing internal callers. `union_find.py` has no repository caller. |
| 59 `mark_notified` calls across 21 test files | **Count reproduced exactly.** Those create the legacy status projection, not a durable delivery. Use the delivery repository when a scenario claims a confirmed send. Keep direct legacy-status setup in tests specifically about that projection; replacing every call would blur those contracts. |
| Crawlee dependency | **Confirmed.** No source import; five application modules import BeautifulSoup directly. The lockfile has only Crawlee declaring BeautifulSoup. Replacing it with an explicit BeautifulSoup dependency is worth a separate locked-install/build check. `respx` is also unused by the tests inspected. |
| Unknown Settings keywords are silently ignored | **Confirmed.** Fix invalid test arguments now. Changing global `extra` policy also affects dotenv compatibility and deserves a deliberate configuration change. [Pydantic settings documentation](https://docs.pydantic.dev/latest/concepts/pydantic_settings/#dotenv-env-support). |
| Skip / Archive / Archived | **Confirmed label difference.** "Archive" as an action and "Archived" as its resulting state are coherent. Telegram's "Skip" deserves a product terminology decision; all three need not be identical words. |
| Fit score's no-confident-dimensions branch | **Supported for valid rated inputs today.** The condition dimension already gives a valid overall rating confidence. Treat this as a defensive/unreachable-under-current-input-invariants branch, not evidence that the feature is untested. |
| `_MAX_GROUP_SIZE` branch is unreachable | **Rejected.** Five ordinary matching listings from the four supported sources, with one source repeated, execute the size-limit `continue` before the later source-collision check. An unmocked traced production call reached line 703 and returned groups of four and one. The cap may be logically redundant with source uniqueness; its branch is executable. |
| No circuit check after visual image-building awaits | **Confirmed as a race window in the code, not a reproduced production failure.** This is a missing recheck, not an "unreachable branch." An event-controlled concurrency test should establish the desired behavior before changing it. |
| Repaired live quality evaluation | **Improved setup, still unverified live.** It now supplies image bytes, a ledger and a policy, and removes the obsolete bedroom override. Its images are 10×10 solid-red JPEGs, so this is structural API smoke coverage, not property-analysis accuracy evaluation. The ledger is recreated per test; the stated $5 policy is not a suite-wide spend ceiling. |

The phrase "tests that could not fail" should generally become "tests that would miss
the named regression." Several former assertions could fail for unrelated reasons;
the broken live evaluation could fail consistently. More precise language helps the
next reader distinguish vacuity, wrong setup, weak assertions and broken execution.

## Recommended integration order

1. Retain the verified assertion improvements, responsibility-based splits, image
   fixture optimizations, explicit pacing and corrected temporary data paths.
2. Correct and behaviorally test the template and cleanup plugins before adopting
   them. Repair session-settings isolation and either strengthen or narrow the cache
   guard's guarantee.
3. Combine observability: keep Claude's useful test-window/worker-balance summary and
   the richer environment, CPU, memory, fixture and crash metadata from our profiler.
   Preserve semantic parameter IDs and subprocess coverage.
4. Preserve complementary coverage added in our work: command execution, ward lookup,
   Telegram lifecycle, and actual browser outcomes. Avoid treating file moves as a
   reason to discard those tests.
5. Track detail-fetcher retry behavior, destructive maintenance commands and meaningful
   live model evaluation as remaining work. Decide browser CI frequency from measured
   CI cost and desired feedback, rather than assuming the offline default covers it.

No paid API calls, live property-portal checks or GitHub Actions runs were performed.
The focused five-test corrected-template run does not validate a fully enabled
template across the whole suite. The reviewed large restructuring still warrants
contract-level review when the two sets of changes are integrated.
