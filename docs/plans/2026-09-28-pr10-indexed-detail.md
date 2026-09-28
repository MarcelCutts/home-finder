# PR10 follow-up: Decode the retained Rightmove detail response

**Date:** 28 September 2026. **State:** implemented and verified.
**Predecessor:** search and legacy-detail repairs, `11d08d7`.
**Parent:** [PR10 specification](2026-09-28-research-synthesis-and-delivery-plan.md#pr10--repair-and-fixture-the-rightmove-parsing-contract-m).

## Evidence and result

The owner explicitly requested a terminal `curl` probe after the retained research corpus proved to contain only unsuccessful Rightmove detail responses. One GET to the public `/properties/93161367` page returned **HTTP 200**, **379,026 bytes**, using system curl 8.18.0 over HTTP/2. The response SHA-256 is `5a8f6defa750357dd9af2e1398cf90e8cec9eb4d5482d9c7fcb784e08bc6cc7f`. The request used no session, inherited proxy, redirect following or retry. Its journal was reserved before sending; the 30-second/8 MB limits were not reached. No other portal requests, media downloads, model calls or notifications accompanied this probe.

The successful body confirms `window.__PAGE_MODEL = {"data": "…", "encoding": "on"}`. The inner JSON array has **1,588 entries and 3,861 references**, with a longest reference path of seven edges. All entries are reachable from the root. Dictionary values and array elements are nonnegative integer indexes; scalar entries are literals. The body contains shared references, boolean and numeric values, and an indexed literal `null`. It contains **no negative-reference sentinel**. The earlier report's proposed negative-value semantics remain unverified and are not implemented.

Replaying these exact bytes against the prior helper returns `unsupported_page_model_variant`. The new decoder, through the application's real detail extraction path, returns a **396-character description, nine gallery references, eight features, a full postcode and a coordinate pair**. The source's floorplan and sizings arrays are empty; neither a floorplan nor floor area is invented. Replay uses the matching property from the retained 27 September search response as its listing input.

Private raw capture and manifest: `/tmp/home-finder-pr10/terminal-curl/20260928T180441Z-rightmove-detail/`. The aggregate before/after report is `/tmp/home-finder-pr10/indexed-comparison.json`; it links the separate offline capture/replay artifacts. Temporary raw artifacts are not a durable repository archive. The committed fixture and provenance below are the reproducible regression evidence.

## Supported contract and bounds

`scrapers/rightmove_payloads.py:detail_property()` supports the retained plain `window.PAGE_MODEL` form and the newly observed encoded form. It uses JSON decoding rather than evaluating JavaScript. The new format requires a property ID matching the requested listing; legacy records retain their historical allowance for an absent ID, while an explicit mismatch still fails.

The limits are 4,000,000 page characters, 20,000 table entries, 100,000 references and 64 reference edges per path. Every container reference must be an actual integer, excluding booleans, within the table. Cycles, unsupported encodings, negative/out-of-range references, non-finite numbers, missing property objects/IDs and excessive size/depth fail explicitly. Unused table nodes are validated too. Memoization preserves shared container identity and avoids expanding repeated references into a larger tree; cached subtrees cannot bypass the depth limit when reached through a longer path.

The decoder does not generalise the encoding to special tags, negative sentinels or arbitrary JavaScript. Such variants need retained evidence and their own tests. HTTP/challenge checks continue to run before extraction. The existing fetcher's warning path makes decoder failures visible. The input size bound follows Python's warning about resource consumption when parsing untrusted JSON. [Python JSON documentation](https://docs.python.org/3.14/library/json.html).

## Fixture and verification

`tests/fixtures/rightmove_indexed_detail.html` is a **93-node, 4,334-byte structural derivative**. It selects actual source nodes, preserves their references, empty arrays, numeric/boolean literals and shared null, then remaps surviving indexes. Listing identity, location, text, features and media URLs are replaced with examples. Agent/tracking metadata, disclaimers and irrelevant markup are removed. `rightmove_indexed_detail.provenance.json` records source and derivative hashes, time, transport, transformations and limits.

The new suite exercises this fixture through the actual detail fetcher, plus deliberately authored malformed graphs. It checks shared object identity, null versus missing fields, integer/boolean distinction, wrong/missing IDs, invalid references, cycles, unknown encodings, all graph budgets, cached-subtree depth, numeric overflow and refusal to evaluate JavaScript. Existing legacy, HTTP-error and challenge-precedence tests remain in place.

Focused parser/detail verification: **151 passed**, 12 default-excluded tests. The complete default suite passes **2,690 tests on both Python 3.11.16 and 3.14.7**, with **87.86% / 87.76%** combined line/branch coverage and four pre-existing warnings each. Ruff lint/format pass (191 files), strict mypy passes (78 source files), and `git diff --check` passes. Full results use the preceding committed dependency lock; the independent SDK work was saved and removed while verifying this segment. Logs and coverage are under `/tmp/home-finder-pr10/indexed-*`. No schema or dashboard changes are involved.

## Remaining work and next segment

This closes the missing evidence and decoder portion of PR10. It does not close typeahead/mapping, current query/filter/order behaviour, dated metadata projections, encoded examples with floorplans, or PR11's complete production-adapter transport qualification. One terminal response does not establish reliable scheduled collection or hosted source access. The production transport and retry policy are unchanged.

Continue **PR20's SDK transport migration**, preserving the model, prompts and request semantics for that first commit. Then make phase configuration and typed output failures explicit before PR13 spending admission. Paid evaluation still requires its evidence/label/budget gates. Normal collection is not activated by this parser commit.
