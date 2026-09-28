# PR05a: Consistent backups, restore proof and database authority

**Date:** 28 September 2026. **State:** implemented and verified, including the full asset restore.
**Predecessor:** PR13b native spend enforcement, `58d538b`.
**Parent:** [PR05 specification](2026-09-28-research-synthesis-and-delivery-plan.md#pr05--establish-one-database-authority-and-retire-live-file-replacement-l).

## Decision and delivery boundary

Retain the **existing hosted SQLite store as the initial authority** and introduce a **new search episode within that retained store** when the observation/work contracts are ready. Keep a protected archive and its provenance. This is option (b) in PR05: it preserves existing property links and personal history without importing a different whole database or treating February inventory as current. The service's intended scale remains a gradual owner pilot followed by tens to hundreds of users. The 23.7 MB database provides no volume-based reason to require PostgreSQL at this stage.

Collection should be colocated with the authority if measured source access permits. If qualified acquisition must remain local, it must send bounded, authenticated, idempotent observations to the authority. PR06b owns that observation contract; no SQL/file-replacement endpoint or second authoritative writer is enabled here. SQLite WAL is a same-host contract, so sharing its files between hosts is not this architecture. [SQLite WAL constraints](https://www.sqlite.org/wal.html).

Historical properties remain accessible with their original status, prices, aliases, saved messages and suppression history. PR12b implements active/paused/dormant episodes and due work; PR07 handles ambiguous identity links. New observations establish current evidence. Starting an episode must not bulk-reset notification flags or automatically rebill all historical analyses. A new active database is unnecessary unless a later measured migration problem justifies it; the retained archive makes that choice reversible without deleting history.

This segment establishes backup/restore tooling and evidence, records the authority decision and retires the dangerous replacement command. It does **not** perform the production migration/cutover or implement the future observation interface. A code release, collector activation and user-visible episode switch remain explicit operational steps after their prerequisites are ready.

## Observed stores and writers

Read-only Fly API and SSH checks ran as the existing `appuser`; no service was restarted. Aggregate evidence is under `/tmp/home-finder-pr05/` and private capture/rehearsal artifacts are under `data/backup-rehearsal-2026-09-28/`.

| Store / component | Observed state | Treatment |
| --- | --- | --- |
| Hosted `/app/data/properties.db` | Schema 6; 23,683,072 bytes; 1,016 properties, 3,267 source listings, 1,001 analyses, 15,872 image references | Preserve as authority; migrate only after final backup and a write pause. |
| Hosted protected history | 106 price events, 30 status events, 201 aliases and one viewing message | Compare exact retained content, not only row counts or modification times. |
| Hosted image cache | 27,517 files, 2,139,308,595 bytes | Retrieve and reconcile individual file hashes separately from DB references. |
| Hosted process | One `--serve --no-pipeline` process in London | Dashboard writes remain possible; collection is disabled. |
| Hosted runtime | Python 3.14.3, SQLite 3.40.1 | PR03's repaired runtime is committed locally but has not been deployed. |
| Hosted volume | One attached encrypted 3 GB volume; automatic snapshots enabled; live retention **5 days** | Configuration drift: the repository requests 14 days. Do not claim that planned retention is active. |
| Frozen 27 September reference | Schema 6; known SHA-256 `e50897b46ebefea0119fd62951ac277d764684f9590d52801e66c964b1352c63`; no bundled cache | Preserve unchanged and use immutable read-only access. |
| Local `data/properties.db` | Schema 0; two properties, 1,035 image references, **1,016 foreign-key violations**; no image cache | Preserve in a separate development backup; do not import automatically or use as the hosted replacement. |
| Local scheduling | No crontab for the current user; no matching loaded/installed user or system service/timer; no matching application process after excluding the Codex sandbox | No local collector was found in this inspection. Other machines, unnamed wrappers and root-owned cron require the final operator inventory. |

The existing manual release workflow deploys the dashboard image and retains the mounted volume. `scripts/sync-to-fly.sh` now exits with status 2 for **every invocation**, including `--full` and `--force`, before contacting Fly or touching any file. Its active DB/WAL/SHM removal, upload, image-directory shortcuts and machine restart have been removed. Installed copies on other machines must still be retired during cutover; a repository edit cannot fence an uninspected old script.

## Backup and restore contract

[Database backup implementation](../../src/home_finder/db/backup.py) uses Python's SQLite online backup API against an explicit read-only source connection. A read transaction pins one consistent snapshot while unrelated WAL writers can continue. It neither checkpoints nor initializes/migrates the source. The backup uses a standalone destination DB with no required WAL companion. This avoids copying only the main file while committed data still resides in its WAL. [SQLite backup API](https://sqlite.org/backup.html), [Python `Connection.backup`](https://docs.python.org/3.14/library/sqlite3.html#sqlite3.Connection.backup).

The private package contains `properties.db`, optional `image_cache/`, and a completion `manifest.json`. Directories are created with mode 0700 and files with mode 0600. Every destination must be new; existing directories, including an interrupted earlier attempt, are rejected. Restore cannot target a child of its input package. Snapshot destinations cannot be nested inside their source asset tree. No restore operation overwrites a live authority.

The manifest records the source/mode, capture window, SQLite version used for backup, schema/version fingerprint, per-table row counts and duplicate-preserving content hashes, foreign-key violation count, DB checksum and per-file asset checksums. Values are type-sensitive, including blobs and floats; row order does not affect logical comparison. Raw notes, model responses and row values are not written into the manifest, but the database and file paths remain private data. Protect the whole package; checksums detect mismatch against this retained manifest, not malicious replacement of both data and manifest.

The DB must pass its integrity check. Existing foreign-key violations are recorded rather than silently repaired or discarded, so a preserved development/forensic backup can remain an invalid candidate for deployment. Verification recomputes bytes, schema and table inventories. It rejects missing/extra files, changed content, symlinks, special files and unsafe manifest paths. Copying reads files back to check the destination. Creation checks the asset inventory again before completion so additions, deletions or modifications during the capture fail instead of appearing complete.

The manifest is written and synced last, after files and directories. A killed process can leave an incomplete private directory, which verification rejects. Handled failures remove only the newly owned output directory. A deadline and explicit resource bounds limit work: 300 seconds by default, configurable up to 3,600 seconds; 8 GiB DB, 20 GiB assets, 100,000 asset files, 1,000,000 rows per table and a 64 MiB manifest read limit. Larger inventories require an intentional reviewed change to those bounds.

Assets and SQLite do not share a transaction. `copied_over_capture_window` means the selected files were individually checked and their inventory remained stable during copying; it does not certify that every historical DB image reference exists, or that every file reflects the exact database snapshot instant. The final cutover must fence asset writers and reconcile references. PR14 will strengthen content-addressed evidence and lifecycle rules. Omitting `--assets` records **`not_included`**, never an implied complete asset backup.

## Operator commands

Run these on a host with direct access to the chosen SQLite files. Do not mount a remote WAL database over a network filesystem. The commands load neither `.env` nor provider credentials and never start processing.

```sh
# The parent must exist. Each destination below must not exist yet.
uv run python -m home_finder.pipeline.backup create \
  --database /path/to/properties.db \
  --assets /path/to/image_cache \
  --destination /private/backups/capture-2026-09-28

uv run python -m home_finder.pipeline.backup verify \
  --package /private/backups/capture-2026-09-28

uv run python -m home_finder.pipeline.backup restore \
  --package /private/backups/capture-2026-09-28 \
  --destination /private/rehearsals/restore-2026-09-28
```

For a genuinely frozen offline DB export, add `--frozen` to creation. This opens `mode=ro&immutable=1`, rejects nonempty WAL/journal sidecars and checks that the original DB hash remains unchanged. Never use that flag to hide the WAL of a running database. Missing input paths fail instead of creating an empty application database.

The command emits aggregate JSON, including asset inclusion state and foreign-key violations. A successful snapshot is not permission to deploy it. To test migrations, make a separate restored working copy, retain/rename its origin manifest, then explicitly initialize storage there. A migrated working copy is no longer byte-identical to the original backup package and must not masquerade as one.

## Rehearsal evidence

The supplied frozen database was snapshotted, verified and restored exactly. A second isolated restored copy was initialized through schema 6 → 9. All original application columns and rows, including all protected histories and `sqlite_sequence`, remain identical. SQLite refreshed `sqlite_stat1` and `sqlite_stat4` while applying connection optimization; those internal query-planner statistics are recorded as expected changes. Integrity and foreign-key checks pass. The original frozen file retains its known SHA-256.

A fresh read-only Fly acquisition used an in-memory SQLite backup and streamed the current cache over the existing authenticated SSH connection to a private local tar file. It performed no source database/file writes and ran no application initialization. The database and each regular asset have capture-side hashes; the asset identity/size/mtime inventory is checked before and after transfer. The receipt describes a database snapshot and a separately stable asset capture window. It is not an atomic DB/filesystem snapshot or source-access canary. No listing portal, model API or Telegram send was invoked.

The fresh acquisition ran **20:30:43–20:36:17 UTC** on 28 September. Its 2,188,308,480-byte archive has SHA-256 `a80c761d7aa051304f8a375f7f28683185ade48d06e5cc1a0e2b3a2b0b06031a`. Every table and the schema in the fresh database match the frozen reference exactly, including all protected user history; this comparison finds no intervening row conflict between those two snapshots. The normalized standalone backup DB has SHA-256 `e1494adf0f5b174cd9a2e142f50aa25b8ffd5a85ba3f4d66383a3e8762cea639`; raw file hashes can differ when SQLite serializes/normalizes a snapshot, so the logical comparison is recorded separately.

The fresh package was verified, restored into another new local directory, then verified again. **All 27,517 asset files and 2,139,308,595 asset bytes match the capture-side hashes.** The restored database inventory is exact, with zero foreign-key violations. Private receipts are `fresh-rehearsal.json` and `reference-rehearsal.json` under `data/backup-rehearsal-2026-09-28/`. The raw archive, database, acquisition manifest and photographs remain git-ignored private artifacts, not committed fixtures. These are tested local/off-host copies; scheduled backup retention and ongoing restore drills still require operational setup.

An additional audit uses the application's existing canonical-directory/role/URL-hash lookup against the restored cache. It matches **11,545 of 15,872 image references**: 10,979 gallery and 566 floorplan references. **4,327 references across 294 properties have no matching local file**: 4,103 gallery, 214 floorplan and 10 EPC references. One reference matches multiple filenames. All captured files restored correctly; reference counts also include inputs that may never have been selected/cached. This measures retained availability, not when or why an image became absent, nor exact historical model inputs. PR14 must distinguish referenced, actually retained and selected material; PR19 must consume verified existing bytes. No image refetch was performed.

The automated suite passes **2,857 tests on both Python 3.11.16 and 3.14.7**, with **87.99% / 87.90%** combined line/branch coverage. The 34 new cases cover committed WAL changes, edits during backup, exact histories and assets, private permissions, immutable export protection, unsafe manifests, changed files, bounded resources, existing destinations, a real subprocess exit before the completion marker, the CLI round trip and all retired sync modes. Ruff lint/format (207 files), strict mypy (87 source files) and `git diff --check` pass. Logs are `/tmp/home-finder-pr05/backup-*`.

## Remaining authority cutover

1. Complete O1 access/release setup and qualify collector placement/source access. Keep collection, inference and notifications disabled during preparation.
2. Inventory/fence all actual dashboard, webhook, collector, maintenance, sync and asset writers. An application runner lock alone does not stop dashboard status writes or an old remote copy script.
3. Take final consistent DB and asset snapshots of every relevant store during a short write pause. Compare records and origins explicitly; preserve unresolved differences. The archived local development DB is not a replacement candidate.
4. Verify restore and migration against those final inputs, reconcile asset references and retain source manifests. Configure and verify the desired snapshot retention plus a separately verified off-host backup. A volume setting is not a restore proof.
5. Deploy the compatible runtime/application against the retained authoritative volume. Introduce the dormant historical episode and new active episode only with PR06b/12b identity/evidence/work semantics ready. Fence obsolete writers before admitting observations.
6. Verify protected histories, legacy links, status edits, alias resolution and discovery suppression. Enable only the qualified collection subset first; inference and delivery retain their later gates.

Rollback requires another write pause and preservation/replay of post-cutover events. Keep the new observations and paid ledger even if processing is disabled. Restoring an earlier file over newer dashboard history is not a rollback procedure.

**Next eligible work:** PR05's observation-ingestion/cutover preparation can proceed with the retained hosted-authority decision. Actual activation awaits its contracts and operator gates. The recovered cache also makes a blind PR19 annotation pack feasible; model output is not a human reference and must not supply the labels. No whole-plan or production-readiness completion is claimed here.
