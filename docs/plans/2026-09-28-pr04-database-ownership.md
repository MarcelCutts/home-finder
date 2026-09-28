# PR04: Complete SQLite operation ownership

**Date:** 28 September 2026. **State:** implemented and verified for local commit; not deployed.
**Parent:** [delivery plan, PR04](2026-09-28-research-synthesis-and-delivery-plan.md#pr04--give-every-database-write-an-explicit-unit-of-work-m).
**Predecessor:** PR03, commit `8bb73cc`. No deployment, collection, paid calls or notifications.

## Behaviour and scope

Every storage, pipeline-repository and dashboard database operation now owns the shared connection for its complete work, including reads, fetches, nested image/analysis helpers and transaction completion. Previously a standalone `commit()` could commit another task's incomplete price/status update. A lock around the existing transaction helper alone would leave those paths exposed.

The initial implementation deliberately serializes short database operations in one `PropertyStorage` instance. Read scopes use a read-only SQLite snapshot; an unrelated reader waits for the owner and cannot see its uncommitted changes. This works for both file databases and `:memory:` fixtures without a pool or shared-cache special case. Network, model and Telegram work stays outside these scopes. The temporary HTTP-wide mutation lock is removed; unrelated HTTP work can proceed while storage provides the actual correctness boundary.

This matches the distinction in [SQLite's isolation documentation](https://www.sqlite.org/isolation.html): operations sharing a connection do not get the isolation provided between separate connections. [aiosqlite](https://aiosqlite.omnilib.dev/en/stable/) queues individual operations on a worker thread; that queue does not establish an application transaction owner.

Nested helpers receive an explicit connection handle, replacing `_commit=False`. The current task must own that handle; child tasks cannot inherit ownership, read scopes cannot become writes, and accidental nested scopes raise promptly instead of deadlocking. Joined helpers do not begin, commit or create savepoints. Their exceptions must propagate to the outer transaction when the complete operation should roll back. `get_property(..., _conn=conn)` and image reads support explicit read-after-write within the owner's transaction.

Cancellation is handled from `BEGIN` through cleanup. The owner waits for commit/rollback/close to finish before another operation can use the connection, including under repeated cancellation. A cancelled await does not remove already queued SQLite work. Once commit starts it may complete despite cancellation; the caller receives cancellation only after the result is settled, and must not blindly retry a potentially committed operation. Cancellation before commit rolls back the write. This is atomic completion, not an exactly-once external-effects guarantee.

Connection creation/configuration, migrations/backfill, health probes and closure share the same ownership boundary. Failed rollback closes the old connection before any replacement. Failed closure prevents further use until cleanup succeeds. The former health-check behaviour that abandoned a possibly live worker is removed. A truly stuck worker can therefore delay shutdown; restarting/fencing that process is safer than silently creating a second owner. SQLite's existing five-second busy timeout remains; an external lock failure rolls back and returns the error without replaying application work automatically.

Debug event `db_scope_finished` records operation kind, wait time and duration in milliseconds without SQL, credentials or property contents. Inspect these timings during the pilot before considering separate read connections. Raw `_get_connection()` remains private for isolated fixture setup; application repositories use only ownership scopes. Future helpers must follow that rule and explicitly pass handles when composing database work.

## Reproduction and verification

Before the fix, controlled interleaving reproduced an uncommitted £1,700 price visible to an unrelated reader and persisted by the notification writer, despite the price operation failing. Two simultaneous status transactions raised `cannot start a transaction within a transaction`. Cancelling a price operation also left its price committed through the unrelated writer. These are now regression tests asserting the intended outcome.

| Check | Result |
|---|---|
| Ownership regressions | **18 passed**: storage/dashboard reads, notification and pipeline interleaving, concurrent statuses, explicit handles, child/nested rejection, read-only scopes, cancelled waiters/BEGIN/commit/rollback, repeated cancellation, commit/rollback/close failure, busy timeout/manual retry, partial connection setup and shutdown |
| Database, pipeline, integration and access subset before expanded failure checks | **488 passed**, four existing deprecation warnings |
| Chromium dashboard suite | **55 passed**, two existing deprecation warnings |
| Ruff lint/format and strict mypy | Passed: 166 formatted Python files; 64 source files checked |
| Full suite, Python 3.11.16 / SQLite 3.53.1 | **2,466 passed**, four existing warnings, **85.58%** coverage, 217.47 seconds |
| Full suite, Python 3.14.7 / SQLite 3.53.1 | **2,466 passed**, four existing warnings, **85.49%** coverage, 239.95 seconds |
| Isolated reference startup/restore | Passed; all 13 tables' original columns preserved, integrity and foreign-key checks passed |

Reference SHA-256 remains `e50897b46ebefea0119fd62951ac277d764684f9590d52801e66c964b1352c63`. A consistent read-only SQLite backup was copied into a temporary directory; the reference was never initialized. Preserved counts include 1,016 properties, 3,267 source rows, 1,001 analyses, 106 price events, 30 status events and one saved viewing message. PR04 adds no migration; the reference copy reaches the already-established schema 7.

Local evidence is under `/tmp/home-finder-pr04/`. On disposable copies, 50 sequential dashboard queries had median/p95 **1.24/1.49 ms** on PR03 and **1.55/2.03 ms** on PR04. A PR04 burst of 100 concurrent operations, split evenly between dashboard queries and pipeline run create/complete writes, completed in **89.83 ms**, with **87.45 ms** p95 per operation. This is a small warm-cache local comparison under concurrent test load, not an HTTP benchmark, capacity guarantee, multi-process qualification or substitute for a canary.

## Rollout, recovery and next segment

Keep one writer process during the initial rollout. SQLite handles file-level exclusion, but this segment does not qualify independent scheduled collectors, deployment fencing, background work claims or multi-user throughput. Those remain PR05/12/25–28. Preserve backups and verify ownership timing/error rates when the protected release is deployed.

There is no schema rollback. Returning to PR03 preserves schema compatibility but reintroduces the concurrency defect, so use a protected corrected build as the recovery point for an active service. Do not restore an older database merely to roll back this code.

**Next: PR06a**, retain fetched descriptions/features through persistence, reload and the actual analysis request, with explicit missing/correction handling. Its broader immutable observation system remains PR06b. Continue verified commits under the owner's latest instruction; this segment does not complete the full delivery goal.
