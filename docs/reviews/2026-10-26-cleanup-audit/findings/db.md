# DB area findings (src/home_finder/db/)

Scope read in full: storage.py, delivery.py, processing_repo.py (1-760, 855-905), row_mappers.py,
migrations.py (720-969 + 004/005 backfills), listing_text.py, merge_safety.py, image_ops.py,
source_listing_ops.py, enrichment_recovery.py, backup.py (1-300), web_queries.py (1-240, 370-615), spend.py header.
Searches: `git grep -w` over src, tests, scripts, experiments, main.py, docs, README/CLAUDE/CONTRIBUTING and the PR #77
branch (`origin/codex/review-opus-experiments`); plain `grep -r` of the working tree (no untracked files beyond
.coverage/.hypothesis/.ruff_cache/.venv). Coverage = scratchpad/evidence/coverage.json + per-test contexts from coverage.db.

Headline: storage.py is not a pass-through façade. It is (a) the connection/transaction owner (lines 67-335), (b) the
host of the remaining domain methods that were never split out, and (c) about **475 lines (~23%) of methods reachable only
from tests or from nowhere**. The sub-repositories (`processing`, `runs`, `spend`, `deliveries`, `web`) are attributes, not
wrapper methods, so there is little pure pass-through to remove; the waste is superseded and test-only API plus
duplicated SQL between storage.py and processing_repo.py/delivery.py.

---

### DB-1: Pre-outbox notification readers/writers still on PropertyStorage
- Category: SUPERSEDED  | Confidence: HIGH | Action: MOVE (seed helpers to tests/support) then DELETE | Risk: medium (test seeding; no prod path) | Effort: M | Size: ~130 src lines (+~120 test lines deleted)
- Location: src/home_finder/db/storage.py:472-540 (get_pending_notifications, get_unsent_notifications, mark_notified, mark_notification_failed), :1717-1749 (get_unsent_price_drops, mark_price_drop_notified), :2034-2052 (_row_to_tracked_property, only used by test-only readers)
- Evidence: zero callers in src/scripts/experiments/main.py/PR #77; last production callers removed in ccf7b9a "fix: resume Telegram deliveries from durable part receipts" (`git log -S ".mark_notified(" -- src`). The only production writers of `notification_status='sent'/'failed'` and `price_drop_notified=1` are now `DeliveryRepository._acknowledge` (delivery.py:436-449) and `fail` (delivery.py:376-383); `mark_notified` writes `'sent'` directly, bypassing the outbox — exactly the competing writer A16 targets. Test use: `mark_notified` 59 refs/20 files, mostly as a seed for "already notified" state (tests/integration/test_off_market_e2e.py ×10, tests/test_pipeline/test_recovery.py ×5) and as an arbitrary write in tests/test_db/test_transaction_ownership.py ×15; `get_unsent_price_drops` 8 refs used as an oracle in tests/test_notifiers/test_delivery_recovery.py:160-231,480-486; `mark_notification_failed` used to seed a legacy row at test_delivery_recovery.py:532. Self-tests of dead behaviour: tests/test_db/test_storage.py:134-182, 246-313; tests/test_db/test_storage_analysis_retry.py:473-490 (`TestNotificationRetryExcludesPendingAnalysis`); tests/test_db/test_price_history.py:211,245-265.
- Recommendation: Add `tests/support/seed.py` with explicit raw-SQL seeds (`seed_sent(storage, uid)`, `seed_failed(...)`) named as legacy/fixture state; replace `get_unsent_price_drops` assertions with direct `price_drop_notified` / delivery-state assertions; in transaction-ownership tests use a production write such as `update_property_last_checked`. Then delete the six methods, `_row_to_tracked_property` and the self-tests. Pairs with A7 (old Telegram senders, notifiers area).
- Prior item: A16 (PARTIAL — production callers gone, methods remain), A7 (DB half) | Owner decision needed: no

### DB-2: Enquiry-log accessors and delete_viewing_message are dead (never wired)
- Category: DEAD  | Confidence: CERTAIN | Action: DELETE | Risk: low | Effort: S | Size: ~55 lines
- Location: src/home_finder/db/storage.py:1556-1562 (delete_viewing_message), :1751-1797 (log_enquiry, get_enquiries_for_property, has_enquiry)
- Evidence: `git grep -w` for each name: only the definitions and docs/tickets/ticket-8/ticket-9 design docs; zero refs in src, tests, scripts, experiments, templates, PR #77; `grep -r` of the working tree likewise. Coordinator's uncovered_functions.txt: all four have ZERO coverage (storage.py:1558, 1757, 1781, 1791). No feature module for tickets 8/9 exists in history (`git log --all --diff-filter=D -- '*enquir*' '*viewing*'` empty).
- Recommendation: Delete the four methods and the "Enquiry log (Ticket 9)" section header. Keep the `enquiry_log`/`viewing_messages` tables and migration 001 DDL: merge_safety.py:16-18 still protects any retained rows from staging merges, backup inventories them, and tests/integration/test_core_upgrade.py seeds them. Stored-data implication: any existing rows stay readable via SQL/backup only.
- Prior item: A4 | Owner decision needed: no

### DB-3: get_viewing_message / save_viewing_message are test-only seeds
- Category: TEST-ONLY  | Confidence: HIGH | Action: MOVE (seed via raw SQL) then DELETE | Risk: low | Effort: S | Size: ~20 lines
- Location: src/home_finder/db/storage.py:1530-1554
- Evidence: zero src/scripts callers; tests: tests/test_db/test_listing_text.py:61,108 and tests/test_pipeline/test_enrichment_persistence.py:187 — both only seed a "personal state must survive" row and read it back. No UI or route reads viewing messages.
- Recommendation: Seed with `INSERT INTO viewing_messages` in those two tests (unique preservation assertions stay), then delete both methods. Keep the table (see DB-2).
- Prior item: A4 | Owner decision needed: no

### DB-4: processing_repo.mark_enriched is test-only and contradicts the authoritative lifecycle
- Category: TEST-ONLY  | Confidence: HIGH | Action: DELETE | Risk: low | Effort: S | Size: ~30 src + ~60 test lines
- Location: src/home_finder/db/processing_repo.py:336-365; tests/test_db/test_storage_enrichment.py:187-243 (TestMarkEnriched); tests/test_pipeline/test_enrichment_admission.py:86-87
- Evidence: zero src/scripts callers. It moves `pending_enrichment` → `pending` and enqueues discovery *without an assessment*; production completes enrichment via `_save_pre_analysis_property` (→ pending_analysis, enrichment_status='enriched', processing_repo.py:460-471) or `_save_dropped_property`, and `complete_analysis` (processing_repo.py:636-659) accepts `pending_enrichment` directly and enqueues in the same transaction. The A14 precondition ("lifecycle test proves completion/enqueue transitions occur through the authoritative path") is already met: tests/test_db/test_storage_analysis_retry.py:160-194 (unenriched → save_pre_analysis_properties → enrichment_status='enriched'), tests/test_db/test_storage_dropped.py:~190-215 (pending_enrichment → dropped/enriched), plus complete_analysis enqueue tests (39 refs).
- Recommendation: Delete the method and TestMarkEnriched; in test_enrichment_admission.py:86-87 replace with `storage.processing.save_pre_analysis_properties(selected, {})` (the real transition) or simply drop the loop if the test only needs them out of the pending set.
- Prior item: A14 (OUTSTANDING; precondition satisfied) | Owner decision needed: no

### DB-5: get_all_known_source_ids is the orphaned DB half of disabled early-stop
- Category: TEST-ONLY  | Confidence: HIGH | Action: DELETE | Risk: low | Effort: S | Size: ~17 src + ~35 test lines
- Location: src/home_finder/db/storage.py:1297-1313; tests/test_db/test_storage.py:1198-1222; tests/test_pipeline/test_orchestration.py:536-546
- Evidence: zero src/scripts callers; pipeline/collection.py:48 hard-codes `known_ids_by_source = None` and pipeline/flow.py:54 documents `full_scrape` as a "Compatibility flag; collection no longer stops at known IDs". The docstring itself says routine scraping must not use it. test_orchestration.py:541-545 monkeypatches the method and asserts it is not called — vacuous since nothing calls it.
- Recommendation: Delete with its two storage tests; the orchestration spy test should be removed or rewritten by whoever removes the A6 plumbing (pipeline area).
- Prior item: A6 (DB half, OUTSTANDING), A4 | Owner decision needed: no

### DB-6: Legacy property writers are the de-facto test seeding API and create a state production cannot
- Category: TEST-ONLY  | Confidence: HIGH | Action: MOVE (to tests/support/seed.py) | Risk: medium (large test churn) | Effort: L | Size: ~190 src lines (save_property 376-415, save_merged_property 850-907, link_source_listings 1262-1275, delete_property 602-617, _upsert_source_listing 1134-1196) + `extra=` param of row_mappers.build_merged_insert_columns
- Evidence: zero src/scripts/experiments/PR #77 callers for all five public methods; `_upsert_source_listing` is only called from save_property/save_merged_property (storage.py:413, 896); `extra=` only from save_merged_property (storage.py:872). Test refs: save_merged_property 315/40 files, save_property 58/9, link_source_listings 9/5, delete_property 5/3. Rows they write have `notification_status='pending'` and no `notification_deliveries` row; after migration 012 production only reaches 'pending' through `complete_analysis` + `enqueue_discovery` in one transaction, so `DeliveryRepository.recover()` (run every pipeline start, flow.py:131) reclassifies such seeded rows as `review/legacy_untracked` (delivery.py:172-178). Tests using these seeds therefore exercise a legacy-shaped state, sometimes deliberately (test_delivery_recovery.py:531-533), mostly not.
- Recommendation: Do not delete outright. Move them mechanically into `tests/support/seed.py` as functions taking `storage` (same SQL via `storage._transaction()`), naming the legacy one explicitly (e.g. `seed_legacy_pending`). Then, test by test, prefer production entry points (`processing.save_pre_analysis_properties` + `complete_analysis`) where the test is about current behaviour (A15). Check Marcel's local untracked scripts before removing from src.
- Prior item: A15, A16 | Owner decision needed: no (test-strategy judgement, not product)

### DB-7: "no such table" tolerance in _upsert_source_listing exists only for one test
- Category: COMPAT  | Confidence: HIGH | Action: SIMPLIFY | Risk: low | Effort: S | Size: ~6 lines
- Location: src/home_finder/db/storage.py:1141-1145 (docstring), 1186-1189
- Evidence: coverage contexts for lines 1187-1188: exactly one test, tests/test_db/test_storage_quality.py::TestSaveAndGetQualityAnalysis::test_migration_fixes_wrapped_one_line, which hand-builds a pre-migration schema (lines 186-220) and calls the test-only `save_property` before `initialize()`. Production always runs `run_migrations` in `initialize()` before any write.
- Recommendation: In that test, seed the property with a raw `INSERT INTO properties` (it already seeds quality_analyses that way at 233-237) and drop the try/except. Keep the test — it is a unique migration regression scenario. Falls away automatically with DB-6.
- Prior item: B1 | Owner decision needed: no

### DB-8: is_seen / filter_new duplicate filter_new_merged and are test-only
- Category: DUP  | Confidence: HIGH | Action: DELETE | Risk: low | Effort: S | Size: ~33 src lines
- Location: src/home_finder/db/storage.py:417-438 (is_seen), 572-582 (filter_new)
- Evidence: zero src callers; production uses `filter_new_merged` (pipeline/candidates.py:114) via `_get_seen_ids`. `is_seen` re-implements `_get_seen_ids` SQL for one id; tests: test_storage.py (11), test_source_listings_linkage.py (9); `filter_new` 1 test (test_storage.py:214-228).
- Recommendation: Rewrite the is_seen assertions as `await storage.filter_new_merged([m]) == []`, delete both methods and test_filter_new_properties.
- Prior item: A15/A16 | Owner decision needed: no

### DB-9: Test-only read accessors (assertion oracles)
- Category: TEST-ONLY  | Confidence: MEDIUM | Action: MOVE (tests/support) or KEEP-BUT-DOCUMENT | Risk: low | Effort: M | Size: ~60 src lines
- Location: storage.py:440-470 (get_property, get_all_properties), :1428-1445 (get_quality_analysis); web_queries.py:606-615 (get_property_count)
- Evidence: zero src/scripts callers; heavy test use: get_property 68 refs/20 files (incl. `_conn=` use in test_transaction_ownership.py:94-109), get_quality_analysis 41/19, get_property_count 16/8, get_all_properties 5/2. `TrackedProperty` (models/core.py:193) is constructed only by these readers.
- Recommendation: Low value per line; A15 says inspect, not auto-delete. If DB-1/DB-6 are done, move these into tests/support too (and let the models agent retire TrackedProperty); otherwise leave and add a one-line "test/support accessor" docstring.
- Prior item: A15 | Owner decision needed: no

### DB-10: Dead `descriptions` computation in row_to_merged_property
- Category: DEAD  | Confidence: CERTAIN | Action: DELETE | Risk: low | Effort: S | Size: ~7 lines
- Location: src/home_finder/db/row_mappers.py:207, 218-219, 237-239
- Evidence: `descriptions` is filled from source_listings (218-219) and `descriptions_json` (237-239) but never read before being reassigned wholesale at 273-277 from `source_text` (which already folds in both via text_from_property_row/text_from_source_row, listing_text.py:24-46). Overwrite added in 3570e26 "refactor: assemble bounded core repairs"; earlier lines from 936b087.
- Recommendation: Remove the dead initialisation and both fill loops; keep the 273-277 assignment.
- Prior item: none | Owner decision needed: no

### DB-11: Impossible "pre-migration rows" guard for floor_area in row_to_merged_property
- Category: OVERBUILT  | Confidence: HIGH | Action: SIMPLIFY | Risk: low | Effort: S | Size: ~7 lines
- Location: src/home_finder/db/row_mappers.py:258-265
- Evidence: all five callers select `*`/`p.*` from properties after migrations (storage.py:638-655; processing_repo.py:301, 539-557, 869-885; delivery.py:30). The same function unconditionally reads `source_text_json` (added by migration 008) via text_from_property_row at 267, so a row lacking `floor_area_sqm` (migration 002) would already raise there. Coverage: 264-265 never executed.
- Recommendation: Read `row["floor_area_sqm"]`/`row["floor_area_source"]` directly.
- Prior item: B1 | Owner decision needed: no

### DB-12: price_changed_at card column is computed per card and never read
- Category: DEAD  | Confidence: CERTAIN | Action: DELETE | Risk: low | Effort: S | Size: ~4 lines (+1 TypedDict field)
- Location: src/home_finder/db/web_queries.py:75-77; row_mappers.py:83
- Evidence: whole-tree `git grep price_changed_at` (src, tests, templates, static JS, experiments, PR #77): only the SQL alias and the TypedDict field (plus ticket-10 design doc). Map JSON is built from a separate query (routes.py:470), so no whole-dict dump reaches JS. Each card row pays a correlated subquery for it.
- Recommendation: Drop the subquery and the field; keep `last_price_change` (read by _property_card.html).
- Prior item: A17 | Owner decision needed: no

### DB-13: Stale docstrings/comments naming renamed classes and wrong ownership
- Category: STALE-DOC  | Confidence: CERTAIN | Action: REWRITE-DOC | Risk: low | Effort: S | Size: ~15 lines
- Location: row_mappers.py:173-190; source_listing_ops.py:1; storage.py:61; CLAUDE.md "Database → db/storage.py … paginated queries with filters"
- Evidence: row_mappers docstring lists `PipelineRepo.get_unenriched_properties()`, `PipelineRepo.get_pending_analysis_retries()`, `WebQueryRepo.get_property_detail()` — the classes are `ProcessingRepository`/`WebQueryService`, the method is `get_pending_analysis_properties`, and `get_property_detail` never calls row_to_merged_property (web_queries.py imports no mapper for it); it also labels the JSON path "backward compat" although 4 of 5 production callers use it (only get_recent_properties_for_dedup passes source_listings) and omits delivery.discovery_snapshot. source_listing_ops.py:1 says "used by both storage and pipeline_repo" (no such module). storage.py:61 `# Re-export TypedDicts for backward compatibility` sits above `__all__ = ["PropertyStorage"]` and re-exports nothing. CLAUDE.md: paginated queries live in web_queries.WebQueryService.get_properties_paginated.
- Recommendation: Rewrite the row_mappers caller list (or delete it — it rots), fix the module docstring, delete the storage.py:61 comment, fix the CLAUDE.md pointer (docs agent may also own that line).
- Prior item: none | Owner decision needed: no

### DB-14: Unused TypedDict re-exports from home_finder.db
- Category: COMPAT  | Confidence: CERTAIN | Action: DELETE | Risk: low | Effort: S | Size: ~2 lines
- Location: src/home_finder/db/__init__.py:3,6
- Evidence: every `from home_finder.db import …` in src/tests/scripts/experiments imports only PropertyStorage, spend or backup (74 sites); PropertyListItem/PropertyDetailItem are imported only from row_mappers (web_queries.py:17-18). PR #77 does not import them.
- Recommendation: Reduce `__init__` to the PropertyStorage export.
- Prior item: A3 | Owner decision needed: no

### DB-15: property_images read SQL duplicated three times
- Category: DUP  | Confidence: HIGH | Action: MERGE (into image_ops.read_property_images(conn, uid)) | Risk: low | Effort: S | Size: ~25 lines
- Location: storage.py:982-1004 (get_property_images), 1052-1070 (get_property_images_and_row), delivery.py:35-41 (discovery_snapshot.images)
- Evidence: identical `SELECT source, url, image_type, floorplan_origin FROM property_images WHERE property_unique_id=? ORDER BY image_type, id` and PropertyImage construction in all three; get_property_images_and_row's docstring even says it "Combines get_property_images()". image_ops.py already hosts the write half (upsert_property_images).
- Recommendation: One conn-level reader in image_ops.py; storage.get_property_images wraps it in `_read_connection(conn=_conn)`, get_property_images_and_row and discovery_snapshot call it with their handle.
- Prior item: none | Owner decision needed: no

### DB-16: source_listings row tuple and properties INSERT boilerplate repeated five times
- Category: DUP  | Confidence: MEDIUM | Action: SIMPLIFY (shared row builder, keep per-site ON CONFLICT) | Risk: medium (merge/linkage semantics) | Effort: M | Size: ~80 lines
- Location: storage.py:1146-1185, 1209-1248; processing_repo.py:149-182, 249-283, 473-511 (and the `col_list/placeholders` INSERT boilerplate at processing_repo.py:125-147, 223-247, 451-471; storage.py:391-411, 866-894)
- Evidence: the same 16/17-value tuple (`prop.unique_id … prop.first_seen.isoformat(), now, merged_id`) is hand-written five times. The ON CONFLICT clauses differ deliberately (dropped/unenriched update only last_seen/merged_id; pre-analysis also price/title/coords; scrape-time also image_url) — those must not be unified.
- Recommendation: Add `source_listing_values(prop, now, merged_id)` (and optionally an `insert_property(conn, columns, values, on_conflict_sql)` helper) in source_listing_ops.py; leave each conflict policy at its call site. Do after DB-6 so the storage.py copies disappear first.
- Prior item: none | Owner decision needed: no

### DB-17: Redundant function-local imports and an impossible ImportError catch
- Category: HYGIENE  | Confidence: CERTAIN (imports) / HIGH (ImportError) | Action: SIMPLIFY | Risk: low | Effort: S | Size: ~6 lines
- Location: storage.py:339, 1375, 1676 (`from home_finder.filters.fit_score import FIT_SCORE_VERSION`); storage.py:1688 (`ImportError` in except tuple); spend.py:285 (`from home_finder.models.spend import RateCard`)
- Evidence: storage.py:41 already imports `compute_fit_score` from the same module at top level, so the three local imports avoid nothing; spend.py:14-24 already imports from home_finder.models.spend at top level. ruff PLC0415 flags all four. The lazy `from home_finder.analysis.analyzer import assess_value` (1657) is a deliberate decoupling (importing home_finder.db currently does not load the analysis stack — verified), but catching ImportError for an in-repo module only hides breakage.
- Recommendation: Hoist FIT_SCORE_VERSION and RateCard to top-level imports; drop ImportError from the except tuple. Better long-term: move pure `assess_value` out of analyzer.py (architecture review's "extract pure value/fit policy") and import it normally.
- Prior item: none | Owner decision needed: no

### DB-18: Runtime legacy-delivery classification is a no-op for production-written rows
- Category: OVERBUILT  | Confidence: MEDIUM | Action: ASK-OWNER | Risk: medium (notifications) | Effort: S | Size: ~20 lines
- Location: src/home_finder/db/delivery.py:172-189, 242
- Evidence: migration 012 (migrations.py:846-863, frozen in 71d69d6) already classified pre-outbox rows once. After v12, every production path to `pending`/`failed` creates a delivery row in the same transaction (complete_analysis→enqueue_discovery processing_repo.py:640-659; DeliveryRepository.fail requires an existing row), and every price drop enqueues `price:<id>` (storage.py:1638-1644). Older binaries refuse a newer schema (migrations.py:942-946). The only producers of rows this pass can match are the test-only writers in DB-1/DB-6.
- Recommendation: Keep as a cheap safety net or remove after DB-6; owner's call. If kept, add a comment that it is defence for restored/externally-edited databases.
- Prior item: none | Owner decision needed: yes

### DB-19: Legacy tolerant readers need a fixture-backed audit before removal
- Category: COMPAT  | Confidence: MEDIUM | Action: ASK-OWNER | Risk: medium (stored data) | Effort: M | Size: ~40 lines
- Location: row_mappers.py:224-242 (JSON fallback when sources/source_urls NULL; 229, 235 uncovered); row_mappers.py:124-130 (off_market_history decode tolerance; 125-128 uncovered); storage.py:1853-1855 (same, mark_returned_to_market); storage.py:1884-1960 (off-market source filter "legacy records without linkage"; 1952-1960 uncovered); detect_and_record_price_change except at 1688 (uncovered)
- Evidence: migration 004 Phase 1 backfills a canonical source_listings row for every property and 005 relinks by URL, so "no linkage" rows should not exist; but NULL `sources`/`source_urls` may exist in real retained rows and the September export is not available here. pipeline/commands.py:99-135 also falls back to source_urls for partially linked homes (not only legacy).
- Recommendation: Run a read-only query against the real DB (counts of NULL sources/source_urls, invalid off_market_history JSON, golden records with zero linked source_listings). Remove only branches with zero rows, adding a fixture for any kept branch.
- Prior item: B1 (OUTSTANDING) | Owner decision needed: yes

### DB-20: db.web_queries imports the web layer (fastapi) at runtime
- Category: HYGIENE  | Confidence: HIGH | Action: MOVE (ADDED_OPTIONS days table to a neutral module) | Risk: low | Effort: S | Size: ~5 lines
- Location: src/home_finder/db/web_queries.py:31, 279
- Evidence: `from home_finder.web.filters import ADDED_OPTIONS` (web/filters.py imports fastapi). Verified: `import home_finder.db` → `'fastapi' in sys.modules` is True. Only the `(value, label, days)` tuple is needed.
- Recommendation: Move ADDED_OPTIONS (or just the days map) to models/ and import it from both web/filters.py and web_queries.py.
- Prior item: none | Owner decision needed: no

### DB-21: Ticket/PR narration comments in DB modules
- Category: AI-ISM  | Confidence: LOW | Action: SIMPLIFY | Risk: low | Effort: S | Size: ~8 lines
- Location: storage.py:1462 "(Ticket 7)", 1531 "(Ticket 8)", 1565 "(Ticket 10)", 1752 "(Ticket 9)", 1615 "…projection revisions are PR08b."; row_mappers.py:79, 81
- Evidence: comments reference planning tickets/PR labels rather than behaviour; "PR08b" is not a term anywhere else in src.
- Recommendation: Drop ticket numbers from section headers and the PR08b sentence when touching these files.
- Prior item: none | Owner decision needed: no

---

## Prior items in this area
| Item | Status | Where |
| --- | --- | --- |
| Migration 012 calls a live delivery helper (freeze it) | DONE | 71d69d6 inlined frozen SQL at migrations.py:846-863; migrations.py imports only logging |
| A3 re-exports | OUTSTANDING (DB part) | DB-14 |
| A4 dead accessors | OUTSTANDING | DB-2, DB-3, DB-5 |
| A6 early-stop plumbing (DB half) | OUTSTANDING | DB-5 |
| A7 old senders (DB half) | OUTSTANDING | DB-1 |
| A14 mark_enriched | OUTSTANDING, precondition met | DB-4 |
| A15 test-only wrappers | OUTSTANDING | DB-8, DB-9 |
| A16 superseded storage methods / competing notification writers | PARTIAL — no production caller since ccf7b9a, code remains | DB-1, DB-6 |
| A17 dead branches/context | OUTSTANDING (DB part) | DB-12 |
| B1 legacy JSON readers | OUTSTANDING | DB-7, DB-11 (removable now), DB-19 (needs audit) |

## Verified keep (looks dead, isn't)
- delivery.record_legacy_deliveries vs migration 012 SQL duplication — intentional freeze (71d69d6); the runtime copy is a separate decision (DB-18).
- delivery.delivery_report `sqlite_master` check — needed by the read-only operator report on an unmigrated DB (pipeline/delivery_admin.py:17-26).
- backup.BackupManifest.safe_assets / consistent_metadata — pydantic field/model validators invoked by decorators.
- backup `model_config`, `schema_sha256`, `source_path`, `backup_sqlite_version` (vulture) — pydantic fields serialized into the manifest.
- backup._regular_files.failed (zero coverage) — os.walk `onerror` callback; uncovered only because no test injects an OSError.
- backup.inventory_database (test-only) — deliberate pure public oracle that tests use to compare restores with the original; keep (A15).
- backup.py:344 ARG001 `progress(status, remaining, total)` — signature required by sqlite3 backup progress callback.
- storage.py:182 `row_factory` (vulture) — aiosqlite attribute; mypy "unreachable" at 182/187/189 is a narrowing false positive (`self._conn` assigned inside the nested open_connection closure).
- storage.py:170 BLE001 — health probe deliberately treats any failure as a dead connection.
- row_mappers TypedDict keys flagged by vulture — all read by templates/routes (checked each in src/home_finder/web) except price_changed_at (DB-12); `quality_rating` is read by get_map_markers (web_queries.py:453) and tests.
- filter_new_merged / _get_seen_ids — production (pipeline/candidates.py:114).
- get_recent_properties_for_dedup(days=None) — used by pipeline/commands.py:262.
- save_property_images — used by update_merged_sources (storage.py:787) inside merges.
- viewing_messages / enquiry_log tables — still read by merge_safety._PROTECTED_CHILDREN, backup inventory and upgrade tests.
- price_drop_notified / notified_at columns — live: written by detect_and_record_price_change and DeliveryRepository._acknowledge, read by record_legacy_deliveries, merge_safety, enrichment_recovery.
- `SpendDeferred as SpendDeferred` in db/spend.py — raised in-module and imported from db.spend by tests/test_db/test_spend.py:15.

## Notes (cross-area leads)
- Notifiers (A7): old senders still in notifiers/telegram.py — send_property_notification :838, send_merged_property_notification :925, send_batch_notifications :988, send_price_drop_notification :1105. DB-1 removes their DB counterparts.
- Models: `TrackedProperty` (models/core.py:193, re-exported in models/__init__.py) is only constructed by storage's test-only readers (DB-9).
- Pipeline (A6): collection.py:48 `known_ids_by_source = None`, flow.py:54 "Compatibility flag"; tests/test_pipeline/test_orchestration.py:536-546 is vacuous.
- Scripts: scripts/backfill_wards.py duplicates pipeline/assessment._lookup_wards (get_properties_without_ward + update_wards).
- Docs: CLAUDE.md "Property card prop dict" lists `quality_rating`, but no template reads it.
- Possible correctness issue (not cleanup): storage.save_quality_analysis (1333-1342) logs and returns without writing on serialization failure, yet complete_analysis (processing_repo.py:637-659) still sets 'pending' and enqueues discovery for that home without an assessment row. Worth a look by the analysis/pipeline owner (relates to A8).
- Standalone (own-transaction) modes of update_merged_sources and save_quality_analysis are test-only; production always joins via `_conn` (absorb_staging_properties, ProcessingRepository). Low value; consider making them conn-level helpers when DB-6 lands.
- Inconsistency: `storage.runs`/`storage.deliveries` are public attributes while `spend`/`processing`/`web` are read-only properties over private attributes (storage.py:127-144).
- Untracked local work on Marcel's machine may still call save_merged_property / mark_notified; check before DB-1/DB-6 removals.
