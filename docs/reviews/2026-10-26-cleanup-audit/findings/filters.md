# FILTERS area audit (filters/, matching/, notifiers/ + utils/union_find.py)

Repo @ 51dd9c6. Read-only. Searches used `git grep -w` across the whole tree (src, tests, scripts, experiments,
docs, templates, static, .github, Dockerfile*, fly.toml), `git grep` on `origin/codex/review-opus-experiments`
(PR #77, zero hits for every candidate below), untracked/ignored files (`git status --ignored`: only coverage/cache
artefacts). Coverage contexts were queried from `scratchpad/evidence/coverage.db` with
`scratchpad/ctx_query.py` (which test files execute a given line).

Authoritative delivery path today: `pipeline/flow.py` builds `TelegramNotifier(delivery_repository=storage.deliveries)`;
startup recovery and final drain call `notifier.deliver_pending()`; per-result callback calls
`send_merged_property_notification`, which with a repository returns `deliver(self, repo, "discovery:<uid>")`
(`notifiers/delivery.py:179-267`, outbox in `db/delivery.py`). Intents are enqueued transactionally by
`db/delivery.enqueue_discovery` / `enqueue_price_drop`. No production code path reaches any other sender.

### FILTERS-1: Old Telegram senders are test-only (A7) — delete once the owner accepts the qualification route
- Category: SUPERSEDED  | Confidence: HIGH | Action: ASK-OWNER (then DELETE) | Risk: low (no production caller; notifications go through `deliver`) | Effort: M | Size: ~330 src lines, ~450 test lines
- Location: src/home_finder/notifiers/telegram.py:40-76 (`_telegram_retry*` + decorator), :436-476 (`format_property_message`), :732-740 (`_get_best_image_url`), :770-794 (`_resolve_gallery_photos`), :837-923 (`send_property_notification`), :948-973 (standalone no-repository branch of `send_merged_property_notification`), :988-1009 (`send_batch_notifications`), :1011-1059 (`send_status_message`), :1104-1165 (`send_price_drop_notification`); src/home_finder/notifiers/__init__.py:6,9 (re-export of `format_property_message`)
- Evidence: `git grep -w` per symbol — src callers: `send_property_notification` only from `send_batch_notifications` (itself test-only); `send_batch_notifications`, `send_status_message`, `send_price_drop_notification`, `_get_best_image_url`, `_resolve_gallery_photos` = 0 src/scripts/experiments/PR77 callers; `format_property_message` only from `send_property_notification`. Coverage contexts: telegram.py:857/1003/1021 executed only by tests/test_notifiers/test_telegram.py, :1118 only by tests/test_notifiers/test_telegram_status.py; `_telegram_retry_wait/_log/_exhausted` have zero coverage (uncovered_functions.txt) and decorate only these three senders. The standalone branch (:948-973) is reached only by tests and `scripts/test_notification.py:125` (sends a dummy alert to the real bot without persistence). Superseded by ccf7b9a "fix: resume Telegram deliveries from durable part receipts" (2026-10-10), which moved main.py to `deliver`. Precondition is documented as unmet: docs/operations.md:519-521 "The old sender remains available until a separately authorized controlled live qualification verifies the actual bot/chat path"; docs/reviews/2026-10-13-core-reliability-completion-audit.md:217-219; no later qualification record (`git log --since=2026-10-14`, docs/reviews/2026-10-2x: none). Note that "remains available" is only importable Python: no CLI flag, operator command or route reaches these methods, so keeping them gives no operational fallback. The "bounded operator-status path" the review asked to preserve does not exist as a runnable entry point: `send_status_message` has no caller anywhere.
- Recommendation: Prior to deletion, owner authorizes one bounded live qualification *of the durable path* — rewrite `scripts/test_notification.py` to enqueue the dummy into a scratch `PropertyStorage` and call `deliver()` (exercises plan, assets, receipts) instead of the standalone branch. Then delete the listed code, `tenacity`/`random`/most `aiogram.exceptions` imports in telegram.py, and move `price_drop_plan` (telegram.py:1061-1102, live via delivery.py:206) next to `discovery_plan` in delivery.py. Decide explicitly whether `send_status_message` becomes a wired operator command or is deleted. Retarget unique assertions first: (a) tests/test_notifiers/test_telegram_status.py:96-126 (only assertions of price-drop text: "Price Drop", prices, drop, postcode, "14 days") -> assert on `notifier.price_drop_plan(...).parts[0].payload["text"]`; (b) test_telegram.py:492-887 and :1403-1550 (photo vs album vs single photo, album only for fit>=70 with >=3 images, venue only for high fit with coords, follow-up text, thumbnail for single photo / originals for album, URL fallback without data_dir) plus tests/test_notifiers/test_telegram_web.py:121-145 and tests/integration/test_telegram_notification_e2e.py -> call `discovery_plan` + `request_payload` directly. Delete with the code: test_telegram.py:354-395 (TestGetBestImageUrl), :408-490 (send_property/batch), :1092-1107 (status), :1109-1127 (LinkPreviewOptions, old path only), :1371-1401 (TestResolveGalleryPhotos); tests/notifier_helpers.py:15-16 and the five `send_property_notification`/`send_price_drop_notification.assert_not_awaited()` lines (test_recovery.py:154,259,370; test_run_pipeline_e2e.py:319,387; test_cross_run_reanalysis_e2e.py:1315) which only guard against the deleted senders.
- Prior item: A7 — OUTSTANDING   | Owner decision needed: yes

### FILTERS-2: Full text-message alert format is unreachable in production (deliver defers imageless homes before planning)
- Category: DEAD  | Confidence: HIGH | Action: ASK-OWNER (DELETE, or make it an intentional fallback) | Risk: low (only affects a format nobody receives) | Effort: M | Size: ~280 src lines + ~250 test lines
- Location: src/home_finder/notifiers/delivery.py:93-102 (text-only branch of `discovery_plan`); src/home_finder/notifiers/telegram.py:479-550 (`format_merged_property_message`), :370-433 (`_format_quality_block`), :91-154, :279-349 (`_format_kitchen_info`, `_format_light_space_info`, `_format_space_info`, `_format_bathroom_info`, `_format_outdoor_info`, `_format_listing_extraction_info`), :190-210 (non-brief `_format_value_info`), :264-274 (non-brief header), :86-88 (`_html_link`)
- Evidence: `deliver()` (delivery.py:193-199) returns `defer(key, "no_images")` when `_get_gallery_urls(snapshot.merged)` is empty and only then calls `discovery_plan`, which branches on the *same* `_get_gallery_urls(merged)` (delivery.py:59,66) — so its `else` (text message) can never run from `deliver`. The callback in pipeline/flow.py:346-357 applies the same gate earlier. Coverage contexts: delivery.py:98 executed only by tests/test_notifiers/test_telegram.py (standalone sender) and tests/test_web/test_score_consistency.py (calls `discovery_plan` directly on an imageless fixture); telegram.py:379 (`_format_quality_block`) and :499 only by test_telegram.py, test_telegram_notification_e2e.py, test_score_consistency.py, tests/test_analysis/test_outdoor_evidence.py. `git grep` of each helper: zero src callers outside telegram.py; no template/JS usage. docs/operations.md:471-473 confirms imageless intents wait for images.
- Recommendation: Owner decides whether imageless homes should ever alert as text. If not: delete the branch and the full-message formatter family, keep the caption/follow-up helpers that `discovery_plan` actually uses (`format_merged_property_caption`, `_format_header_lines(brief=True)`, `_format_value_info(brief=True)`, `_format_viewing_notes`, `_epc_qualifier`, `_build_inline_keyboard`). Update tests/test_web/test_score_consistency.py:78-85 (currently "all three Telegram formatters" + plan text, cited in the core-reliability audit:170) to assert caption + a photo plan's caption. Delete/retarget test_telegram.py:43-180, :967-1025, :1064-1090, value-info full-mode tests, test_outdoor_evidence.py `_format_quality_block/_format_outdoor_info` cases, test_floor_area.py `_format_space_info` cases. Do together with FILTERS-1.
- Prior item: A7 (adjacent) | Owner decision needed: yes

### FILTERS-3: `send_merged_property_notification` is two functions; the production one ignores its arguments (B6)
- Category: OVERBUILT  | Confidence: HIGH | Action: SIMPLIFY | Risk: medium (touches the live alert callback) | Effort: S | Size: ~40 lines src (+ flow.py callback)
- Location: src/home_finder/notifiers/telegram.py:925-973; src/home_finder/pipeline/flow.py:331-394
- Evidence: with `delivery_repository` set (always, flow.py:116-122) the method is `return await deliver(self, self.delivery_repository, f"discovery:{merged.unique_id}")` — `commute_minutes`, `transport_mode`, `quality_analysis` are discarded; the snapshot comes from the outbox row. The flow.py callback still unpacks commute info to pass them, repeats deliver's no-images gate (flow.py:346 vs delivery.py:195), and `await asyncio.sleep(1)  # Telegram rate limit` after every assessed home even when `deliver` returned immediately for a suppressed (dashboard-only) intent. Timely per-completion delivery is retained (good).
- Recommendation: Replace with `async def deliver_discovery(self, unique_id: str) -> bool` (or call `deliver` directly from the callback); pass only the key; drop the duplicate image gate and sleep only after an actual send. Pipeline auditor owns flow.py; coordinate. The standalone branch goes with FILTERS-1.
- Prior item: B6 — PARTIAL (timeliness kept; interface not reduced to the intent key)   | Owner decision needed: no

### FILTERS-4: `UnionFind` is dead code (A4)
- Category: DEAD  | Confidence: CERTAIN | Action: DELETE | Risk: low | Effort: S | Size: ~29 lines (whole file)
- Location: src/home_finder/utils/union_find.py:1-29
- Evidence: `git grep -n "union_find\|UnionFind"` over HEAD and PR #77 branch: only the definition. No importer in src/tests/scripts/experiments; no dynamic import string. Coverage 0% (all lines missing). Never imported anywhere in visible history (`git log -S UnionFind` only root commit 63f4271). Grouping is now greedy (`deduplication._group_items_greedy`, "don't merge groups to avoid transitive chains").
- Recommendation: Delete the file.
- Prior item: A4 — OUTSTANDING   | Owner decision needed: no

### FILTERS-5: Binary `coordinates_match` / `prices_match` superseded by graduated scores, test-only
- Category: TEST-ONLY  | Confidence: HIGH | Action: DELETE | Risk: low | Effort: S | Size: ~45 src + ~160 test lines
- Location: src/home_finder/matching/scoring.py:156-198; tests/test_matching/test_deduplication.py:42-70 (TestFuzzyPriceMatching), :94-227 (TestCoordinateMatching)
- Evidence: `git grep -w` — only tests/test_matching/test_deduplication.py and docs (A3-filters.md:220-228 flagged them unused on 2026-02-16; still present). `calculate_match_score` uses `graduated_coordinate_score`/`graduated_price_score`, which have their own tests (test_deduplication_weighted.py:225-249, test_property_based.py). Not in experiments/scripts/PR77.
- Recommendation: Delete both functions and their two test classes; keep `haversine_distance` (used by `graduated_coordinate_score`, tests and experiments).
- Prior item: A15   | Owner decision needed: no

### FILTERS-6: `Deduplicator.deduplicate_and_merge_async` is a test-only composition; cross-platform/image-hash defaults are the opposite of production
- Category: TEST-ONLY  | Confidence: HIGH | Action: DELETE (retarget tests) + SIMPLIFY defaults | Risk: low | Effort: S-M | Size: ~45 src lines; ~38 test call sites
- Location: src/home_finder/matching/deduplication.py:129-170, :107-127 (defaults + stale docstring "based on postcode + price + beds"), :232-233
- Evidence: production uses `properties_to_merged` (pipeline/candidates.py:103) then `deduplicate_merged_async` (pipeline/matching.py, pipeline/commands.py:278). `deduplicate_and_merge_async` = `_dedupe_by_unique_id` + `_single_to_merged` + `_deduplicate_merged_items`, i.e. exactly `deduplicate_merged_async(properties_to_merged(props))`. Callers: tests/test_matching/test_deduplication_e2e.py (29), tests/integration/test_selection_persistence.py (5), test_cross_platform_dedup_e2e.py (3), test_real_scraping.py (1, slow); coverage contexts for deduplication.py:145 confirm only those. A3-filters.md:358-366 flagged it in Feb. All three production `Deduplicator(...)` calls pass `enable_cross_platform=True`; the `False` default and `if not self.enable_cross_platform: return items` are exercised only by test_deduplication_e2e.py:281 via this wrapper (other `False` uses only call `properties_to_merged`).
- Recommendation: Retarget tests to `deduplicate_merged_async(d.properties_to_merged(...))` (mechanical), delete the wrapper; then consider dropping `enable_cross_platform` (always True) and fixing the docstring. Leave `enable_image_hashing` until A19 is resolved (see Notes).
- Prior item: A15   | Owner decision needed: no

### FILTERS-7: `compute_fit_breakdown` is a test-only wrapper
- Category: TEST-ONLY  | Confidence: HIGH | Action: DELETE (retarget tests) | Risk: low | Effort: S | Size: ~8 src lines, ~17 test refs
- Location: src/home_finder/filters/fit_score.py:775-782 (and the now-stale advice at :790-791)
- Evidence: `git grep -w compute_fit_breakdown`: tests/test_filters/test_fit_score.py (14), tests/test_web/test_score_consistency.py (3), docs only; production (web/routes.py:312,739) uses `compute_fit_score_and_breakdown`. Coverage contexts for :782 = only those two test files. Not in scripts/experiments/PR77.
- Recommendation: Replace test calls with `compute_fit_score_and_breakdown(...)[1]`; delete the wrapper and the "Use this instead of calling compute_fit_score + compute_fit_breakdown" docstring sentence.
- Prior item: A15   | Owner decision needed: no

### FILTERS-8: `OffMarketChecker` httpx path is unreachable by construction; docstring says the opposite
- Category: DEAD  | Confidence: CERTAIN | Action: DELETE | Risk: low | Effort: S | Size: ~25 lines
- Location: src/home_finder/filters/off_market.py:84-100 (`_httpx_client`, `_get_httpx_client`), :120-127 (`_fetch_with_httpx`), :129-133 (`_fetch` branch), :264-269 (close), :11 `import httpx`, :52 (mid-file import `# noqa: E402`), :79-82 docstring
- Evidence: `check_url` returns UNKNOWN unless `source in _SOURCE_DELAYS` (:137) whose keys are {zoopla, onthemarket, openrent, rightmove}; `_CURL_SOURCES` (:55) is the same four, so `_fetch` always takes curl. Rightmove was added to `_CURL_SOURCES` in a064e99 (2026-03-01) without removing the httpx path. Docstring still says "curl_cffi for Zoopla/OTM/OpenRent, httpx for Rightmove". (Coverage note: `_fetch`/`_fetch_with_curl` are also uncovered, but they are the live network path — tests patch `_fetch`; only the httpx half is dead.)
- Recommendation: Remove the httpx client/branch and import, make `close()` a no-op (or drop the context manager after updating test_async_context_managers.py:63-70), move the `ConsecutiveFailureBreaker` import to the top, fix the docstring. Consider whether one curl `AsyncSession` per URL (:106) is intended.
- Prior item: none   | Owner decision needed: no

### FILTERS-9: Explicit re-export aliases kept only for tests
- Category: COMPAT  | Confidence: HIGH | Action: SIMPLIFY | Risk: low | Effort: S | Size: ~12 lines + 4 test imports
- Location: src/home_finder/filters/location.py:8-16 (`BOROUGH_OUTCODES as BOROUGH_OUTCODES` unused in module; `normalize_area as normalize_area`); src/home_finder/filters/availability.py:21-22 (`is_challenge as is_challenge`, `listing_id as listing_id`); src/home_finder/filters/off_market.py:17 (`ListingStatus as ListingStatus`); src/home_finder/notifiers/__init__.py:3-9 (`format_property_message`, `format_merged_property_caption` in `__all__`)
- Evidence: consumers of the aliases: tests/test_filters/test_location.py:6-11 (imports BOROUGH_OUTCODES, normalize_area and — with `# type: ignore[attr-defined]` — `extract_outcode` from location), tests/test_filters/test_off_market.py:10 (`is_challenge` via availability), pipeline/commands.py:87 + 2 tests (`ListingStatus` via off_market). `from home_finder.notifiers import` is used only by flow.py:18 for `TelegramNotifier`; the two format names have no package-level consumer.
- Recommendation: Point the tests (and commands.py) at the defining modules (`data.location_mappings`, `utils.address`, `utils.http_evidence`, `filters.availability`), drop the `X as X` aliases and the unused `__all__` entries.
- Prior item: A3 — PARTIAL in this area   | Owner decision needed: no

### FILTERS-10: CLAUDE.md filters/matching claims contradicted by code
- Category: STALE-DOC  | Confidence: CERTAIN | Action: REWRITE-DOC | Risk: low | Effort: S | Size: ~6 lines
- Location: CLAUDE.md:116 ("Cross-platform matching requires full postcodes"), :157 (Filters pointer), :192 (tests/test_filters description); also CLAUDE.md Quality Analysis section ("`_build_tool_schema()` in `quality.py`; prompts in `quality_prompts.py`")
- Evidence: scoring.py:264-333 has no full-postcode gate; test_deduplication_weighted.py:283-310 `test_rightmove_scenario_with_image` asserts an outcode-only Rightmove listing *matches* (image 40 + street 20 + outcode 10 + price 15). :157 lists `CommuteFilter`, `Deduplicator`, `PropertyQualityFilter` (`quality.py` + `quality_prompts.py`) under filters/ — 41f499e (2026-10-25) moved them to enrichment/commute.py (`CommuteAssessor`), matching/deduplication.py, analysis/analyzer.py (`PropertyQualityAnalyzer`) and analysis/prompts.py. tests/test_filters now holds availability, criteria, fit_score, location, off_market, shadow_rating; dedup tests live in tests/test_matching. Verified accurate: threshold 60 + 2 signals, weights 40/40/40/20/10/15, graduated 1.0/0.5/0.0 (scoring.py:24-37, 201-261).
- Recommendation: Rewrite the Conservative-matching bullet (blocking by outcode+bedrooms; full postcode is one signal; zero image matches between two galleries vetoes, deduplication.py:642-660), the Filters/Matching/Analysis pointers and the test-structure line. Docs auditor may already cover; same edit.
- Prior item: none (A13/docs)   | Owner decision needed: no

### FILTERS-11: experiments import modules removed by yesterday's move
- Category: HYGIENE  | Confidence: CERTAIN | Action: ASK-OWNER (fix imports or mark frozen) | Risk: low | Effort: S | Size: ~4 import lines
- Location: experiments/dedup/signals.py:16 (`home_finder.filters.scoring`); experiments/layout-inference/run_inference.py:33,164 (`home_finder.filters.quality.PropertyQualityFilter`, `filters.quality_prompts`)
- Evidence: `importlib.util.find_spec` returns None for `home_finder.filters.scoring`, `.quality`, `.quality_prompts`; both experiment pyprojects depend on `home-finder` as an editable path dependency. Moved by 41f499e "refactor: make pipeline stages and contracts explicit" (scoring -> matching/scoring.py; quality.py -> analysis/analyzer.py with class renamed `PropertyQualityAnalyzer`; quality_prompts -> analysis/prompts.py). Not collected by pytest (`testpaths = ["tests"]`).
- Recommendation: Owner chooses: update the imports (dedup is a one-line fix to `home_finder.matching.scoring`; layout-inference also needs the class rename) or record the experiments as frozen evidence pinned to a commit. Do not delete (decision evidence).
- Prior item: none   | Owner decision needed: yes

### FILTERS-12: Function-local aiogram imports buy nothing; telegram/delivery split is circular
- Category: OVERBUILT  | Confidence: HIGH | Action: SIMPLIFY (after FILTERS-1) | Risk: low | Effort: S | Size: ~20 lines
- Location: src/home_finder/notifiers/telegram.py:31-35, 666, 755-757, 780, 827-829, 876, 939, 977, 1071, 1116; src/home_finder/notifiers/delivery.py:24-31, 134
- Evidence: telegram.py:9 imports `aiogram.exceptions` at module level; `python -c "import sys, aiogram.exceptions; print('aiogram.types' in sys.modules, 'aiogram.client.bot' in sys.modules)"` -> `True True`, so the deferred `from aiogram.types import ...` / `from aiogram import Bot` and `TYPE_CHECKING` block avoid no import cost. delivery.py imports four *private* helpers from telegram.py at module level while telegram.py imports `deliver`/`discovery_plan`/`request_payload` lazily to dodge the cycle; `price_drop_plan` is a notifier method while `discovery_plan` is a delivery function.
- Recommendation: After FILTERS-1/2, make telegram.py pure formatting (public names, top-level imports) and delivery.py own both plan builders plus transport; the remaining `TelegramNotifier` needs only bot/config + `deliver_pending`.
- Prior item: none   | Owner decision needed: no

### FILTERS-13: Unreachable defensive branches and history-narrating comments in matching/fit_score
- Category: AI-ISM  | Confidence: HIGH | Action: DELETE | Risk: low | Effort: S | Size: ~12 lines
- Location: src/home_finder/matching/deduplication.py:688-692; src/home_finder/matching/scoring.py:13, 32-33; src/home_finder/filters/fit_score.py:689-703 (`else: score = 50.0`), :696/:700 `.get(..., 'Fair'/'Poor')` defaults
- Evidence: deduplication.py:691-692 re-checks source overlap "belt-and-suspenders ... guards against future refactors" — pairs with overlapping sources were already skipped at :627 and sources don't change; coverage: line 692 executed by 0 test contexts. fit_score `_score_*` are only reached through `normalized_fit_inputs` (:797, :851), which returns None unless `overall_rating` is an int in 1..5 (:42-48), so the else branch can't run (coverage: :703 0 contexts) and `int(overall)` always hits the dict. scoring.py comments "3% - tighter than before", "raised from 55" narrate history.
- Recommendation: Remove the dead branches/defaults; reword comments to state the rule. (Tests call `_score_*` with raw dicts only for hosting/kitchen/vibe/workspace, not condition.)
- Prior item: none   | Owner decision needed: no

### FILTERS-14: Score working re-implements the scorer's weighted mean
- Category: DUP  | Confidence: LOW | Action: SIMPLIFY | Risk: low | Effort: S | Size: ~15 lines
- Location: src/home_finder/web/working.py:24-65; src/home_finder/filters/fit_score.py:146-157 (`raw_score`/`raw_confidence` NotRequired), :803-830
- Evidence: `fit_working` recomputes `sum(w*c*s)/sum(w*c)` and `round()` "the way the scorer does" (959984f "fix: round the working's total the way the scorer does" — a drift bug already happened once). Guarded by tests/test_web/test_working.py:41-52. The `dim.get("raw_score", dim["score"])` fallback only serves hand-built test dims; every production breakdown comes from `compute_fit_score_and_breakdown` (routes.py:312, 739).
- Recommendation: Have the scorer expose its unrounded total (e.g. a third tuple element or a small result object) and pass it in; make the raw fields required. Web auditor owns working.py.
- Prior item: none   | Owner decision needed: no

### FILTERS-15: Vibe score double-counts canal/park views
- Category: DUP  | Confidence: LOW | Action: ASK-OWNER | Risk: medium (changes stored fit scores; needs FIT_SCORE_VERSION bump) | Effort: S | Size: ~4 lines
- Location: src/home_finder/filters/fit_score.py:111-120, 612-624, 646-652
- Evidence: "Canal views"/"Park views" add a view bonus in cluster 4 (:616-623) and again via `_HIGHLIGHT_SCORES` in cluster 6 (:648-652). No comment says this is intended.
- Recommendation: Owner confirms intent; if unintended remove them from `_HIGHLIGHT_SCORES` and bump `FIT_SCORE_VERSION` (backfill runs automatically, storage.py:330-373).
- Prior item: none   | Owner decision needed: yes

### FILTERS-16: Misnamed / misplaced off-market tests
- Category: TEST-HYGIENE  | Confidence: HIGH | Action: MOVE + rename | Risk: low | Effort: S | Size: ~45 test lines
- Location: tests/test_filters/test_off_market.py:73-108 (TestCloudflareHeaderDetection), :118 (`test_get_404_is_removed`)
- Evidence: TestCloudflareHeaderDetection only exercises `utils.http_evidence.is_challenge` (imported via the availability alias); `test_checker_returns_unknown_on_cf_mitigated_header` and `test_zoopla_cf_mitigated_header` never construct a checker. There is no tests/test_utils/test_http_evidence.py, so these are the only `is_challenge` tests — move, don't delete. `test_get_404_is_removed` mocks status 410 (a bare 404 is UNKNOWN per availability.py:268-279).
- Recommendation: Move the class to tests/test_utils/test_http_evidence.py importing from `utils.http_evidence`; drop the two duplicate "checker" cases or make them call `check_url`; rename to `test_get_410_is_removed`.
- Prior item: A12   | Owner decision needed: no

## Verified keep (looks dead, isn't)
- `FitTier.tier_class` / `color_class` (vulture 60%): read by `fit_tier_class`/`fit_color_class` (web/routes.py, _macros.html) and FIT_TIERS JSON injected for map-dashboard.js.
- `FitDimension.raw_score` / `raw_confidence` (vulture): read by web/working.py:36-37.
- `LifestyleIcon.tooltip` (vulture): templates/_macros.html:38-43.
- `ShadowRating.policy_version/observed_*/missing_components` (vulture): serialized by `asdict` in pipeline/rating_shadow_report.py:66.
- `_ScoredPair.neg_score` (vulture): sort key via `@dataclass(order=True)` (deduplication.py:666).
- `MatchConfidence`, `MatchScore.to_dict`, `MatchDecision`: dedup logs and `properties_merged` match records; `MatchScore`/`calculate_match_score` also used by operator script scripts/audit_duplicate_candidates.py.
- `price_drop_plan`: live via delivery.py:206 (only its `send_price_drop_notification` wrapper is dead).
- `format_merged_property_caption`, `_format_followup_detail`, `_get_gallery_urls`, `_resolve_photo`, `_build_inline_keyboard`, `_format_viewing_notes`, `_epc_qualifier`, brief branches of `_format_value_info`/`_format_header_lines`: live via `discovery_plan`.
- `OffMarketChecker._fetch` / `_fetch_with_curl` (zero coverage): live network path; tests mock `_fetch`.
- `--check-off-market`: wired at main.py:91,209 -> pipeline/commands.run_check_off_market (README/CLAUDE.md commands accurate).
- availability.py vs off_market.py: no overlapping logic — availability is the pure, fixture-tested page evaluator (sole consumer off_market); off_market is transport, rate limit and circuit breaker. Both Rightmove `window.__PAGE_MODEL` and `window.PAGE_MODEL` forms have fixtures (tests/fixtures/rightmove_*), so the second parser path is not an unsupported fallback.
- `filters/__init__` re-exports `CriteriaFilter`, `LocationFilter`: used by pipeline/candidates.py:10.
- media.py `filter_by_floorplan` / `is_floorplan_exempt`, notification_policy.py, shadow_rating.py: live (candidates.py, db/delivery.py, report CLIs).
- `# pragma: no mutate` (5, scoring.py + location.py): match `[tool.mutmut] paths_to_mutate`; consistent with B8's optional targeted use.
- CLAUDE.md dedup constants/graduated scoring description: accurate.
- `_telegram_retry_*` callbacks (zero coverage): not independently dead — they go with FILTERS-1.

## Notes
- Prior items: A7 OUTSTANDING (FILTERS-1); A4 union-find OUTSTANDING (FILTERS-4); A3 PARTIAL (FILTERS-9); A15 OUTSTANDING here (FILTERS-5/6/7); B6 PARTIAL (FILTERS-3); B7 DONE (utils/negotiation.py now emits only first-observed/price-change observations and "do not support a fixed discount" guidance — no scores; outside my lane); A19 OUTSTANDING — pipeline/candidates.py:536 hard-codes `enable_image_hashing=True` for cross-run dedup while :100 and commands.py:275 honour `settings.enable_image_hash_matching` (pipeline/config auditor).
- DB lead (A16): storage legacy notification API has no src callers — `mark_notified` (59 test refs), `get_unsent_notifications` (10), `get_pending_notifications` (2), `mark_notification_failed` (5), `mark_price_drop_notified` (2), `get_unsent_price_drops` (8, used as assertions in delivery tests — check before removing). `TrackedProperty` otherwise only feeds these and tests/test_notifiers/conftest.py `sample_tracked_property` (old-sender tests).
- Pipeline lead: pipeline/notification_report.py:70 `--minimum-fit-score` default 60 duplicates `NotificationPolicy.min_fit_score` default (models/notifications.py:12).
- Web leads: web/static/modules/map-dashboard.js:34 hard-codes FIT_TIERS fallback, contradicting fit_score.py:66-67 "never hard-code thresholds"; web/brief.py:58-60,129 couples to scorer internals (`_THIN_CONFIDENCE = 0.5` "the scorer gives 0.5 when it has a bedroom count", and `label[:1].isdigit()` to spot "2+ bedrooms") — will silently drift if `_score_workspace` changes.
- delivery.py:55 `high = fit >= 70` (album + venue) is neither a FIT_TIERS boundary (80/60/40) nor policy-driven; undocumented in operations.md. Owner may want it named or tied to a tier.
- fit_score.py size (1014 lines): no parallel implementation of the six dimension scorers exists elsewhere; the only re-derivation is the final mean in web/working.py (FILTERS-14). ~180 lines (lifestyle icons + `fit_tier_class`/`fit_color_class`) are web-only presentation and could MOVE to web/ (LOW). `_icon_vibe` keyword matching tolerates historical free-text highlights (`highlights: list[str]`), so keep it.
- Docs leads: CLAUDE.md Pipeline Flow steps 1 ("Retry Unsent") and 12 ("Save & Notify") describe the pre-outbox path; Notifier pointer omits notifiers/delivery.py + db/delivery.py; docs/operations.md:519 "old sender remains available" should say it has no operator entry point.
- `TelegramNotifier.__init__` docstring omits `delivery_repository`; `deliver_pending` raises if it is None (only the standalone path tolerates None).
