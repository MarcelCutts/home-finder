# PR06b-2: Retain search revisions and assessment evidence references

**Date:** 28 September 2026. **Predecessor:** observation foundation `a48fcb2`.
**Scope:** local search-response integration. Detail capture, the authoritative projection switch and remote transport remain separate steps.

## Result

All four search adapters attach a normalized source observation after a successful response, before selection, postcode inference or result caps. The source-row writer retains those observations in the same owned SQLite transaction as its current-row updates. Repeated delivery of the same captured event is idempotent; a new successful fetch is a new observation even if its values have not changed.

Pagination now retains all captured revisions, including repeated provider IDs across pages. Its existing selected/latest-listing view remains available, and run counts still measure unique provider IDs. This matters when a later page repeats a home with a changed price: the ledger retains both revisions while filtering uses the latest received property. Cancellation carries already received revisions into the existing accounting finalizer.

Source text stores per-field observation IDs. Selection includes only IDs belonging to the description/features actually supplied to analysis. Both model phases receive the same bounded, escaped text, and the completed assessment preserves those selected inputs and IDs through a database reload. Legacy rows and mocked/older callers without acquisition provenance receive no fabricated evidence IDs or observation times.

This is a local implementation commit, not source qualification, activation of the collector, a remote intake endpoint or a production deployment. No paid request, portal request or notification was needed for this slice.

## Acquisition and provenance

The HTTP adapters stamp receipt time immediately after their fetch returns and before parsing. Rightmove's Crawlee adapter stamps the successful response handler before application parsing; that timestamp is not claimed to be the exact network arrival time. Observation time is independent of `Property.first_seen`, provider dates, database receipt time and the later assessment time.

Each adapter records a parser name/version and a SHA-256 fingerprint of its adapter file, shared pagination/parsing/observation code, and Rightmove's payload decoder where applicable. This identifies the shipped parser implementation; it is not a hash of the complete interpreter/dependency environment. Event IDs are generated once per successful page result and retained as the property moves through pagination, filtering and persistence. General property serialization excludes the transient capture object, so loading a current row cannot manufacture a new receipt.

These observations are explicitly **normalized-only**. Existing transports do not retain raw response bodies, so `raw_capture` remains absent. A normalized ledger checksum supports field replay; it cannot reconstruct omitted HTML or replay a parser. The existing PR18a capture tool remains the means to retain bounded raw bodies. Connecting capture manifests to normal collection and defining its operational retention policy remain outstanding.

Known tracking parameters/fragments are removed from evidence URLs only for recognized portal hosts. Unknown query parameters fail the strict normalized contract; they are not silently copied into evidence. The legacy property URL is unchanged in this slice. This normalization does not authenticate the provider, validate a fetched target or authorize access. Invalid normalized records count as parse errors and are excluded while valid neighboring records survive.

## Rightmove metadata verified against retained material

The previously retained search body, `experiments/modernization/data/collection-v1/01-rightmove-search.html`, has SHA-256 `afcc377a5dd7939e15e47974b865de8a4b15bc42598fc221cb8aefc8cd2b26b9`. The sanitized fixture remains unchanged, with SHA-256 `d922dbe46c64e5e4b0ee2c6cfc7a0023acf51200549442613c1c8d68a2411751`. Inspection used those local files; no fresh portal response is implied.

The adapter now retains:

- `firstVisibleDate` in a separate `first_visible_date` field, with its exact literal and aware instant when supplied.
- `listingUpdate` as a portal date with the reported reason preserved. The retained `price_reduced` example is a reduction date, not a first-listing date.
- `letAvailableDate` as availability evidence, not an automatic change to the application's availability projection.
- Explicit `customer.branchId` and supplied branch name; no agent identity inferred from marketing text.
- `numberOfFloorplans`, including an observed zero, and nonempty `keyFeatures` when supplied.

The raw example's first-visible time is 15 September, reduction time 22 September and availability time 18 November. Collapsing those into a single “listed at” timestamp would lose meaning. Tests retain the real field shapes and separate semantics. The sanitized fixture covers first-visible/branch/floorplan metadata; tests add the verified update/availability shapes in memory without changing the frozen fixture. Unrecognized date wording is preserved without an invented timezone or instant. `updateDate` and other unverified source fields are not assigned new semantics.

## Persistence and rollout limits

The ledger joins the existing task-owned transaction; it never commits its caller's work independently. Large source batches are divided only to respect the ledger's 100-event/4 MB validation bounds. The surrounding source write remains atomic across every chunk. Tests inject a source-row failure and a reused event identity with different content: neither can leave orphan evidence or partially overwrite the current listing.

The search text projection now uses the observation's actual description/features, time and IDs, rather than later enriched or inferred property values. Existing sparse/stale/detail-precedence rules remain in place. Search-area postcode inference can still populate the legacy current view, but it is deliberately absent from source evidence unless the adapter actually parsed it.

Current price/title/location rows still follow the older application's update rules. In particular, this slice does **not** make all visible projections arrival-order independent, implement conflict display, or replace legacy receipt-based `last_seen` semantics. The foundation's deterministic replay remains a separate comparison surface. That switch needs its own projection tests and isolated-copy comparison before claiming the whole PR06b contract is implemented.

The existing text-selection policy remains version 1 because its character/feature limits and selected text have not changed. New source hashes include the added evidence-reference fields; stored historical assessment inputs are not rewritten. Complete image-byte identity, detail provenance, evidence retention tied to assessments and artifact reuse remain later work.

## Validation and handoff

The focused integration suite passes 37 tests: all search boundaries, malformed-neighbor handling, metadata semantics, repeated revisions, cancellation, atomic rollback, idempotent fallback, batching, unique pipeline counts and actual mocked two-phase request/save/reload references. External network calls are blocked. The complete suite passes **2,957 tests on each of Python 3.11 and 3.14**, with branch-inclusive coverage **88.24% / 88.15%**. Ruff checks/format verification pass across 218 files; strict mypy passes across 94 source files. Private logs and coverage XML are under `/tmp/home-finder-pr06b/search-{full,coverage}{311,314}.*`. No dependency, browser-facing or schema change calls for a repeated runtime audit, browser run or production-copy migration in this slice.

Next: attach detail observations at their successful response boundaries, ensure each portal uses its own actual provider ID, and retain received detail evidence before image downloading can fail or be cancelled. Then connect deterministic field replay to current projections on the isolated production copy. Authenticate a bounded observation transport only if the final collector topology requires a separate host. Keep raw-capture qualification, source permission, human labels and budget activation as independent operating gates; none is silently satisfied by this commit.
