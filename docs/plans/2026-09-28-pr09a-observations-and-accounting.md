# PR09a: Retain fetched observations and account for incomplete runs

**Date:** 28 September 2026. **State:** implemented and verified; not deployed.
**Parent:** [delivery plan, PR09](2026-09-28-research-synthesis-and-delivery-plan.md#pr09--preserve-known-page-observations-and-report-collection-coverage-honestly-m).
**Predecessor:** PR08a, commit `9149e48`. No schema migration or live external effects.

## Behaviour and scope

The controlled baseline reproduced three failures: a known-only fetched page returned no properties, a later page exception discarded the previous page, and a known-only pipeline run recorded zero scraped properties and no scraper records. A result with one fetched and one failed page also reported healthy.

The shared paginator now retains the fetched page before checking known IDs, duplicates or the processing cap. Repeated IDs keep the latest received data in this scrape's current projection. Known-page early stopping still avoids fetching later pages. `properties` contains the bounded processing selection; `observations` also contains any additional listings already received on the final page. No extra provider request or model call is justified merely by retaining these facts. OpenRent still does not use known-ID early stopping because its existing distance ordering does not support that assumption.

Each completed area, including a partially successful one, sends retained observations to storage before criteria, location, new-property and analysis selection. This updates source text/last-seen/quotes and detects known canonical price changes even outside the current price criteria or processing cap. Repeated identical prices do not create more history. Existing notification status and analysis remain intact. Unlinked captured listings are retained without automatically scheduling inference.

Later request failures preserve earlier pages and mark the result unhealthy. Unexpected area errors retain earlier areas. Cancellation carries the already received prefix to the orchestrator, which completes bounded persistence and cleanup before propagating cancellation. Cancellation during ordinary observation persistence also waits for that persistence. Storage errors propagate as pipeline failures; they are not swallowed as recoverable scraper errors. All instantiated participating scrapers are closed, including when one close fails.

The runner now writes its mutable progress snapshot from a `finally` block, including the three common early exits: every listing filtered out, every listing already known, and every enriched listing rejected by the floorplan gate. Counts and available timings also survive exceptions/cancellation. Notifier construction/context-entry failures mark an already-created run failed. The reusable cancellation helper is extracted unchanged from PR04's storage cleanup; it retains and awaits its task, including under repeated cancellation, rather than leaving a detached write.

## Accounting semantics

| Field/outcome | Meaning in this slice |
| --- | --- |
| `ScrapeResult.observations` | Distinct provider listing IDs received within an area attempt, latest received value retained |
| `properties_found` | Distinct provider IDs observed across attempted areas for this source, including already-fetched data beyond the processing cap |
| `scraped_count` | Sum of those source counts; compatibility fallback for adapters returning only processing results |
| Criteria/new/enrichment/floorplan counts | Counts of the processing selection at that stage; they can be smaller than observed count |
| `areas_attempted` | Calls actually begun, excluding areas skipped by a cap or breaker |
| `areas_completed` | Area calls returning a result, including partial/failed results; **not proof of complete coverage** |
| Healthy | At least one fetched page and no recorded request/parse error or cancellation |
| `completed` run | No attempted source reports a failure; this does not assert exhaustive coverage |
| `partial` run | At least one source failed, with some observations or another healthy source |
| `failed` run | All attempted sources failed without observations, or orchestration/storage failed |
| `cancelled` run | Cancellation propagated after available collection accounting settled |

Paginator stop reasons distinguish known page, result/page limit, duplicate page, empty page, page exception, parse error and cancellation. They are logged and available on the returned result. They are **not yet a durable per-query coverage schema**. The latest-run repository now includes partial and cancelled terminal runs, avoiding an older successful run masking the latest failure.

An empty page is healthy only according to the adapter's existing parse result. This is not a claim that current portal HTML contracts have been freshly qualified: ambiguous empty/parser-drift handling still needs PR10/11 fixtures and PR09b's outcome model. Search absence never causes an off-market transition.

## Verification

- Baseline: `/tmp/home-finder-pr09a/baseline.json` and the corresponding isolated mocked experiment.
- Focused scraper, pipeline, run repository, transaction ownership and runner integration suites: **422 passed**.
- Added deterministic coverage for changed known quotes, all three early exits, fetched overflow beyond the cap, duplicate-page updates, later page exceptions/failures, parse failures, actual area attempts, cancelled pagination, cancellation during persistence, repeated cancellation during accounting, storage failure and notifier setup failure.
- Full Python 3.11.16 suite: **2,548 passed**, 86.86% branch-inclusive coverage; four existing deprecation warnings.
- Full Python 3.14.7 suite: **2,548 passed**, 86.77% branch-inclusive coverage; the same four existing warnings.
- Ruff lint/format, strict mypy and diff checks passed (180 formatted Python files, 72 source files). No templates/assets changed; the browser suite was not repeated for this segment.
- Frozen reference database SHA-256 remains `e50897b46ebefea0119fd62951ac277d764684f9590d52801e66c964b1352c63`. No historical row was rewritten or initialized.

The cleanup design follows [Python's documented cancellation contract](https://docs.python.org/3.14/library/asyncio-task.html#task-cancellation): use cleanup paths and propagate cancellation after cleanup. Its [shield documentation](https://docs.python.org/3.14/library/asyncio-task.html#shielding-from-cancellation) also explains why shielding alone does not stop cancellation of the caller. The repeated-cancellation storage/run tests verify the application-specific ownership behaviour.

## Limits, rollout and next segment

This slice retains current source projections, not immutable page/observation history. Source upsert and individual price changes are separate owned transactions; a persistence failure can leave a partially applied page, and repeated input is idempotent. A hard process kill before the bounded flush can still lose in-memory observations. PR06b's capture/observation contract remains necessary. PR08b retains source freshness/selection and historical-price reconstruction work.

Periodic deeper/watchlist refresh, query coverage revisions, source freshness, worker heartbeat and readiness remain PR09b. A processing cap does not drain every newly retained unlinked row; PR12b must give that work durable admission and fair ordering. Dry runs retain the existing no-run-record behaviour and still can incur analysis charges; they are not collection-only captures. Run-wide paid-attempt accounting remains PR13, including cancellation during analysis and standalone reanalysis.

No schema change is required. The PR06a schema-8 compatibility and protected reference backup remain applicable. Preserve known-page retention and truthful partial/failed outcomes when rolling back later changes; do not restore zero-total early exits as a fallback. Logs and coverage artifacts are under `/tmp/home-finder-pr09a/`.

**Next: PR12a**, make existing pending enrichment/analysis/reanalysis and price notifications progress without new discoveries, preserve recoverable missing-evidence outcomes, and establish one documented completion-relative schedule with exclusion across processes. Live collection/paid processing activation remains separately gated in the parent plan.
