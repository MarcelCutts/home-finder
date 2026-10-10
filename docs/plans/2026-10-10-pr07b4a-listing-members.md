# PR07b-4a — preserve every listing in text and link consumers

Status: implementation, author review and local verification complete; published as [draft #32](https://github.com/MarcelCutts/home-finder/pull/32). Application commit: `3a47502a01e378ef0a8b5f431a71962f4225e56d`; the publication follow-up changes documentation only. Base: `515b687c2cbb7a3cf70ca1140ff8e42fad3a30b7`, [draft #31](https://github.com/MarcelCutts/home-finder/pull/31). Branch: `codex/stack/29-member-projections`; position 29 in native stack #29. This is the first boundary within PR07b-4. The remaining projection lifecycle and reviewed reconciliation are PR07b-4b, described below. Schema remains 014.

## Outcome and architectural boundary

The reviewed Southborough and Wilmot cases contain multiple advertisements from the same portal. Their database rows survived the preceding ownership and routing work, but a dictionary keyed by portal could carry only one URL, description and feature set. Saving, reloading or enriching a group could silently select a different advert. A split could also leave the surviving representative's cached description mentioning a departed member.

An explicit `ListingMember` now carries each retained representation through deduplication, queue loading, enrichment, assessment input selection, the detail page, bill-claim explanations and Telegram links. Repeated portals receive deterministic labels such as “Rightmove 1” and “Rightmove 2”. Contradictory claims remain attributable to their separate adverts. This changes the representation and its consumers; automatic matching thresholds and the same-portal guard remain unchanged.

These consumers belong in one PR: introducing a new collection while a subsequent save or model-input builder still collapses it would not establish a usable preservation contract. Full projection rebuilding has a separate boundary because it must define eligibility, material revisions, asset provenance, invalidation acknowledgement and paid-work freshness. This PR does not make reviewed reconciliation ready for active data.

Architectural prerequisites: transaction ownership (#6), retained text and observations (#7, #18–20), exact provider claims (#27), stable homes (#28), journaled membership (#30), and current group routing (#31). The immediate Git base is #31; these are semantic prerequisites, not a claim that every preceding stack layer is independently required by this change.

## Contracts for subsequent agents

### Retained member identity

`MergedProperty.members` is either an explicit tuple or `None` for an old serialized object. An empty tuple means there are no current members; it must never revive portal-cache evidence. `listing_members` provides a compatibility reader for old objects and `listing_urls` exposes every current URL. Database queue/anchor readers supply explicit tuples, including empty ones.

Each `ListingMember` carries the stored source-row ID, declared source ID, portal, URL, retained text, price and legacy availability/backfill flags. A stored row ID is evidence identity, not proof of a verified provider identity. Historical declared/stored mismatches remain available to the identity audit. Price and availability fields here do not establish current listing eligibility or freshness.

Legacy URL-only members have neither an invented source-row ID nor an invented declared ID. Their evidence key is `legacy:<portal>:<URL SHA-256>`. Exact retained members use their stored source-row ID. Repeated representations are rejected within one explicit tuple. Provider URL aliases are accepted only when the existing exact identity resolver agrees, or the stored URLs are exactly equal; a different advert URL fails the write and rolls back the whole save.

The old `sources`, `source_urls`, `source_text` and `descriptions` fields remain readable compatibility summaries. They cannot represent repeated portals and must not be used as a complete membership collection. `text_by_source` returns explicit-member text only for portals with exactly one member, avoiding an arbitrary winner. Old serialized objects keep their previous compatibility behavior.

### Persistence and current reads

Every noncanonical explicit member must already have been received into `source_listings` before saving a merged property. This is the real pipeline's ingestion order. Do not synthesize another listing's price, location or identity from the representative to satisfy that precondition. Missing rows, incompatible IDs/URLs and another home's owned rows refuse the whole save. Exact unowned members are linked through the existing ownership journal; a routine save cannot reclaim a reviewed member from another home.

Text persistence targets the exact owned representation. A correction or explicit clear on one member does not clear another member from the same portal. Canonical text comes from the canonical representation when present, not whichever same-portal row happened to be loaded last.

Dedup anchors, pending enrichment, pending analysis and reanalysis load all source rows in their owned read snapshot, ordered by portal and stored identity. Enrichment cache reads now obtain canonical fields, retained images and current members in one snapshot. A real two-connection test commits a membership change between the component reads and proves the reader sees one consistent version, then observes the new version on its next read. Batched current-member lookup is limited to 500 candidate properties and uses 100-property SQL chunks.

### Enrichment and assessment inputs

Detail enrichment visits each member URL and retains that advert's description/features and observation references before downstream image work. It no longer skips a second advert merely because its portal has already been visited. Existing exact URL parsing still binds a detail request to a provider representation; no fuzzy identity inference is added.

Before analysis, the selected candidates reload their active current members. Empty or missing groups are refused. Text selection policy **3** records exact member keys, original URLs, display labels, full evidence hashes and selected observation references. Both model phases receive the same bounded text selection. Historical assessments keep their stored input record; reading them does not reinterpret their old portal keys as new member IDs.

The existing total allowance remains 3,000 description characters and 15 features. Descriptions share the allowance; features rotate across adverts. Full evidence hashes survive truncation. If even a member label cannot fit in its allowance, its description key is explicitly recorded as omitted. A 400-member unresolved fixture verifies bounded output and complete references. Markup is escaped and conflicting within-listing facts remain withheld. This is evidence handling, not a claim to eliminate model prompt injection or prove assessment quality.

The hash is still over the retained text-evidence object, including its evidence metadata. It is not yet the stable material revision/admission contract required by PR07b-4b and later assessment caching work. Refreshing membership before selection does not prevent a new change after selection or during an in-flight model request.

### Dashboard, Telegram and diagnostics

The detail page and Telegram message/keyboard retain every member URL, including two links to the same portal. The detail description labels each advert; the monthly-budget explanation considers every member separately. The synthetic “electricity included” / “electricity excluded” case stays conflicting and displays both labelled claims. An explicitly empty membership supplies no old cached description, advert link or bill evidence.

Descriptions use normal template escaping and CSS `white-space: pre-line`. They display line breaks without trusting advert HTML or showing literal `<br>` text. The fade and expansion control now depend on actual overflow; resize observation rechecks wrapping. Short multiline descriptions remain fully legible, and long descriptions have working Read more / Show less controls. The escaping regression includes literal script markup.

New diagnostics are `detail_listing_started` (property, portal, member-key hash and group size), a member-key hash on detail failures, `current_listing_members_loaded` (property/member/repeated-portal counts), and `analysis_listing_inputs_selected` (selection version, member/portal/evidence/observation counts, selected characters/features and omitted descriptions). Raw listing text and model credentials are not added to these events. Existing model reservation/attempt/run logs remain the spending and execution record.

## Author review and online verification

Reviewed the changed source writers, all queue/anchor reload paths, URL-to-group lookups, text selection, both actual mocked provider request bodies, detail rendering, cost evidence and Telegram links. The review retains the distinction between listing representation, provider claim, display home and original personal state. It is an author review, not independent approval.

Findings corrected during implementation/review:

- An anchor query lacked explicit member ordering; a copied-database comparison exposed inconsistent tuple order.
- Separate cache/member reads could combine different committed memberships; they now share an owned snapshot.
- Strict string URL comparison rejected benign provider aliases; acceptance now uses the existing exact identity resolver, with wrong-advert rollback tests.
- Old manual pipeline tests saved absorbed members without first ingesting them. They now follow the real ingestion order; refusal tests preserve the new contract.
- Three multi-source queue fixtures declared ranges and portals without the corresponding received source rows. They now retain real source rows with those prices, preserving their original reconstruction assertions.
- Browser review exposed escaped `<br>` text and an unconditional short-description fade. Both are fixed and checked on desktop/mobile, with long-text expansion checked separately.
- The detail consumer's empty-tuple fallback could revive old caches. It now distinguishes missing legacy membership from explicitly empty current membership.
- Five Python 3.14 warnings came from one parametrized test's synchronous SQLite reader. The connection context managed its transaction but did not close it. The reader now uses `contextlib.closing`; all five cases pass on both interpreters with `ResourceWarning` treated as an error. This is a test-resource correction, with application code unchanged.

Primary documentation was checked on 10 October 2026. SQLite's [isolation reference](https://www.sqlite.org/isolation.html) supports reading all components inside one WAL read transaction; that is also exercised with separate application connections. Jinja's [HTML escaping documentation](https://jinja.palletsprojects.com/en/stable/templates/#html-escaping) explains why ordinary escaped text should remain distinct from trusted markup. MDN's [`white-space` reference](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/white-space) documents preserved line breaks and wrapping for `pre-line`. Python's [SQLite context-manager reference](https://docs.python.org/3.14/library/sqlite3.html#how-to-use-the-connection-context-manager) confirms the explicit-close requirement for the test reader. These references justify the implementation choices; browser and application tests establish this application's behavior.

## Verification

Final counts, hashes and publication evidence are recorded in the [verification record](2026-10-10-pr07b4a-verification.json). Full socket-blocked suites pass **3,365 tests** on Python **3.14.7 / 88.33%** coverage (four workers) and **3.11.16 / 88.40%** (eight workers), including 17 additional cases. The final description-overflow and explicitly-empty-membership UI corrections followed full-suite collection; all **335 web tests** were then run again and passed on each interpreter. No other application Python subsystem changed after collection. The final test-reader cleanup was then checked in the five affected cases on each interpreter with warnings treated as errors, and the archives were rebuilt; the full-run log still records its five original warnings. Ruff lint/format (261 files), strict mypy (116 source files), JavaScript syntax and diff checks pass. The final wheel matches all **150 application files** byte-for-byte; 316 allowlisted source/archive files match the checkout. Builds used uv 0.12.19 and Hatchling 1.32.4; the backend is not pinned by this segment.

Both initial full runs found five outdated reference/receipt-fixture assumptions described above; those failed runs are excluded from the passing counts. The new regressions cover every queue loader, split/reversal with deliberately stale text caches, exact member enrichment and clearing, missing/foreign/wrong-URL rollback, equivalent provider URLs, unchanged same-portal automatic matching, concurrent snapshots, both Telegram URLs, large bounded text selection and same-portal evidence reaching both model phases. Existing integration fixtures now model the real receipt-before-save order. No paid API or real Telegram call is used.

Fresh socket-blocked copy rehearsal retains every original table row, 1,016 historical homes, 1,217 home aliases, 3,267 membership records and all 17 historical ownership conflicts. It reads 1,015 eligible anchors with 1,713 linked members; the total membership census includes rows not owned by those active anchors. Integrity, foreign-key, replay and restart checks pass. All eight historical price-history rows across the six affected homes remain preserved.

The recent copy reads 35 anchors with 50 selected members, retaining all 55 stored source representations. Southborough's two Rightmove adverts and Wilmot's Rightmove plus two OnTheMarket adverts survive merge → split → old-command replay and merge → reversal. Every member URL, text input key and selected evidence reference survives grouping; splits remove departed-member inputs. The exercises append eight decisions, twelve member changes, twelve route changes and twenty invalidations on the copies only. Both original database hashes remain unchanged.

The retained 50-listing replay reproduces 35 groups and all 50 selected members in both same-run and split-run shapes. Known listings produce no repeat work, no model attempts occur, and original database/image inputs remain unchanged. This remains a development replay, not an independent deduplication benchmark or fresh live portal test.

Synthetic Playwright review uses a loopback server with the pipeline disabled and Telegram unconfigured. It verifies both same-portal links, separately labelled descriptions and conflicting bill claims, actual newlines, safe rendering, no short-text fade, long-text expansion/collapse and no horizontal overflow at 1365×1000 or 390×844. The first browser harness attached fixture Basic Auth to every origin and caused CDN preflight errors; it was closed and corrected to attach credentials only to loopback. The final browser session has **zero console errors or warnings**. No real credentials or private property captures are in the screenshots.

Sanitized screenshots: [desktop detail](../reviews/images/2026-10-10-listing-members-desktop.png) and [mobile detail](../reviews/images/2026-10-10-listing-members-mobile.png). Private copies, logs and replay inputs are under `.worktrees/evidence/29-member-projections`, excluded from Git. Distribution checks compare the wheel with all application files and the source archive with its explicit allowlist; unrelated research and local scripts are not implicitly release inputs.

## Limits and next eligible segment

Proceed to **PR07b-4b: projection lifecycle and reviewed reconciliation** before moving to PR08b/PR09b. Its acceptance gates are:

1. Consume journal invalidations with a durable, repeatable rebuild/acknowledgement contract. Rebuild after merge, representative change, split and reversal; verify restart and concurrent reads without silently losing invalidations.
2. Define member eligibility for current price and availability. Repeated/backfilled representations must not invent independent price evidence or fresh availability. Preserve original prices, history and immutable observations. Complete PR08b/PR09b freshness and source-coverage policy follows this foundation.
3. Carry exact representation/observation provenance for assets, floorplans and cached image routing. Preserve unknown historical provenance explicitly; a representative's old image cache is not proof that every image belongs to the current group.
4. Bind assessment inputs, freshness and paid admission to a stable membership/material revision. Recheck before dispatch and before publishing an outcome; keep prior assessments and attempts under their original provenance, release unused reservations safely and prevent duplicate charges. An in-flight provider request cannot be cancelled retroactively by changing membership.
5. Provide an inspectable dry-run/review artifact containing all before/after members, routes, projection changes, affected personal/history counts, unresolved cases and preconditions. Rehearse Southborough/Wilmot and the historical census on fresh copies before any owner-authorized active reconciliation.

This PR does not consume invalidations or make all old representative/portal caches authoritative. Price/availability policy, image identity, assessment invalidation and the operator reconciliation surface remain open. The 17 historical conflicts remain separate. No matching threshold, automatic same-portal policy, service topology or hosted-scale target changes.

Remote CI remains disabled by inactivity; unmerged `main` still has the old live-portal schedule and automatic deployment path. The workflow is not enabled as part of publishing this draft. No deployment, production mutation, portal fetch, paid model call or Telegram notification was performed. The delivery goal remains active under the instruction to commit each segment and continue.
