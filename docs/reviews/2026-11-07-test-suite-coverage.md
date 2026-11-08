# Application Python coverage — 7 November 2026

> Historical snapshot. The [integration report](2026-11-07-test-suite-integration.md) describes the implemented combined suite. Counts, paths, timings and worker-cap recommendations below describe the earlier branch or verification state.

Measured by the final offline run with branch coverage, test contexts, the Hypothesis `ci` profile, and subprocess/`os._exit` patches. All 122 Python files under `src/home_finder` are included; existing type-checking/main-guard/pragma exclusions omit 50 lines. Empty modules have no statement percentage. Browser, live-service, scripts, and research execution are not included in this metric.

The [audit](2026-11-07-test-suite-audit.md) explains behavioral gaps and the [measurements JSON](2026-11-07-test-suite-measurements.json) contains every missing line and branch, plus unexecuted function names. Domains reflect named test contexts attached to executed lines. Import-only/default-context execution has no named domain; cross-cutting imports can produce many domains without proving a contract.

| Application module | Statements | Branches | Combined | Exercising test domains |
| --- | ---: | ---: | ---: | --- |
| [__init__.py](../../src/home_finder/__init__.py) | 1/1 (100.0%) | — | 100.0% | No named test context |
| [analysis/__init__.py](../../src/home_finder/analysis/__init__.py) | — | — | — | No named test context |
| [analysis/analyzer.py](../../src/home_finder/analysis/analyzer.py) | 558/578 (96.5%) | 114/130 (87.7%) | 94.9% | analysis, integration, pipeline |
| [analysis/badge_consistency.py](../../src/home_finder/analysis/badge_consistency.py) | 46/46 (100.0%) | 18/18 (100.0%) | 100.0% | analysis, integration |
| [analysis/contracts.py](../../src/home_finder/analysis/contracts.py) | 126/129 (97.7%) | 56/60 (93.3%) | 96.3% | analysis, integration |
| [analysis/image_review.py](../../src/home_finder/analysis/image_review.py) | 26/26 (100.0%) | 9/12 (75.0%) | 92.1% | analysis, integration |
| [analysis/listing_text.py](../../src/home_finder/analysis/listing_text.py) | 45/45 (100.0%) | 16/16 (100.0%) | 100.0% | analysis, integration |
| [analysis/prompts.py](../../src/home_finder/analysis/prompts.py) | 80/80 (100.0%) | 36/36 (100.0%) | 100.0% | analysis, integration |
| [analysis/request_budget.py](../../src/home_finder/analysis/request_budget.py) | 46/49 (93.9%) | 21/24 (87.5%) | 91.8% | analysis |
| [analysis/space_evidence.py](../../src/home_finder/analysis/space_evidence.py) | 50/50 (100.0%) | 20/20 (100.0%) | 100.0% | analysis, integration |
| [analysis/spend.py](../../src/home_finder/analysis/spend.py) | 211/222 (95.0%) | 60/66 (90.9%) | 94.1% | analysis, integration |
| [config.py](../../src/home_finder/config.py) | 100/100 (100.0%) | 14/14 (100.0%) | 100.0% | analysis, integration, web |
| [data/__init__.py](../../src/home_finder/data/__init__.py) | — | — | — | No named test context |
| [data/area_context.py](../../src/home_finder/data/area_context.py) | 129/134 (96.3%) | 40/46 (87.0%) | 93.9% | analysis, data, integration, notifiers, utils, web |
| [data/location_mappings.py](../../src/home_finder/data/location_mappings.py) | 62/65 (95.4%) | 19/20 (95.0%) | 95.3% | filters, integration, scrapers |
| [data/rental_guidance.py](../../src/home_finder/data/rental_guidance.py) | 1/1 (100.0%) | — | 100.0% | No named test context |
| [db/__init__.py](../../src/home_finder/db/__init__.py) | 3/3 (100.0%) | — | 100.0% | No named test context |
| [db/backup.py](../../src/home_finder/db/backup.py) | 293/316 (92.7%) | 114/132 (86.4%) | 90.8% | db, integration, notifiers |
| [db/delivery.py](../../src/home_finder/db/delivery.py) | 176/189 (93.1%) | 42/56 (75.0%) | 89.0% | analysis, db, integration, notifiers |
| [db/enrichment_recovery.py](../../src/home_finder/db/enrichment_recovery.py) | 29/35 (82.9%) | 14/18 (77.8%) | 81.1% | integration, pipeline |
| [db/image_ops.py](../../src/home_finder/db/image_ops.py) | 7/7 (100.0%) | 2/2 (100.0%) | 100.0% | analysis, db, integration |
| [db/listing_text.py](../../src/home_finder/db/listing_text.py) | 54/57 (94.7%) | 17/20 (85.0%) | 92.2% | analysis, db, integration, notifiers |
| [db/merge_safety.py](../../src/home_finder/db/merge_safety.py) | 48/50 (96.0%) | 30/32 (93.8%) | 95.1% | analysis, db, integration, pipeline |
| [db/migrations.py](../../src/home_finder/db/migrations.py) | 225/250 (90.0%) | 75/94 (79.8%) | 87.2% | analysis, db, integration |
| [db/processing_repo.py](../../src/home_finder/db/processing_repo.py) | 233/238 (97.9%) | 42/46 (91.3%) | 96.8% | analysis, db, integration, notifiers, pipeline |
| [db/row_mappers.py](../../src/home_finder/db/row_mappers.py) | 142/152 (93.4%) | 37/42 (88.1%) | 92.3% | analysis, db, integration, notifiers, pipeline |
| [db/runs.py](../../src/home_finder/db/runs.py) | 63/63 (100.0%) | 13/14 (92.9%) | 98.7% | db, integration, pipeline, web |
| [db/source_listing_ops.py](../../src/home_finder/db/source_listing_ops.py) | 22/23 (95.7%) | 11/12 (91.7%) | 94.3% | analysis, db, integration, pipeline |
| [db/spend.py](../../src/home_finder/db/spend.py) | 171/179 (95.5%) | 57/66 (86.4%) | 93.1% | analysis, db, integration, pipeline |
| [db/storage.py](../../src/home_finder/db/storage.py) | 667/703 (94.9%) | 154/170 (90.6%) | 94.0% | analysis, db, integration, notifiers, pipeline, web |
| [db/web_queries.py](../../src/home_finder/db/web_queries.py) | 249/257 (96.9%) | 94/96 (97.9%) | 97.2% | analysis, db, integration, notifiers, pipeline, web |
| [enrichment/__init__.py](../../src/home_finder/enrichment/__init__.py) | — | — | — | No named test context |
| [enrichment/commute.py](../../src/home_finder/enrichment/commute.py) | 123/132 (93.2%) | 38/48 (79.2%) | 89.4% | enrichment, integration |
| [enrichment/details.py](../../src/home_finder/enrichment/details.py) | 157/161 (97.5%) | 75/80 (93.8%) | 96.3% | analysis, db, enrichment, pipeline, scrapers |
| [filters/__init__.py](../../src/home_finder/filters/__init__.py) | 3/3 (100.0%) | — | 100.0% | No named test context |
| [filters/availability.py](../../src/home_finder/filters/availability.py) | 175/182 (96.2%) | 100/106 (94.3%) | 95.5% | filters |
| [filters/criteria.py](../../src/home_finder/filters/criteria.py) | 10/10 (100.0%) | — | 100.0% | integration |
| [filters/fit_score.py](../../src/home_finder/filters/fit_score.py) | 621/653 (95.1%) | 274/300 (91.3%) | 93.9% | analysis, db, filters, integration, notifiers, pipeline, web |
| [filters/location.py](../../src/home_finder/filters/location.py) | 33/33 (100.0%) | 10/10 (100.0%) | 100.0% | filters, integration |
| [filters/media.py](../../src/home_finder/filters/media.py) | 12/12 (100.0%) | 4/4 (100.0%) | 100.0% | db, enrichment, pipeline |
| [filters/notification_policy.py](../../src/home_finder/filters/notification_policy.py) | 27/27 (100.0%) | 16/16 (100.0%) | 100.0% | analysis, db, integration, notifiers, pipeline, web |
| [filters/off_market.py](../../src/home_finder/filters/off_market.py) | 125/149 (83.9%) | 23/36 (63.9%) | 80.0% | filters, integration |
| [filters/shadow_rating.py](../../src/home_finder/filters/shadow_rating.py) | 30/30 (100.0%) | 4/4 (100.0%) | 100.0% | filters, pipeline |
| [logging.py](../../src/home_finder/logging.py) | 11/12 (91.7%) | 1/2 (50.0%) | 85.7% | web |
| [main.py](../../src/home_finder/main.py) | 86/91 (94.5%) | 37/38 (97.4%) | 95.3% | cli |
| [matching/__init__.py](../../src/home_finder/matching/__init__.py) | — | — | — | No named test context |
| [matching/deduplication.py](../../src/home_finder/matching/deduplication.py) | 270/279 (96.8%) | 141/152 (92.8%) | 95.4% | analysis, integration, matching, pipeline |
| [matching/scoring.py](../../src/home_finder/matching/scoring.py) | 138/139 (99.3%) | 49/50 (98.0%) | 98.9% | integration, matching, pipeline, property_based, scripts |
| [models/__init__.py](../../src/home_finder/models/__init__.py) | 5/5 (100.0%) | — | 100.0% | No named test context |
| [models/analysis.py](../../src/home_finder/models/analysis.py) | 75/76 (98.7%) | 5/6 (83.3%) | 97.6% | analysis, integration |
| [models/commute.py](../../src/home_finder/models/commute.py) | 63/63 (100.0%) | 20/20 (100.0%) | 100.0% | enrichment, integration, pipeline |
| [models/core.py](../../src/home_finder/models/core.py) | 136/138 (98.6%) | 13/14 (92.9%) | 98.0% | analysis, db, filters, integration, matching, models, notifiers |
| [models/delivery.py](../../src/home_finder/models/delivery.py) | 17/17 (100.0%) | — | 100.0% | integration, notifiers |
| [models/listing_text.py](../../src/home_finder/models/listing_text.py) | 53/53 (100.0%) | 12/12 (100.0%) | 100.0% | analysis, db, enrichment, integration, pipeline, scrapers, web |
| [models/notifications.py](../../src/home_finder/models/notifications.py) | 14/14 (100.0%) | — | 100.0% | No named test context |
| [models/quality.py](../../src/home_finder/models/quality.py) | 358/364 (98.4%) | 69/78 (88.5%) | 96.6% | analysis, db, integration, notifiers, web |
| [models/source_area.py](../../src/home_finder/models/source_area.py) | 56/58 (96.6%) | 19/20 (95.0%) | 96.2% | integration, scrapers |
| [models/spend.py](../../src/home_finder/models/spend.py) | 106/107 (99.1%) | 13/14 (92.9%) | 98.3% | analysis, db, integration, pipeline |
| [notifiers/__init__.py](../../src/home_finder/notifiers/__init__.py) | 2/2 (100.0%) | — | 100.0% | No named test context |
| [notifiers/delivery.py](../../src/home_finder/notifiers/delivery.py) | 153/156 (98.1%) | 53/54 (98.1%) | 98.1% | integration, notifiers, pipeline, web |
| [notifiers/telegram.py](../../src/home_finder/notifiers/telegram.py) | 427/511 (83.6%) | 178/230 (77.4%) | 81.6% | analysis, db, integration, notifiers, pipeline, web |
| [pipeline/__init__.py](../../src/home_finder/pipeline/__init__.py) | — | — | — | No named test context |
| [pipeline/assessment.py](../../src/home_finder/pipeline/assessment.py) | 257/261 (98.5%) | 102/112 (91.1%) | 96.2% | analysis, db, integration, pipeline |
| [pipeline/backup.py](../../src/home_finder/pipeline/backup.py) | 29/33 (87.9%) | 6/6 (100.0%) | 89.7% | No named test context |
| [pipeline/cache_recovery.py](../../src/home_finder/pipeline/cache_recovery.py) | 15/16 (93.8%) | 5/6 (83.3%) | 90.9% | db, integration, pipeline |
| [pipeline/candidates.py](../../src/home_finder/pipeline/candidates.py) | 250/268 (93.3%) | 87/102 (85.3%) | 91.1% | integration, pipeline |
| [pipeline/collection.py](../../src/home_finder/pipeline/collection.py) | 137/142 (96.5%) | 51/54 (94.4%) | 95.9% | integration, pipeline, scrapers |
| [pipeline/commands.py](../../src/home_finder/pipeline/commands.py) | 165/177 (93.2%) | 59/72 (81.9%) | 90.0% | integration, pipeline |
| [pipeline/contracts.py](../../src/home_finder/pipeline/contracts.py) | 108/108 (100.0%) | 8/8 (100.0%) | 100.0% | analysis, integration, pipeline |
| [pipeline/delivery_admin.py](../../src/home_finder/pipeline/delivery_admin.py) | 50/51 (98.0%) | 7/8 (87.5%) | 96.6% | notifiers, pipeline |
| [pipeline/event_recorder.py](../../src/home_finder/pipeline/event_recorder.py) | 39/39 (100.0%) | 2/2 (100.0%) | 100.0% | integration, pipeline |
| [pipeline/flow.py](../../src/home_finder/pipeline/flow.py) | 214/220 (97.3%) | 40/48 (83.3%) | 94.8% | integration, pipeline |
| [pipeline/image_recovery.py](../../src/home_finder/pipeline/image_recovery.py) | 50/50 (100.0%) | 18/18 (100.0%) | 100.0% | analysis, integration, pipeline |
| [pipeline/matching.py](../../src/home_finder/pipeline/matching.py) | 127/131 (96.9%) | 54/60 (90.0%) | 94.8% | db, integration, pipeline |
| [pipeline/notification_report.py](../../src/home_finder/pipeline/notification_report.py) | 41/43 (95.3%) | 2/2 (100.0%) | 95.6% | notifiers, pipeline |
| [pipeline/rating_shadow_report.py](../../src/home_finder/pipeline/rating_shadow_report.py) | 51/51 (100.0%) | 2/2 (100.0%) | 100.0% | pipeline |
| [pipeline/reanalysis.py](../../src/home_finder/pipeline/reanalysis.py) | 57/57 (100.0%) | 12/12 (100.0%) | 100.0% | analysis, integration, pipeline |
| [pipeline/recovery_admin.py](../../src/home_finder/pipeline/recovery_admin.py) | 26/29 (89.7%) | 5/6 (83.3%) | 88.6% | pipeline |
| [pipeline/run_lock.py](../../src/home_finder/pipeline/run_lock.py) | 90/91 (98.9%) | 21/22 (95.5%) | 98.2% | analysis, integration, notifiers, pipeline |
| [pipeline/spend_admin.py](../../src/home_finder/pipeline/spend_admin.py) | 39/46 (84.8%) | 7/10 (70.0%) | 82.1% | analysis, pipeline |
| [pipeline/spend_report.py](../../src/home_finder/pipeline/spend_report.py) | 25/27 (92.6%) | — | 92.6% | analysis, db |
| [scrapers/__init__.py](../../src/home_finder/scrapers/__init__.py) | 12/14 (85.7%) | 1/2 (50.0%) | 81.2% | No named test context |
| [scrapers/base.py](../../src/home_finder/scrapers/base.py) | 101/105 (96.2%) | 22/24 (91.7%) | 95.3% | db, pipeline, scrapers |
| [scrapers/constants.py](../../src/home_finder/scrapers/constants.py) | 2/2 (100.0%) | — | 100.0% | No named test context |
| [scrapers/detail_fetcher.py](../../src/home_finder/scrapers/detail_fetcher.py) | 502/601 (83.5%) | 179/248 (72.2%) | 80.2% | db, enrichment, integration, pipeline, scrapers |
| [scrapers/detail_identity.py](../../src/home_finder/scrapers/detail_identity.py) | 74/74 (100.0%) | 36/36 (100.0%) | 100.0% | integration, scrapers |
| [scrapers/onthemarket.py](../../src/home_finder/scrapers/onthemarket.py) | 160/173 (92.5%) | 50/62 (80.6%) | 89.4% | pipeline, scrapers |
| [scrapers/onthemarket_payloads.py](../../src/home_finder/scrapers/onthemarket_payloads.py) | 14/14 (100.0%) | 8/8 (100.0%) | 100.0% | filters, integration, scrapers |
| [scrapers/openrent.py](../../src/home_finder/scrapers/openrent.py) | 210/242 (86.8%) | 77/106 (72.6%) | 82.5% | db, pipeline, scrapers |
| [scrapers/parsing.py](../../src/home_finder/scrapers/parsing.py) | 34/34 (100.0%) | 18/18 (100.0%) | 100.0% | scrapers |
| [scrapers/retry.py](../../src/home_finder/scrapers/retry.py) | 8/8 (100.0%) | — | 100.0% | scrapers |
| [scrapers/rightmove.py](../../src/home_finder/scrapers/rightmove.py) | 251/303 (82.8%) | 88/134 (65.7%) | 77.6% | pipeline, scrapers |
| [scrapers/rightmove_payloads.py](../../src/home_finder/scrapers/rightmove_payloads.py) | 165/170 (97.1%) | 81/86 (94.2%) | 96.1% | filters, integration, scrapers |
| [scrapers/source_area.py](../../src/home_finder/scrapers/source_area.py) | 24/26 (92.3%) | 10/10 (100.0%) | 94.4% | integration, scrapers |
| [scrapers/zoopla.py](../../src/home_finder/scrapers/zoopla.py) | 364/428 (85.0%) | 122/164 (74.4%) | 82.1% | pipeline, scrapers |
| [utils/__init__.py](../../src/home_finder/utils/__init__.py) | — | — | — | No named test context |
| [utils/address.py](../../src/home_finder/utils/address.py) | 31/31 (100.0%) | 10/10 (100.0%) | 100.0% | analysis, db, enrichment, filters, integration, matching |
| [utils/async_tasks.py](../../src/home_finder/utils/async_tasks.py) | 36/36 (100.0%) | 12/12 (100.0%) | 100.0% | analysis, db, integration, notifiers, pipeline |
| [utils/circuit_breaker.py](../../src/home_finder/utils/circuit_breaker.py) | 67/67 (100.0%) | 16/16 (100.0%) | 100.0% | analysis, filters, integration, scrapers, utils |
| [utils/cost_calculator.py](../../src/home_finder/utils/cost_calculator.py) | 75/75 (100.0%) | 30/30 (100.0%) | 100.0% | notifiers, utils, web |
| [utils/epc_detector.py](../../src/home_finder/utils/epc_detector.py) | 82/84 (97.6%) | 34/36 (94.4%) | 96.7% | enrichment, utils |
| [utils/floorplan_detector.py](../../src/home_finder/utils/floorplan_detector.py) | 65/69 (94.2%) | 18/22 (81.8%) | 91.2% | utils |
| [utils/http_evidence.py](../../src/home_finder/utils/http_evidence.py) | 7/7 (100.0%) | 2/2 (100.0%) | 100.0% | filters, integration, scrapers |
| [utils/image_cache.py](../../src/home_finder/utils/image_cache.py) | 147/151 (97.4%) | 50/52 (96.2%) | 97.0% | analysis, db, enrichment, integration, notifiers, pipeline, scrapers, scripts, utils, web |
| [utils/image_hash.py](../../src/home_finder/utils/image_hash.py) | 126/130 (96.9%) | 45/48 (93.8%) | 96.1% | integration, matching, pipeline, utils |
| [utils/image_processing.py](../../src/home_finder/utils/image_processing.py) | 58/59 (98.3%) | 21/22 (95.5%) | 97.5% | analysis, integration, utils |
| [utils/listing_facts.py](../../src/home_finder/utils/listing_facts.py) | 35/35 (100.0%) | 18/18 (100.0%) | 100.0% | analysis, db, integration, utils, web |
| [utils/listing_identity.py](../../src/home_finder/utils/listing_identity.py) | 13/16 (81.2%) | 3/4 (75.0%) | 80.0% | analysis, enrichment, filters, integration, pipeline, scrapers |
| [utils/negotiation.py](../../src/home_finder/utils/negotiation.py) | 16/16 (100.0%) | 2/2 (100.0%) | 100.0% | notifiers, utils, web |
| [utils/postcode_lookup.py](../../src/home_finder/utils/postcode_lookup.py) | 58/68 (85.3%) | 19/24 (79.2%) | 83.7% | utils |
| [utils/union_find.py](../../src/home_finder/utils/union_find.py) | 0/17 (0.0%) | 0/6 (0.0%) | 0.0% | No named test context |
| [web/__init__.py](../../src/home_finder/web/__init__.py) | — | — | — | No named test context |
| [web/access.py](../../src/home_finder/web/access.py) | 154/168 (91.7%) | 58/66 (87.9%) | 90.6% | web |
| [web/annotate.py](../../src/home_finder/web/annotate.py) | 86/87 (98.9%) | 29/32 (90.6%) | 96.6% | web |
| [web/app.py](../../src/home_finder/web/app.py) | 101/110 (91.8%) | 12/18 (66.7%) | 88.3% | pipeline, web |
| [web/brief.py](../../src/home_finder/web/brief.py) | 130/137 (94.9%) | 47/56 (83.9%) | 91.7% | notifiers, web |
| [web/deliveries.py](../../src/home_finder/web/deliveries.py) | 12/12 (100.0%) | 2/2 (100.0%) | 100.0% | web |
| [web/filters.py](../../src/home_finder/web/filters.py) | 262/265 (98.9%) | 89/92 (96.7%) | 98.3% | db, integration, pipeline, web |
| [web/routes.py](../../src/home_finder/web/routes.py) | 364/406 (89.7%) | 107/134 (79.9%) | 87.2% | integration, notifiers, web |
| [web/telegram_webhook.py](../../src/home_finder/web/telegram_webhook.py) | 76/78 (97.4%) | 21/22 (95.5%) | 97.0% | web |
| [web/verdict.py](../../src/home_finder/web/verdict.py) | 24/24 (100.0%) | 4/4 (100.0%) | 100.0% | notifiers, web |
| [web/working.py](../../src/home_finder/web/working.py) | 19/19 (100.0%) | 4/4 (100.0%) | 100.0% | notifiers, web |

## Unexecuted functions with at least nine statements

These are candidates for review, not an automatic test-writing queue. A function may be obsolete, be behind a replaced transport, or need a scenario at a more meaningful caller boundary.

| Module | Function | Statements |
| --- | --- | ---: |
| `src/home_finder/scrapers/detail_fetcher.py` | `DetailFetcher._httpx_get_with_retry` | 9 |
| `src/home_finder/scrapers/detail_fetcher.py` | `DetailFetcher._curl_get_with_retry` | 25 |
