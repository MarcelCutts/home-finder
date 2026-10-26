# PIPELINE area findings

Scope: `src/home_finder/pipeline/` (all 20 modules), `src/home_finder/main.py`, `src/home_finder/config.py`,
`src/home_finder/logging.py`, root `main.py`, `.env.example`, plus the CLAUDE.md, README and docs claims about them.
Repo at 51dd9c6. I only read files and ran `--help` smoke tests and in-process `Settings`/logging checks; no tests and no network.

## Prior-item status (architecture review §"Which deletions are valuable")

| Item | Status | Where |
|---|---|---|
| A3 re-exports/lazy loaders (pipeline package) | **DONE**: 41f499e replaced the 74-line `pipeline/__init__.py` re-export list with a docstring | pipeline/__init__.py |
| A6 disabled early-stop plumbing | **OUTSTANDING** | PIPELINE-1 |
| A10/B9 hidden repair/reanalysis tools | **OUTSTANDING** (scripts); the main CLI has one hidden mutation (B5 deletes corrupt files) | PIPELINE-27, PIPELINE-16 |
| A13 root leftovers | **PARTIAL**: root `main.py` and `crontab` remain | PIPELINE-19, Notes |
| A15 wrappers only reached from tests | **OUTSTANDING** in this area | PIPELINE-11, -12, -26 |
| A19 image-hash setting | **OUTSTANDING**: the setting has no effect on the live pipeline | PIPELINE-2 |
| B2 default model selection | **DONE**: `new_runtime_analysis_config` plus the merge in `_parse_analysis_config` (fe31547) | config.py:17-46, 292-316 |
| B4 `--dedup-existing` | **PARTIAL**: narrowed to staging retirement and a read-only report exists, but the mutation is kept and README is stale | PIPELINE-15 |
| B5 thumbnail command | **PARTIAL**: enrichment thumbnails, but the repair download does not; the command also deletes files | PIPELINE-16 |
| B6 per-result callback | **OUTSTANDING** | PIPELINE-14 |

---

### PIPELINE-1: `--full-scrape` and the disabled early-stop plumbing (A6)
- Category: SUPERSEDED  | Confidence: HIGH | Action: DELETE | Risk: low | Effort: M | Size: ~70 src lines + ~80 test lines
- Location: src/home_finder/main.py:111-115,144-145,230,240; src/home_finder/pipeline/flow.py:41,54,67,78,89,145; src/home_finder/pipeline/candidates.py:46,70; src/home_finder/pipeline/collection.py:39,46-48,71,155,174,202-204,301; src/home_finder/scrapers/base.py:92,115,191-197 (+ the `known_source_ids` param in the 4 scrapers); src/home_finder/db/storage.py:1297-1314 (`get_all_known_source_ids`); tests/test_pipeline/test_orchestration.py:480-546
- Evidence:
  - `collect_listings` accepts `full_scrape` and never reads it (ruff ARG001, collection.py:39).
  - `known_ids_by_source = None` is hard-coded (collection.py:48), so `scrape_all_platforms` always passes `known_source_ids=None`. The `early_stop_all_known` branch in base.py:191-197 is unreachable in production.
  - `get_all_known_source_ids` has no src caller (`git grep get_all_known_source_ids -- src scripts` finds only its definition). Its production call was removed in ec9c2fc "fix: preserve source text and recover safe duplicate merges".
  - `--full-scrape` help says "Compatibility flag", and README.md:66 says the same.
  - `test_full_scrape_skips_known_ids_lookup` is vacuous: it asserts a lookup that never happens. `test_known_ids_passed_to_scrapers` keeps the dead plumbing alive.
  - Nothing references this in experiments/, scripts/ or the PR #77 branch.
- Recommendation: Remove the flag, the `full_scrape` params (flow, candidates, collection), `known_ids_by_source`, the scraper-level `known_source_ids` and early-stop branch, `get_all_known_source_ids` and their tests. Keep the page and result caps (the review says to preserve newest-first behaviour and explicit run limits). Update README.md:66 and the test_cli cases for `--full-scrape`. Coordinate with the scrapers and db agents on base.py and storage.py.
- Prior item: A6   | Owner decision needed: no (already decided). Marcel's local scripts may pass `--full-scrape`; argparse would then error loudly, not silently.

### PIPELINE-2: `enable_image_hash_matching` has no effect on the live pipeline (A19)
- Category: CONFIG  | Confidence: CERTAIN | Action: ASK-OWNER | Risk: low | Effort: S | Size: ~10 lines + docs
- Location: src/home_finder/config.py:188-192; src/home_finder/pipeline/candidates.py:98-103,531-538; src/home_finder/pipeline/commands.py:273-277; CLAUDE.md:208; README.md:191; .env.example:28
- Evidence:
  - The live path reads the setting at candidates.py:100, but that `Deduplicator` is used only for `properties_to_merged()`. That function only wraps (matching/deduplication.py:172-184 → `_single_to_merged`) and never hashes.
  - Real cross-run consolidation hard-codes `enable_image_hashing=True` (candidates.py:531-538, with the comment "independent of the global enable_image_hash_matching flag").
  - The only other reader is `run_dedup_existing` (commands.py:275).
  - Docs still call it "Enable perceptual image hashing for deduplication".
- Recommendation: Make one truthful policy, as A19 asks. Either delete the setting (and the throwaway `Deduplicator` arguments at candidates.py:98-102) and let `--dedup-existing` always hash, or wire it into consolidation. Reconcile with B4 first, since its only consumer is that command.
- Prior item: A19   | Owner decision needed: yes

### PIPELINE-3: CLAUDE.md "Pipeline Flow (main.py)" and Architecture Pointers contradict the code
- Category: STALE-DOC  | Confidence: CERTAIN | Action: REWRITE-DOC | Risk: medium (agents follow CLAUDE.md) | Effort: S | Size: ~20 lines
- Location: CLAUDE.md:73-86, 140, 157
- Evidence:
  - **Location of the flow.** The heading says main.py. main.py only parses the CLI and dispatches; the flow is `pipeline/flow.py` (docs/pipeline.md "Reading order").
  - **Step 1 "Retry Unsent".** Recovery is now `storage.deliveries.recover()` plus `notifier.deliver_pending()` (flow.py:126-134), skipped in dry runs. It is preceded by the lock, spend recovery, run creation and cache repair (flow.py:60-124), none of which is listed.
  - **Steps 7/8 order.** Commute is listed before enrichment. Code assesses commute *after* detail enrichment (candidates.py:159 "Assess commute after detail acquisition"). It is also advisory and never filters ("dropped=0", candidates.py:471).
  - **Step 8.** Omits the shared `max_enrichment_per_run` admission and the pending-enrichment interleave (candidates.py:298-346).
  - **Step 9.** Describes in-batch `deduplicate_merged_async`. Code consolidates against stored 30-day anchors via `consolidate_candidates` (pipeline/matching.py:51).
  - **Step 10.** Says it "drops" properties. It also admits by photo count or exempt source, and persists the dropped ones (candidates.py:560-594).
  - **Steps 11/12.** Saving happens *before* analysis (`save_pre_analysis_properties`, assessment.py:94). The pending-analysis interleave, the reanalysis phase and the final delivery drain are missing.
  - **Line 157** names `CommuteFilter` and `PropertyQualityFilter` (`git grep` finds neither class) and `filters/quality.py`/`quality_prompts.py`. 41f499e moved those to `analysis/analyzer.py`, `analysis/prompts.py`, `enrichment/commute.py` and `matching/`. Line 140 has the same stale `quality.py` reference.
  - README.md:262-288 and docs/pipeline.md describe the current flow correctly.
- Recommendation: Replace the 12-step list with a 3-line pointer to docs/pipeline.md and the README "full workflow" list, and fix the Architecture Pointers paths. Coordinate with the docs agent.
- Prior item: none (review asks to "fix CLAUDE.md claims that contradict current … handling")   | Owner decision needed: no

### PIPELINE-4: CLAUDE.md Commands says `--serve` starts the recurring pipeline
- Category: STALE-DOC  | Confidence: CERTAIN | Action: REWRITE-DOC | Risk: medium (it contradicts the "keep collection manual" scope) | Effort: S | Size: ~8 lines
- Location: CLAUDE.md:39 (also :30-44)
- Evidence:
  - CLAUDE.md:39 reads `--serve  # Web dashboard + recurring pipeline scheduler`.
  - Code: main.py:217 `create_app(settings, run_pipeline=args.with_pipeline)`, with help text "Start the dashboard; collection requires --with-pipeline". README.md:97 and operations.md:20 agree with the code.
  - The list also omits `--reanalyze/--all/--outcode/--request-only`, `--backfill-commute`, `--dedup-existing`, `--with-pipeline/--no-pipeline`, `--scrapers` for collection, and every documented `python -m home_finder.pipeline.*` operator command except rating_shadow_report.
- Recommendation: Correct line 39 and point to README "Usage" for the full list instead of duplicating it.
- Prior item: none   | Owner decision needed: no

### PIPELINE-5: CLAUDE.md "Quality Analysis" model and token limits contradict config.py defaults
- Category: STALE-DOC  | Confidence: CERTAIN | Action: REWRITE-DOC | Risk: medium (cost reasoning by agents) | Effort: S | Size: ~6 lines
- Location: CLAUDE.md:136-139; src/home_finder/config.py:29-46
- Evidence:
  - CLAUDE.md says "Uses claude-sonnet-4-6", Phase 1 `max_tokens: 21000`, Phase 2 `max_tokens: 4096`.
  - `new_runtime_analysis_config` defaults both phases to `claude-sonnet-5-5`, `effort="low"`, `max_output_tokens=8192`, `sdk_retries=0`, and evaluation `reasoning="disabled"`. operations.md:99-105 agrees with the code.
- Recommendation: Replace with a pointer to operations.md "Runtime model defaults". Cross-area with the analysis/docs agents; dedupe there.
- Prior item: B2 (implementation done, docs not)   | Owner decision needed: no

### PIPELINE-6: `.env.example` quotes stale per-attempt reservation amounts
- Category: STALE-DOC  | Confidence: CERTAIN | Action: REWRITE-DOC | Risk: medium (spend-cap choices) | Effort: S | Size: 1 line
- Location: .env.example:89-90
- Evidence:
  - .env.example says "At 20 images, reservations are $0.98392 visual / $0.64192 evaluation per attempt".
  - operations.md:112-113 says $0.64817 visual and $0.43192 evaluation. The test pins it: tests/test_analysis/test_runtime_analysis_defaults.py:295 asserts `reserved_nanousd == 648_170_000` at 20 images.
  - Arithmetic check: 226,500 input × $2.50/M + 8,192 output × $10/M = $0.64817.
  - The .env.example figures match the superseded one-hour cache-write (v1) card ($4/M): 140,000 × 4/M + 0.08192 = $0.64192. `git log -S0.98392` → 1b0d4bc and fe31547; it was not updated by 122cb6f or later.
- Recommendation: Replace with the operations.md figures, or with "see docs/operations.md" so there is a single source.
- Prior item: none   | Owner decision needed: no

### PIPELINE-7: Default `SEARCH_AREAS` is documented differently from the code
- Category: STALE-DOC  | Confidence: CERTAIN | Action: REWRITE-DOC | Risk: low | Effort: S | Size: 2 lines
- Location: src/home_finder/config.py:226-229; README.md:203; .env.example:55
- Evidence:
  - Code default (checked in-process): `e2,e3,e5,e8,e9,e10,e15,e17,n1,n5,n15,n16,n17`.
  - README and .env.example say `e2,e3,e5,e9,e10,e15,e17,n15,n16,n17`, which is missing e8, n1 and n5.
  - The code default changed in 33bd1c4 (2026-02-24) and the docs were never updated. Every other commented .env.example line shows the real default.
- Recommendation: Update both lines.
- Prior item: none   | Owner decision needed: no

### PIPELINE-8: Settings fields missing from the env reference (two documented nowhere)
- Category: STALE-DOC  | Confidence: HIGH | Action: REWRITE-DOC | Risk: low | Effort: S | Size: ~10 lines
- Location: src/home_finder/config.py:159-165 (`property_analysis_timeout`), 262-265 (`carto_basemap_api_key`), 246-249, 153, 182, 201, 123, 139-152; README.md:169-218; .env.example
- Evidence:
  - I checked every field programmatically: `git grep HOME_FINDER_<FIELD>` in README, .env.example, docs, fly.toml, Dockerfile and .github.
  - `CARTO_BASEMAP_API_KEY` (a browser tile key whose description says "restrict allowed origins at the provider") and `PROPERTY_ANALYSIS_TIMEOUT` appear in no doc at all.
  - `TELEGRAM_WEBHOOK_SECRET`, `QUALITY_CONCURRENCY`, `EVENT_RETENTION_RUNS`, `BRIEF_MUST_HAVES`, `NOTIFICATION_POLICY`, `MAX_INITIAL_ANALYSES_PER_RUN` and `MAX_REANALYSES_PER_RUN` are in .env.example but not in README's "Configuration" reference.
  - README.md:120 calls `PIPELINE_INTERVAL_MINUTES` "Minutes between pipeline runs". The code (config.py:270) means the minimum delay after completion plus 0-5 minutes of jitter.
  - Conversely, no doc mentions an env var that is not a Settings field. The only hit is `HOME_FINDER_ENQUIRY_*` in a ticket plan.
  - Minor: `telegram_webhook_secret` is a plain `str` while every other secret is `SecretStr`, so it appears in `repr(settings)`.
- Recommendation: Make README point to .env.example as the single env reference, or complete it, and add the two missing keys. Consider `SecretStr` for the webhook secret (touches web/notifier readers).
- Prior item: none   | Owner decision needed: no

### PIPELINE-9: `--debug` is silently ignored under `--serve`
- Category: CONFIG  | Confidence: CERTAIN | Action: SIMPLIFY | Risk: low | Effort: S | Size: ~2 lines
- Location: src/home_finder/main.py:158; src/home_finder/web/app.py:149; src/home_finder/logging.py:36-42
- Evidence:
  - `main()` configures DEBUG, then `create_app()` calls `configure_logging(json_output=False)` again at the default INFO.
  - I verified in-process: after the second call, a new logger's `.debug()` is dropped and `.info()` still prints. `cache_logger_on_first_use=True` means only loggers already used before the reconfigure keep DEBUG.
- Recommendation: Configure logging once at the entry point. Either drop the call from `create_app`, or give it a `level` argument / skip when already configured. Cross-area with web.
- Prior item: none   | Owner decision needed: no

### PIPELINE-10: The startup error hint blames Telegram variables, which cannot fail
- Category: STALE-DOC  | Confidence: CERTAIN | Action: REWRITE-DOC | Risk: low | Effort: S | Size: 1 line
- Location: src/home_finder/main.py:160-168; src/home_finder/config.py:60-67
- Evidence:
  - On `Settings()` failure the hint says "Ensure .env has HOME_FINDER_TELEGRAM_BOT_TOKEN and HOME_FINDER_TELEGRAM_CHAT_ID".
  - Both fields have defaults (`SecretStr("")`, `0`), so their absence never raises.
  - Real failures come from validators: unknown areas (`_validate_csv_fields`), `brief_must_haves`, `analysis_config` null or bad JSON, spend-policy JSON, range bounds.
- Recommendation: Use a generic hint ("check HOME_FINDER_* values; see .env.example").
- Prior item: none   | Owner decision needed: no

### PIPELINE-11: `run_dry_run` is a deprecated wrapper only one test calls
- Category: TEST-ONLY  | Confidence: HIGH | Action: DELETE | Risk: low | Effort: S | Size: ~6 src lines
- Location: src/home_finder/pipeline/flow.py:503-508; tests/integration/test_cross_run_reanalysis_e2e.py:30,1298
- Evidence:
  - Its docstring says "Deprecated: use run_pipeline(settings, dry_run=True)" and it carries a `type: ignore`.
  - `git grep run_dry_run` (whole tree and the PR #77 branch) finds only this test and Feb-2026 plans describing the old main.py. vulture flags it as used only by tests or scripts.
- Recommendation: Change the test to `run_pipeline(settings, dry_run=True, storage=shared_storage)`, then delete.
- Prior item: A15   | Owner decision needed: no

### PIPELINE-12: `EventRecorder.record_batch` and `.run_id` are test-only
- Category: TEST-ONLY  | Confidence: HIGH | Action: DELETE | Risk: low | Effort: S | Size: ~8 src lines + 2 tests
- Location: src/home_finder/pipeline/event_recorder.py:52-54,60-62; tests/test_pipeline/test_event_recorder.py:95-102,153
- Evidence: `git grep "record_batch\|recorder.run_id" -- src tests scripts` finds only the definitions and test_event_recorder.py. Every production site uses `record()` (candidates, matching, assessment, flow). vulture lists `record_batch` as used only by tests.
- Recommendation: Delete both, along with their two test assertions.
- Prior item: A15   | Owner decision needed: no

### PIPELINE-13: Observation retention and price-change detection run up to three times per listing
- Category: SUPERSEDED  | Confidence: HIGH | Action: SIMPLIFY | Risk: medium (price history and source rows) | Effort: S | Size: ~35 lines
- Location: src/home_finder/pipeline/collection.py:52-59,78-83; src/home_finder/pipeline/candidates.py:80-82,110-111,647-670
- Evidence:
  - `retain_observations` (added in 3570e26) upserts source rows and calls `detect_and_record_price_change` for **every** observation, page by page (collection.py:52-59, called from `consume` at :261-262).
  - `collect_listings` then re-runs it on the selected listings "Also supports simple adapters/fakes" (:80-83).
  - `prepare_candidates` upserts the same listings again (candidates.py:80-82, the "source_listings_upserted" upsert that predates per-page retention, 936b087).
  - `_detect_price_changes` (candidates.py:111, 647-670) calls `detect_and_record_price_change` again with identical arguments (`unique_id`, `price_pcm`, `source`). The function is idempotent: storage.py:1597 returns None when `old_price == new_price`. Its `price_changes_detected` log therefore can never fire in production. No test references `_detect_price_changes`.
  - Caveat: three tests patch `home_finder.pipeline.candidates.collect_listings` directly (test_enrichment_admission.py:93,112; test_processing_recovery.py:106) and may depend on the candidates.py:81 upsert.
- Recommendation: Delete `_detect_price_changes` and its call. Then remove the candidates.py:80-82 upsert and the "fakes" re-retain in `collect_listings` after running those three test files. Keep the consolidation upsert (matching.py:49), which covers recovering candidates.
- Prior item: none   | Owner decision needed: no

### PIPELINE-14: The per-result notify callback carries an ignored payload and repeats the delivery layer's no-image check (B6)
- Category: SUPERSEDED  | Confidence: MEDIUM | Action: SIMPLIFY | Risk: medium (immediate notifications) | Effort: M | Size: ~40 lines
- Location: src/home_finder/pipeline/flow.py:331-394; src/home_finder/pipeline/assessment.py:46-50,274; src/home_finder/notifiers/telegram.py:925-947; src/home_finder/notifiers/delivery.py:179-197
- Evidence:
  - `_notify` passes `commute_minutes`, `transport_mode` and `quality_analysis` to `send_merged_property_notification`. With a `delivery_repository`, which the pipeline always supplies (flow.py:121), that method ignores them and calls `deliver(…, f"discovery:{uid}")` (telegram.py:946-947).
  - `_notify` also pre-skips with its own no-image rule (`not merged.images and not merged.canonical.image_url`). `deliver()` already refreshes images and defers with reason `no_images` using `_get_gallery_urls` (delivery.py:183-197), and the final drain then retries the key anyway.
  - The two rules can disagree. A tested example: tests/test_pipeline/test_recovery.py:412-418 pins the callback skip.
- Recommendation: Per B6, simplify the callback to "deliver this key now" and leave image readiness to `deliver()`. Keep the dry-run accumulator's payload for the summary log. Retain timely per-completion delivery.
- Prior item: B6   | Owner decision needed: no

### PIPELINE-15: `--dedup-existing` is still a mutation command; README calls it a retroactive merge (B4)
- Category: STALE-DOC  | Confidence: MEDIUM | Action: ASK-OWNER | Risk: medium (identity merges) | Effort: S-M | Size: ~90 lines if retired
- Location: src/home_finder/pipeline/commands.py:1,253-338; src/home_finder/main.py:57-61,185-186; README.md:74; tests/test_pipeline/test_dedup_maintenance.py
- Evidence:
  - The help text and docstring now say it only retires pending staging rows and preserves established homes. README.md:74 still says "Retroactively merge duplicates already in the DB", and the module docstring says "retroactive dedup".
  - Its only mutation is `absorb_staging_properties`. Normal runs do the same through `consolidate_candidates` (matching.py:128-133), but only for pending rows admitted under `max_enrichment_per_run` and anchors from the last 30 days. The command uses all-time anchors (`days=None`).
  - A read-only duplicate report already exists (scripts/audit_duplicate_candidates.py; operations.md:73-76).
  - It is also the only consumer of `enable_image_hash_matching` (PIPELINE-2).
- Recommendation: Owner decides between (a) retiring it, since normal runs plus the audit script cover the safe part, and (b) keeping it renamed and documented as "retire duplicate staging rows". Fix README.md:74 either way.
- Prior item: B4   | Owner decision needed: yes

### PIPELINE-16: `--generate-thumbnails` deletes files without saying so, and the image-repair path makes no thumbnails (B5)
- Category: CONFIG  | Confidence: MEDIUM | Action: ASK-OWNER | Risk: low-medium (cached evidence deletion) | Effort: S | Size: ~20 lines
- Location: src/home_finder/main.py:62-66,187-197; src/home_finder/utils/image_cache.py:269-310; src/home_finder/pipeline/image_recovery.py:89-127; src/home_finder/enrichment/details.py:283-292
- Evidence:
  - The help text is "Generate missing thumbnails". `backfill_thumbnails` also unlinks any `gallery_*` file whose header fails `is_valid_image_bytes` (image_cache.py:296-305).
  - Ordinary enrichment generates thumbnails (details.py:289-292).
  - The repair download `download_missing_images` saves originals only, with no `generate_thumbnail` call, so repaired homes still depend on the CLI backfill.
  - This is the only maintenance mode run with a hand-rolled `runner_lock` (main.py:188) rather than `@exclusive_runner`.
- Recommendation: Per B5, make `download_missing_images` thumbnail what it saves. Then either remove the command or rename it and document it as "repair thumbnails and delete corrupt cache files". Never delete without saying so in the help text.
- Prior item: B5   | Owner decision needed: yes

### PIPELINE-17: The legacy `max_analysis_per_run` fallback
- Category: COMPAT  | Confidence: MEDIUM | Action: ASK-OWNER | Risk: low | Effort: S | Size: ~15 lines (config + .env.example + 2 docs)
- Location: src/home_finder/config.py:132-152; .env.example:35-36; docs/operations.md:551-556; docs/pipeline.md "Queue limits"
- Evidence:
  - vulture flags it (60%) but it is live: it is read by the `default_factory` lambdas of `max_initial_analyses_per_run` and `max_reanalyses_per_run` (config.py:140,147).
  - Only one test touches it. The fallback was introduced one day ago in 41f499e as a compatibility path.
  - fly.toml sets no HOME_FINDER_MAX_* variable. Fly secrets and Marcel's local .env are not visible from here.
- Recommendation: Ask whether any deployed or local .env sets `HOME_FINDER_MAX_ANALYSIS_PER_RUN`. If none does, drop the field, default both limits to 75, and delete the doc rows.
- Prior item: none   | Owner decision needed: yes

### PIPELINE-18: `create_quality_analyzer` passes an `enable_extended_thinking` argument that is ignored
- Category: DEAD  | Confidence: HIGH | Action: DELETE | Risk: low | Effort: S | Size: 1 line (+ analyzer fallback, cross-area)
- Location: src/home_finder/pipeline/assessment.py:315-326; src/home_finder/analysis/analyzer.py:778-806; src/home_finder/config.py:115-122
- Evidence:
  - The analyzer uses `enable_extended_thinking` only when `analysis_config is None` (analyzer.py:803-806, `analysis_config or AnalysisConfig(...)`).
  - `Settings.analysis_config` is never None: its `default_factory` always builds it, and `_parse_analysis_config` rejects null.
  - The thinking setting already reaches the config through `new_runtime_analysis_config(enable_extended_thinking=…)`.
- Recommendation: Drop the argument from `create_quality_analyzer`. The analysis agent can decide whether the analyzer's `analysis_config=None` fallback is test-only.
- Prior item: B2-adjacent   | Owner decision needed: no

### PIPELINE-19: Root `main.py` is an unused "backwards compatibility" wrapper
- Category: COMPAT  | Confidence: HIGH | Action: DELETE | Risk: low | Effort: S | Size: 6 lines
- Location: /home/user/home-finder/main.py:1-6
- Evidence:
  - The Dockerfile uses `CMD ["home-finder"…]` (lines 40, 44) and pyproject defines `home-finder = "home_finder.main:main"`. fly.toml builds the Dockerfile.
  - `git grep -nE "python main\.py|\bmain\.py\b"` finds only references to src/home_finder/main.py (CLAUDE.md:73, README.md:315 tree) and a stale test comment.
  - Its history is a squash at 63f4271; it is not on the PR #77 branch.
- Recommendation: Delete it with the A13 root-leftover housekeeping. It only matters if Marcel personally types `python main.py`.
- Prior item: A13   | Owner decision needed: no (trivial confirm)

### PIPELINE-20: `scripts/backfill_wards.py` duplicates the per-run `_lookup_wards`, and its default DB path is wrong; `_lookup_wards` sits in the wrong module and is never tested
- Category: SUPERSEDED  | Confidence: HIGH | Action: DELETE (script) / MOVE (function) | Risk: low | Effort: S | Size: ~90 lines (script)
- Location: scripts/backfill_wards.py:1-91; src/home_finder/pipeline/assessment.py:159,570-613
- Evidence:
  - The script ("One-time backfill") repeats the same split (coordinates → bulk reverse lookup, full postcode → forward lookup) and the same `update_wards` call as `_lookup_wards`.
  - `_lookup_wards` already runs in every pipeline run whose initial-assessment queue is non-empty (assessment.py:149-159).
  - The script's default `--db-path data/home_finder.db` does not match `Settings.database_path` (`data/properties.db`). Its `storage.initialize()` would create a new empty database at that path.
  - Coverage (scratchpad/evidence/uncovered_functions.txt): `_lookup_wards` has 0% (26 statements). Every pipeline test patches it out (`git grep _lookup_wards -- tests`, 9 sites).
  - It does postcodes.io network I/O inside the "initial_assessment" phase, although ward lookup is enrichment, not assessment.
- Recommendation: Delete the script. Move `_lookup_wards` beside enrichment, either at the end of candidate preparation or in `enrichment/`, and give it one network-free test using `pytest-httpx`.
- Prior item: A10 (scripts)   | Owner decision needed: no

### PIPELINE-21: Duplicated orchestration wiring
- Category: DUP  | Confidence: HIGH | Action: MERGE | Risk: low | Effort: S | Size: ~30 lines
- Location: src/home_finder/pipeline/collection.py:61-77 vs 114-127; src/home_finder/main.py:221-243; src/home_finder/pipeline/assessment.py:114-116 vs src/home_finder/pipeline/reanalysis.py:71; src/home_finder/pipeline/flow.py:60-80
- Evidence:
  - `run_scrape_only` and `collect_listings` build the same 11 `scrape_all_platforms` kwargs from Settings. `run_scrape_only` has 0% coverage: test_cli mocks it out.
  - main.py has two `run_pipeline(...)` branches that differ only in `dry_run=True`. `dry_run=args.dry_run` would do.
  - The "analysis enabled" predicate (`enable_quality_filter and anthropic_api_key`) is written twice.
  - `run_pipeline` has two near-identical branches for provided vs owned storage.
- Recommendation: Add a small `_scrape_kwargs(settings)` helper or let `run_scrape_only` call `scrape_all_platforms` through it. Collapse the main.py branches. Add an `analysis_enabled(settings)` helper in assessment.py.
- Prior item: none   | Owner decision needed: no

### PIPELINE-22: Four operator reports hand-roll the same read-only snapshot opener
- Category: DUP  | Confidence: LOW | Action: SIMPLIFY | Risk: low | Effort: S | Size: ~25 lines
- Location: src/home_finder/pipeline/spend_report.py:15-25; src/home_finder/pipeline/delivery_admin.py:17-26; src/home_finder/pipeline/notification_report.py:23-28; src/home_finder/pipeline/rating_shadow_report.py:77-82
- Evidence: Each does resolve(strict) → `?mode=ro` URI → `row_factory` → `PRAGMA query_only=ON` → `BEGIN` → rollback (2× aiosqlite, 2× sqlite3).
- Recommendation: Optional: one `read_only_snapshot()` context manager per flavour in db/. These are deliberate self-contained tools, so this is low priority.
- Prior item: none   | Owner decision needed: no

### PIPELINE-23: `configure_logging(json_output=True)` is never used
- Category: DEAD  | Confidence: MEDIUM | Action: ASK-OWNER | Risk: low | Effort: S | Size: ~8 lines
- Location: src/home_finder/logging.py:9-34
- Evidence:
  - Both callers pass `json_output=False` (main.py:158, web/app.py:149), and no setting or flag selects JSON.
  - The "Production: JSON output" branch is therefore dead, and the Fly dashboard logs through `ConsoleRenderer(colors=True)` (ANSI codes in Fly logs).
- Recommendation: Either delete the parameter and branch, or wire it to an env setting for the hosted dashboard. Owner preference.
- Prior item: none   | Owner decision needed: yes

### PIPELINE-24: `Settings.data_dir` is "." for in-memory or bare DB paths, so the `if not data_dir` guards never fire
- Category: CONFIG  | Confidence: MEDIUM | Action: SIMPLIFY | Risk: low | Effort: S | Size: ~10 lines
- Location: src/home_finder/config.py:325-328; src/home_finder/pipeline/cache_recovery.py:16; src/home_finder/pipeline/image_recovery.py:34; src/home_finder/pipeline/commands.py:309; src/home_finder/pipeline/candidates.py:344; src/home_finder/pipeline/matching.py:186
- Evidence:
  - Checked in-process: `Settings(database_path=':memory:').data_dir == '.'` and `Settings(database_path='x.db').data_dir == '.'`.
  - The `if not data_dir` / `if settings.data_dir` guards are therefore always true for Settings-derived values; only direct `data_dir=""` callers (tests, defaults) hit them.
  - A side effect: in-memory runs place the image cache in the CWD. An empty untracked `/home/user/home-finder/image_cache/` directory appeared at 09:01:49 today during the concurrent coverage run. My `--help` runs in a scratch CWD created nothing.
- Recommendation: Make the intent explicit, either with an optional data_dir setting or a None/temporary dir for `:memory:`. Then delete the dead guards. The test-hygiene angle goes to the tests agent.
- Prior item: none   | Owner decision needed: no

### PIPELINE-25: The off-market check keeps a fallback for "legacy golden records without source_listings"
- Category: COMPAT  | Confidence: LOW | Action: ASK-OWNER | Risk: medium (availability state) | Effort: S | Size: ~25 lines
- Location: src/home_finder/pipeline/commands.py:80-81,99-137; src/home_finder/db/storage.py:1884-1894
- Evidence:
  - The migration backfills a canonical source_listings row for every property (db/migrations.py:494-509, Phase 1, `merged_id = unique_id`). The final `elif not linked_sls` branch is therefore probably unreachable.
  - The partial-linkage JSON path is still plausible, because Phase 3 skips sources without a URL.
  - The docstring narrates "(the previous behaviour)".
- Recommendation: Per B1, remove it only after a fixture-backed read audit of a real snapshot. No DB is available here.
- Prior item: B1-adjacent   | Owner decision needed: yes

### PIPELINE-26: Small pass-throughs and unused return values in the assessment helpers
- Category: OVERBUILT  | Confidence: LOW | Action: INLINE | Risk: low | Effort: S | Size: ~25 lines
- Location: src/home_finder/pipeline/assessment.py:169,289-300,329-341,358-359,466,552-567; src/home_finder/pipeline/image_recovery.py:31-32,86; src/home_finder/pipeline/reanalysis.py:83
- Evidence:
  - `complete_initial_assessment` is a one-call wrapper around `storage.processing.complete_analysis`. Its other callers are tests only (test_orchestration.py, test_candidate_preparation.py).
  - `run_concurrent_analysis` returns a count that both callers discard (`_process_admitted_batch` returns it into the void). Its defaults (`concurrency=15`, the log-event names) are always overridden.
  - `restore_missing_analysis_images` "Returns the queue unchanged", yet reanalysis reassigns `queue =` from it.
- Recommendation: Inline the wrapper, migrating the tests to `storage.processing.complete_analysis`. Return `None` where the result is unused.
- Prior item: A15   | Owner decision needed: no

### PIPELINE-27: Maintenance scripts with default or hidden mutations (A10/B9): cross-area, possibly unowned
- Category: SUPERSEDED  | Confidence: HIGH | Action: DELETE / ASK-OWNER | Risk: high if run (deletes homes and assessments) | Effort: S | Size: ~175 + ~460 lines
- Location: scripts/audit_merges.py:2,49-160; scripts/audit_image_cache.py:312-390,428-457
- Evidence:
  - `audit_merges.py` ("Detect and delete falsely merged…") mutates **by default**: `--dry-run` is opt-in. It runs raw `DELETE FROM property_images/quality_analyses/properties` and `clear_image_cache`, bypassing `merge_safety`. The review's line 146 already flagged it: "its default is mutation … bypasses the current application's merge preservation contract".
  - `audit_image_cache.py --reflag-partial` writes `reanalysis_requested_at` directly, a hidden paid-reanalysis trigger that bypasses `request_reanalysis_by_filter`. `--fix` and `--purge-disk` delete DB rows and files. It has tests in tests/test_scripts/test_audit_image_cache.py.
- Recommendation: Delete `audit_merges.py` (or move it to an archive). For `audit_image_cache.py`, keep a read-only inventory and drop `--fix/--purge-disk/--reflag-partial`, as A10 says: "do not keep hidden repair/reanalysis flags by accident". Confirm with the owner whether he uses these.
- Prior item: A10, B9   | Owner decision needed: yes (whether he uses them)

---

## Verified keep (looks dead, isn't)
- `Settings._known_must_haves`, `_parse_analysis_config` (`@field_validator`) and `_validate_csv_fields` (`@model_validator`): pydantic validators that run on construction. vulture false positive.
- `Settings.model_config`: the pydantic-settings configuration.
- `Settings.max_analysis_per_run`: read through the `default_factory` lambdas at config.py:140,147 (see PIPELINE-17 for whether to keep it).
- `Settings.furnish_types` and `search_areas`: read through `get_furnish_types()` and `get_search_areas()`.
- `ScraperMetrics.areas_completed` and `duration_seconds`: persisted through `asdict(m)` → `RunRepository.save_scraper_runs` (db/runs.py:139-151) into `scraper_runs` columns.
- `row_factory` assignments in the four report modules: sqlite API attributes.
- `EventRecorder.__aexit__(exc_type, traceback)`: async-context-manager protocol signature.
- The `main()` functions of backup, delivery_admin, notification_report, recovery_admin, spend_admin, spend_report and rating_shadow_report: coverage shows 0% for the first six `main`s, but all are documented operator commands (operations.md:50-56, 188-189, 277, 314, 422, 486). `--help` succeeds for all seven via `python -m` (smoke-tested). backup and spend_report are exercised by subprocess tests (tests/test_db/test_backup.py:298, tests/test_db/test_spend.py:387), which coverage cannot see.
- `--no-pipeline`: a no-op by default, but used by Dockerfile:44 CMD, operations.md:17 and README.
- `--backfill-commute`, `--check-off-market` and `--reanalyze` (plus modifiers): live, documented, and with no equivalent in normal runs. Normal runs measure commutes only for candidates.
- `RunSkipped`, `CommuteInfo`, `AssessmentCallback` and `_days_since`: all referenced.
- `print` in operator modules (ruff T201): intentional CLI JSON output.
- PLC0415 lazy imports in main.py:212-214 (uvicorn, web app): they avoid loading the web stack for CLI modes.
- Legacy `pipeline_runs` column translation in `CandidateProgress.run_columns()`: a deliberate persistence-boundary mapping, documented in docs/pipeline.md.

## Notes (cross-area leads)
- **Scrapers/db (PIPELINE-1):** the `known_source_ids` parameter in all four scrapers, `BaseScraper._paginate`'s early-stop branch and its scraper tests, and `storage.get_all_known_source_ids` all go with A6.
- **Notifiers (A7):** `TelegramNotifier.send_merged_property_notification`'s non-repository "standalone, non-recoverable send helper" branch (telegram.py:948-972) and `send_batch_notifications` (:988) look like the old senders. The pipeline always supplies a repository. operations.md:519-521 says "The old sender remains available until … live qualification".
- **DB (A14):** `ProcessingRepository.mark_enriched` (db/processing_repo.py:336) is referenced only by tests/test_db/test_storage_enrichment.py.
- **Hygiene (A13):** root `crontab` is comments only, with no references (`git grep crontab` finds nothing outside plans and reviews). Delete it with root `main.py`. The other root files (`dedupe.json`, `output.txt`, `rental_market_research.md`, `mutmut_config.py`) are for the hygiene agent.
- **Tests:** an empty untracked `image_cache/` appeared in the repo root during the coverage run, consistent with `data_dir == "."` for `:memory:` settings (PIPELINE-24). Some test probably writes cache files into the CWD.
- **Models:** `SourceListing`/`PropertyAggregate` are aliases for `Property`/`MergedProperty`, introduced in 41f499e. pipeline/ uses the new names, while commands.py, db and web use the old ones, so the same models carry two vocabularies. Leave this unless the owner wants one name.
- **Logging:** two parallel phase-logging schemes coexist: `_pipeline_phase` (debug, with outcome and timing) and `logger.info("pipeline_started", phase=…)` for sub-steps in candidates and collection. This is low value; mention it if someone touches the logging.
- **Behaviour asymmetry (not cleanup):** initial assessment records a failure on wall-clock timeout (`on_timeout=_record_timeout`), but reanalysis passes no `on_timeout`, so timed-out reassessments never accumulate failures. Worth an owner look.
