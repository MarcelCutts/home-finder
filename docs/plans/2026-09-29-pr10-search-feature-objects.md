# PR10 follow-up: structured search feature objects

Date: 29 September 2026. Parent: the [delivery plan](2026-09-28-research-synthesis-and-delivery-plan.md#pr10--repair-and-fixture-the-rightmove-parsing-contract-m).

## Trigger and repair

The owner's one-property pipeline smoke reached Rightmove successfully, but the application rejected every record on its first search page. The new discovery event exposed `collection_status=failed`, one fetched page, ten parse errors and zero parsed listings. No model request was admitted or charged on that attempt.

A separate, single diagnostic GET to the same search URL returned HTTP 200 and a structured search payload containing ten records, representing nine distinct property IDs. All ten failed validation because `keyFeatures` contained objects with `order`, `description` and `htmlDescription`; our observation model requires source text strings.

The parser now extracts each object's plain `description` in its received order. Existing string features continue to work. Missing or malformed descriptions remain parse errors; the decoder does not stringify arbitrary objects, silently discard invalid feature values, or weaken identity/price validation. Search warnings now include counts by field/error type, without echoing validation input values.

Two contract tests cover the object representation and malformed feature values. They use small constructed examples rather than committing portal response bodies. The existing search/detail/replay contracts remain in force, including rejection of an unqualified empty page.

## Evidence

The diagnostic fetch was a new request, **not the exact response body from the failed application run**:

| Item | Result |
| --- | --- |
| Private directory | `data/smoke-runs/2026-09-29-rightmove-search-diagnostic/` |
| Body | `search.html`, 572,606 bytes, HTTP 200, `text/html` |
| Observed | 2026-09-29 13:09:12 UTC |
| SHA-256 | `9cafa7ec39336f92afc615160dafe06f3c538a994a511c903d2e99c3985c9342` |
| Before repair, offline | 0 properties; 10 `features.value` string validation errors |
| After repair, same bytes | 10 records; 9 unique IDs; 0 parse errors |
| Source-specific regression/replay checks | 109 passed |
| Subsequent application search | 1 page; 9 distinct listings; 0 parse errors |

The diagnostic manifest is a local debugging record, not a qualified PR18 capture manifest. Its explicit retention deadline is **6 October 2026 at 13:09:11 UTC**. It is not covered by an automatic expiry scheduler; remove that specific private diagnostic body at the deadline. Preserve the hash and non-content findings in this handoff. Existing captures were not deleted.

The successful application run subsequently enriched one property and completed both analysis phases; its separate smoke handoff records the budget, artifacts and assessment limitations. Search records received before candidate limiting are retained honestly, so a one-property analysis limit does not imply that only one listing appeared in the search response.

## Remaining scope and rollback

This is a verified parser repair for the observed representation. It does not qualify every Rightmove query, pagination, typeahead, empty response or transport; nor does one successful page establish sustained collection reliability. The new payload includes a string `resultCount`; that observation alone does not qualify an empty string-count response.

Revert the parser and tests together if necessary. No database migration, historical rewrite or production deployment is part of this change. Retained normalized observations and private diagnostic evidence remain independently readable.
