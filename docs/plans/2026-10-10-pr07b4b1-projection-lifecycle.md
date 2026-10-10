# PR07b-4b-1 — durable listing input generations and rebuild receipts

Status: implementation, author review and local verification complete; draft publication in progress. Base: `859228467031a609721a3ad86adf119e3dbd2d2a`, [draft #32](https://github.com/MarcelCutts/home-finder/pull/32). Branch: `codex/stack/30-projection-lifecycle`; intended position 30 in native stack #29. Schema advances from 014 to 015. The delivery goal remains active under the instruction to commit each segment and continue.

## Outcome and reason for this boundary

A home can change while work based on an earlier set of listings is still pending. Previously, reviewed membership decisions appended invalidations, but nothing consumed them; ordinary automatic ownership changes and subsequent source refreshes had no shared home-input revision. Clearing an invalidation after rebuilding only one cache would also imply more work had finished than actually had.

Each home now has a durable input generation. Changes to retained listing inputs, new field observations, membership and routing advance that generation in the same SQLite transaction as the change. A rebuild reads one generation, retains an immutable snapshot, updates its current pointer and acknowledges the membership invalidations for the **listing-input component** in one owned write transaction. A later change makes that pointer stale immediately. Historical snapshots remain readable without being presented as current.

This is the common lifecycle needed by the next consumers. It keeps the existing Python application and SQLite transaction model; no queue service, worker fleet or generic event framework is introduced. The preceding exact-member contract (#32) and this lifecycle have different review questions: #32 preserves every advert through current consumers; this PR records which immutable inputs a future derived result actually used. Combining all price, availability, asset and paid-work policy changes into this migration would make both contracts harder to verify.

Architectural prerequisites are transaction ownership (#6), immutable observations and current text (#18–20), provider/home identities (#27–28), ownership and route journals (#30–31), and exact members (#32). The immediate Git base is #32. This is the first of the remaining PR07b-4b boundaries, not completion of PR07b or hosted readiness.

## Durable data contract

Migration 015 adds four tables:

| Table | Responsibility |
| --- | --- |
| `home_projection_generations` | Monotonic input generation and most recent invalidation cause for each retained home. |
| `home_listing_projection_snapshots` | Immutable, versioned listing input payloads with full snapshot and material-content hashes. |
| `home_listing_projection_heads` | The last published snapshot for a home. Readers must also check generation and policy version. |
| `membership_projection_consumptions` | Immutable receipts acknowledging a particular membership invalidation for `listing_inputs` only. |

Every pre-existing home starts pending at generation 1. The migration does not rewrite prices, availability, images, assessments, observations, personal state or membership. Homes created later receive a generation through their route creation. A generation can advance several times within one transaction, for example when a detail observation inserts several fields and its source row is refreshed. It is a change token, not a count of user-visible revisions.

All membership decisions now append invalidations through their shared writer, including automatic initial links, staging links and cleanup. Affected homes include previous/new owners, explicit reviewed scope and previous/new route destinations. Exact decision replay appends nothing. Previously, the presence of an invalidation also meant a home had been operator-reviewed. The merge-preservation query and two database guards now test the decision operation (`merge`, `split`, `reversal`) explicitly. Ordinary staging still works; reviewed home history remains protected.

Database triggers advance the generation for changed retained source columns, inserted observation fields, route changes, storage-binding changes and journal invalidations. Unowned observations do not invent a home; their first later ownership link schedules the inputs, including earlier evidence. Source no-op updates leave the generation alone. New receipt timestamps can make a snapshot stale even if its material content is identical. Personal status changes do not alter listing inputs.

The triggers use the owning write transaction. A source write or rebuild rollback also rolls back its generation changes and acknowledgements. Snapshot and receipt rows cannot be updated or deleted. Explicit insert guards also reject `INSERT OR REPLACE`, which could otherwise defeat delete-only protections. Generation reset, cross-home current pointers and acknowledgements against an obsolete snapshot are refused. These are data-integrity guards, not a security boundary against an administrator who can drop the schema.

## Snapshot contents and the two hashes

`HomeListingProjection` policy 1 contains the stable home, its retained property binding, its own display route, input generation, relevant membership revision and every exact currently owned representation. The member order is deterministic. An alias or retired home retains its own route and empty membership; it does not copy another home's listings into its own input history.

Each representation keeps its stored identity, declared provider identity/URL resolution, legacy backfill and availability flags, price, retained text, winning normalized field observations and exact observation references. Additional retained source columns remain separate from the normalized observations. This separation matters: a legacy `last_seen` or false off-market flag does not prove that a fresh portal check succeeded. The snapshot preserves evidence without assigning the later price/availability eligibility policy.

Text uses the existing authority/time fold inside the same database snapshot, including observations received before the mutable source text was refreshed. Equal-rank conflicting values remain conflicting. A per-field material hash includes the actual winning alternative values, so two different sets of conflicting prices do not collapse to the same `conflicting/None` material fingerprint.

- The **snapshot hash** identifies the complete canonical JSON, including generation, membership revision, timestamps and evidence references. Reads validate its checksum, material hash and stored metadata against the decoded payload.
- The **listing material hash** excludes receipt-only metadata, generation and membership revision while retaining the home/binding/route, exact members, identity resolution, content, availability/backfill flags and actual conflicting alternatives. An unchanged-content refresh produces a new snapshot but the same material hash. A merge followed by split or reversal can restore the original material hash while retaining distinct history and generations.

The material hash is deliberately scoped to retained listing inputs. It is conservative about changes to raw retained columns and is **not the final assessment cache/admission key**: image bytes, preprocessing, selected input policy, model/prompt policy and other evaluation dependencies still need their own revision contract. Do not use equality of this hash alone to authorize paid work or declare an old assessment fresh.

Bounds are 500 members and 4 MB per home, with the existing maximum of 512 tied winning fields and 4 MB of field values per representation. Oversized or inconsistent evidence refuses rebuilding and leaves pending work intact. Unknown representation provenance remains explicit. No matcher threshold or automatic same-portal rule changes.

## Repository API, retries and observability

`PropertyStorage.projections` provides:

- `inspect(home_id)` to build a read-only candidate in an owned snapshot.
- `status(limit=100, pending_only=False)` to inspect bounded current/built generations, snapshot IDs, pending state and latest cause.
- `current(home_id)` to return only a snapshot matching the current generation and policy; stale/missing returns `None`.
- `get(snapshot_id)` to read validated historical inputs without a currency claim.
- `rebuild(home_id, expected_generation=...)` to rebuild and acknowledge atomically. The generation precondition is optional for an immediate rebuild and available for previously inspected inputs.
- `rebuild_pending(limit=25)` to commit each selected home independently. Selection supports 1–100 homes. A generation changed between selection and rebuilding is deferred; other failures stop the batch and remain observable. Already completed homes survive a later failure and exact retries reuse their snapshots.

Rebuilding holds the existing `BEGIN IMMEDIATE` write scope while reading and writing its bounded database evidence. It performs no portal, filesystem-asset or model request inside that scope. Another connection's source update waits, then advances the generation after the rebuild commits; the just-built snapshot is immediately stale. If a previously stored snapshot exists for a generation but its newly read inputs disagree, rebuilding refuses `inputs_changed_without_invalidation` rather than silently reusing it.

`home_listing_projection_rebuilt` is emitted after commit with home, generation, snapshot and material hashes, member count, payload bytes, acknowledged count and replay status. `home_projection_rebuild_failed` records the home and exception type; `home_projection_refresh_deferred` records a concurrent-generation deferral. Existing database-scope logs supply wait/duration and rollback diagnostics. Raw advert text is not added to these logs. The generation table holds the latest cause, not an independent complete event history; source observations and membership journals retain their existing evidence histories.

No periodic rebuild worker or startup backfill is enabled here. The repository is the concrete write contract, exercised on disposable copies; the CLI below is inspection only. Existing dashboard and pipeline outputs retain their previous behavior until each downstream consumer adopts its own verified revision. A `listing_inputs` receipt must never clear price, availability, asset or assessment work. Retention/expiry of referenced snapshots also remains future artifact-dependency work; this PR never deletes them.

## Read-only operator inspection

Use an already migrated **copy**, with application/development dependencies installed:

```sh
uv run python -m home_finder.pipeline.projection_audit \
  --database /absolute/path/to/schema-15-copy.db \
  --output /absolute/path/to/new-private-review-directory \
  --limit 10
```

Repeat `--home-id h_...` to inspect specific stable homes. Otherwise it selects pending homes in stable ID order. The default is 10, maximum 25; explicit IDs must be unique and fit the limit. The complete JSON report, including its newline, is bounded to 16 MB. A missing database, unsupported schema, missing home or evidence bound failure produces no report directory.

The command uses SQLite `mode=ro`, `query_only` and one explicit read transaction, respecting committed WAL content. It does not initialize storage, load environment configuration, migrate the copy, rebuild, acknowledge or apply membership changes. The report includes previous and candidate inputs, exact added/removed member keys, route/material comparisons, snapshot IDs and expected input generations. It counts all pending homes so a limited selection cannot be mistaken for a full census.

Output goes to a new mode-0700 directory and mode-0600 file, never replacing existing output. The report contains private source evidence and stays out of Git. Console output contains the report path and counts, not advert text, source IDs or URLs. This is a listing-input inspection surface; it is not yet the final reconciliation artifact containing every downstream projection and protected personal/history count.

## Author review and primary-source checks

Reviewed every source-input field against invalidation coverage; the shared automatic/reviewed membership writer; route and ownership guards; snapshot canonicalization; conflict hashing; before/after generation checks; transaction failure and cancellation; historical reads; and CLI bounds, permissions, schema handling and WAL behavior. This is an author review, not independent approval.

Findings corrected during implementation and review:

1. Extending invalidations to automatic decisions accidentally classified all initial links as reviewed history. Explicit decision-operation checks restore the prior preservation meaning while retaining the new scheduling evidence. Staging, cleanup and reviewed-history tests pass.
2. The migration reinitialization test resets an empty current database to schema zero. New schema creation and baseline seeding now remain idempotent in that exercised path; normal restart does not add generations or snapshots.
3. A legacy raw description can change while richer retained text wins the text fold. The snapshot now retains that raw column separately so the input/material record does not overlook the change.
4. A conflict flag alone loses which values conflict. Material fingerprints include each actual winning alternative.
5. Restart can create SQLite's internal query-statistics tables. Preservation comparisons exclude reserved SQLite tables while continuing to compare every application table; mutation tests separately permit only the expected generation change.
6. Failure injection at snapshot insertion, current-pointer insertion and receipt insertion proved all preceding writes roll back. Cancellation at pointer publication does the same. Explicit replacement, stale acknowledgement, corrupted checksum and bypassed-invalidation tests refuse misleading current evidence.

SQLite's [trigger documentation](https://www.sqlite.org/lang_createtrigger.html) supports using `AFTER` triggers for dependent changes and `RAISE(ABORT, ...)` for guards. It also notes that an outer conflict policy controls triggered statements, so the implementation avoids relying on an inner ignore policy. Its [conflict documentation](https://www.sqlite.org/lang_conflict.html) explains the conditional firing of delete triggers during `REPLACE`, which motivates explicit insert guards. Its [isolation documentation](https://www.sqlite.org/isolation.html) supports the consistent read snapshots and serialized write boundary, exercised here using separate real connections. Checked online on 10 October 2026; application tests and copied-database experiments establish this repository's behavior.

## Verification

The [machine-readable record](2026-10-10-pr07b4b1-verification.json) records final suite counts, source/archive hashes and copied-data results. Full socket-blocked suites pass **3,406 tests** on Python **3.14.7 / 88.33%** and **3.11.16 / 88.41%** branch-inclusive coverage, including 41 additional cases. Focused migration, membership, grouping, observations, current text, lifecycle and audit checks pass 187 tests. A final typing/lint-only adjustment to audit member lists followed full-suite collection; all eight audit cases then passed against the final module on each interpreter. No other application/test file changed after collection. Ruff lint/format (267 files), strict mypy (120 source files) and diff checks pass. The wheel matches all 154 application files, and the source archive matches 325 included checkout files byte-for-byte; only generated package metadata is additional. Builds used uv 0.12.19 and Hatchling 1.32.4; no dependency or build-backend policy change is included. No UI changed, so the preceding browser verification remains its original evidence rather than being claimed as rerun here.

Fresh socket-blocked migration/rebuild experiments preserve every original table row on both inputs. The historical schema-6 copy becomes schema 15 with 1,016 homes, 1,217 aliases, 3,267 membership records and all 17 ownership conflicts unchanged. All 1,016 listing snapshots rebuild; exact retries and restart change no application rows. All eight price-history rows across the six affected homes remain intact. The registry/conflict audit is unchanged.

The recent schema-10 copy retains 35 homes and all 55 stored source representations. Southborough's two Rightmove adverts and Wilmot's Rightmove plus two OnTheMarket adverts survive merge → split → exact old-command replay and merge → reversal. Listing material returns to its original value after split/reversal; prior grouped snapshots remain readable. The exercises append eight decisions, twelve member changes, twelve route changes, twenty invalidations and twenty `listing_inputs` consumption receipts. There are 55 immutable input snapshots after the exercised 35 initial rebuilds and 20 affected-home rebuilds. No downstream receipt is fabricated. Private CLI artifacts were inspected and left pending work unchanged.

Integrity, foreign keys, membership replay and ownership consistency pass; both original database hashes are unchanged. The retained 50-listing development replay again produces 35 groups and 50 selected members in both same-run and split-run shapes, with no new known-listing work and no model attempts. Original database/image artifacts remain unchanged. This provides regression evidence, not an independent matcher-quality benchmark or a fresh portal run.

Private experiments, SQLite copies, source manifests and logs are under `.worktrees/evidence/30-projection-lifecycle`, excluded from Git. No active-data reconciliation, deployment, live portal request, paid model call or Telegram message occurred. Remote CI remains disabled by inactivity; no remote passing check is claimed.

## Updated priority after the independent review

While final verification was running, the owner-local `opus-ultra-findings.md` appeared. It reviews #3–#32 and questions both correctness and proportionality. The file remains untracked and unmodified; it is not a dependency available to a fresh checkout. The material findings and our new checks are summarized here so reviewers can assess the consequence without that private/local file.

Three socket-blocked synthetic reproductions against this branch confirm existing defects: an OnTheMarket response for advert 222 is recorded under requested advert 111; all three optional/paid-package water examples are labelled included in rent; and historical export reads an uncommitted price of 1000 from a hot-journal database whose committed price is 2000. These remain open, pre-existing defects, not failures repaired by this lifecycle PR. The export result agrees with SQLite's [immutable URI contract](https://www.sqlite.org/uri.html): it bypasses normal locking/change detection and cannot certify a crash-recovery input. The new audit uses ordinary read-only mode instead.

The review's assessment-freshness blocker also remains: retaining inputs and consuming `listing_inputs` does not stop old assessments being displayed or admitted as current. The next bounded change should enforce that reviewed regrouping is disabled by default while its consumers are incomplete. Then repair returned-detail identity, bill inclusion targets and frozen-export validation, with adversarial regressions, before investing in further reconciliation. The intermediate #7 refresh boundary must either receive the real observation handoff or explicitly remain coupled to #19 for activation. Safe CI bootstrap and actual required validation must precede any merge; current green local tests do not negate the reproduced defects.

The proportionality criticism is worthwhile. The demonstrated priority is a working collection → grounded assessment → shortlist → notification flow with measured costs. The completed lifecycle work is retained as a reviewable optional draft. The remaining reconciliation work below is conditional on demonstrated duplicate-related user friction or an explicit need to enable regrouping; it is no longer the automatic next investment. Preserve useful code and the conservative matcher instead of expanding architecture just because the larger plan contains it. Existing paid-run budgets are not renewed by this change in priority.

## Conditional reconciliation sequence and operating limits

If active reviewed reconciliation is adopted, finish these boundaries sequentially before enabling it:

| Next boundary | Required result and review gate |
| --- | --- |
| PR07b-4b-2: current price and availability membership | Derive values from the same exact eligible current-member set. Distinguish duplicate/backfilled representations, unverified freshness, unknown/removed state and conflicting sources. Preserve original prices/history; bind the derived result to these input generations and keep broader PR08b/09b freshness/coverage policy explicit. Integrate bounded rebuilding where its consumer needs it. |
| PR07b-4b-3: asset provenance and cache routing | Bind galleries/floorplans/cache keys to exact representation and observation/content evidence; retain unknown historical provenance. A representative change or split must not attribute another member's cached image to the current home. |
| PR07b-4b-4: assessment revisions and paid admission | Define the complete material/dependency key and freshness outcome. Check before dispatch and before publishing; retain every original assessment/attempt, safely release unused reservations and suppress duplicate charges. A request already sent cannot be made uncharged retroactively. |
| PR07b-4b-5: reviewed reconciliation | Combine all before/after members, routes and derived projections, protected personal/history counts, unresolved cases and preconditions in a review artifact. Rehearse supported cases and the historical census on fresh copies before any active reconciliation. |

These are bounded parts of the existing PR07b-4b gates, not prerequisites for proving the basic product or an instruction to implement them all now. More segmentation is justified only when a concrete acceptance boundary requires it. The 17 historical conflicts remain separate until evidence, consumer behavior and operator review support a particular change. Automatic deduplication thresholds stay unchanged; independent labels remain a later prerequisite for changing them.

Schema 15 is additive but the schema-14 application rejects a newer database. Rollback cannot be a blind checkout of the previous binary against the migrated file. Retain the new evidence and use schema-compatible corrective code; an older database/application pair requires the separately verified restore procedure and an explicit decision about subsequent writes. Disabling use of the new repository is enough to stop new snapshot construction; its durable invalidations should not be deleted to make a backlog disappear.
