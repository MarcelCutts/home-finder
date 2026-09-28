# PR06b-4: Derive current assessment text from retained observations

**Date:** 29 September 2026. **Predecessor:** detail capture `d736289`.
**Scope:** deterministic current description/features, explicit conflicts and corrections, and the saved text supplied to analysis. Non-text projections and the complete observation/authority cutover remain follow-on work.

## Result

Received detail text now takes precedence over search summaries by authority first, then observation time. Sparse arrivals cannot erase it. Equal-authority, equal-time observations with different values produce an explicit conflict, retaining every winning observation ID and withholding the disputed values. A later authoritative observation or reasoned correction can resolve it; an explicit correction can clear a field. Repeated or reordered delivery produces the same result for captured evidence.

Description and features are selected independently. Equal-ranked identical values combine their evidence IDs. Current SQL selection and the in-memory merge use the ledger's existing field-selection rules. SQL reads every winning tie, including receipts saved before a source row existed, with bounds of 512 winning rows and 4 MB of value JSON; excessive ties fail explicitly rather than selecting a truncated winner. Per-field reference lists are also bounded to 512 IDs.

Historical text without immutable IDs remains readable. Undated values cannot invent capture times or revise dated values. Unreferenced equal-time disagreements retain the previous legacy value; the deterministic replay claim applies to captured observations, not unknowable historical receipt ordering. A missing ledger table is tolerated only in the existing pre-version-10 migration construction path; in a current database it is an error and the write rolls back.

The internal `retain_text_correction` API accepts only reasoned description/features corrections. It saves the observation and projection atomically. This adds no externally accessible correction endpoint or automatic correction policy. Current source-row saves, aggregate rebuilds and detail retention all use the same merge rules.

## Actual model inputs and dashboard

In-memory properties retain their original captured search text and features instead of substituting a later inferred description. Before analysis, the bounded candidate batch loads its saved current text in one consistent database read scope. This covers fresh discoveries and recovery candidates: saving richer retained text is no longer followed by analysing the older in-memory object. A missing saved candidate fails explicitly.

Text-selection policy version 2 retains the existing escaping, 3,000-character description and 15-feature bounds. Conflicts supply a plain statement of uncertainty instead of either disputed value. The selected marker retains the winning observation references; actual selected text and source hashes remain in assessment provenance. Legacy policy-version-1 assessments remain readable. An explicit clear contributes no selected text; its evidence remains in the source state rather than being misrepresented as a supplied sentence.

The detail page displays a short source-specific notice when listing text conflicts. Both disputed descriptions are withheld. Resolving the conflict removes the notice and displays the new description. Existing stored quality assessments are not automatically invalidated or rewritten: dependency/freshness handling belongs to PR14. Price, title, location, source freshness and availability still need their separate projection work.

## Verification

- Pure selection was checked against the independent ledger projection over all arrival permutations for authority, conflict, clear and resolution scenarios, including repeated reverse replay. SQL tests cover receipts before source creation, all current save paths, rebuilding, identical ties, explicit bounds and current-schema corruption. Notes, viewing messages and price history survive.
- A real-storage pipeline test verifies that the quality-filter entry point receives retained detail and its exact IDs even when its incoming discovery object has no source-text map. Conflict text cannot supply a council-tax Band C/D fact. Route tests verify withholding and correction in the rendered detail page.
- A synthetic local browser fixture was inspected at 1,365 × 1,000 and 390 × 844. The notice is visible, neither disputed description appears, and mobile content has no horizontal overflow. External requests were blocked. Screenshots: `/tmp/home-finder-pr06b/output/playwright/text-conflict-{desktop,mobile}.png`. The preview server and browser session were closed afterward.
- Full suites: **2,999 tests pass on each of Python 3.11 and 3.14**, with branch-inclusive coverage **88.37% / 88.29%**. Ruff checks/format verification pass across 221 files; strict mypy passes across 95 source files. Final logs and coverage: `/tmp/home-finder-pr06b/text-{full,coverage}{311,314}.*`. An earlier expanded run found the historical-schema fixture compatibility issue; it was fixed and the full matrix rerun.

The isolated production-copy rehearsal used the verified PR05a backup, SHA-256 `e1494adf0f5b174cd9a2e142f50aa25b8ffd5a85ba3f4d66383a3e8762cea639`. Migration from schema 6 to 10 preserved all original columns and rows in all 13 application tables, including 1,016 properties, 3,267 source rows, 1,001 assessments, 106 price events, 30 status events and the saved message. SQLite internal statistics/sequence tables were excluded from that logical comparison.

Five synthetic observations were then added only to the isolated working copy. Conflicting detail receipts before source creation were recovered; a clear survived a later search summary; a later correction resolved the text; stale duplicate replay changed neither evidence count nor selected text. Original application rows remained byte-equivalent at the column-value level. Integrity was `ok`, foreign-key errors were zero, and the original backup checksum was unchanged. Program and report: `/tmp/home-finder-pr06b/text_projection_probe.py` and `/tmp/home-finder-pr06b/text-projection-rehearsal/rehearsal.json`. These synthetic additions are test fixtures, not new production evidence or human reference labels.

## Compatibility and next work

No schema migration or dependency change is needed. The current-text model adds optional conflict flags; new assessment inputs state their selection-policy version. Rolling back readers that ignore these flags would hide uncertainty and lose the new selection behavior; retain compatible readers and immutable observations. No raw capture, database, screenshot or personal data is committed.

Next complete bounded raw-capture association/retention and exact source quantities where supported, documenting what can actually be replayed. Remote authenticated transport remains conditional on collector placement; no second writer is activated merely to finish a checklist. PR07 identity and PR08b non-text projection work follow the completed observation contract. There were no portal requests, paid model calls, notifications or production changes in this slice.
