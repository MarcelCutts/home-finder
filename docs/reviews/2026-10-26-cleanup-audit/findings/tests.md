# TESTS area findings

Scope: tests/ (184 .py files, 65,270 lines; 4,380 collected, 4,251 in the default selection), tests/fixtures/, tests/support/,
tests/notifier_helpers.py, pyproject `[tool.pytest.ini_options]` / `[tool.mutmut]`, mutmut_config.py.
Method: AST scripts in the scratchpad (`fixture_usage.py`, `helper_dups.py`, `symbol_tests.py`, `no_assert.py`, `fixture_files.py`),
`git grep` over the whole tree including the PR #77 branch, and targeted `-n0` runs of 3 files. A scratch socket probe ran
outside the repo against the project's pyproject.toml. No portal, Anthropic or Telegram network calls were made.

---

### TESTS-1: Autouse aiosqlite leak fixture runs a full-heap gc scan after every test (~70% of DB-test wall time)
- Category: OVERBUILT  | Confidence: HIGH | Action: SIMPLIFY | Risk: low | Effort: S | Size: ~20 lines
- Location: tests/conftest.py:102-144 (`_cleanup_aiosqlite_threads`)
- Evidence: After each of the 4,251 default tests the fixture runs `gc.collect()` and then `for obj in gc.get_objects(): isinstance(obj, Connection)`.
  I ran `pytest -n0 tests/test_db/test_storage.py tests/test_notifiers/test_delivery_recovery.py --durations=0` on 87 tests.
  With the fixture as is: teardown 23.68s (avg 0.272s/test), call 9.76s, run 36s.
  With a scratch `-p` plugin that turns `gc.collect`/`gc.get_objects` into no-ops: teardown 0.02s, call 10.02s, run 18.0s. All 87 still pass.
  Even `tests/test_utils/test_address.py`, which has no DB, spends 0.06–0.07s of teardown per test.
  Running 3 DB-heavy files with `-W always::ResourceWarning` produced no "leaked aiosqlite connection" warnings.
  Caveat: the machine was CPU-contended by the coverage run, so the absolute numbers are inflated. The ratio is the point.
  Strategy 2 in the same fixture (scan `threading.enumerate()` for `_connection_worker_thread` and inject a sentinel) is cheap, and on its own it addresses the stated problem (non-daemon threads blocking exit).
- Recommendation: Drop Strategy 1 (gc scan), or move it to a single `pytest_sessionfinish` check. Keep the thread scan.
  Consider `filterwarnings = ["error:Test leaked aiosqlite"]` so leaks get fixed rather than silently repaired.
  This is probably the single largest default-suite speedup available.
- Prior item: none (prior review mentioned the fixture) | Owner decision needed: no

### TESTS-2: The "slow" live suite cannot reach the network: sockets are blocked, so live tests fail or silently skip (CI job included)
- Category: CONFIG  | Confidence: CERTAIN | Action: ASK-OWNER (delete, or replace with an explicit smoke procedure) | Risk: low | Effort: M | Size: ~1,100 lines
- Location: pyproject.toml:56 (`addopts ... --disable-socket --allow-unix-socket`); .github/workflows/checks.yml:118-137 (`slow-tests`: `uv run pytest -m slow -v --timeout=120`);
  tests/integration/test_real_scraping.py (344), test_detail_fetcher_e2e.py (317), test_enrichment_pipeline_e2e.py (128), test_cross_platform_dedup_e2e.py (134);
  tests/test_scrapers/test_scraper_location_leakage.py:273-458; tests/test_analysis/test_quality_eval.py (226)
- Evidence: A `-m slow` test that creates a TCP socket, run with `pytest -c pyproject.toml -m slow`, failed with `pytest_socket.SocketBlockedError`.
  Only browser-marked items get `enable_socket` (tests/test_web/conftest.py:44-66). No other slow test or the CI command sets it,
  and `git grep enable_socket|force-enable-socket` finds nothing for them.
  The helpers turn the block into skips or passes:
  - `_scrape_platform` has `except Exception: return []` (test_cross_platform_dedup_e2e.py:24-38).
  - `if not properties: pytest.skip(...)` patterns.
  - `assert len(with_images) >= 0  # Soft assertion` (test_enrichment_pipeline_e2e.py:56).
  - `if properties:` guards in test_real_scraping.
  CLAUDE.md:54 ("`pytest -m slow` Run slow tests (real network scraping)") and README.md:253 are therefore contradicted.
- Recommendation: Per the A20 disposition, remove the live CI job, or replace it with a controlled source smoke procedure that explicitly enables sockets and has fail-not-skip assertions.
  Delete or rewrite the live modules at the same time. Keep the browser sort-verification tests only if someone runs them deliberately.
- Prior item: A20 (OUTSTANDING) | Owner decision needed: yes

### TESTS-3: Live Claude evaluation test cannot work and would spend money if "fixed" naively
- Category: TEST-HYGIENE  | Confidence: HIGH | Action: DELETE (or move to experiments/) | Risk: low | Effort: S | Size: ~226 lines
- Location: tests/test_analysis/test_quality_eval.py:137-145
- Evidence:
  - The fixture builds `PropertyQualityAnalyzer(api_key=..., max_images=5, enable_extended_thinking=True)` with no `spend_repository`/`spend_policy`.
    The constructor docstring says "absent caps defer every real SDK request" (src/home_finder/analysis/analyzer.py:798-799).
    So `analysis` is None or deferred, and `assert analysis.overall_rating ...` cannot pass.
  - The `--disable-socket` block (TESTS-2) also applies.
  - Gallery images are remote URLs that are not cached to disk, but analysis reads only the disk cache.
  - It runs only if ANTHROPIC_API_KEY is set.
  - The model bake-off and shadow-report tooling (CLAUDE.md "Quality Analysis") has superseded this kind of evaluation.
- Recommendation: Delete. If a live check is wanted, it belongs with the bounded experiment tooling, behind an explicit spend policy.
- Prior item: A12 | Owner decision needed: yes (paid spend)

### TESTS-4: Location-leakage module tests its own helper copies; its live part can never fail
- Category: TEST-HYGIENE  | Confidence: HIGH | Action: DELETE most, MOVE URL assertions | Risk: low | Effort: S | Size: ~430 of 541 lines
- Location: tests/test_scrapers/test_scraper_location_leakage.py:25-159 (constants plus `extract_outcode`, `categorize_property_location`), 273-458, 461-541
- Evidence:
  - `TestPostcodeExtraction` and `TestLocationCategorization` (461-541) exercise test-local functions: a regex copy of
    `home_finder.utils.address.extract_outcode` (src/home_finder/utils/address.py:138) and a test-only `categorize_property_location`. No production code is exercised.
  - `TestLiveScraperLocationLeakage` (7 tests) has no assertions (`no_assert.py`). It skips on empty results, and its failure is commented out:
    `# Comment out the pytest.fail if you just want to gather data` / `# pytest.fail(` at 454-458. The helper docstring nevertheless says "Fails if significant leakage is detected".
  - The docstring "Run with: pytest -m integration" runs it with sockets blocked.
  - The comment "Target search areas from main.py" (line 24) is stale.
  - `TestScraperUrlConstruction` (162-271) carries the unique portal URL assertions that A12 asks to keep.
- Recommendation: Move `TestScraperUrlConstruction` to a `test_search_urls.py` module or into the per-portal modules, then delete the rest.
  `LocationFilter` already has production tests in tests/test_filters/test_location.py.
- Prior item: A12 (OUTSTANDING); also docs/reviews/B4-testing.md "soft assertions in leakage tests" | Owner decision needed: no

### TESTS-5: Crawlee state-reset fixtures and E402 ignore are dead: src no longer imports crawlee
- Category: DEAD  | Confidence: CERTAIN | Action: DELETE | Risk: low | Effort: S | Size: ~85 lines
- Location: tests/conftest.py:417-491; tests/integration/conftest.py:12-13; `pytestmark = pytest.mark.usefixtures("reset_crawlee_state", "set_crawlee_storage_dir")` in
  tests/integration/test_cross_platform_dedup_e2e.py:11, test_detail_fetcher_e2e.py:10, test_enrichment_pipeline_e2e.py:8, test_real_scraping.py:11;
  tests/test_scrapers/test_scraper_location_leakage.py:276; pyproject.toml:78 (`"tests/integration/*.py" = ["E402"]  # imports after pytestmark for Crawlee state reset`)
- Evidence: `git grep -n -i crawlee -- src` returns 0 hits. deptry reports "DEP002 'crawlee' defined as a dependency but not used".
  The fixture body is guarded by `except (ImportError, AttributeError): pass` everywhere.
  The E402 ignore exists only because these 4 files put `pytestmark` above their imports (`ruff check --isolated --select E402` flags exactly these files).
- Recommendation: Delete both fixtures and the five usefixtures references, move imports to the top, and drop the per-file E402 ignore.
  Do this together with removing the `crawlee` dependency (A1, deps auditor) and fixing CLAUDE.md:98 and :187.
- Prior item: A1 (OUTSTANDING) | Owner decision needed: no

### TESTS-6: Superseded PropertyStorage writers survive as the suite's seeding API (40 files)
- Category: TEST-ONLY  | Confidence: HIGH | Action: MOVE seeding to tests/support, then let the DB cleanup delete the methods | Risk: medium | Effort: L | Size: ~500 call sites
- Location: src/home_finder/db/storage.py:376 `save_property`, :850 `save_merged_property`, :502 `mark_notified`, :524 `mark_notification_failed`,
  :472 `get_pending_notifications`, :486 `get_unsent_notifications`, :417 `is_seen`, :572 `filter_new`, :461 `get_all_properties`, :602 `delete_property`, :1262 `link_source_listings`;
  read helpers :440 `get_property`, :1428 `get_quality_analysis`, web_queries.py:606 `get_property_count`
- Evidence: `git grep -w` finds no caller in src/ or scripts/ for any of them. Test usage:
  - `save_merged_property`: 315 refs in 40 files (test_web/test_routes.py 128, test_db/test_storage_quality.py 36, test_price_history 18, test_off_market 16, test_storage 12, ...).
    It writes `notification_status=PENDING` directly, which is the competing-writer problem A16 names.
  - `save_property`: 58 refs / 9 files.
  - `mark_notified`: 59 / 20 (test_transaction_ownership 15, integration/test_off_market_e2e 10, test_recovery 5, ...).
  - `get_property`: 68 / 20.
  - `get_quality_analysis`: 41 / 19.
  - `get_property_count`: 16 / 8.
  - Legacy-API classes: test_db/test_storage.py::TestPropertyStorage. Of its 17 tests, 4 use `get_unsent_notifications`, 4 `mark_notification_failed`, 1 `get_pending_notifications`, 1 `filter_new` and 2 `is_seen`.
  - Also test_db/test_storage_enrichment.py::TestDeleteProperty and test_storage_analysis_retry.py::TestNotificationRetryExcludesPendingAnalysis.
  - No tests/test_db/conftest.py or seeding module exists.
- Recommendation: Add `tests/support/storage.py` with explicit seeds, written either via production entry points (pre-analysis save / analysis completion) or via documented direct SQL.
  Migrate callers file by file, then delete the writers in the DB cleanup.
  Read helpers like `get_property`/`get_quality_analysis` may be kept as deliberate inspection API (A15 caveat) or moved to support.
  Delete the legacy notification-status tests in TestPropertyStorage together with their methods. Keep any unique upgrade-read assertions.
- Prior item: A16 (OUTSTANDING), A15 | Owner decision needed: yes (which read helpers are deliberate API)

### TESTS-7: Test-only production wrappers and the test classes coupled to them (retarget or delete together)
- Category: TEST-ONLY  | Confidence: HIGH | Action: see per-row | Risk: low–medium | Effort: M | Size: ~1,000 test lines affected
- Location / Evidence (`symbol_tests.py` AST map; src callers = 0 for every symbol per `git grep -w -- src scripts`):
  | Production symbol | Coupled tests | Suggested action |
  | --- | --- | --- |
  | `Deduplicator.deduplicate_and_merge_async` (matching/deduplication.py:129) | test_matching/test_deduplication_e2e.py::TestDeduplicateAndMergeE2E (11/11), TestDeduplicateAndMergeAsyncE2E (10/10), TestDeduplicationInvariants (2/3), TestRealWorldEdgeCases (4/8); integration/test_selection_persistence.py TestSelectionAndPersistence (2/3), TestEdgeCases (2/3); test_cross_platform_dedup_e2e (3), test_real_scraping (1) | Retarget to `deduplicate_merged_async(properties_to_merged(...))` (the production path in pipeline/candidates.py:103), then delete the wrapper |
  | `PropertyQualityAnalyzer.analyze_merged_properties` (analyzer.py:1188) | test_analysis/test_analyzer.py::TestPropertyQualityAnalyzer (16/20), TestFloorplanNoteWiring (2/4), TestWorkHostingAndBroadbandAssembly, TestEvalHelpers (1); test_quality_respx.py::TestTwoPhaseAnalysisViaHTTP (4/4), TestTokenUsageParsing, TestHTTPErrorHandling (1/3); integration/test_quality_analysis_integration.py::TestQualityAnalysisIntegration (7/8) | Retarget to `analyze_single_merged` |
  | `_extract_eval_json`, `_sanitize_eval_enums` (analyzer.py:730, 746) | test_analyzer.py::TestEvalHelpers (6 of 7 tests) | Delete with the helpers (check A8 first) |
  | `run_dry_run` (pipeline/flow.py:503, docstring "Deprecated") | integration/test_cross_run_reanalysis_e2e.py::TestDryRunPath | Retarget to `run_pipeline(settings, dry_run=True)`, then delete |
  | `coordinates_match`, `prices_match` (matching/scoring.py:156, 181) | test_matching/test_deduplication.py::TestCoordinateMatching (5/5), TestFuzzyPriceMatching (4/4); they are also mutmut targets | Delete both, or keep as pure API (owner) |
  | `detect_floorplan` (utils/floorplan_detector.py, whole module) | tests/test_utils/test_floorplan_detector.py (202 lines, whole file) | Delete together (A11) |
  | `is_valid_media_type` (utils/image_processing.py:40) | test_utils/test_image_processing.py::TestIsValidMediaType (7) | Delete together |
  | `reverse_lookup_ward` (utils/postcode_lookup.py:45) | test_utils/test_postcode_lookup.py::TestReverseLookupWard (2) | Delete together |
  | `CircuitBreaker.raise_if_open` (utils/circuit_breaker.py:94) | test_utils/test_circuit_breaker.py (2 of 8) | Delete together |
  | `EventRecorder.record_batch` (pipeline/event_recorder.py:60) | test_pipeline/test_event_recorder.py (1 of 8) | Delete together |
  | `DetailFetcher.fetch_floorplan_url` (scrapers/detail_fetcher.py:547) | test_detail_fetcher_parsing.py::TestDetailFetcherLifecycle (2 of 5) | Retarget to the production fetch method |
  | `_zoopla_size_from_rsc` (detail_fetcher.py:348) | integration/test_floor_area.py::TestZooplaFloorArea (4/4) | Delete or retarget (scraper auditor) |
  | `_parse_search_results` (openrent/rightmove/zoopla), `_extract_rsc_listings`, `_parse_rsc_properties` (zoopla) | test_openrent::TestOpenRentParser (4/13), TestOpenRentNoEarlyStop; test_rightmove::TestRightmoveParser (2/10), TestRightmoveEarlyStop (2/3); test_rightmove_contracts (1); test_zoopla::TestZooplaHtmlParser (2/9), TestZooplaRscExtraction (6/10) | B3: retarget to the live parse entry point with fixtures before removing the parsers |
  | `get_viewing_message`/`save_viewing_message` (storage.py:1534/1544) | test_db/test_listing_text.py::test_partial_observations_stale_arrival_and_explicit_clear; test_pipeline/test_enrichment_persistence.py (1) | Storage auditor decides |
  | `get_unsent_price_drops`/`mark_price_drop_notified` (storage.py:1717/1743) | test_db/test_price_history.py::TestGetUnsentPriceDrops (2/2) and TestDetectAndRecordPriceChange (1); test_notifiers/test_delivery_recovery.py (3 tests); test_pipeline/test_recovery.py (1) | Retarget to the delivery repository |
  | `get_all_known_source_ids` (storage.py:1297) | test_db/test_storage.py::TestSourceListingsReadPath (2/9); test_orchestration.py::test_full_scrape_skips_known_ids_lookup | Delete with A6 (see TESTS-9) |
  | `mark_enriched` (processing_repo.py:336) | test_db/test_storage_enrichment.py::TestMarkEnriched (3/3); test_enrichment_admission.py (1) | A14: delete after a lifecycle test proves the transition |
  | `compute_fit_breakdown` (filters/fit_score.py:775) | test_filters/test_fit_score.py::TestComputeFitBreakdown (9/9), TestFactorTags (3/8); test_web/test_score_consistency.py (2) | Retarget to the combined production function its docstring names |
- Recommendation: Handle each row together with its production symbol: retarget the tests that carry unique contracts, delete the rest in the same PR.
- Prior item: A15 (OUTSTANDING); A11, A14, B3, A6 for specific rows | Owner decision needed: yes for pure-API rows (scoring, compute_fit_breakdown)

### TESTS-8: Legacy Telegram senders kept alive by tests; pipeline tests assert a never-called method was not awaited
- Category: TEST-ONLY  | Confidence: HIGH | Action: DELETE (after keeping unique formatting assertions) | Risk: low | Effort: M | Size: ~150 test lines
- Location: tests/test_notifiers/test_telegram.py:396-490 (TestTelegramNotifier sends via `send_property_notification`/`send_batch_notifications`), :354 TestGetBestImageUrl, :1092 TestStatusMessageSilent, :1109 TestLinkPreviewOptions, :1371 TestResolveGalleryPhotos, :43 TestFormatPropertyMessage;
  tests/test_notifiers/test_telegram_status.py::TestPriceDropNotification; tests/notifier_helpers.py:15-16;
  `assert_not_awaited()` on `send_property_notification` at tests/test_pipeline/test_recovery.py:259, :370, integration/test_run_pipeline_e2e.py:319, :387, integration/test_cross_run_reanalysis_e2e.py:1315, and on `send_price_drop_notification` at test_recovery.py:154
- Evidence: `send_batch_notifications`, `send_status_message`, `send_price_drop_notification`, `_get_best_image_url` and `_resolve_gallery_photos` have no src/scripts callers.
  `send_property_notification` is called only from `send_batch_notifications` (telegram.py:1007).
  `format_property_message` is called only from `send_property_notification` (telegram.py:857) plus the `notifiers/__init__` re-export.
  Production delivers through `send_merged_property_notification`/`deliver_pending` (pipeline/flow.py:132, 260, 363).
  The `assert_not_awaited` checks target MagicMock attributes that production can never call, so they can never fail.
- Recommendation: Do this when the outbox path is qualified (A7). Port any unique formatting assertions in TestFormatPropertyMessage/TestLinkPreviewOptions to the merged-caption tests.
  Then delete these tests, the vacuous `assert_not_awaited` lines and the two proxies in notifier_helpers.py.
- Prior item: A7 (OUTSTANDING) | Owner decision needed: no

### TESTS-9: Early-stop pagination tests protect a path production hard-disables
- Category: TEST-HYGIENE  | Confidence: HIGH | Action: DELETE (keep sort-parameter URL asserts) | Risk: low | Effort: S | Size: ~200 lines
- Location: tests/test_scrapers/test_rightmove.py:442 TestRightmoveEarlyStop; test_onthemarket.py:273 TestOnTheMarketEarlyStop; test_zoopla.py:404 TestZooplaEarlyStop;
  test_openrent.py:382 TestOpenRentNoEarlyStop; test_pagination.py::test_known_page_retains_changed_quote_before_stop;
  test_pipeline/test_orchestration.py:~480-546 (per-source `known_source_ids` routing and `test_full_scrape_skips_known_ids_lookup`); test_db/test_storage.py:1198-1222;
  integration/test_sort_verification.py:7 docstring ("The early-stop pagination in base.py assumes ...")
- Evidence: src/home_finder/pipeline/collection.py:46-48 says "A known prefix does not prove that earlier collection finished..." and sets `known_ids_by_source = None`. That is the only production value passed.
  flow.py:54 says "full_scrape: Compatibility flag; collection no longer stops at known IDs." `test_full_scrape_skips_known_ids_lookup` asserts a distinction that no longer exists.
- Recommendation: Delete these with the A6 plumbing removal. Keep the `sortType=6` / `results_sort=newest_listings` / `sort-field=update_date` URL assertions, because newest-first still matters with run limits.
- Prior item: A6 (OUTSTANDING) | Owner decision needed: no

### TESTS-10: Two fixture data files referenced nowhere
- Category: DEAD  | Confidence: CERTAIN | Action: DELETE | Risk: low | Effort: S | Size: 2 files (~3.6 KB)
- Location: tests/fixtures/onthemarket_search.html, tests/fixtures/traveltime_response.json
- Evidence: I ran `git grep -F` for basename and stem across the whole tree, on the PR #77 branch, and over the untracked working tree (`grep -r`): 0 hits.
  The only dynamic fixture-name pattern is `f"{request.param}_search.html"` with params `["openrent","zoopla"]` (test_scrapers/test_search_page_health.py:27-40).
  onthemarket_search.html is the old `otm-PropertyCard` HTML layout. The scraper now uses `__NEXT_DATA__` (`onthemarket_search_next_data.html`).
- Recommendation: Delete both.
- Prior item: none | Owner decision needed: no

### TESTS-11: Unused pytest fixtures
- Category: DEAD  | Confidence: CERTAIN | Action: DELETE | Risk: low | Effort: S | Size: ~20 lines
- Location: tests/test_utils/test_postcode_lookup.py:15-20 (`mock_response`), tests/test_db/test_storage_quality.py:59-72 (`prop_c`)
- Evidence: An AST scan of every function parameter, `usefixtures(...)`, `getfixturevalue(...)` and parametrize name in tests/ found no consumer, and neither fixture is autouse.
  `base_url` (test_browser_e2e.py:297) was also flagged but is a pytest-playwright override (see Verified keep).
- Recommendation: Delete both.
- Prior item: none | Owner decision needed: no

### TESTS-12: Identical `storage` fixture copied 18 times; identical prop_a/merged_a in 3–4 files
- Category: DUP  | Confidence: HIGH | Action: MERGE (into tests/conftest.py, or tests/test_db/conftest.py + tests/test_pipeline/conftest.py) | Risk: low | Effort: S | Size: ~150 lines
- Location: byte-identical `async def storage(): s = PropertyStorage(":memory:"); await s.initialize(); yield s; await s.close()` in
  integration/test_floor_area.py, integration/test_off_market_e2e.py, test_db/{test_pipeline_runs, test_off_market, test_storage_enrichment, test_quality_analysis_timestamp, test_storage_dropped, test_storage_analysis_retry, test_observability, test_status, test_storage_reanalysis, test_source_listings_linkage, test_price_history, test_storage_quality}.py,
  test_web/{test_telegram_webhook, test_routes}.py, test_pipeline/{test_orchestration, test_event_recorder}.py. There are 10 more variants (28 definitions in total).
  `in_memory_storage` has 3 definitions (integration/conftest.py:17, test_db/test_merge_safety.py:18, test_db/test_spend.py:32).
  `prop_a`/`merged_a` are byte-identical in test_db/test_off_market.py:21/35, test_price_history.py:26/40, test_status.py:20/34 (`merged_a` also in test_web/test_telegram_webhook.py:66).
- Evidence: md5 of each fixture body (`awk '/^(async )?def storage\(/,/^$/'`). The 18 copies share hash be914eca. tests/test_db and tests/test_pipeline have no conftest.py.
- Recommendation: Add one `storage` fixture to a conftest, delete the copies, and replace prop_a/merged_a with `make_merged_property` or a test_db conftest fixture.
  Leave deliberately different variants (file-backed, reopened) alone.
- Prior item: none (B4-testing.md "helper duplication") | Owner decision needed: no

### TESTS-13: Duplicated builders across test modules; test modules acting as helper providers
- Category: DUP  | Confidence: HIGH | Action: MOVE (into tests/support/) | Risk: low | Effort: M | Size: ~450 lines
- Location / Evidence (`helper_dups.py`):
  - `_pipeline_mocks` (119/116 lines) and `_fake_enrich` (12/12) are near-duplicates in integration/test_cross_run_reanalysis_e2e.py:226-359 and integration/test_run_pipeline_e2e.py:49-179.
    The diff is only `shared_storage`/`scrape_side_effect` and comments.
  - `_make_epc_bytes` is AST-identical in test_enrichment/test_detail_enrichment.py:888 and test_scripts/test_reclassify_epc_images.py:23.
    `_make_photo_bytes` is similar in both files (:666 and :46).
  - `_make_image_bytes` is AST-identical in test_utils/test_epc_detector.py:11 and test_floorplan_detector.py:11.
  - `_hosting_analysis` is AST-identical in integration/test_floor_area.py:90 and test_filters/test_fit_score.py:668. tests/support/fit_score.py already has `make_fit_analysis`.
  - `_make_property`/`_make_merged` exist in 4 modules (test_matching/test_deduplication_e2e.py:31/69, integration/test_floor_area.py:36/74, integration/test_commute_integration.py:20/41, test_scrapers/test_detail_fetcher_parsing.py:33; integration/test_off_market_e2e.py:42).
    Root `make_property`/`make_merged_property` and tests/support/matching.py `make_listing` already exist.
  - `_mock_httpx_response`/`_mock_curl_response` are duplicated in integration/test_floor_area.py:56/65 and test_scrapers/test_detail_fetcher_parsing.py:52/61.
  - Child-process scripts import `from tests.test_db.test_spend import spec, policy` (tests/test_db/test_spend.py:258, tests/test_pipeline/test_spend_recovery.py:155).
    That works only because test_spend.py re-imports them from tests/support/spend.py.
  - tests/notifier_helpers.py lives outside tests/support/ (README.md:48 documents both locations).
- Recommendation: Move the pipeline-mock context, image byte builders and httpx/curl response fakes into tests/support/.
  Point the subprocess scripts at `tests.support.spend` and fold notifier_helpers.py into tests/support/delivery.py.
  Replace local `_make_property` with the shared factories.
- Prior item: none (B4-testing.md item 1: PARTIAL, down from ~10 files to 4) | Owner decision needed: no

### TESTS-14: Declared markers never or inconsistently applied
- Category: CONFIG  | Confidence: HIGH (`unit`: CERTAIN) | Action: SIMPLIFY | Risk: low | Effort: S | Size: ~15 lines
- Location: pyproject.toml:58-65
- Evidence (`git grep mark.<name> -- tests`):
  - `unit`: 0 uses.
  - `integration`: 5 files. Only 4 of the 15 tests/integration modules carry it, plus test_scrapers/test_scraper_location_leakage.py.
  - `e2e`: 2 files (integration/test_candidate_preparation.py, test_run_pipeline_e2e.py), though 6 module names end in `_e2e`.
  - `slow`: 7 files. `browser`: 2. `allow_hosts`: 1 (needed).
  - No CI job, doc or script selects `-m unit|integration|e2e`. The only mention is the leakage docstring (TESTS-4).
  - tests/README.md:23-24 explains `e2e` but not its inconsistent application.
- Recommendation: Delete `unit`. Either delete `integration`/`e2e`, or apply them by directory in `pytest_collection_modifyitems`.
  Add `--strict-markers` so a typo cannot silently create a marker.
- Prior item: none | Owner decision needed: no

### TESTS-15: mutmut residue: documentation-only config file and mutant-specific test commentary
- Category: AI-ISM  | Confidence: HIGH | Action: REWRITE-DOC + DELETE mutmut_config.py | Risk: low | Effort: S | Size: ~40 lines
- Location: mutmut_config.py (root, 17 lines: "mutmut 3.x does NOT auto-load this file ... kept for documentation purposes only").
  It points to a hand-installed `.venv ... _mutmut_setproctitle_fix.pth` that is not reproducible from the repo.
  tests/test_matching/test_deduplication_weighted.py:132 `class TestStreetNormalizationMutantKillers` ("Additional tests to kill surviving mutants"), :340, :360, :375, :390, :531, :553 ("Kills mutant: coord_value > 0 → coord_value >= 0");
  tests/test_matching/test_deduplication.py:20, :36, :204; tests/test_filters/test_location.py:147
- Evidence: The B8 disposition says "no broad CI gate or permanent mutant-specific commentary requirement". `[tool.mutmut]` (pyproject.toml:117-127) is the real config.
- Recommendation: Move the two workaround notes into a comment beside `[tool.mutmut]` and delete mutmut_config.py.
  Rename TestStreetNormalizationMutantKillers after the behaviour it protects, and replace "Kills mutant X" docstrings with the contract stated in domain terms.
  Keep mutmut as an optional dev tool.
- Prior item: B8 (PARTIAL), A13 (root leftover) | Owner decision needed: no

### TESTS-16: Unused dev test dependencies: dirty-equals and respx
- Category: CONFIG  | Confidence: CERTAIN | Action: DELETE | Risk: low | Effort: S | Size: 2 deps + lock
- Location: pyproject.toml:45-46
- Evidence:
  - `git grep -i dirty -- . ':!uv.lock'` finds only pyproject and an experiments lock.
  - `git grep -i respx -- tests src` finds only the string "test-key-for-respx" and the filename test_quality_respx.py. Its docstring says it uses the native httpx2 `MockTransport` and adds "The historical filename remains for handoff links."
  - deptry DEP002 lists both.
- Recommendation: Remove both deps. Optionally rename test_quality_respx.py to test_quality_http.py and drop the handoff sentence.
- Prior item: A2 (OUTSTANDING) | Owner decision needed: no

### TESTS-17: Test docs contradict the suite layout
- Category: STALE-DOC  | Confidence: CERTAIN | Action: REWRITE-DOC | Risk: low | Effort: S | Size: ~25 lines
- Location / Evidence:
  - CONTRIBUTING.md:71 (`uv run pytest tests/test_scrapers.py`) and :123 ("Add tests in `tests/test_scrapers.py`"): the file does not exist; tests/test_scrapers/ is a directory of 18 modules.
  - CLAUDE.md "Test structure": "`tests/test_filters/` — criteria, commute, dedup, quality, location, floorplan".
    Commute tests are in tests/test_enrichment/test_commute.py, dedup in tests/test_matching/, quality in tests/test_analysis/, and there is no floorplan test in test_filters.
    The list also omits test_analysis, test_matching, test_enrichment, test_pipeline, test_data and test_scripts.
  - CLAUDE.md "Common fixtures in tests/conftest.py: sample_property, sample_property_no_coords, default_search_criteria": the last two are used only by tests/test_models.py.
    The shared factories are `make_property` (23 files), `make_merged_property` (38), `make_quality_analysis` (29) and `cached_analysis_property` (10).
  - CLAUDE.md:187 "Integration tests ... reset Crawlee global state with autouse fixtures": the fixtures are not autouse (`Not autouse` in their own docstrings) and are dead (TESTS-5).
  - CLAUDE.md "All tests use in-memory SQLite": 43 test files create tmp_path databases. The concurrency claim still holds via tmp_path.
  - CLAUDE.md:54 / README.md:253 "slow = real network scraping": see TESTS-2.
- Recommendation: Point CONTRIBUTING at tests/test_scrapers/. Replace CLAUDE.md "Test structure" with a pointer to tests/README.md (which is accurate), and fix the fixture and Crawlee lines.
- Prior item: none | Owner decision needed: no

### TESTS-18: Conditional skips that can never fire (or hide regressions)
- Category: TEST-HYGIENE  | Confidence: HIGH | Action: SIMPLIFY (skip → assert) | Risk: low | Effort: S | Size: ~15 lines
- Location: tests/test_db/test_off_market.py:313, :341, :370 (`pytest.skip("No source_listing created by migration backfill")`); tests/test_scrapers/test_zoopla_models.py:387-388 (`if not fixture_path.exists(): pytest.skip`); tests/test_web/test_browser_e2e.py:539-540 (gallery skip)
- Evidence: `save_merged_property` always calls `_upsert_source_listing` (src/home_finder/db/storage.py:~895), so the off-market skips are unreachable and the "migration backfill" rationale is stale.
  The zoopla fixture file is committed. The browser seed (test_browser_e2e.py:185-205) stores image rows but never populates the disk cache, so the gallery-lightbox test may always skip (MEDIUM; not run, browser).
- Recommendation: Replace the first two with assertions. For the browser test, seed the cache with `tests/support/images.populate_image_cache` or delete it.
- Prior item: none | Owner decision needed: no

### TESTS-19: Overlapping coverage of scoring invariants and the dedup→store flow
- Category: DUP  | Confidence: MEDIUM | Action: MERGE | Risk: low | Effort: M | Size: ~150 lines
- Location / Evidence:
  - Scoring properties appear in tests/test_property_based.py (root; TestCalculateMatchScoreProperties, TestGraduated*, TestHaversineProperties), test_matching/test_deduplication_e2e.py:756 TestScoringInvariants (self-match, symmetry, different bedrooms → 0, non-negative, min signals) and test_matching/test_deduplication.py (haversine ground truth).
    "Different bedrooms → 0" is asserted three times (test_property_based:371, TestScoringInvariants:779, TestDeduplicateAndMergeAsyncE2E:362).
    TestScoringInvariants also hard-codes `@settings(max_examples=50)`, which overrides the conftest "fast" profile.
  - test_matching/test_deduplication_e2e.py:1150 `test_full_flow_with_storage` (save then `filter_new_merged` returns nothing) duplicates integration/test_selection_persistence.py:133 `test_pipeline_rerun_finds_no_new`.
  - integration/test_real_scraping.py:19-68 TestRealRightmoveOutcodeMapping (no network, but marked slow so it never runs) duplicates test_data/test_location_mappings.py:60-80, :185-189.
  - Not duplicates: test_matching/test_deduplication_e2e.py (synthetic, default suite) and integration/test_cross_platform_dedup_e2e.py (live). Only the latter is redundant, because it is nonfunctional (TESTS-2).
- Recommendation: Move test_property_based.py into test_matching/ and keep one invariant class. Delete TestRealRightmoveOutcodeMapping and one of the two rerun tests.
- Prior item: A12 | Owner decision needed: no

### TESTS-20: test_scripts protect mutating maintenance flags slated for retirement, against a hand-written schema
- Category: TEST-ONLY  | Confidence: MEDIUM | Action: ASK-OWNER | Risk: medium (operator scripts) | Effort: M | Size: ~1,030 lines
- Location: tests/test_scripts/test_audit_image_cache.py (830 lines: TestPhase1DbToDisk `--fix`, TestPurgeDiskFiles `--purge-disk`, TestReflagPartial `--reflag-partial`); tests/test_scripts/test_reclassify_epc_images.py (199)
- Evidence:
  - The A10/B9 disposition says "Retire unsafe mutations. Keep a useful read-only cache inventory".
  - The audit tests build their own `CREATE TABLE property_images/properties/quality_analyses` (test_audit_image_cache.py:29-94) instead of the migrated schema, so schema drift would go unnoticed.
  - reclassify_epc_images.py is a one-off repair ("This reverses the earlier script that renamed files on disk").
  - Neither script appears in docs/operations.md.
  - test_audit_duplicate_candidates.py covers a documented read-only audit (docs/audits/offline-duplicate-candidates.md) and is a keep.
- Recommendation: When the scripts auditor retires `--fix/--purge-disk/--reflag-partial` and the one-off EPC reclassifier, delete the matching test classes or files.
  Rebuild the remaining read-only inventory tests on `PropertyStorage(...).initialize()`. Marcel may still run these locally.
- Prior item: A10/B9 (OUTSTANDING) | Owner decision needed: yes

### TESTS-21: 497 redundant `@pytest.mark.asyncio` markers and 26 `pytest_asyncio.fixture` under asyncio_mode=auto
- Category: TEST-HYGIENE  | Confidence: HIGH | Action: SIMPLIFY (mechanical, optional) | Risk: low | Effort: S | Size: ~520 lines
- Location: 30 files (e.g. tests/test_db/test_storage.py, tests/integration/test_real_scraping.py, tests/test_scrapers/test_scraper_location_leakage.py)
- Evidence: pyproject.toml:53 sets `asyncio_mode = "auto"`. `git grep -c mark.asyncio -- tests` gives 497. The suite mixes both styles.
- Recommendation: Remove with a scripted edit in a standalone commit, or leave it. Low value, zero risk.
- Prior item: none | Owner decision needed: no

### TESTS-22: Tests import names through re-exports, needing `type: ignore[attr-defined]`
- Category: COMPAT  | Confidence: MEDIUM | Action: SIMPLIFY (import from defining module) | Risk: low | Effort: S | Size: ~10 lines
- Location: tests/test_filters/test_location.py:6 (`extract_outcode`, `BOROUGH_OUTCODES` via filters.location); tests/test_analysis/test_analyzer.py:12 (`build_evaluation_prompt` and others via analyzer); tests/test_web/test_routes.py:42; tests/integration/test_real_scraping.py:43, :54 (`RIGHTMOVE_OUTCODES` via scrapers.rightmove)
- Evidence: filters/location.py:19 imports `extract_outcode` from utils.address. analyzer.py:217 imports `build_evaluation_prompt` from analysis.prompts.
  The tests are the consumers keeping these re-exports necessary (A3).
- Recommendation: Point the test imports at the defining modules when the A3 re-export cleanup runs.
- Prior item: A3 | Owner decision needed: no

---

## Verified keep (looks dead, isn't)
- `base_url` fixture (tests/test_web/test_browser_e2e.py:297): overrides pytest-playwright's `base_url`, which `page.goto("/...")` consumes implicitly.
- `browser_context_args`, `browser_type_launch_args`: pytest-playwright override hooks.
- tests/fixtures/rightmove_indexed_detail.provenance.json and rightmove_next_search.provenance.json: not read by code, but they are provenance records for sanitised fixtures. rightmove_next_search.html:2 points at its provenance file. This is decision evidence; keep it.
- Fixture HTML referenced only from docs/plans/2026-02-02-floorplan-analysis-implementation.md: all are also loaded by test_detail_fetcher_parsing.py.
- `inventory_database` (db/backup.py:190): test-only caller, but it is the backup round-trip oracle (test_backup.py:65-306) over the production `_inventory`. A deliberate pure interface.
- `allow_hosts` marker: used by test_rightmove_local_http.py:13 (pytest-socket).
- `fixtures_path` duplicate in test_zoopla_models.py:419: a class-local override with the same value. Harmless; could be deleted.
- Shadowed `sample_property` (root conftest, test_analysis/test_analyzer.py:424, test_notifiers/conftest.py:26): intentionally different values per suite.
- tests/test_scripts/test_audit_duplicate_candidates.py: a documented read-only operator audit.
- `_isolate_settings_from_dotenv`, `_isolate_app_logging`, `isolated_geocoding_cache`, `clear_cache` (autouse): cheap and justified isolation.
- `pytest_collection_modifyitems` in tests/test_web/conftest.py also affects tests/integration/test_sort_verification.py (browser marker). This is intended.
- All 11 tests/support modules have importers (2–18 each).
- Hypothesis "mutmut" profile (conftest.py:57-61): consumed by `--hypothesis-profile=mutmut` in `[tool.mutmut]`. The "ci" profile is set by checks.yml:89.

## Notes
- Prior-item status in this area:
  - A1: OUTSTANDING (crawlee dep plus test fixtures).
  - A2: OUTSTANDING (dirty-equals, respx).
  - A6: OUTSTANDING (tests in TESTS-9).
  - A7: OUTSTANDING (TESTS-8).
  - A11: OUTSTANDING (test_floorplan_detector.py is the module's only consumer).
  - A12: OUTSTANDING (TESTS-3/4/19).
  - A14: OUTSTANDING (TestMarkEnriched).
  - A15: OUTSTANDING (TESTS-7).
  - A16: OUTSTANDING (TESTS-6; the largest coupling, 40 files).
  - A20: OUTSTANDING (TESTS-2, mechanically verified).
  - B8: PARTIAL (TESTS-15).
  - A10/B9: OUTSTANDING (TESTS-20).
- Ordering: TESTS-1, 5, 10, 11, 12, 16 and 17 are independent quick wins. TESTS-6/7/8/9 must land together with the production deletions they enable.
  TESTS-6 is a prerequisite for the DB auditor's storage-method removal.
- For other auditors:
  - CLAUDE.md:98 still says the Rightmove scraper uses `crawlee.BeautifulSoupCrawler`.
  - CLAUDE.md "EPC detection ... renamed from gallery_* to epc_* on disk" conflicts with scripts/reclassify_epc_images.py's statement that classification now lives only in the DB.
  - CLAUDE.md still describes floorplan PIL heuristics, but `detect_floorplan` has no production caller.
- Fixtures named like tests (`test_property`, `test_merged_property` in test_quality_respx.py:53/69, `test_settings` in integration/conftest.py:26 and test_orchestration.py:55) are not collected, but they read like tests. A cosmetic rename is optional.
- Weak tests worth a glance during cleanup:
  - test_utils/test_floorplan_detector.py:109 ("May or may not be detected -- the important thing is it doesn't crash").
  - "Ticket N" labels in test docstrings: test_db/test_price_history.py:1, test_status.py:1, test_storage.py:1106, test_web/test_filters.py:254, test_routes.py:2697, :2883.
