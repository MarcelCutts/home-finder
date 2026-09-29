# Fifty-listing pipeline and deduplication experiment

Date: 29 September 2026. This is an isolated execution record, not source or model qualification.

## Authorization and scope

The owner requested 50 properties to exercise deduplication, then explicitly selected **US$10 total; stop model work at the cap**. This is a new experiment; the earlier two-request, one-property authorization is exhausted.

- Select up to **25 Rightmove + 25 OnTheMarket listings** using the configured outcodes, £1,800–£2,250, 1–2 bedrooms and the current furnishing/bathroom/let-agreed criteria. Each source uses the same shuffled area order. The selection is not a random or exhaustive sample.
- Fifty means selected source listings **before** local filtering and deduplication. A received search page can contain additional retained observations. A merged home receives one assessment; failed enrichment and filtering can reduce assessments further. Do not silently top up another source or widen the price range.
- Fresh private output directory and database; no production-copy recovery backlog, Telegram notifications, TravelTime calls or proxy service. Keep the production data and earlier experiments unchanged.
- Sonnet 5.5 for both phases, `between_tools`, low effort, four model image blocks including a floorplan, zero SDK retries. One model request in flight; at most **100 physical attempts** in this database, one attempt per operation. Per-property timeout 300 seconds; whole experiment 40 minutes.
- Exact-decimal lifetime, day and month caps of US$10. The existing conservative reservation is US$0.881920 per request; admission can stop before US$10 is consumed. Unknown outcomes retain their reservation. This is local admission control, not a provider-enforced account spending limit. Usage estimates need invoice reconciliation.

## Reproduction and observation

```sh
python -m home_finder.pipeline.batch_smoke \
  --output data/smoke-runs/2026-09-29-fifty-01 \
  --properties 50 --sources rightmove onthemarket --budget-usd 10
```

This command makes live portal and model requests. Another invocation requires a new directory and its own authorization; a fresh ledger does not inherit previous spending.

The runner shares the one-property smoke's redaction, durable spend ledger and private artifacts. `plan.json` records source code hash, runtime, scope and budgets. `events.jsonl` flushes every event and contains selection IDs, source health, stage timings, input blocks, scored cross-source pairs, image coverage, rejected/accepted matches, canonical fates, analysis requests and settlements. `report.json` independently checks property/assessment/attempt limits, selected input count, spend, unresolved outcomes, notifications and database integrity. Report arrays are not truncated to 100 entries; individual log values remain bounded. `analyzed` describes persisted assessments, while source health and whether the target was reached must be inspected separately.

Model prompts, response bodies, authorization headers and credentials are excluded from event logs. Listing evidence, images and assessments remain private in the isolated data directory. A full page can produce more source observations than the input cap; `scraping_summary.total_found` is the selected count, whereas scraper metrics include observed inventory.

Deduplication still uses the existing matching policy. New debug events expose every scored cross-source pair, including scores below the logging threshold, gallery-hash availability and why a pair qualifies or is rejected. Block summaries show final group membership, so a qualifying pair is not confused with an accepted group. Missing-outcode candidates are counted separately. These logs do not prove the matcher is correct: accepted matches need evidence review, and zero live matches would be a valid observed result.

## Verification before live execution

- 87 focused tests passed on Python 3.14.7: smoke and batch controls, native paid-request limits, deduplication and cross-platform integration.
- Ruff lint and formatting passed; strict mypy passed across 102 application files.
- Offline batch tests use a real empty SQLite database and a replaced pipeline entry point. They check equal source quotas, no inherited backlog/providers, 100-attempt/US$10 limits, zero retries, rejection of invalid scope before directory creation, and failure reporting if selection exceeds 50.

## Live results

Executed code: `7414332`; Python 3.14.7, Anthropic SDK 1.8.0. Started **14:04:02 UTC**, finished **14:06:04 UTC**, **122.423 seconds**. Evidence directory: `data/smoke-runs/2026-09-29-fifty-01/`. The directory and sensitive artifacts remain outside Git.

| Stage or safeguard | Observed result |
| --- | --- |
| Selected listings | 50: Rightmove 25, OnTheMarket 25 |
| Local rent/bedroom/location filters | All 50 passed |
| Detail enrichment | All 50 succeeded; all had galleries; 36 had floorplans before merging |
| Dedup candidate scoring | 240 cross-source pairs within five outcode/bedroom blocks |
| Pair decisions | 15 qualifying pairs; 220 below the score threshold; five otherwise qualifying pairs vetoed by no matching photo hashes |
| Canonical properties saved | 35: 15 with both portals, 20 single-source |
| Source retention | 55 unique source rows and 109 immutable observations; all 50 selected listings linked to a canonical home |
| Images referenced by saved homes | 139 gallery images and 23 floorplans; original per-source caches also retained |
| Model dispatch | Three physical requests: one invalid visual result, one successful visual result, one evaluation 503 |
| Complete assessments | **0**; all 35 homes remain `pending_analysis` |
| Recovery state | Two properties have one failed analysis attempt each; 33 have no property-level attempt recorded |
| Usage estimate | **US$0.080853000**, including the invalid visual output; zero held/unresolved reservations and zero over-reservations |
| Database checks | Integrity `ok`, zero foreign-key errors, all nine experiment operating checks passed |
| Notifications / other paid providers | None |

Collection took 18.166 seconds; filtering 0.006 seconds; enrichment, geocoding and deduplication together took 37.956 seconds. Rightmove queried E2, E9 and N15; OnTheMarket filled its quota in E2 and E9. Rightmove made five search-page fetches, OnTheMarket three. Additional observed listings arrived in the final search response; they were retained but not added to the 50 selected inputs.

The run correctly finished **`incomplete`**, with discovery health **`partial`**. A selected count of 50, successful database checks and successful deduplication do not mean end-to-end analysis succeeded. There were no paid retries and no subsequent paid run. The US$10 authorization is an upper bound, not a spending target or permission to reset the ledger for another batch.

### Accepted and rejected match review

Every accepted live pair had four gallery-hash matches. Recomputing from each source's original image URLs found an exact perceptual-hash match in all 15 pairs; it did not compare against galleries augmented by the merge. Prices and bedrooms agree for all pairs. The lowest accepted total was 102.838, the highest 125; these are weighted scores, **not probabilities**.

The reviewed Chelmer Road pair (`rightmove:90358545`, `onthemarket:19836825`) shares the same kitchen photograph and explicit property reference 2487475. The Wilmot Street pair (`rightmove:93075393`, `onthemarket:9750088`) also shares the same kitchen image and listing features. These developer checks support those accepted pairs; they are not independent labels for a benchmark and do not establish overall dedup precision or recall.

Two additional candidate types need review:

1. **Same home photographed differently.** `rightmove:91938783` and `onthemarket:20385686` both advertise a £1,850 one-bedroom Wilmot Street flat. Their sitting-room photos show the same distinctive mirror, chandelier, wall artwork, curtains, furniture and rug, photographed from different positions. The pair scored 80.195 from non-image signals, but its closest cached pHash distance is **24**, above the configured threshold of eight, so it was vetoed. This is a strong suspected missed duplicate, not an instruction to merge it automatically. Another OnTheMarket listing (`20370518`) also merits review. A lack of matching hashes in four selected photos is not proof of different dwellings.
2. **Two listings on the same portal.** `rightmove:92353701` and `rightmove:92353155` advertise the same Southborough Road address/postcode, £2,250 rent and one bedroom, with four matching cached photos and closely matching descriptions. They remain separate because the matcher skips any overlapping source. Changing that guard alone is unsafe: current merged projections include maps keyed by portal, so multiple listings from one portal need explicit identity/membership support before merging.

There is also conflicting furnishing evidence: the accepted £1,800 Wilmot Street pair's features say “furnished”, although the search requested unfurnished/part-furnished. Local filters currently enforce rent, bedrooms and location, not a normalized furnishing contract. Treat this as a source/filter coverage case for PR11, not proof that a portal query parameter is always broken.

### Network-blocked replays

Reproduction, using the retained live batch and development dependencies:

```sh
python scripts/replay_batch_dedup.py \
  --input data/smoke-runs/2026-09-29-fifty-01 \
  --output data/smoke-runs/2026-09-29-fifty-offline-replay-v2
```

Use a new output directory when reproducing. The final replay record is the `-v2` directory; the first replay is retained as an earlier development artifact. Inputs are reconstructed from retained source rows, detail observations and original per-source cached images, not asserted to be the exact original in-memory objects. Sockets are blocked, the source DB is opened read-only, and all mutations occur in new databases with copied images.

| Replay | Result |
| --- | --- |
| All 50 arrive together | 35 homes, 15 pairs; exact canonical/source membership agrees with the live run |
| Save Rightmove's 25 first, then deliver OnTheMarket's 25 | 15 existing anchors gain a source; 10 new homes; same final 35 homes and exact memberships |
| Present all 50 again to the known-listing filter | Zero new work in both databases |
| Safety | Zero model attempts, valid SQLite databases, no FK errors, original DB/log/report/referenced image hashes unchanged |

The second scenario tests matching to previously saved **pre-analysis** anchors. It does not replace PR07a's separate established-history preservation rehearsal or prove every established identity conflict safe. No reanalysis queue was drained during these replays.

## Online investigation and repairs

Checked primary sources on 29 September 2026. The observed failures and their interpretations are separate below.

### Rightmove terminal-page parsing: reproduced and repaired

Both warnings occurred on `index=24`, after successful first pages in E2 and E9. One additional curl GET reproduced the E2 response at 14:09:30 UTC: **HTTP 200**, empty `properties`, `resultCount="14"`, echoed index `"24"`, page size `"24"`, and pagination `{total: 1, page: "2", last: "0"}`. It was a page beyond the search's final results page. Our parser required an integer zero count for any empty page and incorrectly classified this valid shape as parse failure.

This is direct evidence from [Rightmove's queried search endpoint](https://www.rightmove.co.uk/property-to-rent/find.html?locationIdentifier=OUTCODE%5E755&maxBedrooms=2&minPrice=1800&maxPrice=2250&dontShow=houseShare&letType=longTerm&sortType=6&minBedrooms=1&furnishTypes=unfurnished,partFurnished&minBathrooms=1&includeLetAgreed=false&index=24), not a published API guarantee or the original live response body. The E9 warning has the same pattern but was not separately recaptured. Searches did not establish an authoritative current public pagination API contract; old third-party fixtures were not used to override the fresh response.

Repair **`6ed6164`** accepts bounded decimal-string counts and recognizes exhaustion only when the requested index, echoed index, page size, total pages, current page and last-page offset agree. A positive total on an empty first page or contradictory pagination still fails closed. Replaying the retained HTML now gives zero parse errors and `end_of_results=true`; no extra live pipeline or model request was used to verify it. Search errors elsewhere must still degrade source health.

Private diagnostic: `data/smoke-runs/2026-09-29-fifty-pagination-diagnostic/`. Body SHA-256 `ab3c42f39c3a1eb8e9f0da84388f776809cb7a11bdb726294f60365a92e96e7b`, 283,678 bytes. **Manual raw-body deletion deadline: 6 October 2026, 14:09:30 UTC**; no automatic cleanup is claimed.

### Evaluation grammar compilation: temporary provider rejection, exact root cause unproven

Request `req_011CfXrjHSa6CcvEXcpVysXZ` returned HTTP 503 after 41.800 seconds, with `overloaded_error` and “Grammar compilation is temporarily unavailable. Please try again.” The ledger retained the rejection and accounted zero usage for that response. The caller's circuit breaker stopped the remaining batch instead of retrying it.

Anthropic documents compilation latency, a 24-hour grammar cache, explicit complexity limits and HTTP 400 for excessive schema complexity. Our evaluation schema has 21 properties, zero optional parameters and two union nodes; the same configuration completed in the earlier smoke. These facts favor a temporary provider compilation failure, but do not prove its internal cause. Keep the schema and request ID for investigation. [Structured-output documentation](https://platform.claude.com/docs/en/build-with-claude/structured-outputs), [request-ID guidance](https://platform.claude.com/docs/en/api/errors).

The [official status page](https://status.claude.com/) showed all systems operational and no 29 September incident when checked. That does not disprove this request's failure; it means we cannot attribute it to a published general outage. No definitive public report of the exact error was found in the primary-source searches. Avoid changing schemas, disabling validation or adding blind retries on this evidence alone.

### Visual validation: actual rejection known, exact offending field not retained

Request `req_011CfXrhYLxmPzuKBU4kmQqe` returned HTTP 200 with `stop_reason=tool_use`, consumed US$0.052399, and failed our required visual output contract. The old record includes the failure kind but neither the raw response nor a field-level diagnostic. Its exact cause **cannot be recovered from this evidence**. The following property's visual response passed, so the contract did not reject every response.

Our visual tool uses `strict=false`; tool output therefore still requires application validation. Anthropic documents strict tool use separately from JSON response formatting. Its enum-casing caveat is already handled by our unambiguous case normalization. It would be speculation to blame this case on casing, truncation or a missing particular field. [Structured-output documentation](https://platform.claude.com/docs/en/build-with-claude/structured-outputs).

Repair **`42f41f9`** logs validation error counts and up to ten schema-owned field paths/types, with bounded path depth. It distinguishes wrong output shape/tool count, invalid rating, JSON decoding and schema validation. Both physical-request settlement and property failure logs include these diagnostics. Model-generated unknown keys, values, validation messages and context are omitted. Tests demonstrate useful nested paths while excluding private values and unknown key text. [Pydantic's error structure](https://docs.pydantic.dev/latest/errors/errors/) explains why logging whole validation exceptions would expose inputs. This improves future diagnosis; it does not retroactively identify this failure or make its assessment complete.

### Image matching: review a different signal, not just a larger threshold

The [ImageHash project's own documentation](https://github.com/JohannesBuchner/imagehash) describes near-identical image matching and luminance structure, and provides a separate crop-resistant algorithm. That supports using pHash for reused photographs; it does not make pHash distance a dwelling-identity test. Our Wilmot Street comparison provides application-specific evidence of the distinction. Raising the threshold from eight to 10–12 would still miss the distance-24 candidate, while increasing false-match risk elsewhere. Preserve conservative automatic merges and add a review outcome for strong non-image candidates with insufficient comparable imagery. Any alternate matcher needs separately labelled same-building negatives and changed-photo positives before rollout.

## Sequential follow-up for agents

1. **PR07b, next:** retain the 15 accepted pairs, five image vetoes, changed-angle candidate and same-portal photo pair as a development review set. Add stable identity and reversible membership, multiple source listing IDs per portal, and explicit accepted/rejected/review decisions. Preserve the current live records and all source evidence. Do not train/tune a threshold on these examples and then call them a held-out evaluation.
2. **PR09b / PR11:** carry selected-versus-observed counts, terminal pagination and partial coverage through run health. Add the contradictory furnishing example to source contract checks. An empty terminal page is not evidence that previously seen homes disappeared. The repaired single E2 capture does not complete the seven-day source canary.
3. **PR12b / PR14:** support a scoped resume of this saved 35-home queue with explicit allowed IDs, the existing spend ledger, phase-aware artifacts and due times. Persist valid visual output so a later evaluation failure does not require buying the visual phase again. Completed physical attempts, failed output, temporary rejection and never-dispatched work need different retry decisions. Enforce a combined run cap across discovery, recovery and reanalysis.
4. **PR20 / PR21:** collect field-level visual failure frequencies with the new diagnostics; then evaluate a smaller strict visual contract or deliberate phase split using an independently labelled set. A future paid resume must preserve the remaining allowance in this experiment's ledger and explicitly set its retry policy; no fresh US$10 allowance is implied. Record grammar failures by schema hash, model, endpoint, status, latency and request ID.
5. **PR18b / PR24 gates remain open:** this two-minute run is useful acquisition, identity and failure-handling evidence. It is neither a sustained service canary nor an end-to-end assessed/delivered pilot. No production restart, default-model promotion or deployment occurred.

## Evidence checksums and validation

- Original batch events SHA-256: `417d73e2b522ef97f444e9a3dd9a15424452724ab9283de2c52ce526071caf55`.
- Original batch database SHA-256: `e2bdf0073f39067b85a8f1deb707b828cd369f45c9244a9c0360aed2f0934fa7`.
- The final replay report includes input and original artifact checksums, exact live-membership comparisons and explicit integrity/no-spend checks.
- Before repairs: **3,151 tests passed**, 87.91% coverage, Python 3.14.7. After repairs: **202 focused tests passed**, Ruff and strict mypy passed.
- Final full regressions: **3,164 passed on Python 3.14.7** (87.96% coverage) and **3,164 passed on Python 3.11.16** (88.08% coverage). Lint and formatting passed for application/tests and the offline replay script; strict mypy passed across 102 application files.
- Final privacy audit: the batch event hash matches the report; the configured Anthropic key occurs in none of the event/console logs or plan/report files. Those files are mode `0600`, and the experiment directory is `0700`. The private `post_run_privacy_audit.json` records the check without credential contents.
- Completed commits: `7414332` (runner and pair-level visibility), `6ed6164` (verified terminal-page parsing), `42f41f9` (redacted output diagnostics). This completion record and the offline replay script are committed together separately.
