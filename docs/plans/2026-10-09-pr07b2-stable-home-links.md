# PR07b-2: stable home identities and durable links

**Date:** 9 October 2026. **Baseline:** `08e3145`. **Parent:** [PR07](2026-09-28-research-synthesis-and-delivery-plan.md#pr07--reconcile-provider-identity-and-make-canonical-merges-reversible-l). This continues [exact provider identity](2026-09-29-pr07b1-provider-identities.md); it does not complete reviewed membership or merge/split reversal.

An advertisement ID identifies one portal listing. A saved home's identity must survive a change of portal anchor and fit into web links and Telegram callbacks. Previously the application used the raw canonical source ID throughout those interfaces. Long IDs lost their status buttons, and retirement of an empty staging record left its old links without a target.

## Delivered contract

Migration **012** assigns each existing canonical property an independently generated UUID4, represented as `h_` plus 32 lowercase hexadecimal characters. It retains the original property IDs and all original application rows. `homes` separates the permanent ID and original canonical ID from its current storage-row binding. `home_aliases` preserves canonical and historical alias claims. Multiple claims remain representable; resolution refuses an alias that reaches different homes. It does not pick an owner using address, photos, price or provider-ID agreement.

The initial mapping preserves each existing canonical home, including the 28 homes involved in the 17 provider-ownership conflicts. Those conflicts are still awaiting review. The 201 retained `source_aliases` rows are imported as historical link evidence to their recorded anchors, rather than being inferred anew from source URLs. The old table remains unchanged.

All five canonical-save paths register the home inside their existing write transaction: individual save, merged save, enrichment retry, floorplan-dropped save and pre-analysis save. A failure in identity registration rolls back the associated property/source write. Source-only scraping does not create a home. Repeated saves and restarts preserve IDs. A separately deleted unused staging row retains its identity and aliases as an unresolved record; recreating that same row restores its original ID. Ordinary saves cannot revive a redirected home or take over an existing alias.

`storage.homes.resolve(identifier)` accepts a stable ID or an exact stored legacy alias and returns the current `HomeTarget(home_id, property_id)`, or `None` when resolution is unavailable or ambiguous. Redirect chains have cycle detection. Identity fields, alias evidence and staging redirects reject updates/deletion at the schema boundary; the storage-row binding is a projection for the next membership slice to manage.

The existing PR07a admission guard still controls automatic staging absorption. In its owning transaction, a successful absorption now retains the retired staging home's ID and records a redirect to the surviving home. An unpromoted source instead receives an exact source-ID alias. The original source rows/evidence remain preserved. Repeated absorption and chained staging absorptions keep issued links usable. Injected retirement failures roll back redirects together with membership and property changes. Established homes still fail the preservation guard.

## Web and Telegram behavior

Cards, detail-page status controls, map navigation and detail metadata use the stable home link. Existing raw canonical links and imported legacy aliases continue to resolve. The map's internal record ID and cached-image namespace remain the original storage ID. Detail loading resolves the home first, then uses the actual stored property ID for images, price history and status history.

Web status changes, Telegram status changes and explicit reanalysis requests resolve their target **inside the transaction that performs the mutation**. Equivalent aliases in a reanalysis batch are deduplicated. This avoids resolving an alias outside the write lock and later acting on a stale target. Telegram's configured secret, owner/chat checks, callback grammar and update-ID replay protection remain enforced.

The live notifier receives the storage resolver. New property notifications and price-drop detail links use the persisted home ID; a missing or stale identity prevents dispatch. A stable status callback such as `st:h_<32 hex digits>:interested` is 48 ASCII bytes. The existing exact `st:portal:id:status` format remains accepted for already-delivered messages. Standalone notifier callers without storage retain the previous legacy behavior and never truncate an ID.

This is link compatibility, not a new authorization mechanism. The short ID does not bypass the dashboard or webhook access checks. No notification was sent in this implementation or its verification; bot interactions were mocked.

## Observability

`home_identity_prepared` identifies new IDs; `home_identity_backfill_prepared` reports migration counts; `home_links_prepared` reports the destination home and absorbed count. These are transaction-preparation events, not commit acknowledgements. Existing database-scope and canonical-merge events supply the surrounding commit/failure evidence. `home_resolution_refused` distinguishes an ambiguous alias from a redirect cycle without logging raw URL-shaped aliases.

Private maps retain the full legacy-ID to home-ID correspondence for local review. They must not be published with this handoff. No source URL, credential, personal note or database copy is added to public verification artifacts.

## Isolated migration and replay evidence

The rehearsal created new copies of the original historical backup and the 50-listing run database. External sockets were blocked. It compared every original application table using its original columns, compared every post-migration table on a second initialization, checked every canonical link and retained legacy alias, and verified the original input hashes after completion.

| Check | Historical copy | Recent batch copy |
| --- | ---: | ---: |
| Starting schema | 6 | 10 |
| Resulting schema | 12 | 12 |
| Saved homes / stable IDs | 1,016 | 35 |
| Canonical links verified | 1,016 | 35 |
| Historical aliases verified | 201 | 0 |
| Total alias claims | 1,217 | 35 |
| Migration-created redirects | 0 | 0 |
| Provider-ownership conflicts retained | 17 | 0 |
| Original application rows preserved | all | all |
| Rows changed on second initialization | 0 | 0 |
| Foreign-key errors | 0 | 0 |
| Integrity | `ok` | `ok` |

The new home IDs are allocated once in each authoritative database. Independently migrated copies need not generate identical UUIDs; a database backup/restore and repeated initialization preserve its recorded IDs. The rehearsal verified restart stability, rather than comparing randomly generated IDs across unrelated copies.

The 50-listing offline replay again produced **35 homes with all 50 selected memberships** in both single-run and split-run scenarios. Known inputs produced zero new work. All original database, event, report and image hashes were unchanged; no model attempt occurred. The input database contains 55 scraped source rows, including five not selected for the live experiment.

Private evidence locations:

- `data/home-identity-2026-10-09/verification.json`: aggregate migration results.
- `historical/verification.json` and `fifty/verification.json` under that directory: full table counts and content hashes; each case also contains its copied database and private `home-map.json`.
- `data/home-identity-2026-10-09/dedup-replay/report.json`: real same-run/split-run replay and unchanged-artifact checks.
- `/tmp/home-finder-2026-10-09/rehearse_homes.py`, `home-rehearsal.log` and `home-dedup-replay.log`: reproduction and diagnostics.

Original database hashes remain `e1494adf0f5b174cd9a2e142f50aa25b8ffd5a85ba3f4d66383a3e8762cea639` (historical) and `e2bdf0073f39067b85a8f1deb707b828cd369f45c9244a9c0360aed2f0934fa7` (recent batch).

## Verification and review

The initial focused preservation/routing set passed **348 tests**. New coverage includes each save entry point, failure rollback, stable identity restoration, retained redirects, chained/repeated staging absorption, rejected revival, unresolved source-only rows, ambiguous aliases, cycles and additive migration. The nine new route/notifier cases exercise long numeric and URL-shaped raw IDs, rendered stable status actions, the original image/history context, owner checks, update-ID deduplication and price-drop links that refuse stale targets. The old observation-migration fixture explicitly simulates its version-9 writer; the runtime does not skip missing identity metadata.

Final full network-blocked suites on **9–10 October** pass **3,259 tests each**: Python **3.14.7** with **88.32%** branch-inclusive coverage, and Python **3.11.16** with **88.40%**. Logs and coverage XML are `/tmp/home-finder-2026-10-09/homes-final{314,311}.{log,xml}`. Ruff lint and format checks pass for **246 files**; strict mypy passes for **107 source files**. Workflow lint and diff whitespace checks pass. No application code changed during or after these final runs.

The first full matrix found three fixture failures. A legacy quality-migration fixture used the current writer before constructing current tables; it now seeds its actual historical columns directly. Two concurrency interceptors replaced aiosqlite's dual await/context-manager interface with a coroutine; they now preserve the original interface while retaining their injected failure and rollback assertions. The repaired fixtures, new identity tests and route tests passed an **85-case** focused rerun before the final full matrix. The production transaction and migration contracts were not weakened to satisfy these fixtures.

Review concentrated on unintended historical reassignment, a saved link's destination changing between read and mutation, redirect rollback, lost image/history lookups, oversized callbacks, and accidental weakening of owner authorization. Historical owner conflicts remain separate. Source provider keys and home UUIDs are distinct namespaces. No fuzzy matching policy changes are included.

## Remaining work and rollout

**Next implementation: PR07b-3, reversible membership decisions.** Define versioned, append-only decisions with actor/reason/evidence, expected prior membership and explicit reversal. The new stable IDs and original aliases must survive those operations. Preserve personal-state provenance across merge and split; do not overwrite incompatible statuses. Staging redirects here are retained evidence of the old guarded operation, not the future general-purpose reversal API. Introduce the reviewed decision projection without deleting that evidence.

PR07b-4 still needs complete membership across portal-keyed text/price/image projections and reviewed reconciliation, before PR08b/09b. This slice does not introduce an anchor-change command, reconcile the 17 conflicts, or permit established-home absorption. Stable identity is now separate from the current storage binding; the next contract must journal any deliberate reassociation of that binding.

The owner additionally requested a reviewed series of stacked draft PRs for the accumulated work. After this segment's verification and commit, partition the existing commits by dependency and behavior, verify each boundary, and document each PR's rationale and limitations. Preserve the original branch and all user-owned local edits. Remote `main` was verified as `a86e51e9ef3e6f767ed307bcae5cbce5087d3cea`; existing unrelated Dependabot PRs must remain untouched. The full hosted-service goal remains active.

Migration rehearsal affected disposable local copies only. A rollout still requires the existing backup/deployment gate. Older schema-11 binaries correctly reject a schema-12 database; disable new behavior only with compatible migration-aware code. Do not lower `user_version`, delete identity evidence, or restore an older database over newer user actions. No production release, live collection, paid request or Telegram dispatch was performed.

## External checks

Telegram documents the [1–64-byte inline callback limit](https://core.telegram.org/bots/api#inlinekeyboardbutton). Python documents [UUID4 generation](https://docs.python.org/3.14/library/uuid.html#uuid.uuid4); persistence and uniqueness constraints, rather than recomputing IDs from portal strings, make these identities stable. SQLite's [foreign-key behavior](https://www.sqlite.org/foreignkeys.html) supports retaining the home record while clearing its deleted storage-row binding. Those references support the implementation mechanics, not a claim that reversible membership or hosted rollout is complete.
