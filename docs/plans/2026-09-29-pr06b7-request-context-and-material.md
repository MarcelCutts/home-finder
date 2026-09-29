# PR06b-7: Keep request context separate from source content

**Date:** 29 September 2026. **Predecessor:** source quantities `7361f43`.
**Scope:** receipt context and versioned material fingerprints. This finishes the local observation-contract groundwork needed for isolated identity work; it does not qualify collection, activate a remote writer or complete downstream projection/artifact work.

## Result

All four search adapters now retain their requested area, price/bedroom bounds, furnishing filters, minimum bathrooms, let-agreed setting and zero-based page index with each successful observation. The requested URL argument is represented by its SHA-256, not by persisting a raw query URL or headers in the normalized record. Each page's observations share its context; receiving the same listing on another page retains a distinct receipt and that page's request fingerprint.

Detail adapters retain the fingerprint of their requested listing URL. They do not invent a search query or page number. Offline replay also fingerprints the retained request and leaves structured query/page context absent when it was not captured in that form. A capture's attempt index is not mislabelled as a portal page number. Legacy observations with no request context remain unchanged and explicitly lack it.

`CollectionQuery` is bounded, typed collector configuration, outside `ObservationFields`. It is not proof that a portal honored the query, a property postcode, a personal evaluation profile or a source-reported fact. The schema does not include commute destinations or messaging credentials. Query metadata does not enter selected listing text. Request fingerprints identify the supplied request argument; they do not authenticate the producer, validate a URL for fetching, prove a redirect's target or reconstruct an unavailable wire transcript. A digest is not encryption or a public-data entitlement: keep operational metadata private. Normalized-only captures still cannot reconstruct unretained HTML.

Failed, empty and partially acquired queries require their own collection-attempt records and coverage policy in PR09b. This slice records the context of retained property observations, without manufacturing properties or successful coverage for a page that produced none. Rightmove's timestamp remains handler-entry time as documented in PR06b-2, rather than a newly claimed exact wire-receipt instant.

## Content and receipt identities

`SourceObservation.sha256` remains the immutable complete-receipt checksum. New clocks, event IDs, parser revisions and request context correctly produce different receipt payloads. Two derived fingerprints now expose **material policy version 1**:

- `material_sha256` covers provider identity, canonical listing URL, normalized field values/states and explicit normalization omissions. Receipt/producer IDs, observation time, parser provenance, request scope, raw-capture references and correction explanations are excluded. Missing and cleared fields remain different. Empty and absent omission maps have the same meaning.
- `field_material_sha256(name)` covers one field's reported value/state plus provider identity and field name. An absent field returns `None`; an explicit clear has its own digest. A price change alters the price fingerprint while leaving an unchanged description fingerprint alone.

These are fingerprints of the **observed subset**, not a merged current projection or a complete extraction cache key. A sparse response can have different material while the richer selected current text remains unchanged. A price-only change also changes the full material digest. PR14 must choose the actual text/image/fact dependencies and combine them with prompt/schema/model/policy versions; it must not use every new receipt or the entire observation indiscriminately to trigger paid extraction. No scheduling, caching, retry or spending policy is activated by these new properties.

## Verification

Tests cover distinct real adapter page boundaries with identical material, all four search/detail receipt paths, typed bounds and boolean behavior, exact request fingerprints without raw query/credential strings, query separation from selected facts, parser/time/producer changes, field-specific changes, missing versus clear versus normalization omission, and real SQLite round trips. Old payloads remain byte-equivalent when no context is present. The focused set passes **104 tests** on Python 3.14. An earlier expanded run passed 579 tests with one new fixture incorrectly assuming Zoopla shared the other adapters' delay constant; that fixture now mocks Zoopla's actual delay boundary.

Full suites pass **3,089 tests on each of Python 3.11 and 3.14**, with branch-inclusive coverage **88.62% / 88.54%**. Ruff checks/format verification pass across 227 files and strict mypy across 98 source files. Final logs/coverage XML are `/tmp/home-finder-pr06b/context-{full,coverage}{311,314}.*`; source/test files stayed unchanged during those runs.

The private rehearsal reread the PR06b-5 replay of the retained Rightmove bytes and ran the current parser over the same derived fixture. Its older observation still validates with its original payload checksum, `0e5b01ea1321caae0b861af2372b227c90eb46cb62eb7714d65bf1fd7353cce7`. The new parser/context produces receipt `9497b9d18c86322237162f1b6581c4d1661361e1874ec1b67602f9ef0439b868`, while both have identical material digest `0731849f70a9cfa78b6470910e71e7f985fb514bbf8422ec62fb1de2ed82c5bb`. Description and feature digests also agree. Original observation time, raw reference, capture manifest and body bytes are unchanged. Query/page metadata is not invented for this detail fixture.

Report: `data/material-replay-2026-09-29/verification.json`; program: `/tmp/home-finder-pr06b/material_replay_probe.py`. This is a parser-generation comparison over the same development evidence, not independent human correctness or new acquisition. It made no network request, model call, notification or database change.

## Compatibility and next segment

No schema migration or dependency change is needed. Request context is optional and excluded from serialized payloads when absent. The material fingerprints are derived properties and do not rewrite or add fields to existing stored receipts. New context requires a compatible reader; preserve the reader and immutable payloads on rollback.

Proceed to PR07 in bounded slices, beginning with the existing destructive merge paths: a retroactive merge currently deletes absorbed properties together with price, status, assessment and saved-message rows. Preserve those records and make identity/membership decisions reviewable before broader reconciliation or new canonical IDs. The protected historical archive remains available; historical reconciliation begins with an isolated dry-run report.

Remaining activation/parent-epic work is explicit: source access and sustained adapter qualification; collection-attempt coverage in PR09b; non-text current projections in PR08b; authoritative episode/work handling in PR12b; artifact dependencies in PR14; retention scheduling/monitoring; and authenticated remote transport if measured collector placement requires it. None prevents developing the isolated PR07 preservation contract, and none is declared operationally complete here.
