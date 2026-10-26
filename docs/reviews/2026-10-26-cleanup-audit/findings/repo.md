# REPO area findings (non-application files, docs accuracy, branches)

Audited at `51dd9c6` (== origin/main). Local clone is **shallow** (history starts at `63f4271`, 2026-02-23), so
"added in" dates for older files are the shallow root, not true creation dates. Remote branches fetched with
`git fetch origin '+refs/heads/*:refs/remotes/origin/*'`. GitHub data read via `gh api` (REST, read-only).

Prior-item status summary for this area:
A1 Crawlee: OUTSTANDING (REPO-12) · A2 unused test deps: OUTSTANDING (REPO-13) · A10/B9 scripts: OUTSTANDING (REPO-7/8/9) ·
A11 floorplan heuristic: OUTSTANDING for the ADR/doc side (REPO-16) · A13 root leftovers: OUTSTANDING (REPO-10/11) ·
A20 live CI job: PARTIAL (REPO-15) · B8 mutation testing: retained as optional, doc-only config file remains (REPO-10) ·
"short authoritative architecture + archive obsolete prose + fix CLAUDE.md": PARTIAL — `docs/pipeline.md` (2026-10-25) and the README
Architecture section now exist, but CLAUDE.md still carries a contradictory older copy and the Feb-2026 prose is unarchived (REPO-1..3, REPO-17).

---

### REPO-1: CLAUDE.md commands, pipeline flow and architecture pointers contradict current code
- Category: STALE-DOC  | Confidence: CERTAIN | Action: REWRITE-DOC | Risk: low (doc only; currently misleads agents) | Effort: M | Size: ~70 lines
- Location: CLAUDE.md:39, 73-86, 98, 117, 152, 157, 159, 161, 163, 206
- Evidence (each claim vs code):
  - L39 `--serve  # Web dashboard + recurring pipeline scheduler` — `src/home_finder/main.py:226` `create_app(settings, run_pipeline=args.with_pipeline)`; help text L98 "collection requires --with-pipeline". README L67-69 is correct.
  - L73 "Pipeline Flow (main.py)" — main.py only parses/dispatches (docs/pipeline.md:11). Flow lives in `pipeline/flow.py` + `pipeline/candidates.py`.
  - L81-82 order "7 Commute Filter → 8 Detail Enrichment" — `pipeline/candidates.py:153-160` enriches first ("Assess commute after detail acquisition"); commute is advisory, not a filter (README L25, L272-273).
  - L82 "detect floorplans in gallery via PIL heuristics" / L126 "after EPC/floorplan detection" — heuristic removed from enrichment in `a021747` "fix: preserve floorplan origin and stop heuristic promotion"; `git grep detect_floorplan -- src` → only its own module; plan R3 (docs/plans/2026-10-10-focused-reliability-repairs.md:74) "R3 removes the enrichment heuristic entirely".
  - L84 / L206 "Floorplan Gate — Drop properties without floorplans" — config.py:101-110 "Require a source-declared floorplan, an exempt source, or enough photos" + `min_gallery_for_photo_inference=5`.
  - L98 "Rightmove scraper | crawlee.BeautifulSoupCrawler" — `scrapers/rightmove.py:8` `import httpx`; `git grep BeautifulSoupCrawler -- src tests` → 0; docs/sources/rightmove.md:7,9 ("Crawlee 1.7's queue scheduling ... unsuitable").
  - L152 proxy "Passed to OnTheMarketScraper and DetailFetcher" — also OpenRent/Zoopla (`pipeline/collection.py:189-192`) and OffMarketChecker (`pipeline/commands.py:155`).
  - L157 "Filters → CommuteFilter, Deduplicator, PropertyQualityFilter (quality.py + quality_prompts.py)" — now `enrichment/commute.py:20 CommuteAssessor`, `matching/deduplication.py Deduplicator`, `analysis/analyzer.py:776 PropertyQualityAnalyzer`, `analysis/prompts.py` (renames in `41f499e` "refactor: make pipeline stages and contracts explicit"). `git grep "class CommuteFilter\|class PropertyQualityFilter" -- src` → 0.
  - L159 "Models → models.py" — `src/home_finder/models/` package (core, analysis, delivery, ...).
  - L161 "properties + property_images tables" — 16 tables in db/migrations.py (15 migrations, `MIGRATIONS` at migrations.py:910).
  - L163 image_cache "EPC/floorplan file renaming" — no rename in utils/image_cache.py; EPC rename is `enrichment/details.py:111-113`; no floorplan renaming exists.
  - L117 "post-enrichment deduplicate_merged_async() merges" — consolidation against stored anchors is `pipeline/matching.py` (README L307-309).
  - L1-11 "Current delivery scope" sits above the `# CLAUDE.md` title (prepended block); "the review disposition record" is unlinked (docs/reviews/2026-10-10-pr-disposition.md).
- Recommendation: Replace "Pipeline Flow" and most "Architecture Pointers" with links to `docs/pipeline.md` (Reading order / Execution sequence) and README "Project Structure"; keep only agent-relevant gotchas (HTTP client table fixed, run-mode exclusivity, `--with-pipeline`). Point "Commands" at README "Run Modes" instead of duplicating. Fix after REPO-4 so the Grep tool can see the file.
- Prior item: "Fix CLAUDE.md claims that contradict current scrapers or floorplan handling" (assessment L395) — OUTSTANDING | Owner decision needed: no

### REPO-2: CLAUDE.md "Quality Analysis" section describes the pre-Sonnet-5.5 / pre-contract implementation
- Category: STALE-DOC  | Confidence: CERTAIN | Action: REWRITE-DOC | Risk: low | Effort: S | Size: ~15 lines
- Location: CLAUDE.md:136-146
- Evidence:
  - L136 "Uses claude-sonnet-4-6" — `config.py:31,39` `new_runtime_analysis_config` uses `model="claude-sonnet-5-5"` for both phases (.env.example "new runs use Sonnet 5.5").
  - L138 Phase 1 `max_tokens: 21000` — default `max_output_tokens=8192` (config.py:35); L139 Phase 2 "forced tool choice, strict: true, max_tokens: 4096" — Phase 2 now uses `output_config.format = json_schema` (`analysis/analyzer.py:1562-1578`), 8192 tokens (config.py:43); `_build_tool_schema(..., strict=True)` is never called with strict (only caller analyzer.py:707).
  - L140 "`_build_tool_schema()` in `quality.py`; prompts in `quality_prompts.py`" — files renamed to `analysis/analyzer.py` / `analysis/prompts.py` in `41f499e`.
  - L143 "Post-processing strips {"..."} artifacts" — removed (A8); analyzer.py:1655-1658 "Live callers validate both response contracts first ... historical repairs belong to the storage model's tolerant reader".
  - L146 "~$0.05-0.07/property" — unverified for Sonnet 5.5; the only current figures are per-attempt reservations ($0.98392 visual / $0.64192 evaluation at 20 images, .env.example). Spend policy (absent ⇒ every paid attempt deferred, config.py:128-131) is not mentioned at all.
- Recommendation: Rewrite as 5-6 lines: two phases, runtime profile in `config.py:new_runtime_analysis_config`, contracts in `analysis/contracts.py`, spend policy required (link docs/operations.md#runtime-model-defaults). Drop numeric token/cost claims (they belong in source).
- Prior item: A8 (done in code; doc not updated), B2 | Owner decision needed: no

### REPO-3: CLAUDE.md "Testing Patterns" contradicts the test suite
- Category: STALE-DOC  | Confidence: CERTAIN | Action: REWRITE-DOC | Risk: low | Effort: S | Size: ~15 lines
- Location: CLAUDE.md:187-197
- Evidence: L187 "Integration tests ... reset Crawlee global state with autouse fixtures" — tests/conftest.py:430,482 "Not autouse"; integration files use explicit `pytestmark = pytest.mark.usefixtures("reset_crawlee_state", ...)`; Crawlee is not used by src at all (REPO-12). L188 "All tests use in-memory SQLite" — `git grep -l -E 'tmp_path ?/ ?"[^"]*\.(db|sqlite)"' -- tests` → 41 files. L192 "tests/test_filters/ — criteria, commute, dedup, quality, location, floorplan" — dedup/quality/commute tests now live in tests/test_matching, tests/test_analysis, tests/test_enrichment (ls tests/test_filters: availability, criteria, fit_score, location, off_market, shadow_rating). `tests/README.md` (88 lines) already documents suite boundaries.
- Recommendation: Replace the structure list with a link to tests/README.md; keep the PYTHONUNBUFFERED/-n0/timeout guidance.
- Prior item: none | Owner decision needed: no

### REPO-4: CLAUDE.md and .python-version are tracked but gitignored, so ripgrep/Grep silently skip them
- Category: HYGIENE  | Confidence: CERTAIN | Action: SIMPLIFY (delete 2 .gitignore lines) | Risk: low | Effort: S | Size: 3 lines
- Location: .gitignore:83 (`.python-version`), .gitignore:161-162 (`# Claude` / `CLAUDE.md`)
- Evidence: `git ls-files -ci --exclude-standard` → `.python-version`, `CLAUDE.md`. `git check-ignore -v --no-index CLAUDE.md` → `.gitignore:162:CLAUDE.md`. Consequence verified: `rg -l "Current delivery scope" .` → no match (exit 1) and the Claude Code Grep tool returns "No files found" for the same string, although CLAUDE.md:1 contains it. Git itself still tracks changes (ignore rules don't apply to tracked files), but every rg-based search — including agents auditing doc accuracy — misses CLAUDE.md and .python-version. Both were deliberately committed (`.python-version` in `3570e26`; CLAUDE.md modified as recently as `45d9a46`).
- Recommendation: Remove both ignore entries (if a private file is wanted, ignore `CLAUDE.local.md` instead).
- Prior item: none | Owner decision needed: no

### REPO-5: docs/operations.md says plain `--serve` owns the scheduled pipeline
- Category: STALE-DOC  | Confidence: CERTAIN | Action: REWRITE-DOC | Risk: medium (an operator following the "supported active layout" gets a dashboard with no collection) | Effort: S | Size: ~3 lines
- Location: docs/operations.md:436-446
- Evidence: L441 `HOME_FINDER_WEB_HOST=127.0.0.1 uv run home-finder --serve` then L445 "The serve process owns both the dashboard and its scheduled pipeline". main.py:226 `create_app(settings, run_pipeline=args.with_pipeline)`; main.py:98 help "collection requires --with-pipeline"; README L97 "Add `--with-pipeline` to explicitly enable the background scheduler".
- Recommendation: Change the command to `--serve --with-pipeline`.
- Prior item: none (CLI flag repair, assessment item 6, is done in code) | Owner decision needed: no

### REPO-6: README / .env.example / CONTRIBUTING / docs/ci.md factual contradictions
- Category: STALE-DOC  | Confidence: CERTAIN | Action: REWRITE-DOC | Risk: low | Effort: S | Size: ~25 lines
- Location: README.md:74, 130, 151-165, 203; .env.example:55; CONTRIBUTING.md:24-27, 72, 111-114, 127-128; docs/ci.md:8-10
- Evidence:
  - README L74 `--dedup-existing  # Retroactively merge duplicates already in the DB` vs main.py:61 help "Retire duplicate pending staging rows while preserving established homes" and pipeline/commands.py:255-258 docstring.
  - README L203 and .env.example:55 search-area default `e2,e3,e5,e9,e10,e15,e17,n15,n16,n17` vs config.py:227 `e2,e3,e5,e8,e9,e10,e15,e17,n1,n5,n15,n16,n17` (E8, N1, N5 missing).
  - README L130 "Schema migration 7 adds ... The core also adds migrations 8–9" — PR-era narration; there are 15 migrations (migrations.py:910-926).
  - README Endpoints table (L151-165) omits `GET /deliveries` and `POST /telegram/webhook` (git grep of route decorators in src/home_finder/web).
  - `HOME_FINDER_CARTO_BASEMAP_API_KEY` (config.py:262, used web/routes.py:85) is documented nowhere (`git grep -i carto -- docs README.md .env.example` → 0).
  - CONTRIBUTING L72/L128 `tests/test_scrapers.py` — does not exist (directory tests/test_scrapers/). L127 "Add the scraper to the registry in scrapers/__init__.py" — that file is only a lazy re-export (A3); the scraper list is `pipeline/collection.py:189-192`. L111 "Fill out the PR template" — no template (`ls .github` → dependabot.yml, workflows). L3/L139 generic open-source boilerplate for a personal project.
  - docs/ci.md:8-10 "`checks.yml` replaces the inactivity-disabled `ci.yml` ... The existing disabled workflow can remain disabled" — `gh api repos/MarcelCutts/home-finder/actions/workflows` lists only CI (checks.yml), Release dashboard, Dependabot; no ci.yml in tree or on GitHub.
- Recommendation: Fix each line; reduce CONTRIBUTING to setup + checks (or merge into README "Running Tests") since README/CLAUDE already duplicate it; delete the ci.yml paragraph.
- Prior item: none | Owner decision needed: no

### REPO-7: scripts/audit_merges.py — mutation-by-default raw-SQL deleter that breaks the merge-preservation contract
- Category: SUPERSEDED  | Confidence: HIGH | Action: DELETE | Risk: low to delete / high to keep (data loss if run) | Effort: S | Size: 175 lines
- Location: scripts/audit_merges.py:8-9, 49-159
- Evidence: Default (no `--dry-run`) runs `DELETE FROM property_images/quality_analyses/properties` (L143-151) on a plain `sqlite3.connect` (L55) with no `PRAGMA foreign_keys=ON`, whereas the app enables it (db/storage.py:82) and relies on `ON DELETE CASCADE/SET NULL` (migrations.py:74-478). Result: dangling `source_listings.merged_id`, status_events, deliveries, checkpoints. Its docstring promise "Deleted properties will re-appear on the next scrape" (L8) is false: "seen" = `merged_id IS NOT NULL` (db/storage.py:421-434), which survives. Assessment item 7: "audit_merges.py deletes images, assessments and homes with raw SQL; its default is mutation ... Retire or quarantine unsafe tools." No references: `git grep audit_merges` → only the assessment doc; no tests. Unchanged since shallow root (`git log -- scripts` shows only `63f4271`, `b80efe6`, `47ca0f7`, `41f499e`).
- Recommendation: Delete. A read-only duplicate review already exists (`scripts/audit_duplicate_candidates.py`, docs/audits/offline-duplicate-candidates.md). Check Marcel's local copy for uncommitted edits first.
- Prior item: A10/B9 — OUTSTANDING | Owner decision needed: no

### REPO-8: One-time/dev scripts superseded by pipeline code or tied to February cache layouts
- Category: SUPERSEDED  | Confidence: HIGH | Action: DELETE | Risk: low | Effort: S | Size: ~435 lines + 199-line test
- Location: scripts/backfill_wards.py (89), scripts/reclassify_epc_images.py (146) + tests/test_scripts/test_reclassify_epc_images.py (199), scripts/debug_epc_scores.py (100), scripts/validate_epc_detector.py (98)
- Evidence:
  - backfill_wards.py: docstring "One-time backfill"; every pipeline run already calls `_lookup_wards` (pipeline/assessment.py:159, 570-612) using the same storage methods. No references (`git grep backfill_wards` → 0).
  - reclassify_epc_images.py: Feb one-time migration whose premise ("files stay as gallery_* on disk and classification lives solely in the DB", L15-16) contradicts current enrichment, which renames to `epc_*` and drops EPCs from the saved image list (enrichment/details.py:78-122, 283-287). Running it now renames current `epc_*` files back to `gallery_*` (phase 1, L32-43) with no DB row to re-detect against. Referenced only by its own test.
  - debug_epc_scores.py: re-implements the EPC heuristic inline (imports only PIL, no `home_finder`) against hard-coded Feb cache filenames (L73-100); no references.
  - validate_epc_detector.py: calibration helper that `rmtree`s data/epc_review; no references. (MEDIUM on its own — useful only if the EPC detector is re-tuned.)
  - All four import cleanly (`--help` / AST import check), so they are not broken, just obsolete.
- Recommendation: Delete the four scripts and test_reclassify_epc_images.py. Keep generate_icons.py, audit_duplicate_candidates.py, sync-to-fly.sh (see Verified keep). scripts/test_notification.py (sends a real Telegram message via `send_merged_property_notification`, bypassing the delivery outbox) → ASK-OWNER: keep only if the operator still previews formats this way; its dummy builders still validate (checked by importing and calling `_build_dummy_merged/_build_dummy_analysis` without sending).
- Prior item: A10/B9 — OUTSTANDING | Owner decision needed: no (yes for test_notification.py)

### REPO-9: scripts/audit_image_cache.py keeps hidden repair/purge/reanalysis flags
- Category: OVERBUILT  | Confidence: HIGH | Action: SIMPLIFY (narrow to read-only inventory) | Risk: medium (paid reanalysis, evidence deletion) | Effort: M | Size: ~200 of 465 lines + ~400 test lines
- Location: scripts/audit_image_cache.py:14-25, 120-140 (`--fix` DELETE rows), 225-305 (`--purge-disk` rmtree/unlink), 310-390 (`--reflag-partial` sets `quality_analyses.reanalysis_requested_at` ⇒ paid reanalysis), tests/test_scripts/test_audit_image_cache.py (830 lines)
- Evidence: `--purge-disk` removes any cache dir whose property row is gone and any `<type>_NNN_<hash>` file without a matching `property_images` row (regex L41) — this deletes `epc_*` files (EPCs have no image rows) and ignores visual checkpoints / pending deliveries, which the assessment says may be "the only retained evidence for an assessment, a frozen evaluation example or an undelivered message" (assessment L346-350). `--reflag-partial` bypasses the app's reanalysis request path (processing_repo.py:732-819). B9: "Keep a useful read-only cache inventory ... do not keep hidden repair/reanalysis flags by accident."
- Recommendation: Keep phase-1/phase-2 reporting; delete `--fix`, `--purge-disk`, `--reflag-partial`, `--dry-run` and their tests. Precondition: confirm no operator runbook uses them (none in docs: `git grep audit_image_cache -- docs` → 0).
- Prior item: B9 — OUTSTANDING | Owner decision needed: no

### REPO-10: Root leftovers with no references (output.txt, crontab, main.py, mutmut_config.py, dedupe.json)
- Category: HYGIENE  | Confidence: HIGH (output.txt CERTAIN) | Action: DELETE | Risk: low | Effort: S | Size: ~45 lines + 4.5 KB JSON
- Location: /output.txt, /crontab, /main.py, /mutmut_config.py, /dedupe.json
- Evidence (searched `git grep` over whole tree incl. docs/scripts/.github/Dockerfile/fly.toml and `origin/codex/review-opus-experiments`):
  - output.txt: 0 bytes, 0 references, last touched `2a27195` (2026-02-24).
  - crontab: comment-only ("No cron jobs are installed by this repository"), 0 references, excluded from the image by `.dockerignore` (`**` + allow-list); its guidance duplicates docs/operations.md:451-453.
  - main.py: "Entry point wrapper for backwards compatibility"; `git grep -E "python main\.py|python -m main"` → 0; entry point is `[project.scripts] home-finder` (pyproject:51); excluded from Docker and sdist (`only-include`).
  - mutmut_config.py: self-describes "mutmut 3.x does NOT auto-load this file ... kept for documentation purposes only"; references a local `.venv/.../_mutmut_setproctitle_fix.pth` hack that is not in the repo. Real config is `[tool.mutmut]` (pyproject:117-128) + `mutmut` hypothesis profile (tests/conftest.py:58).
  - dedupe.json: 51 labels, all present with identical labels in experiments/dedup/labels/labels_merged.json (140 labels; checked programmatically); only reference is that file's `source_files` metadata (`"../../dedupe.json"`).
- Recommendation: Delete all five. Move mutmut_config.py's three operational notes into CONTRIBUTING (or tests/README.md) under "Mutation testing (optional, B8)". Note Marcel's machine may rely on `python main.py` habitually — trivial to replace with `uv run home-finder`.
- Prior item: A13 — OUTSTANDING; B8 (doc-only config) | Owner decision needed: no

### REPO-11: Root-level evidence files that should move, not be deleted (output/playwright, rental_market_research.md)
- Category: HYGIENE  | Confidence: HIGH (playwright) / MEDIUM (research) | Action: MOVE | Risk: low | Effort: S | Size: 10 images (≈500 KB) + 1 doc (13 KB); 10 link edits
- Location: output/playwright/*.{png,jpg}; rental_market_research.md
- Evidence: The 10 screenshots are linked from 4 decision records (docs/plans/2026-10-10-focused-reliability-repairs.md:60-62,97; docs/reviews/2026-10-10-image-coverage-ui.md:18,20; …mobile-card-containment.md:23-24; …outdoor-evidence-ui.md:17,19) — decision evidence, keep. `output/` is not ignored (.gitignore only ignores root `/*.png`), so tool output lands at the root. rental_market_research.md has 0 references but its numbers match `src/home_finder/data/area_context.json` benchmarks (e.g. E10 1-bed 1550 / 2-bed 1750) — it is the provenance of shipped reference data.
- Recommendation: `git mv output/playwright docs/reviews/assets/2026-10-10/` and update the 10 links; add `/output/` to .gitignore. Move rental_market_research.md to `docs/archive/2026-02/research/` (or `src/home_finder/data/README` note) and cite it from area_context.py.
- Prior item: A13 — OUTSTANDING | Owner decision needed: no (yes for research file location)

### REPO-12: Crawlee is an unused runtime dependency whose only traces are tests/config
- Category: CONFIG  | Confidence: CERTAIN | Action: DELETE (dep) + add explicit `beautifulsoup4`/`lxml` | Risk: medium (bs4/lxml are only transitive today) | Effort: M | Size: ~70 lines across pyproject, tests/conftest.py:418-490, 4 integration files, .gitignore:152-153, CLAUDE.md
- Location: pyproject.toml:8, 78; tests/conftest.py:418-490; tests/integration/{test_cross_platform_dedup_e2e,test_detail_fetcher_e2e,test_enrichment_pipeline_e2e,test_real_scraping}.py (pytestmark usefixtures); .gitignore:152-153 (`storage/`)
- Evidence: `git grep -i crawlee -- src` → 0 imports. deptry: DEP002 crawlee unused; DEP003 `bs4` imported in 5 src modules but transitive (via `crawlee[beautifulsoup]`); `scrapers/rightmove.py:209` uses the `"lxml"` parser (lxml also transitive, only a constraint floor in pyproject:153). The E402 per-file ignore (pyproject:78) exists only because the Crawlee `pytestmark` precedes imports (`ruff --select E402 tests/integration` → 17 errors, all in those files). docs/sources/rightmove.md:9 records why Crawlee was abandoned.
- Recommendation: Remove `crawlee[beautifulsoup]`, add `beautifulsoup4>=…` and `lxml>=…`, delete the two Crawlee fixtures and their `usefixtures`, then the E402 ignore and `storage/` ignore; relock and run the parser/scraper suites. Assessment: "explicitly install/test beautifulsoup4[lxml]".
- Prior item: A1 — OUTSTANDING | Owner decision needed: no

### REPO-13: Unused dev dependencies respx and dirty-equals
- Category: CONFIG  | Confidence: CERTAIN | Action: DELETE | Risk: low | Effort: S | Size: 2 lines + lock
- Location: pyproject.toml:45-46
- Evidence: `git grep -n "import respx\|from respx\|dirty_equals\|IsStr\|IsNow" -- src tests scripts experiments` → 0; same on `origin/codex/review-opus-experiments` (code) → 0 (only docs/reviews/B4-testing.md prose mentions respx). `tests/test_analysis/test_quality_respx.py` keeps the name but now uses `httpx2` (L12) — a historical filename, not a respx user. All other dev deps verified used (see Verified keep).
- Recommendation: Remove both; optionally rename test_quality_respx.py (tests area).
- Prior item: A2 — OUTSTANDING | Owner decision needed: no

### REPO-14: Dev tooling is published as a `dev` extra rather than a uv dependency group
- Category: CONFIG  | Confidence: MEDIUM | Action: ASK-OWNER | Risk: low | Effort: S | Size: ~20 lines (pyproject + 5 CI steps + README/CLAUDE/CONTRIBUTING commands)
- Location: pyproject.toml:30-47; .github/workflows/checks.yml (5× `uv sync --locked --all-extras`); Dockerfile:18,21 (`--no-dev`); docs/operations.md:437
- Evidence: Dev tools live in `[project.optional-dependencies].dev`, so they appear in wheel metadata as `home-finder[dev]` and require `--all-extras`. The Dockerfile/operations `--no-dev` flag only affects `[dependency-groups]`, so it is currently a no-op (harmless: extras are not installed by default). The experiments' own pyprojects already use `[dependency-groups]`.
- Recommendation: Move to `[dependency-groups] dev = [...]`, replace `--all-extras` with plain `uv sync --locked` everywhere. Low value; do with REPO-12/13 relock.
- Prior item: none | Owner decision needed: yes

### REPO-15: The live "slow-tests" CI job is gated but still cannot exercise httpx-based sources
- Category: CONFIG  | Confidence: HIGH | Action: REWRITE (replace with explicit smoke) or DELETE job | Risk: low | Effort: S | Size: ~17 lines CI + 2 doc lines
- Location: .github/workflows/checks.yml:119-137; README.md:253; CLAUDE.md:54, 183
- Evidence: Job is now default-off (`if: inputs.run_live_tests`, L120) — the "explicitly controlled" part of A20 is done. But it runs `uv run pytest -m slow -v --timeout=120` with the global addopts `--disable-socket --allow-unix-socket` (pyproject:55); no slow test carries `enable_socket`/`allow_hosts` (`git grep enable_socket -- tests` → only browser auto-marking in tests/test_web/conftest.py:61). Rightmove scraping/detail tests use httpx (Python sockets) and would raise SocketBlockedError; curl_cffi-based ones bypass pytest-socket because libcurl opens its own sockets. `-m slow` also selects tests/test_analysis/test_quality_eval.py (paid Anthropic calls; skipped without key). Not executed (would contact portals).
- Recommendation: Either delete the job (and the `run_live_tests` inputs, L13-14, L22-23) or replace it with a documented, source-limited smoke command that passes `--force-enable-socket` and excludes paid tests; fix README/CLAUDE `-m slow` lines accordingly.
- Prior item: A20 — PARTIAL | Owner decision needed: yes (keep a live smoke at all?)

### REPO-16: ADR 001 still "Accepted" for a heuristic the pipeline no longer uses
- Category: STALE-DOC  | Confidence: HIGH | Action: REWRITE-DOC (new ADR 002 superseding 001) | Risk: low | Effort: S | Size: ~40 new lines, 1 status line
- Location: docs/adr/001-laplacian-bimodality-floorplan-detection.md:3-9
- Evidence: Status "Accepted", 2026-02-17. `a021747` removed the enrichment heuristic (plan R3, docs/plans/2026-10-10-focused-reliability-repairs.md:67-90 records the reason and the offline replay). `git grep detect_floorplan -- src` → only utils/floorplan_detector.py itself (+ a docstring mention in epc_detector.py:16); only caller is tests/test_utils/test_floorplan_detector.py. CLAUDE.md's ADR guide requires a superseding ADR rather than editing.
- Recommendation: Add `docs/adr/002-source-declared-floorplans-only.md` (Context/Decision from R3) and set 001's status to "Superseded by ADR 002". Then the module/test removal (utils area) has its decision record.
- Prior item: A11 — OUTSTANDING ("preserve the reason ... in a concise decision record") | Owner decision needed: no

### REPO-17: February 2026 planning prose (≈12k lines) is unindexed, unreferenced and describes deleted modules
- Category: STALE-DOC  | Confidence: HIGH | Action: MOVE (archive) | Risk: low | Effort: S | Size: 28 files, ~12,200 lines moved; 0 deleted
- Location: docs/plans/2026-02-0*.md (6 files, 3,567 lines), docs/tickets/*.md (12 files, 4,709 lines), docs/reviews/{A1..A5,B1..B4,C-roadmap}.md (10 files, 3,894 lines), docs/audits/image-handling-findings/README.md
- Evidence: Inbound references (`git grep -F <basename>` across tree) = 0 for every one of these 28 files. They reference pre-refactor paths (e.g. A3-filters.md has 2 broken relative links; B4-testing.md describes respx-based tests; tickets describe features since built or dropped — e.g. ticket-9 Playwright enquiry exists only as an `enquiry_log` table). docs/audits/image-handling-findings/README.md is an empty template linking a non-existent `../image-handling-audit.md`, with no findings files (DEAD). The Oct-2026 records (pr-disposition, architecture assessment, reliability audits, model bake-off, UI reviews) are referenced from CLAUDE.md/operations/each other and are current decision evidence. Assessment row: "Delete old documents and experiments behind a tag — Useful housekeeping after extracting live knowledge."
- Recommendation (concrete layout, no deletion of decision evidence):
  ```
  docs/README.md                 # NEW 20-line index: current vs historical
  docs/pipeline.md, operations.md, ci.md, sources/, adr/   # current, authoritative
  docs/audits/offline-duplicate-candidates.md              # current tool doc
  docs/reviews/2026-10-*.md (+ .py/.json, assets/)          # decision records, unchanged paths
  docs/archive/2026-02/plans/      <- docs/plans/2026-02-0*.md
  docs/archive/2026-02/tickets/    <- docs/tickets/*
  docs/archive/2026-02/reviews/    <- docs/reviews/{A*,B*,C-roadmap}.md
  docs/archive/2026-02/research/   <- /rental_market_research.md (REPO-11)
  ```
  Delete docs/audits/image-handling-findings/ (empty template). Optionally tag `archive/docs-2026-02` before moving. Keep docs/plans/2026-10-*.md in place (5 inbound links).
- Prior item: "archive obsolete prose" (assessment L395) — OUTSTANDING | Owner decision needed: no

### REPO-18: experiments/dedup and experiments/layout-inference no longer run against current code
- Category: DEAD  | Confidence: HIGH (broken) / MEDIUM (action) | Action: ASK-OWNER (tag + delete, or fix 4 imports) | Risk: low | Effort: S | Size: ~7,700 lines of py/md + 1.3 MB of uv.lock
- Location: experiments/dedup/signals.py:16, experiments/dedup/collect.py:21, experiments/layout-inference/run_inference.py:33,164; experiments/*/uv.lock (704 KB + 557 KB)
- Evidence: AST import resolution against src: `home_finder.filters.scoring` (→ matching.scoring), `home_finder.main.scrape_all_platforms` (→ pipeline/collection.py:144), `home_finder.filters.quality` / `filters.quality_prompts` (→ analysis/analyzer, analysis/prompts) all ModuleNotFound/missing since `41f499e` (2026-10-25). signals.py is imported by scorer.py, evaluate.py and generate_pairs.py, so the core dedup calibration path is broken transitively. No CI, ruff or mypy covers experiments (pyproject:70, 84 exclude them); no doc outside docs/reviews references them. layout-inference/BRIEF.md describes "floorplan gate (pipeline step 10)" and Sonnet 4.5. .gitignore (`/experiments/*/data/`, experiments/dedup/.gitignore) is correct; results live in untracked `data/` on Marcel's machine.
- Recommendation: Record each experiment's conclusion (dedup weights → matching/scoring.py; photo-inference threshold 5 → config.py:106) in a short note or ADR, tag `archive/experiments-2026-10`, delete both directories (preserve labels JSON with the tag). If kept, fix the four imports and add a tiny import-smoke test. Precondition: ask Marcel about untracked `experiments/*/data` and reports.
- Prior item: assessment "Delete old documents and experiments behind a tag" | Owner decision needed: yes

### REPO-19: Open draft PR #77 (experiments/model-comparison) imports modules removed by 41f499e
- Category: SUPERSEDED  | Confidence: CERTAIN | Action: ASK-OWNER (rebase/fix or merge as frozen evidence pinned to its base) | Risk: low | Effort: S | Size: 25 files, 6,704 added lines (3,183 uv.lock)
- Location: origin/codex/review-opus-experiments — experiments/model-comparison/{run.py:30, judge.py:22, probe_grammar*.py:11-19, mc/facts.py:15}
- Evidence: `git grep "from home_finder" origin/codex/review-opus-experiments -- experiments/model-comparison` → `home_finder.filters.quality` (PropertyQualityFilter, VISUAL_ANALYSIS_TOOL, _eval_json_schema) and `home_finder.filters.listing_text`; both moved/renamed in `41f499e` (class now `PropertyQualityAnalyzer`, module `analysis/listing_text.py`). Branch is 3 ahead / 24 behind main. docs/reviews/2026-10-21-model-bakeoff-review.md:105 cites this experiment from a local worktree path.
- Recommendation: If merged as evidence, state in its README the commit it runs against; otherwise update 8 import lines. Do not add a fourth ~0.6–3 MB uv.lock without a reason.
- Prior item: none | Owner decision needed: yes

### REPO-20: docs/reviews/2026-10-21-model-bakeoff-audit.py no longer imports
- Category: STALE-DOC  | Confidence: CERTAIN | Action: KEEP-BUT-DOCUMENT (fix 2 imports or pin to commit) | Risk: low | Effort: S | Size: 2-3 lines
- Location: docs/reviews/2026-10-21-model-bakeoff-audit.py:21,23
- Evidence: `from home_finder.filters.analysis_contracts import ...` and `from home_finder.filters.quality import _VisualAnalysisResponse` → ModuleNotFoundError (moved to analysis/contracts.py, analysis/analyzer.py in `41f499e`). Added in `3219524` four days earlier. docs/reviews/2026-10-22-shadow-rating-audit.py still resolves. Similarly docs/reviews/2026-10-13-core-reliability-completion-audit.md has 5 relative links to moved files (filters/quality.py, filters/analysis_spend.py, tests/test_filters/test_analysis_*.py).
- Recommendation: Fix the two imports (keeps the evidence reproducible) or add a header "runs at 3219524". For the audit .md, convert the 5 links to commit permalinks rather than editing content. Decision evidence — do not delete.
- Prior item: none | Owner decision needed: no

### REPO-21: Remote branch clean-up (84 branches)
- Category: HYGIENE  | Confidence: HIGH (merged) / MEDIUM (closed-unmerged) | Action: ASK-OWNER (recommend delete merged; consolidate the rest) | Risk: low (commits preserved) | Effort: S | Size: 40 + 31 refs
- Location: origin/* (classification in scratchpad/branches.txt)
- Evidence: `git branch -r --merged origin/main` → 42 (incl. main and the current audit branch) ⇒ **40 deletable merged branches**: 10 codex/repair/*, 8 codex/reliability/*, 8 fix/*, 2 codex/trial/*, codex/review-ci-bootstrap, codex/review-transactions, codex/stack/14-backup-authority, codex/model-bakeoff-review, codex/pipeline-structure, codex/pipeline-test-intent, docs/operating-qualification, 5 claude/* (blissful-raman, listing-page-1-reorder, listing-page-2-words, listing-page-reorder, ui-ux-design-review). **Unmerged (42)**: 31 codex/stack/{01..13,15..32} (closed PRs #3–#35, all unmerged), 7 archive/review-2026-10-10-pr{1,2,16,39,40,41,42}, codex/review-remediation (56 ahead, no PR), codex/review-opus-experiments (open draft #77), 2 dependabot (open #73, #86). `git ls-remote origin 'refs/pull/*/head'` shows every closed-PR branch tip — all 31 stack branches and 6 of 7 archive branches — is also held by GitHub's permanent `refs/pull/N/head`. The stack is linear (each stack/N is an ancestor of N+1, 13→15 skips merged 14) and stack/32 and archive/pr16 are ancestors of codex/review-remediation, so that single ref preserves every unmerged stack commit. pr-disposition L192: "Closed PR branches are retained; closed does not mean their historical work is lost."
- Recommendation: Delete the 40 merged branches (and this audit branch when done). For the 31 codex/stack/* and 6 archive/pr{1,2,39-42}: their commits survive in refs/pull/N/head; owner may keep (per the recorded decision) or replace with one tag `archive/stack-2026-10-10` at codex/review-remediation. Keep codex/review-remediation and archive/review-2026-10-10-pr16 (no PR ref) — or tag them. Leave open-PR branches.
- Prior item: none | Owner decision needed: yes

### REPO-22: Python version policy — 3.14.7 pinned three ways, 3.11 floor untested
- Category: CONFIG  | Confidence: MEDIUM | Action: ASK-OWNER | Risk: medium (security patch level) | Effort: S | Size: ~8 lines
- Location: .python-version:1; Dockerfile:14,18,21,36; .github/workflows/checks.yml:35; pyproject.toml:6, 69, 82; README.md:3
- Evidence: CI/Docker/.python-version all pin 3.14.7; assessment ("Dependencies and boundaries") says 3.14.7 predates the 3.14.8 security release — still OUTSTANDING. `requires-python >=3.11`, ruff `py311`, mypy `3.11`, README badge "Python 3.11+" advertise 3.11 support, but CI runs only 3.14.7 (the disposition's one-off 3.11 run is not repeated). Local venv is 3.13.
- Recommendation: Bump the pin together in all four places; either add a 3.11 test job or raise the floor to what is actually tested.
- Prior item: assessment Python patch item — OUTSTANDING | Owner decision needed: yes

### REPO-23: Small config/lint drift (unused marker, unused mypy override, scripts unlinted)
- Category: CONFIG  | Confidence: HIGH | Action: SIMPLIFY | Risk: low | Effort: S | Size: ~6 lines (+10 lint fixes)
- Location: pyproject.toml:59 (`unit` marker), 91-93 (`[[tool.mypy.overrides]] module = "scripts.*"`); .github/workflows/checks.yml:53-54 (ruff on `src tests` only)
- Evidence: `git grep -E "mark\.unit\b" -- tests` → 0. mypy only runs on `src` (checks.yml:69, docs), so the scripts override is never applied. `ruff check scripts` → 10 errors, `ruff format --check scripts` → 5 files; scripts are imported by tests/test_scripts yet escape lint. `.gitignore` also carries ~60 lines of unrelated template boilerplate (Django/Flask/Scrapy/Sphinx/Celery/SageMath/Spyder) — LOW.
- Recommendation: Drop the `unit` marker and scripts mypy override, or add `scripts` to the ruff/mypy CI targets after REPO-7/8/9 shrink the directory.
- Prior item: none | Owner decision needed: no

### REPO-24: 28 PR-review screenshots (2.5 MB) committed to main are referenced only by PR descriptions
- Category: HYGIENE  | Confidence: LOW | Action: ASK-OWNER | Risk: low | Effort: S | Size: 28 PNGs
- Location: docs/reviews/assets/2026-10-10-mobile-budget.png, 2026-10-24-*.png (11), 2026-10-25-*.png (16)
- Evidence: `git grep -F <basename> -- '*.md'` → 0 for each. PR bodies #78/#81/#87 embed them via commit-pinned raw URLs (e.g. `raw.githubusercontent.com/.../586b53a.../docs/reviews/assets/...`), which keep working if the files are removed from HEAD.
- Recommendation: Either reference them from a review doc (they are UI-decision evidence) or stop committing PR-only screenshots (attach them to the PR instead). Not worth a deletion commit on its own.
- Prior item: none | Owner decision needed: yes

## Verified keep (looks dead, isn't)
- scripts/sync-to-fly.sh — intentional failing tombstone; documented docs/operations.md:70, tested tests/test_db/test_backup.py:360.
- scripts/audit_duplicate_candidates.py — read-only (`mode=ro&immutable=1`, `query_only`), documented docs/audits/offline-duplicate-candidates.md, tested.
- scripts/generate_icons.py — produces static/apple-touch-icon.png, icon-192/512.png, og-image.png used by templates/base.html:11,25 and static/manifest.json.
- httpx2 (deptry DEP002) — imported under TYPE_CHECKING in analysis/analyzer.py:88/785, used by tests/support and test_quality_respx.py; the Anthropic SDK transport.
- jinja2, python-multipart (deptry DEP002) — FastAPI implicit runtime deps.
- Dev deps pytest-socket (addopts `--disable-socket`), pytest-playwright (browser tests), pytest-httpx (`httpx_mock` in 3 files), hypothesis (6 files + profiles), inline-snapshot (test_prompts.py), mutmut (`[tool.mutmut]`, B8 optional), pytest-xdist/-timeout/-cov (addopts/CI) — all used.
- E402 per-file ignore for tests/integration — still required until the Crawlee fixtures go (REPO-12).
- Dockerfile `collector` target — documented docs/operations.md:30.
- output/playwright/* — referenced by 4 decision docs (move, don't delete; REPO-11).
- archive/* branches and closed codex/stack/* branches — recorded preservation decision (pr-disposition L192); only consolidation is proposed.
- docs/reviews/2026-10-22-shadow-rating-audit.py — imports resolve; referenced by the bake-off review.
- .claude/settings.json — enables the claude-md-management plugin; fine.
- experiments/dedup/labels/*.json — the only ground-truth labels for dedup weights; preserve with any experiments tag.

## Notes
- Rename commit `41f499e` (2026-10-25, PR #84 "make pipeline stages, contracts, and terminology explicit") is the common cause of REPO-1/2 path errors, REPO-18/19/20 breakage. Nothing outside src/tests is import-checked in CI; a 10-line smoke test that imports every `scripts/*.py`, `docs/reviews/*.py` and `experiments/**/*.py` module (AST-level, like scratchpad/check_imports.py) would have caught all of them.
- CLAUDE.md cannot be found by rg/Grep (REPO-4) — other auditors grepping for stale paths will have missed CLAUDE.md; this file covers it by direct reading.
- README.md is the most accurate of the three top-level docs; CLAUDE.md duplicates README/pipeline.md/tests/README.md and drifts. Recommended single sources: run modes → README; pipeline/architecture → docs/pipeline.md; tests → tests/README.md; operations → docs/operations.md. CLAUDE.md should keep only agent guidance (scope, Marcel's preferences, gotchas, ADR guide) plus links.
- Untracked work on Marcel's machine (experiments/*/data, reports, the original "opus-ultra" report, model-comparison worktree) is not protected by any tag; ask before deleting experiments or archiving.
- GitHub secrets/environments could not be read (proxy 403), so the "retire legacy FLY_API_TOKEN" note in docs/ci.md / pr-disposition is unverified.
