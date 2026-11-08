# Assessment of the parallel Codex test-suite audit

> Historical snapshot. The [integration report](2026-11-07-test-suite-integration.md) describes the implemented combined suite. Counts, paths, timings and worker-cap recommendations below describe the earlier branch or verification state.

7 November 2026. Assesses `docs/reviews/2026-11-07-test-suite-audit.md` in the Codex
worktree (`~/.codex/worktrees/7e10/home-finder`), its code changes, and its companion
review of this branch (`2026-11-07-claude-test-suite-assessment.md`). Both
investigations started from 51dd9c6.

## Verdict

The Codex audit is careful, well measured and mostly right. Its performance diagnosis
matches this branch's. It found six real problems this branch had missed, all now
fixed here:

- live tests ran with sockets blocked;
- leak warnings were invisible;
- a racy HTMX wait in the browser tests;
- weak browser assertions;
- a 4-million-character test ID;
- subprocess code missing from coverage.

Its review of this branch found two serious defects in our work, both confirmed and
fixed: the schema template never ran, and the leak check stopped threads without
closing their databases.

Where it falls short, it does so by leaving cost and defects in place rather than by
being wrong:

- Telegram's 1-second pauses and per-test migrations were left in.
- The shared-image-cache flake was not found.
- The directory-traversal test still cannot reach its guard.
- The 3,200-line routes file, shadowed notifier fixtures and dead Crawlee fixtures
  remain.

Its four-worker cap trades about 26% more wall time for CPU it did not need to give up
(see below).

## How it was checked

- Every claim was checked against the code of both branches. "Confirmed" means
  reproduced or read directly in the code; "plausible" means consistent with the code
  but not reproduced.
- Primary documentation was read for the disputed points:
  - [pytest warning capture](https://docs.pytest.org/en/stable/how-to/capture-warnings.html):
    only DeprecationWarning and PendingDeprecationWarning are shown by default.
  - [coverage.py `[run] patch`](https://coverage.readthedocs.io/en/latest/config.html):
    added in 7.10.
  - [pytest-cov subprocess support](https://pytest-cov.readthedocs.io/en/latest/subprocess-support.html):
    "Subprocess support was removed in pytest-cov 7.0".
  - [pytest-socket](https://github.com/miketheman/pytest-socket):
    `--force-enable-socket` takes precedence over `--disable-socket`.
  - [xdist distribution](https://pytest-xdist.readthedocs.io/en/stable/distribution.html):
    `-n auto` uses physical cores.
- Installed versions: pytest 9.1.1, xdist 3.8.0, pytest-cov 7.1.0, coverage 7.13.2,
  aiosqlite 0.22.1, urllib3 2.8.0.
- Both suites ran interleaved on the same host, with warm bytecode, from a copy of the
  Codex tree, so its worktree was not touched.

## Its findings

| Finding | Assessment | Evidence |
| --- | --- | --- |
| Heap scan after every test dominated the run (710 s → 72 s at 4 workers) | **Confirmed.** Same root cause found here (151 s → 12 s at 16 workers). | Teardown was 2,118 of 2,280 test-seconds in this branch's HEAD measurement |
| Rich traceback rendering dominated failure-path tests | **Confirmed.** | Codex measured 6.7 s → 0.02 s; here it was 8 s of an 11 s test |
| Off-market pacing cost 27 s | **Confirmed.** | Zoopla's 2 s delay × 8 and × 5 requests |
| The aiosqlite leak warning was invisible | **Confirmed.** | A deliberately leaking test produced no warning unless run with `-W always::ResourceWarning`, as pytest's documented defaults predict. Codex switched to `PytestWarning`; so did this branch |
| Leak checks must allow closing threads to finish and spare connections that existed before the test | **Confirmed.** | Once the warning was visible, this branch's checker reported false leaks under load. `close()` returns when the worker acknowledges the stop, a moment before the thread exits. A brief join and a before/after thread snapshot fixed it |
| A hidden leak in the full-run fixture (close patched to a no-op) | **Confirmed in HEAD's code.** | This branch had already replaced that helper with real file databases that `run_pipeline` opens and closes |
| The live job needs sockets | **Confirmed, and wider than its fix.** | 43 of 51 slow tests ran with Python sockets blocked, including Rightmove's httpx scraper, so they could not reach the portal. Codex added `--force-enable-socket` to the CI command only. This branch marks `slow` tests `enable_socket` in the root conftest, so local `pytest -m slow` works too |
| A malformed-payload case produced a 4-million-character test ID | **Confirmed.** | 4,000,130 characters here. Semantic IDs (plus two other hostile inputs) bring the longest to 893 |
| Socket warnings come from urllib3's import-time IPv6 probe | **Confirmed.** | `urllib3/util/connection.py:137` runs `HAS_IPV6 = _has_ipv6("::1")`; `CommuteAssessor` imports traveltimepy lazily inside a test. Importing it at collection removes the warnings |
| The HTMX wait registered its listener after the action | **Confirmed by reading the code.** | `wait_for_htmx_settle` ran after `click()`/`select_option()`; a fast settle would leave it waiting 5 s and failing. Codex's auto-retrying `expect(...)` checks are ported; 77 browser tests pass |
| Browser filter, sort and gallery checks were weak | **Confirmed.** | Bedroom, area and sort tests checked only the selected control; the gallery test skipped when the image was missing. Ported |
| Subprocess code is missing from coverage under pytest-cov 7 | **Confirmed.** | With `patch = ["subprocess", "_exit"]`, `pipeline/backup.py` goes from 0% to 90% |
| Coverage 93.87% statements, 86.79% branch exits, 92.05% combined (4,274 tests) | **Plausible.** | The same configuration on this branch gives 94.00% / 87.00% / 92.20% with 4,049 tests |
| A four-worker cap is a "measured practical cap" | **Partly.** | See the comparison below: 8 workers match 16 on wall time with 41% less CPU. Four is a reasonable setting for a heavily shared machine, not the best default |
| Acoustic tests used a copied mapping; mutation testing proves the replacement | **Confirmed;** both branches replaced it | — |
| `test_quality_eval.py` is not a working live gate | **Confirmed.** | This branch repaired its setup, but its 10×10 solid images make it a structural smoke test, as Codex's review notes |
| The detail-fetcher retry wrappers never execute | **Confirmed** | Both branches list it as open |
| Run-scrape-only, ward lookup, admin CLIs and Telegram startup/acknowledgement lacked execution tests | **Confirmed.** | This branch's report wrongly said every CLI command had a direct test; dispatch was tested with the commands mocked. The 19 Codex tests are adopted. Ward lookup and Telegram startup use real storage (ward lookup reopens it), faking only the HTTP and Bot API boundaries; the admin CLI and scrape-only tests are argument contracts with the command internals faked, as their docstrings say |

### What it missed or left

- **Telegram pacing:** 1 second after each delivered plan, and after each send attempt
  in the pipeline. It is about 3 s per recovery test and on the critical path. Codex
  kept these as "residual sleeps".
- **Migrations per test:** 10–35 ms for each of about 1,600 fresh databases.
- **The cross-worker image-cache flake:** `Settings` derives `data_dir` from
  `database_path` and silently ignores a `data_dir=` keyword. So
  `database_path=":memory:", data_dir=str(tmp_path)` writes into the repository's
  `image_cache/`, shared by every worker. The Codex tree still does this in
  `test_recovery.py` and `test_run_observability.py`.
- **Vacuous security test:** `test_blocks_directory_traversal` asserts `!= 200`, but
  httpx removes `/../` before sending, so the guard never runs.
- **Structure:** the 3,219-line `test_routes.py` and 3,551-line `test_analyzer.py`
  (Codex split the latter into three), notifier fixtures that shadow root fixtures with
  different data, the root-level `notifier_helpers.py`, and dead Crawlee state
  fixtures.
- **Unwritten bytecode:** agent shells set `PYTHONDONTWRITEBYTECODE=1`, so bytecode and
  pytest's rewrite cache are never written.
- **Production findings it did not list:** the evaluation prompt's "dB dB", unused
  Crawlee and the BeautifulSoup dependency it hides, and code with no production
  caller. Codex's review of this branch confirms these.

## The same host, side by side

Both suites ran at a load average of 1–8, with warm caches and two repetitions each.
"Changed" is this branch before the template fix below.

| Suite | Tests | 4 workers: wall / CPU | 16 workers: wall / CPU |
| --- | --- | --- | --- |
| Codex | 4,274 | 25.9–26.4 s / 83 s | 17.5 s / 192–199 s |
| This branch | 4,026 | 17.7–18.1 s / 78 s | 13.3 s / 192–194 s |

After the corrections below (4,049 tests, the template working):

| Workers | Wall | CPU |
| --- | --- | --- |
| 4 | 16.0–16.2 s | 69–71 s |
| 8 | 12.7–13.0 s | 104–106 s |
| 16 (`-n auto`) | 12.5–12.9 s | 178–180 s |

The default now caps at 8 workers (`--maxprocesses=8`). Use `-n 4` when several
sessions share the machine.

## Its review of this branch

| Correction | Assessment | Change here |
| --- | --- | --- |
| The schema template never ran | **Confirmed.** `PropertyStorage` uses `aiosqlite.Row`, which never equals the tuple `(0, 0)`, so every database migrated for real. The guard test compared two normally migrated databases. File databases in WAL mode also did not see a copied schema on the same connection. The earlier report's template saving was really a change in host load. | The template applies to in-memory databases only, and a new test fails if a fresh storage does not take the fast path. Measured saving: 17.0 s → 14.7 s at 4 workers |
| Stopping a worker thread does not close its database | **Confirmed.** A sentinel ends the thread but leaves the SQLite handle, and any lock it holds, open. It also stopped connections owned by longer-lived fixtures. | Only threads started during the test count. A leak is closed with `Connection.stop()`, which closes SQLite on its own thread. New tests: the lock is released; an older connection stays usable; a closed one is not reported |
| The repository guard misses nested writes | **Confirmed.** A directory's mtime changes only when its direct entries change. | The docstring now states what the check sees. A recursive scan after every test would bring back the cost this work removed |
| The browser session's settings still read local configuration | **Confirmed.** Session fixtures run before the function-scoped isolation fixture. | The session fixture drops `HOME_FINDER_*` and the dotenv file itself. The browser suite passes with hostile variables exported |
| `PYTHONDONTWRITEBYTECODE` prevents writing, not reading | **Correct.** | Docs reworded |
| "91% line and branch coverage" is the combined figure | **Correct.** | The report now gives statements and branch exits separately |
| "Every CLI command has a direct test" conflates dispatch with execution | **Correct.** | Codex's execution tests adopted |
| `_MAX_GROUP_SIZE` is reachable | **Correct.** The size check runs before the same-source check; it is redundant, not unreachable, and executed by the suite. | Report corrected |
| The missing circuit-breaker recheck is a race window, not an unreachable branch | **Correct.** | Report corrected |
| The timing report needs its run context, and "utilisation" is a misleading label | **Fair.** | The JSON now records worker settings, the Hypothesis profile, coverage, load averages and CPU seconds; the label reads "workers inside tests for N% of the test window" |
| Aggregate coverage cannot prove each of about 225 removed cases redundant | **Fair.** Coverage did not fall, under either configuration. Each agent cited a covering test for every deletion. An independent review sampled about 25 of them, and Codex's review sampled others. | No exhaustive old-to-new mapping exists in the repository; that remains open |

## Remaining work

- Detail-fetcher retry and throttling paths.
- Tests for the destructive maintenance scripts (`audit_merges.py`).
- A real live model evaluation.
- How often browser tests run in CI.
- An old-to-new mapping for the removed tests.
- Owner decisions: the "dB dB" prompt fix, deleting code with no production caller,
  replacing Crawlee with BeautifulSoup.
- Combining the two branches: this branch now carries the Codex fixes listed above.
  Codex's profiler, its inventory and coverage documents, its CI JUnit and summary
  steps, and its structural edits to files this branch moved were not merged.
