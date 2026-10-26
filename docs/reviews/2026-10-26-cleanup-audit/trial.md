The trial applied the best-evidenced deletions from the [cleanup audit](README.md) in a separate,
detached worktree of `51dd9c6` and ran the project's gates against them. Each finding became one
local commit, so any item can be reverted on its own. Those commits are not on any branch; they
are a proof that the batch is sound, and a starting point for the Batch B, C and D pull requests.

The environment was a fresh virtual environment in the worktree (`uv sync --locked --all-extras
--python 3.13`; the pinned 3.14.7 was unavailable). **Net: 32 commits, 54 files, +31 / −1,881 lines**
(src +16/−806, tests +7/−817, pyproject/uv.lock/.gitignore/root `main.py` +8/−258).

**Full-suite comparison.** The same command
(`HYPOTHESIS_PROFILE=ci pytest --cov --cov-report=json`, default `-n auto`, 4 CPUs) was run on
`main` and on the trial tree, each with the machine otherwise idle. `main`: 4,251 passed in
585.7 s. Trial: 4,213 passed in 123.5 s. The trial removed 38 small tests, so nearly all of the
difference comes from TESTS-1 (see [GC timing](#gc-timing-item-12)).

## Gate results (on HEAD `b49ba69`, all items applied)

| Gate | Result |
| --- | --- |
| `ruff check src tests` | All checks passed |
| `ruff format --check src tests` | 304 files already formatted |
| `mypy src` (strict) | Success: no issues found in 120 source files |
| Full suite `HYPOTHESIS_PROFILE=ci pytest --cov ...` | **4213 passed, 0 failed**, 5 warnings, pytest 123.5 s, wall 126 s, exit 0. Baseline `main` with the same command: 4251 passed, 4 warnings, 585.7 s. The -38 tests are exactly the deleted exclusive tests (9+6+4+12+2+2+3). Total coverage 91.13% -> 91.41%. |
| Coverage regression (functions covered in baseline, 0 covered lines in trial) | 1 hit, an artefact: `filters/off_market.py::OffMarketChecker.close` now has 0 statements (a docstring-only no-op after FILTERS-8), so it cannot have covered lines. No real regression, so no downgrade. 992 functions compared; the files missing from trial are the two deleted modules. Only per-file % drop >0.5 pt: row_mappers.py 92.3 -> 91.7 (covered dead lines removed). Output: the audit's scratch evidence. |
| `python -m home_finder.pipeline.{backup,delivery_admin,notification_report,recovery_admin,spend_admin,spend_report,rating_shadow_report} --help` and `home-finder --help` | All 8 rc=0. Run from an empty scratch cwd, which stayed empty. |
| vulture `src --min-confidence 60` before/after | 343 -> 315 lines; 30 items gone, 2 NEW (see cascade list) |
| Browser suite (`-m browser`) | NOT RUN: there are no Playwright browsers in this container (`~/.cache/ms-playwright` is absent). WEB-1/2/3 were checked only by the brace-balance check, `node --check` and `tests/test_web` (471 passed). |

New warning (5th): `ResourceWarning: unclosed database in <sqlite3.Connection>` is attributed to
`tests/test_analysis/test_analysis_output_failures.py::test_failed_required_phase_retains_usage_but_never_completed_facts[visual-missing_visual_field-malformed_output]`.
It comes from TESTS-1. The removed per-test `gc.collect()` used to finalise some test's leaked *stdlib* `sqlite3` connection inside fixture teardown, where the warning was not shown. Now the leak shows up wherever GC happens to run. The test does not fail, so TESTS-1 stays PASSED. It is a real leak to chase, though. Do not add a `filterwarnings=error` for leaks until it is fixed. To find it, use `PYTHONTRACEMALLOC=20`.

## Per-item results

All items below were **APPLIED+PASSED → Verified-by: trial**, unless a note says otherwise. None were reverted or skipped.

| # | Commit | Finding | Result | shortstat | Notes |
| --- | --- | --- | --- | --- | --- |
| 1 | 1fae581 | DB-2 enquiry accessors + delete_viewing_message | APPLIED+PASSED | 1 file, -56 | Only the methods and the section header were removed. Schema/migrations untouched. |
| 2 | 05d50fd | DB-10 dead `descriptions` computation | APPLIED+PASSED | 1 file, -7 | |
| 2 | df154c8 | DB-12 price_changed_at | APPLIED+PASSED | 2 files, +1/-5 | Subquery and TypedDict field removed. No template or test referenced either. |
| 2 | d2dda5f | DB-14 TypedDict re-exports | APPLIED+PASSED | 1 file, +1/-2 | |
| 2 | 27019aa | DB-17 function-local imports | APPLIED+PASSED (imports only) | 2 files, +2/-9 | FIT_SCORE_VERSION and RateCard hoisted. The `ImportError` catch and the lazy `assess_value` import were left alone, as scoped. |
| 3 | 203a363 | SCRAPERS-6 unused constants | APPLIED+PASSED | 4 files, -7 | |
| 3 | 30b610d | SCRAPERS-7/FILTERS-4 union_find.py | APPLIED+PASSED | 1 file, -29 | |
| 3 | 3c83907 | SCRAPERS-4 `_extract_listings_from_parsed` only | APPLIED+PASSED | 1 file, -4 | The other compatibility wrappers were kept, as scoped. |
| 4 | 587ab8e | FILTERS-8 OffMarketChecker httpx path | APPLIED+PASSED | 1 file, +7/-38 | Removed the httpx import, `_httpx_client`, `_get_httpx_client`, `_fetch_with_httpx` and the branch. `close()` is now a documented no-op, kept for `async with` and for the test. Also removed `_CURL_SOURCES`, the routing table that becomes unused once the branch goes. The `ConsecutiveFailureBreaker` import moved to the top (`noqa: E402` gone). Docstring fixed: Rightmove uses curl_cffi. The unused `source` parameter of `_fetch` was kept because tests patch `_fetch(source, url)`. |
| 5 | 9d2c7f2 | FILTERS-5 coordinates_match/prices_match | APPLIED+PASSED | 2 files, -216 | TestFuzzyPriceMatching (4) and TestCoordinateMatching (5) deleted. The haversine and full-postcode tests were kept. |
| 6 | ffb5934 | ANALYSIS-3 `_extract_eval_json`/`_sanitize_eval_enums` | APPLIED+PASSED | 2 files, +1/-80 | 6 of the 7 TestEvalHelpers tests deleted. The kept `test_invalid_live_evaluation_is_rejected` got a new class docstring. |
| 6 | ba9d7df | ANALYSIS-5 strict branch | APPLIED+PASSED | 1 file, -4 | VISUAL_ANALYSIS_TOOL sha256 is unchanged (ac567fdc…), so checkpoint identity holds. |
| 6 | 7769067 | ANALYSIS-10 SQM_PER_SQFT + re-exports | APPLIED+PASSED | 2 files, -10 | Dropped the CoercedBool/TriStateBool/SQM_PER_SQFT exports from models `__init__`. |
| 6 | ad9420c | ANALYSIS-13 legacy string branches | APPLIED+PASSED | 1 file, +1/-4 | `mypy --warn-unreachable` is clean on area_context.py. |
| 6 | 46b5998 | ANALYSIS-14 upper_square_metres + NOT_CONFIGURED | APPLIED+PASSED | 2 files, -5 | CommuteResult is not persisted, so no stored values are at risk. |
| 6 | da5d703 | ANALYSIS-7 four cost tables | APPLIED+PASSED | 4 files, -87 | Removed the constants, `ServiceChargeRange`, the 4 JSON sections (still valid JSON) and TestCostData (4). The negative test is kept and now patches only COUNCIL_TAX_MONTHLY. |
| 7 | f8c2a56 | SCRAPERS-3 floorplan_detector.py + test | APPLIED+PASSED | 3 files, -346 | Also removed the dangling "Follows the same pattern as floorplan_detector.py" line in the epc_detector docstring. **Still needed:** ADR 002 "Retire heuristic floorplan detection", with ADR 001 marked superseded, plus fixes to CLAUDE.md:82/126/163 and docs/sources/README.md:46. No ADR was written. |
| 8 | 85775a7 | SCRAPERS-11 reverse_lookup_ward | APPLIED+PASSED | 2 files, -69 | TestReverseLookupWard (2) deleted. |
| 8 | 5603e16 | PIPELINE-11 run_dry_run | APPLIED+PASSED | 2 files, +2/-11 | The test was retargeted to `run_pipeline(settings, dry_run=True, storage=...)`, a trivial change, and passes. |
| 8 | 6e73fbd | PIPELINE-12 record_batch/.run_id | APPLIED+PASSED | 2 files, -32 | Retargeting gave nothing unique (it duplicated `test_record_and_flush`), so `test_record_batch` and `test_run_id_property` were deleted. |
| 8 | dab1327 | DB-4 mark_enriched | APPLIED+PASSED | 3 files, +1/-92 | Precondition verified before the change: the cited lifecycle tests exist (test_storage_analysis_retry `test_on_conflict_updates_commute_and_coords`, test_storage_dropped `pending_enrichment → dropped/enriched`) and passed (37/37). TestMarkEnriched (3) deleted. test_enrichment_admission.py now uses `save_pre_analysis_properties(selected, {})` and passes. |
| 8 | 7ad8ac6 | PIPELINE-19 root main.py | APPLIED+PASSED | 1 file, -6 | |
| 9 | 614341d | WEB-1 fit-breakdown CSS/JS | APPLIED+PASSED (browser suite not run) | 2 files, +3/-189 | Applied the exact listed ranges, which were verified by content before deletion. sections.js:1-16 removed (`node --check` ok). |
| 9 | 3ac78f2 | WEB-2 CERTAIN group only | APPLIED+PASSED | 1 file, +1/-62 | card-actions-visible, badge-estimate, meta-dim, benchmark-current, area-explore-link, rating-marker, map-popup-stars, signal-*, negotiation-source. The second (HIGH) group was not touched. |
| 9 | 9cb8103 | WEB-3 _status_badge.html + .status-badge | APPLIED+PASSED | 2 files, -17 | |
| 9 | c9b18ea | WEB-4 area-context keys | APPLIED+PASSED | 1 file, -11 | CREATIVE_SCENE/HOSTING_TOLERANCE imports dropped. NOISE_ENFORCEMENT is kept because area_detail still uses it. |
| 9 | 4814678 | WEB-5 context keys (CERTAIN part) | APPLIED+PASSED | 1 file, -6 | Removed dashboard source_names, any_quality_filter_active and user_statuses, and detail source_badges, fit_breakdown and user_statuses. `quality_fields_active` and its tests (the HIGH part) and the optional `"request"` entries were left alone. |
| 10 | 3fca71d | TESTS-5 Crawlee fixtures | APPLIED+PASSED | 8 files, +1/-91 | Removed the fixtures, the 5 usefixtures, the integration/conftest comment, the unused `Generator` import and the pyproject E402 per-file ignore. `ruff --isolated --select E402 tests/integration` is clean. All 205 integration and leakage tests collect. |
| 10 | 588b3c3 | TESTS-10 / SCRAPERS-15 fixture files | APPLIED+PASSED | 2 files, -101 | Deleted onthemarket_search.html and traveltime_response.json. The dynamic `f"{param}_search.html"` covers only openrent and zoopla. |
| 10 | ed5d7d6 | TESTS-11 unused fixtures | APPLIED+PASSED | 2 files, -24 | `mock_response` and `prop_c` deleted, plus the `pytest` import that became unused. |
| 11 | 3f00634 | SCRAPERS-1 / TESTS-16 dependencies | APPLIED+PASSED | 3 files, +8/-251 | See the lock summary below. |
| 12 | b49ba69 | TESTS-1 gc scan | APPLIED+PASSED | 1 file, +2/-10 | The thread-scan fallback is kept. Timing below; for the ResourceWarning caveat, see above. |

## Dependency lock summary (item 11)

- pyproject: `crawlee[beautifulsoup]` replaced by `beautifulsoup4>=4.14` and `lxml>=6.1.0`. lxml is needed because `rightmove.py:209` calls `BeautifulSoup(html, "lxml")`; every other site uses `html.parser`. bs4 is imported by openrent, rightmove, rightmove_payloads, zoopla and filters/availability. Dev extras lose `dirty-equals` and `respx`. In `[tool.uv] constraint-dependencies`, `protego>=0.6.2` was dropped: in the old lock, crawlee was its only dependent. The `lxml>=6.1.0` and `soupsieve` floors are kept. `.gitignore` loses the `# Crawlee storage` / `storage/` lines.
- `uv lock --python 3.13`: resolved 117 packages, down from 132. **Removed (15):** async-timeout 5.0.1, cachetools 6.2.6, crawlee 1.7.0, dirty-equals 0.11, filelock 3.20.3, html5lib 1.1, impit 0.9.3, more-itertools 10.8.0, protego 0.6.2, psutil 7.2.2, requests-file 3.0.1, respx 0.23.1, six 1.17.0, tldextract 5.3.1, webencodings 0.5.1. **Added: none. Version changes: none**, checked programmatically against the old lock. A plain `uv lock` failed because `.python-version` pins 3.14.7, so `--python 3.13` was used.
- The only non-removal change in uv.lock is that httpcore2's `h11`/`truststore` dependencies lost the environment marker `python_full_version != '3.12.*' or sys_platform != 'emscripten'`, a fork marker that crawlee's resolution had introduced. Top-level `resolution-markers` are unchanged.
- `uv sync --locked --all-extras --python 3.13` uninstalled 17 packages: the 15 above plus colorama (on Linux it was pulled in only by crawlee; it stays in the lock for its win32-marked dependents). `import crawlee` now fails as expected, and `BeautifulSoup('<p>x</p>', 'lxml')` works (bs4 4.14.3, lxml 6.1.0).
- deptry (main venv binary, `--known-first-party home_finder`): crawlee, dirty-equals and respx DEP002 are gone, and the bs4 DEP003 ×5 are gone. A new DEP002 for `lxml` is a false positive, because lxml is used as a parser name, not imported. deptry is not in CI.
- Not done (docs / ops): CLAUDE.md:98 (Rightmove is httpx + bs4/lxml) and :187 (Crawlee "autouse fixtures") are stale. The Docker build was not run.

## GC timing (item 12)

Command: `PYTHONUNBUFFERED=1 .venv/bin/pytest tests/test_db tests/test_web -q -p no:cacheprovider` (default `-n auto`, 4 CPUs). All other items were already applied, so the only variable was the gc scan.

| Variant | Run 1 | Run 2 |
| --- | --- | --- |
| gc scan present | 886 passed, pytest 94.58 s (wall 95.9 s) | 886 passed, 89.70 s (wall 91.0 s) |
| gc scan removed | 886 passed, pytest 19.80 s (wall 21.1 s) | 886 passed, 20.36 s (wall 21.8 s) |

That is roughly a 4.5x speed-up on DB/web tests. The full default suite took 123.5 s in this trial versus 585.7 s on `main` with the same command; most of the gap is this fixture. Neither variant printed a "leaked aiosqlite" warning.

## Vulture cascade candidates (not applied)

`vulture src --min-confidence 60` before/after, compared ignoring line numbers . It reports **NEW** items:
- `src/home_finder/data/area_context.py: unused variable 'CREATIVE_SCENE'`. After WEB-4 only `tests/test_data/test_area_context.py` reads it. With it go the `CreativeScene` TypedDict and the outcode-level `creative_scene` JSON block. The micro-area `creative_scene` stays, because area.html uses it. This is ANALYSIS-8, which needs an owner decision on venue data.
- `src/home_finder/web/filters.py: unused property 'quality_fields_active'`. After WEB-5 only `TestQualityFieldsActive` reads it. This is the HIGH half of WEB-5.

`vulture src tests` before/after: 0 new items.

Manual cascades vulture cannot see:
- style.css, 480px media block: the `.fit-breakdown-card h4` half of `.fit-breakdown-card h4, .fit-breakdown-card .fit-brief-title` is just as dead as the :1446 half WEB-1 removed. It was not in WEB-1's location list, so it was left.
- `OffMarketChecker.close()` is now a no-op. The context manager and `close` can go once test_async_context_managers.py:63-70 and the e2e `close` patches are updated.
- `CheckableResponse` docstring ("Both httpx.Response and _CurlResponseAdapter…") is still true only because tests use httpx.Response fakes.
- Docs now stale because of the deletions: CLAUDE.md:82/126/163 (floorplan heuristic), :98/:187 (Crawlee), docs/sources/README.md:46, ADR 001 (needs superseding by ADR 002).

## Downgrades

None. Every applied item passed every gate. The only coverage-gate hit (`OffMarketChecker.close`) is a zero-statement artefact, not lost test coverage of retained code.
