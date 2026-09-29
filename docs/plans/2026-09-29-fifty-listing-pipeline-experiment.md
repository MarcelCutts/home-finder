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

Live results and any supplementary network-blocked replays will be recorded below after execution. No live results are asserted by this initial tooling record.
