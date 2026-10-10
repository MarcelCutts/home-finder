# Focused repair verification and operating qualification

This series addresses the five concrete regressions found after consolidation at
`7fa12c6`. The deployment target remains one owner's workflow, as the first stage
of a small hosted service. It does not activate collection, paid models, Telegram
sending, deployment or a multi-user architecture.

## Review by architectural boundary

| Draft | Change and justification | Review focus |
| --- | --- | --- |
| [#44](https://github.com/MarcelCutts/home-finder/pull/44) | Give the existing hosted suite enough time to finish and report slow tests; correct pagination documentation. | Test selection, socket blocking, coverage, Hypothesis and manual deployment gates stay intact. Hosted checks passed in 15m29s on `7bceffc`. |
| [#45](https://github.com/MarcelCutts/home-finder/pull/45) | Keep useful pending/image-less homes accessible, with assessed/unassessed filtering. | Unknown scores never pass an explicit minimum, including zero. Dropped records stay excluded. Refreshes keep their assessment and sent state. Desktop/mobile browser review and hosted checks passed on `c923079` (16m36s). |
| [#46](https://github.com/MarcelCutts/home-finder/pull/46) | Remove heuristic floorplan promotion; preserve source-declared versus unknown provenance. | Extraction, same-URL updates, reload, grouping, gating, prompts and presentation share the same conservative distinction. The retained bathroom image reproduced the old false positive; offline replay now retains it in the gallery. Hosted checks passed on `78faa9f` (13m53s). |
| [#47](https://github.com/MarcelCutts/home-finder/pull/47) | Retain one validated, receipt-backed visual result until assessment finishes. | Actual request identity, atomic settlement, cancellation, changed inputs, receipt validation, evaluation budgets/holds and matching-checkpoint consumption. The final contract selection passed 72 tests after updating older provenance/cancellation expectations. |
| R5 | Persist and resume the intended Telegram parts for a discovery or price revision. | Domain intent and readiness commit together. Each receipt advances exactly one part; final receipt and domain completion commit together. Unknown outcomes stop. Old sent state and newer price revisions cannot be overwritten by an older completion. |

The PRs form a linear stack in this order. Schema changes are 10 (image origin),
11 (visual checkpoint) and 12 (delivery). Review each diff against its declared
parent. Merge the parent first, then retarget the next draft to main and wait for
its checks on the resulting head. Do not combine the unrelated dependency PR or
revive the retired infrastructure queue.

## Offline verification

The full default suite passed **3,090 tests** with **88.33% branch-inclusive
coverage** (75% required), retaining socket blocking and the normal marker
selection. The final asset-root guard then passed the 27-test delivery/backup
fault selection. Ruff lint/format, strict mypy (94 modules), and an offline
wheel/sdist build passed. The one test warning reports a blocked DNS lookup in
an existing cross-run deduplication scenario; no provider request was allowed.
The new tests use fake responses and never charge a model or send a notification.
Hosted checks remain required on the final PR/merge heads.

## Notification recovery evidence

The Bot API returns message receipts for successful sends, an array of receipts
for albums, and `retry_after` for flood control. The send-method parameter lists
provide no send-idempotency key. Therefore this implementation cannot establish
exactly-once delivery after a lost response. [Telegram Bot API](https://core.telegram.org/bots/api#sendmediagroup),
[response parameters](https://core.telegram.org/bots/api#responseparameters).

Offline faults exercise a successful photo followed by a rate-limited venue;
an acknowledged album followed by a failed follow-up; response loss; receipt
storage failure; cancellation during dispatch and receipt storage; concurrent
callbacks; incomplete receipts; blocked recipients; changed recipients/assets;
and completion racing a newer price revision. Successful earlier parts are not
repeated, no alternative text notification is sent after an ambiguous media
outcome, and retry delays survive restart. Tests use actual storage transactions
and mocked Bot API responses. Pipeline scenarios now mock the provider boundary,
leaving the delivery code and its domain transitions active.

One record holds the current plan, cursor, bounded receipt list and last reason
for an owner-bound logical event. There is no per-attempt event journal or general
messaging framework. A plan's content, keyboard and selected media are fixed
before dispatch. Local files are hashed and uploaded from those verified bytes;
missing/changed planned files defer for repair. URL-only media retain their URL,
which does not guarantee that the remote host will keep identical bytes.
Before any plan exists, images arriving later may refresh a no-image snapshot.

Existing pending/failed discovery records and eligible legacy price alerts enter
review; migration does not infer whether an old send succeeded. Legacy sent homes
are not replayed and receive no invented receipts. An explicit local decision is
required to retry an uncertain/legacy outcome, acknowledge observed message IDs,
or suppress the remaining delivery. The command uses the same runner lock;
owner diagnostics expose only metadata, not content, tokens or recipient IDs.

## Production observations: read-only, October 10

- Fly has one started machine in London, with a volume mounted at `/app/data`.
  Its configured database is `/app/data/properties.db`.
- The serving process has `--serve --no-pipeline`. The inspected machine is a
  dashboard, not an active collector/scheduler. This does not identify any
  independent collector outside that machine; no such authority was qualified.
- The deployed image was last updated March 1. SQLite reports schema 6, 1,016
  properties, 1,001 assessments and 57 pipeline runs. The last recorded run
  completed February 28 at 18:33:30 UTC. `quick_check` passed with zero foreign-key
  violations. Only aggregate metadata was read.
- Anonymous `/` and `/health` returned HTTP 200. Response bodies were discarded.
  Health is intentionally public, but the dashboard result means deployed owner
  access is **not qualified**. Passing access-control tests in main do not prove
  those controls or their settings are deployed.

The deployed database is the current dashboard/owner-state authority. A local
collector must not overwrite it. Activating a scheduler or moving that authority
remains a separate, deliberate operating decision with writes fenced during
cutover.

## Backup, restore and migration rehearsal

The default local `data/properties.db` is an old development file last modified
February 3. Its snapshot has two properties and 1,016 foreign-key violations.
It is not a usable production export and was not repaired or swapped into service.

The documented September 27 export at
`data/production-review/2026-09-27T122222Z/properties.db` has source SHA-256
`e50897b46ebefea0119fd62951ac277d764684f9590d52801e66c964b1352c63`.
It contains the same aggregate counts observed on Fly, but matching counts do not
prove identical contents or establish freshness of owner decisions.

The backup API copied that export, verified the manifest and restored into a new
isolated `/tmp` directory. The restored database then migrated from schema 6 to
12. A hash comparison of every original column in every original table showed
no historical changes. In particular it preserved 1,016 properties, 1,001 quality
analyses, 15,872 image records, 106 price-history records, 30 status events, one
viewing message, 3,267 source listings and 43,521 property events. Integrity and
foreign-key checks passed after migration. The source hash remained unchanged.

This rehearsal excluded image-cache files. It qualifies the supplied database
export and migration path, not a current production backup or an asset restore.
The export predates spending accounting; synthetic offline tests separately
cover preservation of uncertain spending holds and visual checkpoints across
backup, restore and schema upgrade. Never represent unknown historical model
cost as zero merely because a migrated ledger has no rows.

## Remaining release work

1. Finish review of the draft stack and require the hosted checks on each final
   merge head. None of these PRs authorizes deployment.
2. Confirm current production ownership and access settings. After an authorized
   release, verify anonymous rejection, authenticated owner access, webhook owner
   checks and rejected cross-origin mutations against the deployed application.
3. Take a fresh backup of the actual authority **with its assets**, verify it,
   rehearse restoration and record the chosen revision/configuration. Fence
   writers at cutover. A rollback must restore compatible data and reconcile any
   writes made after the backup; never point older code at schema 12.
4. Review legacy/uncertain delivery records and any paid-attempt holds explicitly.
   Review metadata first; do not bulk-reset status fields or replay old sends.
5. Agree a new small live-trial spending allowance and explicit notification
   authorization, then qualify one assessment-to-delivery path with diagnostics.
   Previous experiment caps have expired. Keep discovery/model/send sizes bounded.
6. Only after that trial, decide whether the owner workflow is ready for gradual
   hosting. A second independent user requires isolation first. Manual ingestion
   and reproducible commute scenarios remain later product choices; new database
   topology or orchestration requires measured evidence.

The release and reconciliation commands are in [operations](../operations.md).
