# PR08a: Consistent price ranges and target-specific availability

**Date:** 28 September 2026. **State:** implemented and verified; not deployed.
**Parent:** [delivery plan, PR08](2026-09-28-research-synthesis-and-delivery-plan.md#pr08--repair-price-projections-and-evidence-based-availability-m).
**Predecessor:** PR16a, commit `3e0c05d`. No schema migration, portal requests, paid calls or notifications.

## Price behaviour

A synthetic same-source £1,900→£1,750 change previously left `min_price=max_price=1900`. It now updates the canonical source quote, selected price, minimum/maximum and price history in the same owned transaction. That range uses the newly observed quote plus other attributed source-listing prices linked to the home. A single attributed quote produces a single-price range. An old unattributed aggregate range is not invented as another source's current quote.

The existing cross-run path can also change the selected price after linking a new provider, using the explicit `cross_platform` history marker. That behaviour is preserved: its range includes all linked quotes and the selected price, and it never rewrites the original provider's quote to the other portal's amount. An explicitly named different provider is not accepted as a same-listing update. Source rows owned by another golden record are not rewritten or reassigned. Existing rule-based value/fit recomputation is preserved, with no new image analysis. An injected failure inserting price history rolls back the source quote and projection as well.

This is the current-update repair, not the PR08b rebuild. Retained source prices can still be stale or off market: freshness/availability eligibility, consistent selection across identity changes, projection revisions and the legacy repair remain PR08b. In particular, the legacy price-history comparison still uses the previous selected price, which can itself have come from another portal; it is not a complete per-source rent history. The equal-price/no-change path does not perform a bulk historical correction. Collection of changed known listings before early-stop/filter decisions remains PR09a.

## Availability behaviour

The baseline marked a Rightmove target `let_agreed` because an unrelated recommendation said “Let agreed”. It also marked an OpenRent target removed because navigation contained “Homepage”. Both now return unknown without target-bound evidence.

The new pure parser requires a supported provider host/listing path and matching requested/final listing IDs. It reads only explicit primary records: Rightmove `PAGE_MODEL.propertyData`, Zoopla `__NEXT_DATA__.props.pageProps.listing`, and OnTheMarket `props.initialReduxState.property`. Primary IDs must agree with the requested listing. Known status strings/tags and explicit true flags are supported; empty or unfamiliar fields do not prove active availability. JSON parsing respects quoted braces. It also supports a target-URL-bound JSON-LD offer with recognised Schema.org availability, and exact primary status headings/badges under `main` when the canonical URL identifies the same listing. Hidden, unrelated-ID, recommendation, navigation and aside content is excluded from that heading path.

These are **fixture-backed contracts, not a claim that all portals currently emit these fields**. New/changed layouts require representative retained fixtures and source qualification. No live listing was fetched during this segment. A parser that cannot establish the target or its status reports unknown, which can trip the existing source circuit breaker rather than mark the catalogue unavailable.

| Response/evidence | Result |
| --- | --- |
| Recognised target-specific available status | Observed active on that source |
| Recognised target let-agreed/under-offer status | Let agreed; distinct from a disappeared URL |
| Recognised target removal status | Removed on that source |
| 410 at the same recognised target URL, without a challenge | Source URL removal signal; not proof that the home was let |
| Bare 404 | Unknown; explicit target removal evidence can still support removal |
| Challenge header/body, including on 404/410 | Unknown |
| Generic search/homepage redirect, different listing/host, blank page, timeout, 403/429/5xx | Unknown |
| Conflicting structured/primary status | Unknown |
| Status text in recommendations, descriptions, unrelated scripts or navigation | Not target evidence |

The maintenance command aggregates by **source and URL**, so multiple linked listings from one provider cannot overwrite one another. It deduplicates fetch requests. All known URLs must have removal/let-agreed evidence before the golden record becomes off market. Omitted responses and sources excluded by `--scrapers` count as unknown for that decision; a current active result on any known URL allows return to market. Unrequested batch results are ignored. Existing per-source state, check timestamps and return history remain; an unknown attempt does not erase the last positive state.

Reason codes are logged without raw response previews. This slice does not add the complete durable latest-outcome/evidence revision model: existing check timestamps still mean an attempt, not successful verification, and the legacy default `is_off_market=0` is not proof of availability. PR06b/08b/09b retain that explicit work.

## Primary-source verification

Checked 28 September 2026:

- [RFC 9110, 404](https://www.rfc-editor.org/rfc/rfc9110.html#name-404-not-found) permits temporary absence or deliberate nondisclosure. [410](https://www.rfc-editor.org/rfc/rfc9110.html#name-410-gone) indicates likely permanent unavailability of the source resource. Neither establishes that a tenancy was agreed. The parser's conservative unknown treatment for a bare 404 and generic redirect is application policy.
- [Cloudflare's challenge-response documentation](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/) identifies the `cf-mitigated: challenge` header. Header names are handled without case sensitivity; a challenge takes precedence over status/removal wording.
- [Schema.org availability](https://schema.org/availability) defines offer availability. Recognised values are interpreted only after binding the item and offer URLs to the target; this documentation does not establish any particular portal's current use of the markup.

## Verification and recovery

- Controlled baseline reproduced the stale range and both whole-page status false positives; `/tmp/home-finder-pr08a/baseline.json` records the results.
- Initial focused suite: 153 passed. Expanded DB, pipeline, availability, integration and HTTP suite: 623 passed, with four existing deprecation warnings.
- Fixtures cover every provider, exact and conflicting IDs/statuses, quoted JSON braces, absent/malformed data, misleading recommendations/links, challenge precedence, same-source multiple URLs, partial source selection and reappearance/history.
- Price tests cover increases/reductions, linked alternate quotes, source mismatch, cross-platform selection without quote corruption and transactional rollback; existing deterministic value/fit tests remain.
- Final price/cross-run subset: 48 passed. The first full matrix exposed three failures in the existing `cross_platform` selection path; that path was repaired without weakening its assertions, and source-quote preservation gained a regression test.
- Final full Python 3.11.16 suite: 2,525 passed; 86.53% branch-inclusive coverage; four existing deprecation warnings.
- Final full Python 3.14.7 suite: 2,525 passed; 86.43% branch-inclusive coverage; four existing deprecation warnings.
- Ruff lint/format, strict mypy and diff checks passed (177 formatted Python files, 71 source files). No templates/assets changed, so the already-passing browser suite was not repeated for this segment.

Read-only reference inspection reconfirmed 66 of 1,016 historical prices outside their range, including 65 equal-ended ranges. All 1,016 lacked an availability-check timestamp. No historical rows were rewritten and no migration/initialization was run on the reference. SHA-256 remains `e50897b46ebefea0119fd62951ac277d764684f9590d52801e66c964b1352c63`. Logs/audit outputs are under `/tmp/home-finder-pr08a/`.

There is no new schema. A protected compatible rollback must preserve conservative unknown handling and price consistency; returning to whole-page status matching is not an acceptable fallback. Schema-8 compatibility from PR06a still applies. Historical repair must use PR08b's backup/rebuild/reconciliation procedure, not fabricate observations or price changes.

**Next: PR09a**, retain fetched known-page observations and finalize counters on every exit, including partial scraping. PR12a then removes discovery-dependent recovery and repairs scheduling/run exclusion.
