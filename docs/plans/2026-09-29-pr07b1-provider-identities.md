# PR07b-1: exact provider identities without changing home membership

**Date:** 29 September 2026. **Parent:** [PR07](2026-09-28-research-synthesis-and-delivery-plan.md#pr07--reconcile-provider-identity-and-make-canonical-merges-reversible-l). **Code baseline:** `d50f420`. This is the first identity slice after [PR07a's history guard](2026-09-29-pr07a-merge-preservation.md); stable home identity and reversible membership remain separate follow-on work.

The [50-listing review](../reviews/2026-09-29-deduplication-quality-review.md) supports retaining the matcher while fixing representation and review. Southborough contains two Rightmove advertisements for one apparent home; Wilmot contains one Rightmove and two OnTheMarket advertisements. Those are distinct provider listings even when an eventual reviewed decision groups them into one home. The historical database presents a different problem: real numeric IDs and URL-shaped backfill IDs can represent the very same advertisement, sometimes under different saved homes. This change distinguishes those cases without erasing either history.

## Delivered behavior

Migration **011** adds two tables. `provider_listings` stores a deterministic SHA-256 key for the exact `(provider, provider ID)` pair, with uniqueness in that registry. `source_identity_claims` retains each original source-row ID, declared ID, URL, backfill flag, resolver revision and result. Both tables reject updates and deletion. Claims deliberately have no cascading relationship to mutable source rows, so evidence survives retirement of a permitted empty staging row.

Policy `provider-path-v1` resolves an identity only when the declared numeric ID and a recognized provider URL agree, or when a historical URL-shaped ID resolves to the same exact provider ID as its retained URL. The original strings remain in the claim. Leading zeroes remain significant. Query strings and fragments do not change a recognized listing's provider identity; the URL itself is retained. Different providers and different advertisement IDs always receive different keys.

Unsupported providers, unrecognized URLs, ID/path disagreements, opaque IDs and inconsistent raw unique IDs receive explicit unresolved reasons. A backfill flag never authorizes guessing an ID. URL recognition checks provider host, listing path, scheme, credentials and port; non-ASCII digits and raw whitespace/control characters are rejected. These rules describe identity syntax, not a fresh fetch or proof that a portal still serves the listing.

An identity key is **not a home assignment**. Multiple legacy representations may share one provider key while still pointing to different saved homes. The audit marks this `owner_conflict`; it does not select a winner. The unique registry key therefore does not impose premature uniqueness on legacy rows or resolve conflicting membership.

The migration reads existing source rows in batches of 100. Future search upserts, individual and merged property saves, floorplan-dropped saves, enrichment retries and pre-analysis saves record their persisted identity in the same transaction as their existing writes. Failure to write identity evidence rolls back the operation. Repeated inputs do not duplicate claims, and changing home ownership alone does not create a new claim. Existing source refreshes retain their old URL/backfill behavior: claims describe the actual persisted representation. Newly received URLs can still be retained in source observations; this slice does not turn every received URL into a registered alias.

`storage.identities.audit()` supplies the exact mapping and conflict groups. `storage.identities.members(home_id)` returns every current source representation, including multiple IDs from one portal. The existing portal-keyed `MergedProperty` projections and matcher policy are unchanged. The new internal 64-character keys are not inserted into existing web URLs or Telegram callbacks.

## Read-only operator audit

From an installed application checkout:

```sh
uv run python -m home_finder.pipeline.identity_audit \
  --database /path/to/existing-copy.db \
  --output /path/to/new-private-report-directory
```

The command opens an existing database with SQLite `mode=ro`, uses a read transaction, and never initializes `PropertyStorage` or applies migrations. It also works on the retained version-6 historical database. Ordinary read-only mode respects committed WAL data; it does not assert that a changing file is immutable. Prefer the existing backup workflow when an auditable frozen input is needed.

The new directory is mode `0700`, the JSON report is `0600`, and an existing output directory is refused. The complete report includes raw historical IDs/URLs and current home IDs; keep it private. Console output contains aggregate counts, reason categories, policy revision, an identity-input digest and the report path. The digest covers the sorted identity inputs, not all database contents. The report separates resolved provider entities, duplicate representations and conflicting owners. Zero unresolved provider IDs does not mean zero unresolved home assignments.

`source_identity_claims_prepared` logs input, insertion and unresolved counts at debug level; `provider_identity_backfill_prepared` logs the policy and backfilled row count. These events deliberately say *prepared*, because the owning transaction still controls commit/rollback. Existing database initialization/scope logs report the surrounding operation. No raw URL-shaped ID is added to these diagnostics.

## Evidence from both retained databases

The audit was run against the original files without migration. Then new disposable copies were initialized and audited twice with external networking blocked. The rehearsal compared all rows of every original application table using its original columns, so adding later columns to the version-6 database cannot disguise a change to old data. It separately compared every post-migration application table on the second initialization, including the new claims and their timestamps. SQLite internal statistics tables are excluded.

| Check | Historical backup | Recent batch database |
| --- | ---: | ---: |
| Original schema version | 6 | 10 |
| Original home records retained | 1,016 | 35 |
| Original source rows / new claims | 3,267 | 55 |
| Resolved provider entities | 3,006 | 55 |
| Numeric-ID representations | 2,886 | 55 |
| Historical URL-ID representations | 381 | 0 |
| Unresolved provider identities | 0 | 0 |
| Duplicate representation groups | 261 | 0 |
| Provider groups with conflicting owners | 17 | 0 |
| Homes affected by those conflicts | 28 | 0 |
| Conflicted homes with price history | 6 | 0 |
| Price-history rows on those six homes | 8 | 0 |
| Every original row preserved | yes | yes |
| Second initialization changes any row | no | no |
| Foreign-key errors | 0 | 0 |
| Database integrity | `ok` | `ok` |

The recent database has **55 scraped source rows**, of which **50 were selected** for the bounded live run. Its 35 homes therefore do not imply 55 fully processed properties. All 109 observations, 853 observation-field rows, three paid-attempt records and 162 saved image references remain unchanged. The source data and cached images were not refreshed, and no model or notification request was made.

The historical comparison also retains all 201 old aliases, 1,001 assessments, 15,872 image references, 106 price-history rows, 30 status events, one viewing message and 43,521 property events. The earlier plan's “six price histories” refers to **six affected homes, containing eight history rows**; it must not become a six-row preservation target.

Private artifacts:

- `data/identity-audit-2026-09-29/historical/identity-audit.json` and `fifty/identity-audit.json`: original-file mappings.
- `data/identity-audit-2026-09-29/rehearsal-v2/verification.json`: aggregate preservation results; each case directory contains its copied database, full table comparisons and post-migration audit. `v2` clarifies history-row versus home counts.
- `/tmp/home-finder-smoke/pr07b1_rehearsal.py` and `pr07b1-rehearsal-v2.log`: reproduction script and transaction diagnostics.

The originals remain byte-identical:

| Input | SHA-256 |
| --- | --- |
| Fresh historical backup | `e1494adf0f5b174cd9a2e142f50aa25b8ffd5a85ba3f4d66383a3e8762cea639` |
| Fifty-listing run database | `e2bdf0073f39067b85a8f1deb707b828cd369f45c9244a9c0360aed2f0934fa7` |

## Verification

Focused identity, migration, source-observation and detail-parser checks: **140 passed**. Cases cover provider/legacy equivalence, URL rejection, opaque and contradictory IDs, deterministic mapping, same-portal distinct listings, conflicting owners, immutable evidence, all six source-save entry points, repeat writes, transaction rollback on an injected claim failure and rollback of a failed migration including its schema version. The existing historical observation test now explicitly simulates the old writer when preparing its version-9 fixture; production code does not bypass a missing identity table.

The existing network-blocked batch replay was run with the new storage code and schema. Its single-run and split-run cases both reproduced the original **35 homes and all 50 source memberships**, with 50 provider keys and 50 claims apiece. The split case updated 15 existing anchors and produced 10 genuinely new homes. Replaying all known inputs produced zero new work. Both databases passed integrity/foreign-key checks, no model attempt occurred, and every original database, event file, report and cached image hash remained unchanged. Evidence is in `data/identity-audit-2026-09-29/dedup-replay/report.json` and `/tmp/home-finder-smoke/pr07b1-dedup-replay.log`.

Both full network-blocked suites passed **3,226 tests**: Python **3.14.7** at **88.12%** branch-inclusive coverage and Python **3.11.16** at **88.21%**. The identity model, repository, audit command and shared URL resolver have 100% measured coverage in these runs. Full logs and coverage XML are retained under `/tmp/home-finder-smoke/` as `pr07b1-full314.log`, `pr07b1-full311.log`, `pr07b1-coverage314.xml` and `pr07b1-coverage311.xml`. No application code changed after those runs.

Ruff lint/format checks passed for **242 files**, strict mypy passed for **105 source files**, and changed-document local links and diff whitespace checks passed. The original user edits, research files and `.env.example` changes are excluded from this segment's commit. Private audit reports and database copies remain ignored by Git.

## Next sequential work

1. **PR07b-2 — stable home identity and legacy link resolution.** Allocate an internal home identity independent of the portal anchor, retain every original canonical ID as an alias, and define redirect/alias resolution for existing web links and short Telegram callback tokens. Keep archived IDs resolvable and test long historical forms. A provider listing key from this slice must never double as a home ID.
2. **PR07b-3 — reversible membership decisions.** Introduce immutable decisions with actor/reason/evidence, expected prior membership and revision, and explicit merge/split/reversal operations. Route every owner-changing path through the contract, including `link_source_listings`, URL linkage and staging absorption. Preserve user state by its original home/member provenance, and expose incompatible saved states instead of choosing one silently. Reject stale review maps. Start with fixtures and disposable copies, including the 17 historical conflicts and Southborough/Wilmot cases.
3. **PR07b-4 — complete member projections and reviewed reconciliation.** Carry all source listing IDs through text, price, availability, images and listing links rather than selecting one dictionary entry per portal. Reconcile only decisions supported by evidence; preserve unresolved cases and uncertainty. Prove merge → split → replay recovery without losing observations, price history, assessments, spending, deliveries or saved messages. Existing automatic thresholds and the same-source matching guard remain until this representation is safe and a separate accuracy evaluation justifies changes.
4. Then proceed to **PR08b** and **PR09b** in the parent queue. Their price/availability and source-coverage projections depend on the completed membership contract. Independent adapter/floorplan classification repairs can proceed separately, with their own scoped tests and commits.

The parent's full PR07 acceptance criteria remain open: this slice does not resolve the historical owner conflicts, suppress aliases as already-seen work, change a saved home's membership, or provide merge/split reversal. Resolver-policy changes must use a new policy revision and preserve prior claims. Continue the same delivery goal and verified-commit workflow; the historical pause wording does not supersede the owner's later instruction to continue.

## Rollout and rollback

Only disposable local copies were migrated in this rehearsal. Production startup would apply migration 011, so use the existing backup and deployment gate before rollout. The original source-listing and canonical schemas/IDs remain valid, with no backfilled ownership changes.

Preserve the new evidence tables if an integration needs to be disabled. An older binary whose highest supported schema is 10 correctly refuses a version-11 database. Roll back with compatible code that retains migration awareness; do not lower `user_version`, drop evidence or restore an older database over newer user state. No live reconciliation command is introduced by this slice.

## Online verification

The implementation follows SQLite's documented [read-only URI mode and immutable-mode caveats](https://www.sqlite.org/uri.html), [read-transaction snapshot and transaction ownership behavior](https://www.sqlite.org/lang_transaction.html), and [foreign-key requirements](https://www.sqlite.org/foreignkeys.html). The existing application enables foreign keys per connection. Python documents that [`urlsplit` does not validate URLs and strips some control characters](https://docs.python.org/3/library/urllib.parse.html#url-parsing-security); explicit syntax checks therefore precede exact identity resolution. These references support the storage/parser mechanics, not claims about portal access or live listing availability.
