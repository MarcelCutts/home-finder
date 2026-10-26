# SCRAPERS area findings

Scope: `src/home_finder/scrapers/`, `src/home_finder/enrichment/`, `src/home_finder/utils/`, `docs/sources/*.md`, and the CLAUDE.md claims about them.
Base: `main` @ 51dd9c6. Working tree is clean (no untracked files besides coverage/hypothesis artefacts). Marcel's local untracked work can't be seen from here.
Searches: `git grep` over the whole tree (src, tests, scripts, experiments, docs, templates, static, .github, Dockerfile, fly.toml), plus `git grep … origin/codex/review-opus-experiments -- experiments` (PR #77) for every symbol proposed for deletion. None of the PR #77 searches matched.
Prior-item status, where it applies: A1 OUTSTANDING, A3 OUTSTANDING (scrapers/commute part), A4 OUTSTANDING (union-find), A5 OUTSTANDING (constants), A6 OUTSTANDING (behaviour disabled, plumbing and docs remain), A11 OUTSTANDING, A15 OUTSTANDING (scraper wrappers), A19 OUTSTANDING, B3 PARTIAL, B7 DONE (offer preparation has no scores left).

---

### SCRAPERS-1: Crawlee is an unused runtime dependency; bs4/lxml come in only transitively
- Category: CONFIG | Confidence: CERTAIN | Action: REWRITE-DOC + SIMPLIFY (dependency swap) | Risk: medium (lock/Docker image; Rightmove parsing needs lxml) | Effort: S | Size: ~110 lines + lockfile
- Location: pyproject.toml:8, pyproject.toml:78 (E402 per-file ignore "for Crawlee state reset"), pyproject.toml:139 (`protego` floor exists only for crawlee); tests/conftest.py:417-491 (`reset_crawlee_state`, `_clear_crawlee_caches`, `set_crawlee_storage_dir`); tests/integration/conftest.py:12-13; `pytestmark = usefixtures("reset_crawlee_state", "set_crawlee_storage_dir")` in tests/integration/test_cross_platform_dedup_e2e.py:11, test_detail_fetcher_e2e.py:10, test_enrichment_pipeline_e2e.py:8, test_real_scraping.py:11, and tests/test_scrapers/test_scraper_location_leakage.py:276; .gitignore:152-153 (`# Crawlee storage` / `storage/`); CLAUDE.md:98, CLAUDE.md:187
- Evidence: `git grep -i crawlee -- src scripts Dockerfile fly.toml .github` returns 0 hits. deptry reports DEP002 for crawlee, and DEP003 for `bs4` imported directly in scrapers/openrent.py:7, rightmove.py:9, rightmove_payloads.py:11, zoopla.py:13 and filters/availability.py:17. Rightmove now uses `httpx.AsyncClient` plus `BeautifulSoup(html, "lxml")` (rightmove.py:209, 225-236), so lxml is a real runtime requirement. In uv.lock, `crawlee[beautifulsoup]` is the only thing pulling in `beautifulsoup4[lxml]`, `html5lib`, `impit`, `protego`, `psutil`, `tldextract` and others; nothing else depends on them, and html5lib is never imported. The crawler was removed in 15ae399 "fix: preserve cancellation during Rightmove collection". The conftest fixtures say "only tests that exercise real Crawlee scrapers need this", but no such scrapers exist. `ruff --isolated --select E402 tests/integration` gives 17 errors, all imports placed after the Crawlee `pytestmark`. CLAUDE.md:187 says "autouse fixtures"; they are explicitly not autouse.
- Recommendation: replace `crawlee[beautifulsoup]` with an explicit `beautifulsoup4` + `lxml` requirement (as A1 says), relock, and drop the `protego` constraint. Delete the two Crawlee fixtures and their `usefixtures`, move the imports in the integration tests to the top, drop the E402 per-file ignore and the `.gitignore` `storage/` entry (nothing in src writes `storage/`). Fix CLAUDE.md:98 (Rightmove is "`httpx.AsyncClient` + BeautifulSoup/lxml") and :187. Check that the Docker build still works.
- Prior item: A1 (OUTSTANDING) | Owner decision needed: no

### SCRAPERS-2: The early-stop plumbing is disabled in production but still runs through every scraper, the CLI, tests and the source docs
- Category: SUPERSEDED | Confidence: HIGH | Action: DELETE (plumbing) + REWRITE-DOC | Risk: low-medium (CLI flag compatibility) | Effort: M | Size: ~150 lines src+tests
- Location: scrapers/base.py:78, 92, 104, 115, 192-197 (`known_page` branch); rightmove.py:186, 236; zoopla.py:309, 350; onthemarket.py:76, 115; openrent.py:127, 163-171; pipeline/collection.py:46-48 (hard-coded `known_ids_by_source = None`), 155, 174, 202-204, 301; main.py:111-115 and 144-145 (`--full-scrape` "Compatibility flag"), pipeline/flow.py:41/54/89, candidates.py:46/70; db/storage.py:1297 `get_all_known_source_ids` (test-only now); tests: test_scrapers/test_rightmove.py:442-525, test_zoopla.py:404-515, test_onthemarket.py:273-340, test_openrent.py:382-440, test_pagination.py:12-21, test_pipeline/test_orchestration.py:501-546, test_db/test_storage.py:1198-1221, tests/integration/test_sort_verification.py:7-8, 61, 86-89; docs/sources/rightmove.md:31, zoopla.md:31, onthemarket.md:29, openrent.md:3, 8, 31; README.md:66
- Evidence: in production, `collect_listings` always passes `known_ids_by_source=None` ("A known prefix does not prove that earlier collection finished", added in ec9c2fc). docs/sources/README.md:29-37 says "the production pipeline does not enable it", and OpenRent passes `None` even inside the scraper. The only places that set `known_source_ids` are test calls and tests that call `scrape_all_platforms` directly. `get_all_known_source_ids` has no src caller. The per-source docs still say "Early-stop: Supported", which contradicts README.md and collection.py.
- Recommendation: per A6, remove `known_source_ids` from `BaseScraper.scrape`/`_paginate` and the four scrapers, remove `known_ids_by_source` from `scrape_all_platforms`, and remove `get_all_known_source_ids` (db lane). Keep the newest-first sort params and the URL assertions that check them, but reword "required for early-stop correctness" as "prioritise recent adverts". Keep the `duplicate_page`/`result_limit` stop tests. Rewrite the test_sort_verification rationale and the four source docs. Removing `--full-scrape` needs an owner call: README documents it as a no-op, and an operator's cron could pass it. Keep it accepted-and-ignored or remove it.
- Prior item: A6 (OUTSTANDING) | Owner decision needed: yes (only for removing the CLI flag)

### SCRAPERS-3: The obsolete floorplan heuristic (`utils/floorplan_detector.py`) has no consumer
- Category: SUPERSEDED | Confidence: HIGH | Action: DELETE + write a superseding ADR | Risk: low | Effort: S | Size: ~345 lines (142 src + 202 test)
- Location: src/home_finder/utils/floorplan_detector.py:1-142; tests/test_utils/test_floorplan_detector.py; docs/adr/001-laplacian-bimodality-floorplan-detection.md:13, 51, 111-113; utils/epc_detector.py:16 ("Follows the same pattern as floorplan_detector.py"); docs/sources/README.md:46 ("it remains an offline experiment utility"); CLAUDE.md:82 ("detect floorplans in gallery via PIL heuristics"), CLAUDE.md:126 ("after EPC/floorplan detection"), CLAUDE.md:163 ("EPC/floorplan file renaming")
- Evidence: `git grep -w detect_floorplan` matches only the module, its test and the ADR. There are no callers in src, scripts, experiments or PR #77. The enrichment caller was removed in a021747 "fix: preserve floorplan origin and stop heuristic promotion". The reason it was retired is recorded in docs/plans/2026-10-10-focused-reliability-repairs.md:69-90: a retained bathroom photo scored `(True, 0.665)`, and only source-declared floorplans satisfy the gate. The function has no offline/experiment caller, so the README's "offline experiment utility" claim is false.
- Recommendation: delete the module and its test. Add ADR 002 "Retire heuristic floorplan detection", citing the R3 plan rationale and migration 010 `floorplan_origin`, and mark ADR 001 "Superseded by ADR 002". Fix the CLAUDE.md lines, the README line and the epc_detector docstring. Before deleting, ask Marcel whether any local, untracked calibration notebook still imports it.
- Prior item: A11 (OUTSTANDING) | Owner decision needed: no

### SCRAPERS-4: Search-parser "compatibility readers" are called only by tests
- Category: TEST-ONLY | Confidence: HIGH (CERTAIN for `_extract_listings_from_parsed`) | Action: DELETE after retargeting tests | Risk: low | Effort: S | Size: ~35 src lines, ~25 test call sites
- Location: openrent.py:235-238 `_parse_search_results`; rightmove.py:322-327 `_parse_search_results` (unused `base_url`); zoopla.py:423-425 `_extract_rsc_listings`, 487-489 `_extract_listings_from_parsed`, 531-536 `_parse_rsc_properties`, 686-689 `_parse_search_results`, 174-175 `ZooplaListingsAdapter` (its comment "for RSC format" is stale; production validates each item at zoopla.py:507-516)
- Evidence: vulture lists them as used only by tests. Their docstrings all say "Compatibility reader/view", and they were added in f54eeec "fix: distinguish failed searches and validate configured areas" as list-or-None views over the new `ParsedSearchPage`. Production calls `_parse_search_page`, `_read_rsc_listings` and `_parse_rsc_page`. Test callers: test_openrent.py:116, 168, 310, 340, 412; test_rightmove.py:153, 277, 463, 495; test_rightmove_contracts.py:99; test_zoopla.py:156, 232, 261-338; test_zoopla_models.py:349-400. `_extract_listings_from_parsed` has zero callers anywhere, tests included, and zero coverage.
- Recommendation: retarget the tests at `_parse_search_page(soup).properties` / `.parse_errors`, `_read_rsc_listings(html)[0]`, `_parse_rsc_page(html)` and `ZooplaListing.model_validate`. These tests protect real card-parsing behaviour (dedup, missing lat/lon, fixture parsing), so keep their assertions. Then delete the wrappers and the adapter.
- Prior item: A15 (OUTSTANDING); relates to B3 | Owner decision needed: no

### SCRAPERS-5: Legacy and test-only helpers in detail_fetcher
- Category: TEST-ONLY | Confidence: HIGH | Action: DELETE | Risk: low | Effort: S | Size: ~25 src + ~50 test lines
- Location: scrapers/detail_fetcher.py:348-355 `_zoopla_size_from_rsc` (docstring "Legacy bounded scalar helper") and its private gate constants :44-45 `_FLOOR_AREA_MIN_SQFT/_MAX_SQFT`; detail_fetcher.py:547-557 `fetch_floorplan_url`; tests/integration/test_floor_area.py:198-232; tests/test_scrapers/test_detail_fetcher_parsing.py:903-917
- Evidence: neither helper has a src, scripts or experiments caller. `fetch_floorplan_url` appears only in the 2026-02 plan doc and in its own delegation tests. The four `_zoopla_size_from_rsc` tests exercise a duplicate 100–5000 sq ft gate. The live gate is `display_area_square_metres` (models/source_area.py:63-90), already covered for out-of-range values at test_floor_area.py:163, 170, 271, 278. The live Zoopla extraction `_zoopla_area_from_rsc` is covered at test_source_area.py:157-163.
- Recommendation: delete both helpers, the two constants and their tests. No unique behaviour is lost.
- Prior item: A15 / A5 | Owner decision needed: no

### SCRAPERS-6: Unused constants
- Category: DEAD | Confidence: CERTAIN | Action: DELETE | Risk: low | Effort: S | Size: ~6 lines
- Location: scrapers/openrent.py:41-42 and onthemarket.py:35-36 `INITIAL_BACKOFF_SECONDS`/`MAX_BACKOFF_SECONDS`; scrapers/detail_fetcher.py:46 `_RETRY_BASE_DELAY`; utils/image_cache.py:21 `VALID_IMAGE_EXTENSIONS`
- Evidence: `git grep -w` over the whole tree finds only the definitions. Retries use `SCRAPER_WAIT` (scrapers/retry.py:18) or inline `wait_exponential`. vulture flags them even with tests included.
- Recommendation: delete.
- Prior item: A5 | Owner decision needed: no

### SCRAPERS-7: `utils/union_find.py` has no callers
- Category: DEAD | Confidence: CERTAIN | Action: DELETE | Risk: low | Effort: S | Size: ~29 lines
- Location: src/home_finder/utils/union_find.py:1-29
- Evidence: `git grep -E "UnionFind|union_find"` across the tree and PR #77 finds only the file itself. Coverage is zero for the whole file. It has no callers anywhere in the visible history; the root import 63f4271 already lacked them.
- Recommendation: delete.
- Prior item: A4 (OUTSTANDING) | Owner decision needed: no

### SCRAPERS-8: Package-level lazy re-export loader in `scrapers/__init__.py`, and the `CommuteResult` re-export
- Category: COMPAT | Confidence: HIGH | Action: SIMPLIFY (direct imports) | Risk: low | Effort: S | Size: ~42 lines
- Location: scrapers/__init__.py:1-41 (`__getattr__`/`__dir__`/`_LAZY_IMPORTS`); its consumers are pipeline/collection.py:20-25, tests/integration/test_cross_platform_dedup_e2e.py:16, test_detail_fetcher_e2e.py:13, test_enrichment_pipeline_e2e.py:12. Also enrichment/commute.py:10 `from home_finder.models.commute import CommuteResult as CommuteResult`, whose only consumer is tests/test_enrichment/test_commute.py:9
- Evidence: one production consumer, and it imports all four scrapers eagerly anyway. `__dir__` has zero coverage. The laziness only stops `import home_finder.scrapers.constants` (filters/off_market.py:21) or the payload modules (filters/availability.py:19-20) from loading curl_cffi/bs4, and an `__init__.py` that holds only a docstring does that too. Test patches target `home_finder.pipeline.collection.<Scraper>`, which keeps working with direct imports.
- Recommendation: switch collection.py and the three integration tests to `from home_finder.scrapers.<module> import X`, and cut `__init__.py` down to its docstring. Import `CommuteResult` from `home_finder.models.commute` in the test and drop the re-export. Smoke-test `uv run home-finder --scrape-only --max-per-scraper 0` (no network if no areas are configured) after the change.
- Prior item: A3 (OUTSTANDING for this area) | Owner decision needed: no

### SCRAPERS-9: Circuit-breaker compatibility surface used only by tests
- Category: COMPAT | Confidence: HIGH | Action: DELETE / SIMPLIFY | Risk: low | Effort: S | Size: ~15 lines
- Location: utils/circuit_breaker.py:21-29 (`CircuitBreakerOpenError` and `APIUnavailableError` "Subclass … for backward compatibility"), :94-97 `raise_if_open`, :62-73 `CircuitBreaker.state`/`failure_count` (test-only), :1-9 module docstring ("Used by the quality filter", "Used by the off-market checker"; it is now analysis/analyzer.py:824 and also detail_fetcher.py:1005); tests/test_utils/test_circuit_breaker.py:83-91, 161-164
- Evidence: `raise_if_open` is the only thing that raises `CircuitBreakerOpenError`, and no src code catches it. `APIUnavailableError` is raised and caught only inside analysis/analyzer.py. `state` and `failure_count` on the 3-state breaker are read only by tests (the `failure_count` read at detail_fetcher.py:1059 is on `ConsecutiveFailureBreaker`).
- Recommendation: delete `raise_if_open` and the `issubclass` compatibility test. Make `APIUnavailableError` a plain `Exception`, ideally defined in the analysis package (analysis lane; analyzer.py:104 also re-exports it as `APIUnavailableError as APIUnavailableError`). Fix the module docstring. `state` can stay as a test seam.
- Prior item: A15 | Owner decision needed: no

### SCRAPERS-10: Duplicated image media-type helpers
- Category: DUP | Confidence: HIGH | Action: MERGE / DELETE | Risk: low | Effort: S | Size: ~25 lines
- Location: utils/image_processing.py:17, 40-42 (`VALID_MEDIA_TYPES`, `is_valid_media_type`, both test-only); utils/image_cache.py:26-40 `is_valid_image_bytes` repeats the magic-byte table of image_processing.py:25-37 `detect_media_type_from_bytes`; `Image.MAX_IMAGE_PIXELS = 50_000_000` is set both at image_processing.py:14 and inside image_cache.py:238; analysis/request_budget.py:59 hard-codes the same media-type set (analysis lane)
- Evidence: `git grep -w is_valid_media_type` finds only tests/test_utils/test_image_processing.py:12-98 and :214. `is_valid_image_bytes(d)` is semantically `detect_media_type_from_bytes(d) is not None`: same four signatures, same 12-byte minimum.
- Recommendation: delete `is_valid_media_type` and its tests. Implement `is_valid_image_bytes` through `detect_media_type_from_bytes`, or replace its callers. Set the pixel limit in one place (image_processing is already imported for that side effect by image_hash and floorplan_detector).
- Prior item: A15 | Owner decision needed: no

### SCRAPERS-11: `postcode_lookup.reverse_lookup_ward` is test-only, superseded by the bulk lookup
- Category: TEST-ONLY | Confidence: HIGH | Action: DELETE | Risk: low (an operator script could import it; none in tree) | Effort: S | Size: ~35 src + ~20 test lines
- Location: utils/postcode_lookup.py:45-79 (`reverse_lookup_ward`, `_reverse_lookup_ward_with_client`); tests/test_utils/test_postcode_lookup.py:11, 81-95
- Evidence: production (pipeline/assessment.py:570-607) and scripts/backfill_wards.py:14 use only `bulk_reverse_lookup_wards` and `lookup_ward`.
- Recommendation: delete the single-coordinate path and its two tests.
- Prior item: A15 | Owner decision needed: no

### SCRAPERS-12: `DetailPageData.floor_area_readings`/`floor_area_omission` are computed and then discarded; docstrings claim they are kept as evidence
- Category: DEAD | Confidence: MEDIUM | Action: ASK-OWNER (persist or drop) | Risk: low | Effort: S (drop) / M (persist) | Size: ~10 src lines + doc fixes
- Location: scrapers/detail_fetcher.py:390-391, populated at :685-686, :762-763, :987-988; enrichment/details.py:258-261 reads only `floor_area_sqm`/`floor_area_source`; models/source_area.py:66-67 ("Evidence retains valid out-of-display-range source quantities") and :88-89 ("the original quantities … remain evidence")
- Evidence: `git grep floor_area_readings` and `floor_area_omission` find no reader outside detail_fetcher.py except tests (test_floor_area.py:145, 153, 268; test_onthemarket_detail_contracts.py:81-83; test_source_area.py:151). Nothing persists ranges or minimums, so the "evidence" docstrings are false in production. Added in ec9c2fc/0d0cc5d.
- Recommendation: Marcel decides. Either persist the readings (they carry "≥ 650 sq ft"/range facts that `floor_area_sqm` drops, which fits "retained listing facts"), or remove the two fields and correct the docstrings. The tests asserting `.floor_area_readings` can move to `rightmove_areas`/`single_area`/`_zoopla_area_from_rsc`, which already return the readings.
- Prior item: none | Owner decision needed: yes

### SCRAPERS-13: Zoopla has its own Cloudflare-challenge detector that diverges from `utils/http_evidence.is_challenge`; the "soft challenge" path doesn't do what the comment and docs say
- Category: DUP | Confidence: MEDIUM | Action: SIMPLIFY (owner-visible behaviour) | Risk: medium (retry/block accounting) | Effort: S | Size: ~20 lines
- Location: scrapers/zoopla.py:192-197 `_CF_CHALLENGE_MARKERS`, :277-286 `_is_cloudflare_challenge`, :386-397 (`# Soft challenge (200 with challenge HTML) — treated as retryable`), :555-556; utils/http_evidence.py:6-24; docs/sources/zoopla.md:27
- Evidence: `_is_cloudflare_challenge` checks body markers only for status 403/503. For a 200 response only the `cf-mitigated` header counts. A 200 with challenge HTML but no header therefore resets `_consecutive_blocks = 0`, returns as success, and is then recorded by `_parse_search_page` (zoopla.py:555-556) → `is_challenge(html)` as a `challenge` parse error. It is not retried and does not count toward the session reset or skip-remaining-areas, contrary to the comment and to zoopla.md ("treated as a block"). The two marker lists also differ (`_cf_chl_opt`/`cf-browser-verification`/2000 chars versus `cf-chl-`/"verify you are human"/5000 chars).
- Recommendation: use `is_challenge(response.text, response.headers)` in `_do_fetch` for every status, decide whether a 200 challenge counts as a block, and update zoopla.md. While in there, drop the redundant `(RetryableHttpError, Exception)` tuples (zoopla.py:380, 405; onthemarket.py:141). Retrying on `Exception` already covers `RetryableHttpError`.
- Prior item: none | Owner decision needed: yes (whether a 200 challenge should count as a block)

### SCRAPERS-14: Parser fallbacks with no retained-evidence fixture (B3 partly done)
- Category: SUPERSEDED | Confidence: MEDIUM | Action: ASK-OWNER (per fallback: evidence-backed contract or delete) | Risk: medium (silent coverage loss if a portal really serves the old layout) | Effort: M | Size: ~400 lines
- Location: Rightmove HTML card parser with "old format/old structure" selector cascades, rightmove.py:338-558 (~220 lines); its fixture tests/fixtures/rightmove_search.html is synthetic and has no provenance, while the retained capture (rightmove_next_search.provenance.json) is `__NEXT_DATA__`. Zoopla HTML card fallback zoopla.py:691-810, plus legacy `detailUrl`/dict-`features` handling in `ZooplaListing` (zoopla.py:70-107, 135-153). Zoopla detail 6-way cascade detail_fetcher.py:728-750. OpenRent detail patterns 2/3 detail_fetcher.py:824-852. OTM image `prefix` construction detail_fetcher.py:947-949 and legacy `props.initialReduxState` envelope onthemarket_payloads.py:13-16.
- Evidence: what is done already: search pages distinguish recognized-empty from unrecognized layout (tests/test_scrapers/test_search_page_health.py; f54eeec), and the Rightmove search and indexed-detail contracts have provenance-backed fixtures. What remains is the per-field "old format" fallbacks, which have only synthetic fixtures.
- Recommendation: follow B3 for each fallback. Keep it when a retained capture plus an explicit contract test exists, as for the OTM legacy envelope, which the availability reader also documents. Otherwise delete it. The best-value candidates are Rightmove's legacy card selectors and Zoopla's legacy `ZooplaListing` shapes.
- Prior item: B3 (PARTIAL) | Owner decision needed: yes

### SCRAPERS-15: Unreferenced scraper fixtures
- Category: TEST-HYGIENE | Confidence: CERTAIN | Action: DELETE | Risk: low | Effort: S | Size: 2 files
- Location: tests/fixtures/onthemarket_search.html (OTM HTML-card layout; OTM has no HTML card parser, only `__NEXT_DATA__`); tests/fixtures/traveltime_response.json
- Evidence: `git grep -F` for each basename and stem across tests, src and scripts returns 0 (the only near-hit is `onthemarket_search_next_data.html`, a different file). No test globs the fixtures directory. Unreferenced since the root import.
- Recommendation: delete both. The two `*.provenance.json` files are referenced from fixture headers and docs; keep them as evidence.
- Prior item: A12 | Owner decision needed: no

### SCRAPERS-16: `enable_image_hash_matching` does nothing in normal runs (A19)
- Category: CONFIG | Confidence: CERTAIN (behaviour) | Action: ASK-OWNER (remove, or make it govern cross-run dedup) | Risk: medium (dedup behaviour) | Effort: S | Size: ~15 lines + docs
- Location: config.py:189-192; pipeline/candidates.py:98-103 (the flag reaches a deduplicator that only calls `properties_to_merged`, which never hashes; matching/deduplication.py:172-184); candidates.py:531-537 (cross-run dedup hard-codes `enable_image_hashing=True`, "independent of the global … flag"); pipeline/commands.py:273-276 (`--dedup-existing` is the only consumer); README.md:191, CLAUDE.md:208, .env.example:28
- Evidence: the code paths above. Setting the flag false changes nothing in `run`/`--serve`; it only affects `--dedup-existing`. The docs present it as a general dedup switch.
- Recommendation: one truthful policy, as A19 says: delete the setting (hashing is always on), or wire it into the cross-run deduplicator. Check the Fly secrets/env first. Matching/pipeline lane; reported here because it was assigned.
- Prior item: A19 (OUTSTANDING) | Owner decision needed: yes

### SCRAPERS-17: CLAUDE.md claims about scrapers, enrichment and proxy that the code contradicts
- Category: STALE-DOC | Confidence: CERTAIN | Action: REWRITE-DOC | Risk: low (agents follow CLAUDE.md) | Effort: S | Size: ~12 lines
- Location / contradiction:
  - CLAUDE.md:96: Zoopla "`impersonate="chrome"`". It rotates among `("chrome", "safari", "chrome131", "safari184")` (zoopla.py:200).
  - CLAUDE.md:98: Rightmove `crawlee.BeautifulSoupCrawler`. It is `httpx.AsyncClient` + bs4/lxml (see SCRAPERS-1).
  - CLAUDE.md:81 "Commute Filter" and :157 `CommuteFilter`. That class doesn't exist; `CommuteAssessor` in enrichment/commute.py:20 is "advisory … without excluding candidate homes". `Deduplicator` is in `matching/`, not `filters/`, and `PropertyQualityFilter`/`quality.py` aren't there either (docs/analysis lanes).
  - CLAUDE.md:82, :126, :163: PIL floorplan detection and floorplan file renaming were removed in a021747. EPC renaming happens in enrichment/details.py:111-113, not in image_cache.
  - CLAUDE.md:158: "Zoopla: RSC payload" for detail pages. The primary detail source is the `__NEXT_DATA__` listing, falling back to the RSC taxonomy, then HTML (detail_fetcher.py:728-750).
  - CLAUDE.md:151-152: proxy "Passed to `OnTheMarketScraper` and `DetailFetcher`". It is also passed to `OpenRentScraper`, `ZooplaScraper` (collection.py:189-192) and `OffMarketChecker` (commands.py:155). It is not passed to `RightmoveScraper`, the Rightmove typeahead, or DetailFetcher's httpx path (detail_fetcher.py:427-437; Rightmove details and non-anti-bot images).
  - CLAUDE.md:187: Crawlee "autouse fixtures" (see SCRAPERS-1).
- Recommendation: correct these lines. The docs agent may cover the non-scraper Architecture Pointers lines too; coordinate.
- Prior item: none (review asks to "fix CLAUDE.md claims that contradict current scrapers or floorplan handling") | Owner decision needed: no

### SCRAPERS-18: docs/sources/*.md claims that the code contradicts
- Category: STALE-DOC | Confidence: CERTAIN | Action: REWRITE-DOC | Risk: low | Effort: S | Size: ~15 lines
- Location / contradiction:
  - rightmove.md:3 "Search results parsed from HTML cards". The primary parser is `__NEXT_DATA__` `searchResults` (rightmove_payloads.py:62-80); cards are the fallback (rightmove.py:338-358).
  - rightmove.md:19 "Extracted via brace-counting (not regex)". It uses a regex locator plus `json.JSONDecoder.raw_decode` (rightmove_payloads.py:139-145).
  - rightmove.md:34 and onthemarket.md:32 "Images sent as URL references (Anthropic fetches directly)". Only base64 blocks are built (analysis/analyzer.py:996-1003), and request_budget.py:56-58 rejects any non-base64 source.
  - onthemarket.md:8, :34 "Only scraper that accepts `proxy_url`". OpenRent and Zoopla accept it too (openrent.py:46, zoopla.py:203).
  - openrent.md:7 "`httpx.AsyncClient` for detail pages". OpenRent details go through curl_cffi (`_curl_get_with_retry(..., throttle_name="openrent")`, detail_fetcher.py:775-778).
  - openrent.md:19 "Unavailable properties are detected by redirect to `/properties-to-rent`". Policy is "generic redirects and 404s are unknown" (filters/availability.py:257; off_market.py:3-5).
  - README.md:67 OpenRent "Adaptive backoff based on response time". OpenRent uses a fixed 2 s page delay, the base 2–5 s area delay and tenacity on 429 (openrent.py:36-42, 158-159). Nothing adapts to response time.
  - README.md:46 heuristic "remains an offline experiment utility": no caller (SCRAPERS-3).
  - "Early-stop: Supported" lines (SCRAPERS-2) and zoopla.md:27 soft-challenge (SCRAPERS-13).
- Recommendation: rewrite these lines. CLAUDE.md asks for source docs to be updated whenever a scraper's client or extraction changes, and several changes evidently skipped that.
- Prior item: none | Owner decision needed: no

### SCRAPERS-19: Duplicated parsing helpers and payload locators across scrapers
- Category: DUP | Confidence: MEDIUM | Action: MERGE (into scrapers/parsing.py) | Risk: medium (small semantic differences) | Effort: M | Size: ~60 lines
- Location: zoopla.py:117-129 (`get_price_pcm` string branch reimplements `parsing.extract_price` without its pcm preference); openrent.py:419-424 postcode regex (differs from `parsing.extract_postcode`), :449-472 `_extract_price_from_html`/`_extract_bedrooms_from_html` (OpenRent imports none of parsing.py); weekly→monthly uses `int()` in parsing.py:39 and zoopla.py:113, 127 but `round()` in rightmove_payloads.py:101; the User-Agent literal appears in both rightmove.py:228-230 and detail_fetcher.py:432-435; the `__NEXT_DATA__` regex appears 3 times (onthemarket.py:200-204, detail_fetcher.py:130-134, 918-922) and the RSC `self.__next_f.push` regex 3 times (zoopla.py:429, detail_fetcher.py:190, 324)
- Evidence: the code quoted above. The earlier A2-scrapers review (Feb) flagged bedroom duplication and the duplicated BROWSER_HEADERS; both are now fixed (zoopla uses `extract_bedrooms`, detail_fetcher imports `BROWSER_HEADERS`). These are the remaining copies.
- Recommendation: add `iter_rsc_payloads(html)`/`next_data_json(html)` helpers and one `BROWSER_USER_AGENT` constant. Route the Zoopla/OpenRent price, bedroom and postcode parsing through parsing.py only after a test pins the differences (for example, OpenRent "per week" text must not be taken as pcm by `extract_price`). Pick a single rounding rule.
- Prior item: none (A2-scrapers.md items 2 and 4: DONE) | Owner decision needed: no

### SCRAPERS-20: DetailFetcher throttle buckets by string attribute and float comparison; the httpx retry makes an extra request
- Category: OVERBUILT | Confidence: MEDIUM | Action: SIMPLIFY | Risk: low-medium (portal request pacing) | Effort: S | Size: ~40 lines
- Location: scrapers/detail_fetcher.py:409-422 (six lock/next-time attribute pairs), :471-479 `_throttle` (getattr/setattr by name), :496-515 (bucket chosen by `min_interval >=` comparison when `throttle_name` is absent; the OTM detail call at :902-904 depends on it); :446-469 `_httpx_get_with_retry`, which after two 429s sends a third `client.get(url)` with the comment "unreachable but satisfies type checker" (it is reachable)
- Evidence: docs/reviews/A2-scrapers.md:130-138 already flagged the float coupling and it is still present. Coverage: `_curl_get_with_retry`, `_httpx_get_with_retry` and `_throttle` have zero coverage (uncovered_functions.txt) because the tests mock them, so these retry and pacing semantics are untested.
- Recommendation: a `dict[str, _Throttle]` keyed by an explicit `throttle_name` at every call site. Raise from the last 429 response instead of re-requesting. Add one test per retry helper using the existing fake responses.
- Prior item: none | Owner decision needed: no

### SCRAPERS-21: `_paginate` still accepts a "legacy property list", and `SearchPage.end_of_results` is read only by a test
- Category: COMPAT | Confidence: HIGH | Action: SIMPLIFY | Risk: low | Effort: S | Size: ~15 src lines + test fakes
- Location: scrapers/base.py:101, 116-118, 165-170 (`ParsedSearchPage | list[Property] | None`); scrapers/rightmove_payloads.py:25-27, 79 (`end_of_results`), test_rightmove_contracts.py:298
- Evidence: all four production `fetch_page` closures return `ParsedSearchPage | None` (openrent.py:141, rightmove.py:202, zoopla.py:318, onthemarket.py:90). Plain lists come only from test fakes (test_pagination.py, test_observation_accounting.py:176/255/367, test_db/test_listing_text.py:170). `_paginate` already stops on an empty page, so `end_of_results` adds nothing.
- Recommendation: narrow the type to `ParsedSearchPage | None`, wrap the test fakes, and drop the `SearchPage` subclass field (or the subclass).
- Prior item: A15 | Owner decision needed: no

### SCRAPERS-22: `NegotiationSignal.category` is written but never read
- Category: DEAD | Confidence: HIGH | Action: DELETE (field) | Risk: low | Effort: S | Size: ~5 lines
- Location: utils/negotiation.py:9-12; web/templates/detail.html:448-449 reads only `signal.text`
- Evidence: no template, test or src reads `.category`. The related CSS `.signal-buyer/.signal-seller/.signal-neutral` (web/static/style.css:4006-4014) is unused too; that is web lane. B7 is otherwise DONE: no ornamental scores remain.
- Recommendation: use plain strings, or drop the field.
- Prior item: B7 (DONE except this residue) | Owner decision needed: no

---

## Verified keep (looks dead, isn't)
- detail_fetcher.py:411-422 `_zoopla_next_time`, `_otm_next_time`, `_openrent_next_time`, `_img_*_next_time` (vulture "unused attribute"): read and written through `getattr/setattr` in `_throttle` (string dispatch).
- detail_identity.py:55/68 `handle_starttag`/`handle_endtag`: `HTMLParser` overrides.
- zoopla.py:39/47/56/64/76 `model_config`: Pydantic configuration.
- scrapers/__init__.py `__getattr__`/`__dir__`: live import hooks today (only removable together with SCRAPERS-8).
- BaseScraper.area_delay / should_skip_remaining_areas / max_areas_per_run (zero coverage): live base defaults called by pipeline/collection.py:215, 279 and 325; OpenRent uses the base `area_delay` and `should_skip`. Zero coverage only because tests mock scrapers.
- DetailFetcher._curl_get_with_retry/_get_client/_get_curl_session, ZooplaScraper._get_session/close (zero coverage): network paths that are mocked in tests; live in production.
- `ScrapeResult.is_healthy` (base.py:37-45): only tests read it, but it is the per-area contract predicate behind many source-contract assertions; production aggregates health in collection.py:332-337. Keep it as a deliberate pure interface (A15).
- `onthemarket_payloads.onthemarket_redux_state` legacy-envelope branch: shared by search, detail and availability, with a documented contract (onthemarket.md:18, 22).
- image_hash.py `fetch_and_hash_image`/`fetch_image_hashes_batch`: live as the hero-image fallback in matching/deduplication.py:261-271 when no cached gallery exists.
- image_hash.py `purge_corrupt_cached_images`: called inside `hash_cached_gallery` (:220).
- image_cache.py `_REJECTED_EXTENSIONS`: used by `is_valid_image_url` (:57).
- zoopla.py `_build_search_url` unused `furnish_types`/`min_bathrooms`/`include_let_agreed`, and onthemarket.py `min_bathrooms` (ruff ARG002): kept on purpose for interface uniformity; the comments at zoopla.py:676-679 explain why the filters are not sent.
- negotiation.py `category` is reported above; `cost_calculator`, `listing_facts`, `async_tasks`, `listing_identity` and `http_evidence` all have src callers.
- rightmove_payloads.py:186 `_reject_constant(value)` unused argument (ARG001): JSONDecoder `parse_constant` callback signature.
- scripts/reclassify_epc_images.py, validate_epc_detector.py: live consumers of `epc_detector` (not of floorplan_detector).

## Notes (cross-area leads)
- db lane: `PropertyStorage.get_all_known_source_ids` (db/storage.py:1297) has no src caller since early-stop was disabled (SCRAPERS-2).
- filters lane: `OffMarketChecker` docstring (filters/off_market.py:84-87) says "httpx for Rightmove", but `_CURL_SOURCES` (:59) includes `"rightmove"`. The httpx branch may be unreachable for the four portals. tests/test_filters/test_off_market.py:10 imports `is_challenge` via `filters.availability`, a re-export of `utils.http_evidence.is_challenge`.
- matching lane (possible correctness issue, not cleanup): `fetch_image_hashes_batch` (utils/image_hash.py:95-133) downloads hero images with plain `httpx` from every portal's CDN. Per docs/sources/README.md:17-22, zoocdn, OTM and OpenRent CDNs need curl_cffi, so the network hash fallback probably fails quietly (logged at debug) for 3 of 4 sources.
- analysis lane: analyzer.py:104 `APIUnavailableError as APIUnavailableError` re-export; request_budget.py:59 duplicates the media-type set.
- web lane: unused CSS `.negotiation-points li.signal-buyer/.signal-seller/.signal-neutral` (style.css:4006-4014).
- Removing Crawlee (SCRAPERS-1) also drops several transitive packages (impit, psutil, tldextract, pyee, html5lib…). Re-run `deptry` and the Docker wheel/package-content check afterwards.
- `hash_cached_gallery` deletes cache files PIL cannot open (`purge_corrupt_cached_images`) during dedup. That is a mutation inside a read/compare path, but it only touches undecodable bytes. Mention to whoever owns cache retention (A10/B9).
