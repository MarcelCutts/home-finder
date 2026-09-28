# PR12a: Recover existing work and exclude overlapping runners

**Date:** 28 September 2026. **State:** implemented and verified; not deployed.
**Parent:** [delivery plan, PR12](2026-09-28-research-synthesis-and-delivery-plan.md#pr12--drain-persisted-work-independently-of-discovery-l).
**Predecessor:** PR09a, `67a34a9`. No schema migration, scheduler activation, deployment or external provider effects.

## Discovery-independent recovery

The isolated baseline (`/tmp/home-finder-pr12a/baseline.json`) recorded zero enrichment, initial-analysis and reanalysis drains when discovery returned no properties. A mocked missing-cache result was counted as one processed analysis, invoked one delivery callback and changed the retained item from `pending_analysis` to `pending`.

The pre-analysis stage now continues to load eligible enrichment retries after empty discovery or after all new observations fail criteria/location filtering. Existing retries remain subject to their enrichment attempt limit and the normal dedup/floorplan/commute policy. Recovery takes at most `MAX_ANALYSIS_PER_RUN` enrichment items per invocation. The pipeline invokes initial-analysis recovery, reanalysis and price-drop delivery even when the pre-analysis stage has no new output. Existing unsent-notification recovery still runs before discovery.

Initial analysis alternates older pending items and fresh discoveries before applying its property cap, starting with recovery. This prevents fresh arrivals consuming every slot at normal multi-item caps. Pending initial work also uses the existing missing-image downloader before analysis, bounded to admitted retry items. A completely empty analysis drain performs no ward lookup, cache download or model-client construction. Standalone reanalysis checks model configuration before downloading evidence and no longer logs an unsupported fixed six-cent cost estimate.

## Outcomes and retained state

The concurrent completion callback now distinguishes completed, deferred and failed results. Its legacy `None` callback return remains a completion for compatibility; production callbacks supply an explicit disposition where processing did not complete. Actual cancellation still propagates, with the existing child-task cleanup and PR09a run accounting.

| Outcome | Initial analysis | Reanalysis |
| --- | --- | --- |
| Valid rated analysis | Save result and allow the normal delivery callback | Replace the prior result and clear the request; preserve sent status |
| Quality explicitly disabled | Preserve the intentional analysis-skip behaviour | Leave requests pending; no model/evidence admission |
| Missing cached images (`analysis is None`) | Retain `pending_analysis`, record a deferred reason, do not count a completed analysis or invoke delivery; no model-failure attempt consumed | Keep the request and previous result |
| Incomplete/minimal fallback (`overall_rating is None`) | Retain pending state, consume a failure attempt, do not deliver or count completion | Keep the request and previous result; report failure |
| Initial model exception or per-property timeout | Consume a failure attempt and retain pending state | Existing exception/circuit-breaker handling leaves the request intact |

Initial automatic retries stop when `analysis_attempts` reaches three, including prior retained attempts. Exhausted rows remain in storage with pending status but are excluded from automatic retry selection. Missing-cache deferrals do not spend those failure attempts. Those counters are **application attempts, not a provider billing ledger**. Process cancellation and SDK-internal retries still need PR13's complete attempt accounting.

Old fallback records that were already notified now enter reanalysis without deleting the prior result or changing `sent` to `pending_analysis`. Repeating the recovery request preserves its original request timestamp. Undelivered legacy fallbacks can still use the existing initial-analysis reset path. This prevents recovery itself creating another discovery alert. The notification callback also stops marking a home sent when it skipped delivery for lack of images; a sent state now requires actual delivery on that path. The separate legacy unsent retry path can still send its plain-property message; PR15 will unify notification intents/content.

Reanalysis and enrichment recovery each have their own property cap. Initial analysis retains its combined retry/discovery cap. These are **separate limits**, so a run can admit up to the configured initial cap plus the reanalysis cap. They do not bound dollars, image calls per home, SDK retries or all network requests. The existing initial-only cost summary remains incomplete; normal paid-processing activation must still wait for PR13/14. This slice does not claim a shared global budget.

## One runner and one scheduling policy

`pipeline/runner.py` supplies a nonblocking advisory `flock` on `<resolved database path>.runner.lock`. It is acquired before storage initialization or provider work and held until the operation and cleanup finish. Every public pipeline, scrape-only, reanalysis, commute-backfill, off-market-check and dedup entry point participates; CLI thumbnail maintenance uses the same guard. Serve mode calls the guarded pipeline and keeps its existing in-process lock. A provided storage object's actual path determines its authority; `:memory:` storage uses ownership of that instance.

An overlapping invocation returns `RunSkipped.ALREADY_RUNNING` and logs `runner_skipped` without creating another run or touching providers. CLI callers exit normally after logging the skip. Relative/symlink aliases share the resolved path; existing hard-linked database aliases fail explicitly. Unexpected lock errors fail rather than admit work. Lock files are private, opened without following a lock-file symlink, and excluded from Git. **Never unlink a lock file to recover from a crash**: the OS releases the descriptor lock when the process dies, while unlinking a live file can create competing lock inodes.

This contract covers a single Unix host and local SQLite authority, including separate participating CLI/cron/server processes. It does not coordinate copied databases, different hosts, network filesystems, legacy sync scripts or external code that ignores the guard. Dashboard mutations continue using PR04 transaction ownership; they are not blocked for an entire network/model run. PR05 must still establish one authority, and PR12b must still implement durable claims/generations and stale-worker rejection.

The chosen scheduling policy waits **at least the configured interval after completion**, then adds zero to five minutes of jitter. Serve mode retains its 30-second startup delay. The README systemd alternative uses `OnUnitInactiveSec=55min`, `RandomizedDelaySec=5min` and one-second timer accuracy, with an initial boot delay. A two-hour run therefore finishes before the next 55–60-minute wait starts; clock-hour/day boundaries do not produce catch-up bursts. The old `*/55` crontab is removed and replaced by instructions with no active entries. Only one of the timer and serve scheduling loop should be enabled. No installed crontab/service or Fly process was changed.

## Primary-source verification

Checked 28 September 2026:

- [Python `fcntl`](https://docs.python.org/3.14/library/fcntl.html) documents Unix availability, exclusive/nonblocking locks and contention errors. The implementation checks the documented contention errnos and propagates other errors.
- [Linux `flock(2)`](https://man7.org/linux/man-pages/man2/flock.2.html) documents open-file-description ownership, separate-descriptor contention even in one process, advisory enforcement and release when descriptors close. Its network-filesystem qualifications are why this is explicitly a local-authority guard, not a distributed lease.
- [Upstream systemd timer specification](https://github.com/systemd/systemd/blob/main/man/systemd.timer.xml) defines `OnUnitInactiveSec` relative to deactivation, using monotonic timers independent of wall-clock time/timezone. `RandomizedDelaySec` adds delay; timer accuracy can add small scheduling tolerance. The hosted HTML manual returned 403 during verification; the upstream source supplies the documented contract.

## Verification and remaining work

- Focused recovery, orchestration, reanalysis, storage, scheduler and HTTP suite: **206 passed**, three existing deprecation warnings.
- Tests exercise zero-discovery pending analysis/reanalysis/price delivery, enrichment after empty/all-filtered search, missing-cache deferral then recovery, three-attempt exception/incomplete/timeout bounds, retained old reanalysis results, no duplicate fallback notification, and shared initial retry/discovery slots.
- Real child processes test mutual exclusion and admission after killing the lock holder. Other tests cover resolved aliases, rejected hard links, unexpected lock errors, all guarded entry points, provided-storage ownership, cancellation release, and long runs crossing hour/day boundaries with minimum/maximum jitter.
- The first full matrix exposed an off-market integration helper passing an untyped `MagicMock` as settings, plus one assertion classifying missing evidence as failure. The helper now passes real in-memory `Settings`, preserving all availability assertions; its ten tests pass. The outcome assertion now requires deferred=1, failed=0 and preservation of the prior rating/request.
- Final full Python 3.11.16 suite: **2,583 passed**, 87.47% branch-inclusive coverage; four existing deprecation warnings.
- Final full Python 3.14.7 suite: **2,583 passed**, 87.37% branch-inclusive coverage; the same four warnings.
- Ruff lint/format, strict mypy and diff checks passed (183 formatted Python files, 73 source files).
- No templates/assets changed. Browser rendering has no changed flow; default HTTP/lifespan tests cover the scheduler integration.
- Frozen reference SHA-256 remains `e50897b46ebefea0119fd62951ac277d764684f9590d52801e66c964b1352c63`; no reference row or schema was changed. Logs are under `/tmp/home-finder-pr12a/`.

The read-only reference audit found **25 sent fallback analyses**, one pending enrichment item, no pending initial analyses and no requested reanalyses. Thus the no-renotification repair addresses a concrete retained cohort, and the new initial-attempt cap excludes no pending item in this snapshot. These are frozen historical counts, not current production state; no recovery or migration was run against the reference.

The remaining work is intentional: durable per-item intent keys, due-time backoff, recovery rotation when old missing-evidence items fill a queue, per-request reanalysis attempt limits, explicit retry of exhausted items, claim expiry/generation fencing, and active/paused/dormant search policy belong to PR12b. A one-item analysis cap always starts with the oldest retry; it does not establish general fair scheduling. Existing acquisition/enrichment/notification/network bounds are not replaced by a global admission contract here. PR13 records/reserves every paid attempt, PR14 qualifies artifacts, and PR15 records delivery outcomes through an outbox. Do not present this slice as completion of PR12b or those later contracts.

There is no schema change; the PR06a schema-8 backup remains compatible. A rollback must retain both process exclusion and missing-evidence deferral, or keep all runners disabled. Do not erase pending work or previous analysis to recover an operational failure.

**Next: PR18a tooling and isolated capture**, using qualified source access or appropriate supplied fixtures. Actual access/content scope is not established by successful unit tests. Complete offline tooling while that input is unresolved; do not substitute `--dry-run` for collection-only capture or claim an elapsed canary from a committed harness.
