# PR06b-3: Retain detail evidence before image work

**Date:** 28 September 2026. **Predecessor:** search capture `f305e04`.
**Scope:** normalized detail receipts, correct provider IDs during enrichment, and durable text references. This is another PR06b integration slice, not completion of the full observation/projection epic.

## Result and concrete defect

Detail enrichment previously constructed every source request using the canonical property's `source_id`. A home anchored to OpenRent could consequently ask the Rightmove parser to validate its page against an OpenRent ID. The indexed Rightmove decoder correctly rejects a mismatched ID; the orchestration supplied the wrong one.

Enrichment now resolves the ID from each recognized portal listing URL, using the same explicit host/path rules as availability assessment. The canonical source can retain its existing identity when a legacy URL is not understood. An unresolved noncanonical URL is skipped with an identity diagnostic; it no longer borrows another portal's ID. This is request-identity correction, **not** reconciliation of historical backfills, aliases, relistings or ambiguous canonical membership. Those remain PR07 work.

All four detail adapters stamp a successful response before application parsing and attach a normalized observation to the parsed result. Enrichment commits that observation and its source-text update **before** downloading the first image. Image failure, a cancelled download, or repeated cancellation during persistence cannot discard an acknowledged receipt. A database write failure propagates as `DetailEvidenceError`; it does not become an ordinary provider/enrichment failure that could continue to paid analysis.

The production pipeline already passes storage into enrichment. That argument now supplies the durable sink as well as cached images. Calling enrichment with a freshly captured observation and no storage raises an explicit error before image processing. Direct parser callers can inspect `DetailPageData`; they are responsible for retaining it if used outside this pipeline. Legacy/test adapters without capture metadata retain their previous current-text behavior and acquire no fabricated immutable IDs.

## What is captured

The detail observation contains only fields parsed from that response: description, features, postcode, paired coordinates and supported selected gallery/floorplan URLs. Search price, bedrooms, address and title copied into the temporary request object are **not** mislabelled as detail facts. A parser result with no represented fields does not manufacture positive evidence.

Rightmove also retains the verified customer branch shape and the length of an explicitly present floorplan list. An explicit empty list produces an observed zero; an absent list does not. The retained indexed fixture proves the zero-plan case. Legacy positive-plan fixtures exercise the older parsing shape; this does not close PR10's requirement for a fresh verified positive-plan indexed response.

The previously captured original Rightmove detail body has SHA-256 `5a8f6defa750357dd9af2e1398cf90e8cec9eb4d5482d9c7fcb784e08bc6cc7f`. Its decoded `customer` contains `branchId` and `branchDisplayName`, its `floorplans` is an empty list, and `listingHistory` supplies only a reason. Inspection therefore supports the branch/count mapping, but supplies no new detail listing-date instant. This slice used that local capture and saved fixtures; it made no fresh portal request.

URL sets are the subset selected by existing parser limits and EPC exclusion, not complete source inventories. They do not prove downloaded content, image identity, image classification or rights to retain/reuse it. If any URL in an optional set fails the query-free/credential-free evidence contract, the entire set is omitted and `normalization_omissions` records `unsupported_url`. This preserves usable text without retaining signed/credential parameters or presenting a partial list as complete. Existing media download behavior is unchanged; blob checksums and durable analysis image inputs remain PR14/asset work.

As with search capture, normal detail observations are **normalized-only**: no raw response reference is invented. Parser name/version and implementation fingerprints include the detail adapter, observation normalizer, shared identity/challenge helpers, and Rightmove payload parser where relevant. The ledger does not retain every parsed application value yet. In particular, the existing converted/rounded floor-area fields have not been promoted into an exact source-quantity contract; original units, ranges and interpretation need their own evidence mapping before that claim can be made.

Challenge headers/body markers and non-200 responses are rejected before capture for every adapter. Empty parsing and failure still use the existing detail-result API; this does not implement the broader typed stage taxonomy in PR12b or establish full target-page qualification on every source. An ID parsed from a URL is not proof that a redirect or response body belongs to that target. Python's URL parser also requires explicit application validation; the shared helper rejects user-info, unexpected ports, wrong hosts and non-listing paths. [Python URL-parsing security](https://docs.python.org/3.14/library/urllib.parse.html#url-parsing-security).

## Persistence, cancellation and assessment references

`retain_detail_observation` joins ledger ingestion and current source-text refresh in one owned transaction. Failure in the source projection rolls the ledger insert back. Replaying the same producer/event identity retains one receipt. Evidence can survive without a corresponding source row, so an unresolved historical row cannot erase an independent observation or force fabrication of a replacement source identity.

The bounded write uses the existing `finish_before_cancelling` helper: retain the persistence task, wait until it settles despite repeated parent cancellation, then propagate cancellation. Merely wrapping a detached task in `shield` would not guarantee the caller waits for it. No provider request or image download is shielded. [Python task cancellation and shielding](https://docs.python.org/3.14/library/asyncio-task.html#shielding-from-cancellation).

The text model converts the exact observed description/features into current evidence with per-field timestamps and observation IDs. Those IDs survive save/reload, sparse search refresh and aggregate rebuild. The actual mocked visual and evaluation requests both receive the selected escaped text from two sources; the saved assessment points to both durable detail receipts. Image transport failure cannot erase the text already committed.

Offline `capture_replay` disables fresh receipt generation and excludes that internal capture object from its extraction report. Its existing capture time remains separate from replay time, including when capture time is unknown. Parsing old HTML never creates a new dated provider receipt.

## Verification, compatibility and next slice

The focused offline suite passes **649 tests**, covering all detail adapters, search regressions, challenge/non-200 handling, correct cross-source IDs, unknown identities, URL omissions, save/reload and actual assessment requests, replay, rollback and repeated cancellation. An initial Python 3.11 matrix run caught a type-only forward reference evaluated during import; postponed annotations fixed it, followed by 48 focused tests on 3.11 and a fresh full matrix. The final suite passes **2,983 tests on each of Python 3.11 and 3.14**, with branch-inclusive coverage **88.32% / 88.24%**. Ruff checks/format verification pass across 220 files; strict mypy passes across 95 source files. The authoritative final logs/coverage XML are `/tmp/home-finder-pr06b/detail-stable-{full,coverage}{311,314}.*`; the earlier failed/interrupted runs remain separate. No paid call, notification, live collection or deployment occurred.

No database migration is needed. The optional omission field is excluded from serialized observations when absent, so existing payload checksums remain unchanged. This extends the in-development v1 reader: an older strict reader cannot parse a new record containing the added field. A rollback must retain compatible readers and the immutable evidence; it must not strip fields or rewrite checksums to force a downgrade. New tests also replace three impossible noncanonical fixture URLs with valid numeric provider paths; their behavior assertions remain intact.

Next: connect deterministic ledger selection to current text, including authority-before-time ordering, sparse fields, explicit corrections/clears and equal-rank conflicts. Compare the result on an isolated production copy, protect personal/status/price history, and keep assessments tied to the fields actually used. Then finish raw-capture retention/association and any authenticated collector transport required by the chosen topology. Current non-text projections and historical identity reconciliation remain explicit follow-on work; this commit does not activate the final authority cutover.
