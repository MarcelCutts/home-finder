# PR06a: Retain listing text through analysis

**Date:** 28 September 2026. **State:** committed as `19d6f24`; not deployed.
**Parent:** [delivery plan, PR06](2026-09-28-research-synthesis-and-delivery-plan.md#pr06--preserve-descriptions-features-corrections-and-source-provenance-l).
**Predecessor:** PR04, commit `55f3171`. No live collection, model calls or notifications.

## Behaviour and evidence contract

Fetched descriptions and features now survive detail enrichment, all four merged-property save paths, sparse search refreshes, aggregate rebuilding, cached reload and both actual model requests. The prior enrichment selected a description/features pair and then discarded it; the analysis path also passed `features=None`. A regression assertion failed before the repair. Source text is retained even when an image download subsequently fails, so the retry does not discard text already received.

`ListingTextEvidence` stores description and features independently, with per-field UTC observation times and origins (`legacy`, `search`, `detail`, `correction`). `None` means unavailable. Regular empty/blank input preserves the current value. A deliberate clear requires an explicitly dated correction; older/equal timestamps cannot overwrite newer evidence. Search/legacy values cannot replace retained detail/correction values. Undated input can fill a gap but cannot claim to be a newer correction. The current raw search `Property` does not carry a field observation time, so its description deliberately follows that conservative undated rule. No historical observation dates are invented.

Migration 008 adds nullable `source_listings.text_json` and `properties.source_text_json`. Source-specific data is written to matching provider/URL rows already belonging to the canonical property, or unlinked rows. A URL owned by another canonical record is not reassigned by this text repair. Evidence for a missing provider row remains in the canonical projection without fabricating a provider ID. The canonical `description` comes from its own provider; another portal's longer text cannot silently replace it. Existing legacy description maps remain readable. SQL helpers join PR04's owned transaction and never commit independently.

The merge policy is explicit application behaviour: [SQLite UPSERT](https://www.sqlite.org/lang_upsert.html) supplies conflict handling, but does not define absent-versus-clear semantics. The typed evidence and selected-input record use [Pydantic JSON serialization](https://pydantic.dev/docs/validation/latest/concepts/serialization/) for tuples and UTC datetimes rather than ad hoc string conversion.

The existing model allowance is shared across sources: at most 3,000 description characters and 15 features, each bounded to 200 content characters plus its provider label. Features are selected round-robin so one portal cannot consume the whole allowance. Markup is escaped before selection, sources remain labelled, and disagreements remain visible. Escaping and prompt instructions reduce accidental delimiter confusion; they are not a proof against model prompt injection.

Both visual and evaluation requests receive the same selected listing evidence. Each completed analysis stores an application-generated `listing_text_inputs` record with those exact bounded inputs, provider URLs, normalized evidence hashes and selection-policy version. The model cannot supply this backend record through its response schema. Full current text remains stored separately. Hashes are references, not archives: immutable full-text revisions, parser/raw-response provenance, retention policy and artifact reuse are still PR06b/14. One current text/URL selection per provider also remains; richer multiple-listing identity is PR07.

A conservative council-tax check rejects a model's claimed band unless the actual selected listing description/features contain a single matching, non-estimated band. Absent, conflicting, budgeting or assumed mentions yield `unknown`. This verifies support in the supplied portal text, not the VOA record or the correctness of the portal itself. Broader factual presentation, EPC support, tenant costs and legacy inferred facts are the next PR17a slice.

## Verification

New regressions exercise every persistence path, source attribution, sparse updates, independent field freshness, explicit clear/restart, stale arrivals, conflicting portals, legacy richer projections, foreign canonical ownership, deduplication, timezone validation and bounded HTML/Unicode inputs. The save/reload test preserves the user's status, note, price history and saved viewing message.

The end-to-end test runs mocked source/detail retrieval, enrichment, save, reload, both actual `messages.create` request builders, response validation and analysis persistence. It asserts that conflicting source descriptions and features reach both model payloads. The existing generic `Band C est.` budgeting context is deliberately still present in that test; a mock response claiming C without listing support becomes `unknown`. No paid API is invoked.

| Check | Result |
|---|---|
| Focused evidence, enrichment, persistence and model regression subset | 70 passed |
| Chromium dashboard suite | 55 passed; two existing deprecation warnings |
| Ruff lint/format and strict mypy | Passed; 171 formatted Python files, 67 source files checked |
| Full Python 3.11.16 suite | 2,486 passed; four existing deprecation warnings; 85.97% coverage |
| Full Python 3.14.7 suite | 2,486 passed; four existing deprecation warnings; 85.87% coverage |
| Reference migration and restore rehearsal | Passed, schema 8; original columns of all 13 tables unchanged; integrity and foreign-key checks passed |

The reference SHA-256 remains `e50897b46ebefea0119fd62951ac277d764684f9590d52801e66c964b1352c63`. Only a consistent read-only backup copy was initialized. Preserved counts include 1,016 properties, 3,267 source rows, 1,001 analyses, 106 price events, 30 status events and one saved viewing message. Missing historical evidence stays missing; there is no bulk refetch, invented source text or analysis rebilling. Logs and the migration/restore report are under `/tmp/home-finder-pr06a/`.

## Rollout, recovery and next segment

Deploy only through the protected manual release path after the outstanding operational gates. Confirm a bounded new observation retains text and selected model inputs before broader activation. No live database has been migrated by this work.

Schema 7 code rejects schema 8. Recovery therefore needs a protected compatible build/backport retaining the new columns; do not drop evidence or restore an older database over subsequent user events. The earlier consistent backup is a rehearsed disaster-recovery input, not permission to discard post-cutover changes.

**Next: PR17a**, remove unsupported tenant cost totals, property facts and market guidance. PR06b remains a later immutable-observation slice. Continue sequential verified commits under the owner's latest instruction; the whole delivery goal remains incomplete.
