# PR10: Evidence-backed Rightmove search and legacy-detail repairs

**Date:** 28 September 2026. **State:** offline slice implemented and verified. The reported encoded detail format, current typeahead and live adapter qualification remain open.
**Predecessor:** PR18a tooling, `02a75f1`.
**Parent:** [delivery plan, PR10](2026-09-28-research-synthesis-and-delivery-plan.md#pr10--repair-and-fixture-the-rightmove-parsing-contract-m).

## Retained evidence and measured improvement

The supplied 27 September research capture includes a successful Rightmove search body (`01-rightmove-search.html`, recorded HTTP 200; SHA-256 `afcc377a5dd7939e15e47974b865de8a4b15bc42598fc221cb8aefc8cd2b26b9`). Its `__NEXT_DATA__.props.pageProps.searchResults.properties` array contains 25 listings. The existing HTML-card parser returned 25 listings but discarded every structured summary and coordinate pair. An offline before/after comparison now returns the same 25 listings with **25 descriptions and 25 coordinate pairs**.

A separate run through the PR18a capture/replay CLI contract reproduces the 25 extracted descriptions. There are **24 unique provider IDs**, because one appears twice; the unique ID set, rents, bedrooms and URLs are identical before and after. Both experiments use already supplied bytes and make no source/model/commute/notification requests. Aggregates and private replay artifacts are under `/tmp/home-finder-pr10/`.

`tests/fixtures/rightmove_next_search.html` is a small structural derivative of the first two records. IDs, addresses, descriptions, coordinates, media URLs and branch identity are replaced with non-real examples; irrelevant configuration/tracking data is removed. The adjacent provenance file records the original and transformed hashes, retained fields and the fact that acquisition was a research HTTPX probe. It is not presented as a successful production Crawlee canary.

The two retained Rightmove detail bodies are HTTP 429, not successful examples of `window.__PAGE_MODEL`. The researcher's surviving parser experiment generates a synthetic array from the *described* format. A rendered browser/accessibility result is also not the serialized input needed to qualify that decoder. An optional request for the original successful detail HTML remains pending; no new acquisition is implied.

## Search and failure behaviour

The parser prefers the observed structured path. It validates listing ID against the same-source HTTPS property URL, explicit GBP rent and period, bedroom count and address. Weekly rent is converted deterministically to monthly; unknown period/currency/bedrooms do not become invented defaults. Summaries become plain listing text; portal-provided coordinates remain portal-provided coordinates, not verified unit-level locations. HTML cards remain the fallback when the structured script is absent.

The structured input is limited to 4,000,000 characters and 200 listings. Malformed or unsupported structured data is an error, not a reason to fall back silently to unrelated cards. A zero-result structured response requires an explicit integer `resultCount: 0` and an empty properties array. The older scoped `no properties found` HTML marker is still recognised. A blank page, challenge, missing structure or wholly invalid card set no longer reports healthy empty inventory.

Partial parsing retains the valid neighbours and records the invalid-listing count. The actual Crawlee handler carries that count into `ScrapeResult.parse_errors`, so source metrics remain unhealthy when even a partially useful page has a parser failure. An integration fixture runs the normal scraper/orchestrator/storage path with only the network boundary replaced: a processing cap of one retains both fetched source observations, descriptions and coordinates; the partial case retains the valid listing and records its error.

## Detail boundary and diagnostics

Legacy plain `window.PAGE_MODEL` extraction now uses `JSONDecoder.raw_decode` rather than brace counting, so quoted braces, escaped quotes and backslashes cannot terminate the JSON object early. It limits input size, requires a nonempty property object, and rejects an explicit target-ID mismatch. Python documents `raw_decode` for a JSON document followed by additional text and recommends bounding untrusted JSON input. [Python JSON decoder documentation](https://docs.python.org/3.14/library/json.html#json.JSONDecoder.raw_decode).

Non-200 responses are rejected before detail parsing. Known challenge headers/body markers are classified separately, using a shared conservative helper also used by availability and isolated capture. Missing, invalid and unsupported model variants produce warning-level diagnostics rather than a debug miss or an empty `DetailPageData` object.

The new reference-array format is deliberately **unsupported pending retained successful evidence**. This slice does not deploy a speculative resolver, claim current Rightmove detail repair, or remove Crawlee/browser alternatives. Legacy records without an explicit ID still use the historical parser contract; complete target binding for the new format remains a required fixture gate.

## What remains in PR10 / PR11

1. Retain successful current detail and typeahead examples for the intended areas. Verify the full adapter's actual headers/session/transport, not only a simplified cold probe. O2's source qualification still applies to fresh collection.
2. If the encoded format is confirmed, implement only its observed semantics with bounded references, missing/null handling, identity, invalid-reference/cycle/depth/size cases, and old/new search-to-observation fixtures.
3. `firstVisibleDate`, customer/branch and floorplan-count fields remain preserved in the original retained body and the derivative fixture. They are **not yet projected into application metadata**. Add dated per-source persistence with the observation contract; never substitute the portal date for `Property.first_seen` or treat a search floorplan count as a downloaded floorplan. This is an explicit outstanding PR10/PR06b handoff.
4. Requalify current typeahead/mapping, query leakage, source ordering and availability; keep local criteria/location filtering. Source observations alone do not establish full-market coverage.
5. PR11 still needs matched, bounded full-adapter transport trials and OpenRent/Zoopla pagination/detail coverage evidence. Shared challenge recognition here is a small prerequisite, not completion of PR11's backoff/circuit/transport work.

Normal collection remains paused. These offline repairs can be committed independently while the new detail/transport gates remain open. The next independent implementation is PR20 (SDK transport and explicit phase contracts), followed by PR13 spending controls. Human labelling does not block that transport migration; paid model comparisons remain gated.

## Verification

The focused suite passes **277 tests**, covering the new derivative, existing Rightmove/detail/replay contracts and availability precedence. Cases include malformed neighbours, wrong-source/ID URLs, unknown currency/period/bedrooms, explicit zero versus ambiguous empty, oversized input, ordinary and partial search-to-storage persistence, escaped legacy JSON, wrong-target detail, unsupported encoded variants, HTTP failures and challenge precedence.

The complete suite passes **2,659 tests on each of Python 3.11.16 and 3.14.7**, with combined line/branch coverage **87.79% / 87.69%** and four pre-existing warnings each. Ruff lint and formatting pass (190 files), as does strict mypy (78 source files). Logs and coverage reports are under `/tmp/home-finder-pr10/`. No schema migration or dashboard layout changes are introduced; browser tests are not repeated.
