# PR13c / PR20d: observable one-property pipeline smoke

Date: 29 September 2026. Branch: `codex/pr01-ci-baseline`.
Parent: the [sequential delivery plan](2026-09-28-research-synthesis-and-delivery-plan.md).

## Outcome and authorization

The owner asked whether the pipeline had run recently, requested Sonnet 5.5, chose **US$1 total for one property**, and asked for detailed observability. The budget question explicitly specified at most two model requests and no retries. This record covers that one live experiment and the reusable controls needed to inspect it.

The successful run discovered nine distinct Rightmove listings in E8, selected and enriched one, and saved its two-phase Sonnet 5.5 assessment. It took **26.223 seconds** and settled **two provider-usage estimates totalling US$0.082314500**. No model retries, Telegram notifications or commute-provider calls occurred. No production data or deployment was changed. The two authorized model requests are used; the unused dollar allowance does not authorize additional requests.

The latest completed pipeline record found in both retained production database copies was run 57, **28 February 2026, 18:09:52–18:33:30 UTC**: 1,749 scraped, 41 new, 50 enriched, 41 analysed and 41 notified. That is evidence about the retained snapshots, not proof that no unrecorded or later remote execution happened. Recent engineering checks had been offline tests, replay and isolated source requests. Today's smoke supplies fresh end-to-end execution evidence.

The first attempt failed before model work: the search parser rejected ten feature-object records. A separately retained diagnostic response reproduced the failure, and [PR10's focused repair](2026-09-29-pr10-search-feature-objects.md), committed as `16c4633`, fixed it. That attempt cost zero model dollars. This sequence demonstrates why a zero-result run needs discovery health and parser reasons rather than just a final saved count.

## Scope and effective controls

The new entry point is `python -m home_finder.pipeline.smoke`. It invokes the real `run_pipeline` with a fresh private SQLite database, one source, one outcode and one analysis candidate. A new output directory is required; existing evidence cannot be overwritten. Run it once per CLI process because its logging is process-scoped.

| Control | Effective value |
| --- | --- |
| Source / outcode | Rightmove / E8 for this run |
| Search criteria | £1,800–£2,250 pcm; 1–2 bedrooms; unfurnished or part furnished; at least one bathroom; let-agreed excluded |
| Selected properties | At most 1; analysis concurrency 1 |
| Cached gallery images | At most 4, plus the separate floorplan |
| Images sent to visual analysis | At most 4 **including** the floorplan; this run sent 3 gallery images and 1 floorplan |
| Floorplan gate | Disabled explicitly for the diagnostic smoke; this property did have a floorplan |
| Model in both phases | `claude-sonnet-5-5`, `between_tools`, effort `low` |
| Output per request | At most 8,192 tokens |
| Local input admission limit | 200,000 conservative reservation tokens per request |
| SDK retries / model attempts | 0 retries; at most 2 physical requests total; 1 per operation |
| Spending | US$1 per-call/day/month/lifetime limits; at most 1 in-flight request |
| Timeouts | 120 seconds per model request; 300 per property; 600 for the pipeline invocation |
| Other effects | Telegram and TravelTime credentials blanked; proxy configuration blanked; separate image cache |
| Runtime | Python 3.14.7, Anthropic SDK 1.8.0, locked dependencies |

The search response contains more records than the analysis limit. All received search observations remain available; one-property scope limits selection, enrichment and analysis, not the number of records present in a portal page. The request is one source/outcode, but the source adapter retains its usual pagination and request handling. This helper is not a replacement for PR18's explicit collection-request budget or a seven-day source canary.

Ordinary application model defaults remain Sonnet 4.6. The new model is selected by this explicit smoke configuration; its availability and transport compatibility do not establish superior assessment quality. No general paid-processing budget was activated.

## Verified provider contract and spending mechanics

Anthropic's current [pricing table](https://platform.claude.com/docs/en/about-claude/pricing), checked on 29 September, prices Sonnet 5.5 at US$2 input / US$10 output per million tokens, US$2.50 for five-minute cache writes, US$4 for one-hour writes, and US$0.20 for cache reads. It also documents a newer tokenizer, so a lower token rate does not imply the same percentage saving on a complete workload. Rates are frozen with each admitted attempt; old Sonnet 4.6 ledger entries keep their original rate card.

The [thinking guide](https://platform.claude.com/docs/en/build-with-claude/thinking) documents the newer model's supported reasoning modes and forced-tool restrictions. The smoke uses `between_tools` with automatic tool selection for visual output. Required tool output and evaluation schema validation remain enforced. A read-only model-metadata request also returned HTTP 200 for this account; the subsequent two successful model responses verify access in practice.

The smaller input reservation accepts only this application's single-user-turn text and static inline image payloads. It reserves twice the UTF-8 byte count of the JSON after image data removal, 16,384 framing tokens, and 5,000 tokens per image. The latter exceeds the documented [4,784-token image maximum](https://platform.claude.com/docs/en/build-with-claude/vision). Unknown message shapes, remote image URLs, non-image file inputs, animated or invalid images and oversized payloads cannot use this policy. Ordinary configurations without an explicit input limit still reserve the full context.

This is a **conservative local admission policy**, not a provider-enforced input-token or dollar cap and not a proof of the provider's tokenizer or overhead. Its assumptions are recorded as `bounded_listing_payload_2026_09_29_v1` in analysis provenance. Neither estimated token counts nor an advisory provider task budget are presented as a billing guarantee. The actual run settled well below the owner's cap. Recheck provider limits and pricing before reusing the policy for another payload class or model.

Each request reserves **US$0.881920000** before the SDK sends it: 200,000 input tokens at the highest supported input rate plus 8,192 output tokens. The next phase can start only after the first settles and the remaining budget covers a fresh reservation. A timeout or unreadable response keeps the unknown reservation held; it does not create permission to retry. Admission checks the total lifetime spend and every held reservation, including across restarts and calendar boundaries. Attempt count includes admitted attempts even if subsequently released. A response that exceeds its reservation is recorded truthfully and stops further admission.

Successful-response aggregate logs now use the selected model's rates and cache-write breakdown. The durable ledger remains authoritative for attempted work, failures, rejections and uncertainty. All totals here are USD usage estimates, not reconciled invoice amounts or hosting/network costs.

## Evidence and request accounting

The live artifacts remain private and ignored by Git:

- First, incomplete acquisition: `data/smoke-runs/2026-09-29-rightmove-01/`.
- Successful pipeline: `data/smoke-runs/2026-09-29-rightmove-02/`.
- Each has `plan.json`, `events.jsonl`, `report.json` and its isolated `properties.db`; the successful directory also has cached images and thumbnails. Console output is in the adjacent `.console.log` file.
- The separate search diagnostic, its body hash and seven-day deadline are recorded in the parser handoff. It is not the exact body received in either pipeline invocation.

| Phase | Input | Five-minute cache write | Output | Usage estimate | Request duration |
| --- | ---: | ---: | ---: | ---: | ---: |
| Visual | 6,620 | 9,310 | 1,532 | US$0.051835000 | 11.4943 s |
| Evaluation | 2,996 | 3,707 | 1,522 | US$0.030479500 | 13.3953 s |
| Total | 9,616 | 13,017 | 3,054 | **US$0.082314500** | Pipeline: **26.223 s** |

There were no cache reads or one-hour writes. Local input bounds were 92,728 for visual and 49,408 for evaluation, each below 200,000. Provider-usage fields are non-overlapping; output includes billed thinking if present.

The run began at **13:12:08.898860 UTC** and finished at **13:12:35.121951 UTC**. It retained 9 source listings, 10 search observations including a duplicate record, 1 detail observation, 1 property, 5 image references, 1 assessment and 2 settled paid attempts. The property is `rightmove:93005082`; its assessment is complete under the current schema and has overall rating 3. Notification status is `pending`, because this was a Telegram dry-run. Zero unresolved, in-flight or over-reservation attempts remain. SQLite reports integrity `ok` and zero foreign-key errors.

The assessment stores its exact listing-text inputs and observation references, effective configuration, SDK and prompt/schema hashes. Configuration/provenance fingerprint: `ffb49a3ede857475a8bf7f729776f0ce4abe1d5ee627cc5d85350d0cfa925829`. The log also joins each physical request to its durable attempt, property, phase, request fingerprint and provider request ID. No prompt or image payload is needed in the event stream to trace that chain.

`events.jsonl` contains 112 structured events, no warning/error events, and SHA-256 `616c86f90c54d11241fdf658a7f7772f0a18ead175298692356f297600a15124`, matching the saved report. An offline check found zero occurrences of the configured Anthropic key in the plan, report, events and console log. Output files use mode 0600 inside a 0700 directory.

The original production snapshot and the fresh backup still hash respectively to `e50897b46ebefea0119fd62951ac277d764684f9590d52801e66c964b1352c63` and `e1494adf0f5b174cd9a2e142f50aa25b8ffd5a85ba3f4d66383a3e8762cea639`.

The live reports are schema 1 and have been preserved unchanged. Final schema 2 tooling additionally records effective search criteria, total model-image limit, application version/source digest and explicit outcome checks. Applying those checks offline to the saved successful report passes all eight; `post_run_audit.json` records that later check and the original report hash. The added checks and evidence-failure paths were verified offline; they did not trigger another live request.

## Debugging procedure and event contract

Start with `report.json`: outcome, scope, counts, failed checks, spending and diagnostic events. `analyzed` requires an assessment with a non-null rating and passing operating checks. Zero eligible results or an incomplete assessment is `incomplete`; interruption, evidence failure or an invariant violation is `failed`. The CLI exits nonzero for either. Schema success does not certify assessment accuracy.

Then inspect the relevant structured events:

| Event / artifact | What it establishes |
| --- | --- |
| `smoke_started`, `plan.json` | Effective scope and budget before pipeline work |
| `pipeline_started` | Which stage the pipeline entered |
| `pipeline_discovery_finished` | Completed/partial/failed collection, per-source pages, area coverage, parse errors, counts and discovery-stage durations, including dry runs and early exits |
| `rightmove_search_parse_failed` | Failed record count and bounded field/error reasons |
| `analysis_queue_selected` | Fresh versus recovery work, selected property IDs and configured cap |
| `analyzing_property` | Available gallery count, gallery images actually sent, actual floorplan inclusion and total image blocks; schema 1 events have the older count fields |
| `analysis_attempt_reserved` | Durable reservation, exact USD amount, model, input bound/limit, output limit and provenance/request hashes |
| `analysis_attempt_dispatched` | The attempt entered native SDK transport |
| `analysis_attempt_settled` | Provider request ID, HTTP status, duration, validated outcome, usage units and exact accounted USD |
| `analysis_dispatch_deferred` | No dispatch permitted: budget, attempt, payload or unavailable-policy reason |
| `analysis_attempt_unknown` | Uncertain provider outcome; reservation remains held |
| `analysis_reservation_exceeded` | Observed cost violated the reservation assumption; stop further paid admission |
| `visual_analysis_complete`, `property_analysis_duration` | Phase progress and whole-property latency |
| `smoke_invariants_failed`, `smoke_evidence_failed`, `smoke_interrupted` | Why a run cannot be treated as successful |
| `smoke_finished`, `report.json` | Final counts, budget state, duration and independent operating checks |

Join by `smoke_id`, then property ID and attempt ID. `events.jsonl` is flushed per event and synced on close; the report is written exclusively and includes the log hash. Sensitive field names and configured credential values are redacted, strings and arrays are bounded, and logs omit model payloads. JSON exception formatting supplies readable traces without dumping local variables. Logs still contain property IDs, source URLs and local paths and should be treated as private operational material.

The logger writes structured application events. It is not a distributed tracing service or a complete capture of every third-party library's stderr. Console output remains a separate diagnostic artifact. The helper stores at most 20,000 events in memory for report selection, while continuing to write the event file. Existing `dry_run` execution does not create ordinary `pipeline_runs` rows; the smoke plan/events/report are its run record. PR09b still owns persistent coverage, heartbeat/readiness and alert contracts for normal operation.

For read-only accounting inspection:

```sh
uv run python -m home_finder.pipeline.spend_report \
  --database data/smoke-runs/2026-09-29-rightmove-02/properties.db
```

For a future separately authorized smoke, use a fresh directory and the module's `--help`. Budget defaults to zero and values above US$1 are rejected. An explicit positive `--budget-usd` enables model admission. A zero model budget still performs source acquisition, so this helper is not an offline replay command. Never point ordinary retry or delivery commands at these diagnostic databases just to inspect them.

## Assessment review and follow-on work

The agent inspected the saved text, assessment and five cached source images. This is a development review of one selected example, not independent human labels, a blind evaluation or a holdout result.

1. **Floorplan total was missed.** The plan clearly prints 430 sq ft / 40 sq m. The model correctly read the reception/kitchen dimensions (5.5 × 4.2 m), bedroom (4.4 × 2.4 m), and shower room (3 × 1.2 m), but returned no total area. The current total-area output contract emphasizes summing rooms; it should distinguish an advertised printed total from an inferred sum and partial measurements. Add this case to PR19/21 and design the evidence field before changing the prompt. Do not reinterpret a printed approximate value as a survey measurement.
2. **Missing coverage is not proven absence.** The model says no dishwasher while its note only says none is visible; a hob is typed as induction while the note allows electric. Building appearance does not establish indoor noise transmission or glazing performance. Preserve uncertainty and record explicit versus inferred evidence in the next evaluation contract. These are review findings, not claims that the actual property has the opposite features.
3. **Sampling needs to be visible to evaluation.** The visual request saw three gallery photos, while four gallery photos were cached. Its missing-room and few-photo judgements describe the supplied subset, not necessarily every image published by the source. PR14/19 should retain available/selected/successful image inventories and prevent a spend cap from becoming a listing-quality penalty. The additional cached fourth image here is another kitchen/living-room angle; it supplies neither bedroom nor bathroom coverage.
4. **Text normalization has a remaining defect.** The retained description contains escaped `&lt;br /&gt;` sequences. Keep the original evidence, derive safe plain text with a versioned policy, and test encoded markup and hostile content before changing the projection/prompt input. Do not use unrestricted HTML rendering as the repair.
5. **Fresh response does not date the assets.** The floorplan itself is dated 29 August 2020. Today's observation proves that the source supplied it today, not that its drawing or photos are newly produced. Preserve those separate clocks.

Useful behavior was also visible: the evaluation did not invent a tenant service-charge total or current market median, preserved uncertainty for missing bathroom/bedroom photos, and used the advertised deposit and council-tax band without fabricating bill amounts. A single example cannot establish those outcomes across the corpus.

The next independent implementation remains PR07 preservation/identity work. Bring these concrete quality cases into PR19/21, image selection/provenance into PR14, and operational coverage into PR09b. Do not promote Sonnet 5.5, increase batch sizes or resume scheduled paid runs on this result alone.

## Verification and rollback

Final verification on the unchanged source/test tree:

- Python **3.14.7**: **3,117 passed**, **88.04%** branch-inclusive coverage.
- Python **3.11.16**: **3,117 passed**, **88.12%** coverage.
- Ruff lint passed; **231 files** pass formatting; strict mypy passed for **100 source files**.
- The focused final smoke/spending/integration set passed **34 tests**. The independent parser/replay set passed **109**.
- CLI `--help`, relative handoff links, staged whitespace and the saved live report's eight operating checks passed.

Full commands used `pytest -n 4 --cov --cov-report=term`, with separate coverage files for each interpreter. All external network sockets were blocked in the suites. Final logs and coverage XML are under `/tmp/home-finder-smoke/` (`full314-final.log`, `full311-final.log`, and `coverage314-final.xml` / `coverage311-final.xml`). Both earlier full runs identified four outdated SDK-usage mocks; those fixtures were corrected before these clean full runs. The isolated locked environments left the repository's pre-existing `.venv` unchanged.

Coverage includes invalid model/reasoning combinations, fixed rate-card coherence, native two-phase model requests, zero SDK retries, third-attempt rejection, oversized payload rejection before admission, unknown outcomes retaining spend, lifetime limits across restarts/month boundaries, credential redaction, private file modes, incomplete/failed/cancelled execution, storage failure and report invariant violations. API mocks use actual SDK `Usage` objects so cache-breakdown absence matches real responses instead of manufacturing nested mock values.

No migration is required. To back out model support and smoke tooling, revert this segment while preserving its private artifacts and ledger rows; do not delete or rewrite paid-attempt history. Old configurations and fingerprints omit the optional input-budget field and retain their original behavior. Historical Sonnet 4.6 attempt rate cards remain readable. Reconcile any unknown attempt against provider evidence before allowing a repeat operation.
