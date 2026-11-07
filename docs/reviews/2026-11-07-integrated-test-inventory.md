# Integrated suite inventory — 7 November 2026

4,202 collected cases in 193 test modules: 4,081 offline, 40 live, 4 live browser, 77 local browser. Collected with `pytest --collect-only -n 0 -m ""`. Collection is not execution.

This is the current inventory for the [integration report](2026-11-07-test-suite-integration.md). Scope descriptions come from module/class documentation; boundary and completeness limits are in that report. Counts are expanded pytest cases, including live and expected-failure cases.

| Module | Cases | Lanes | Scope |
| --- | ---: | --- | --- |
| [tests/integration/test_assessment_storage_and_detail.py](../../tests/integration/test_assessment_storage_and_detail.py) | 1 | offline: 1 | An assessment of cached images is stored and reaches the property detail page. |
| [tests/integration/test_candidate_preparation.py](../../tests/integration/test_candidate_preparation.py) | 4 | offline: 4 | Candidate preparation and persistence with mocked acquisition boundaries. |
| [tests/integration/test_core_upgrade.py](../../tests/integration/test_core_upgrade.py) | 1 | offline: 1 | Upgrade the deployed owner schema without losing decisions or rollback evidence. |
| [tests/integration/test_cross_platform_dedup_e2e.py](../../tests/integration/test_cross_platform_dedup_e2e.py) | 4 | live: 4 | E2E tests for cross-platform deduplication with real scraped data. |
| [tests/integration/test_cross_run_reanalysis.py](../../tests/integration/test_cross_run_reanalysis.py) | 3 | offline: 3 | A home notified in one run gains a source in a later run and is reassessed. |
| [tests/integration/test_detail_fetcher_e2e.py](../../tests/integration/test_detail_fetcher_e2e.py) | 12 | live: 12 | E2E tests for detail page fetching from real property platforms. RightmoveDetailFetcher; ZooplaDetailFetcher; OpenRentDetailFetcher; OnTheMarketDetailFetcher |
| [tests/integration/test_enrichment_pipeline_e2e.py](../../tests/integration/test_enrichment_pipeline_e2e.py) | 4 | live: 4 | E2E tests for the scrape → enrich flow with real HTTP. EnrichmentPipelineE2E |
| [tests/integration/test_gallery_evidence_consolidation.py](../../tests/integration/test_gallery_evidence_consolidation.py) | 4 | offline: 4 | Cached gallery images decide whether a candidate joins a stored anchor. |
| [tests/integration/test_off_market_command.py](../../tests/integration/test_off_market_command.py) | 10 | offline: 10 | The off-market command: stored homes, the checker's results, and the stored outcome. |
| [tests/integration/test_real_scraping.py](../../tests/integration/test_real_scraping.py) | 9 | live: 9 | Live collection probes for four rental portals. RealOpenRentScraping; RealRightmoveScraping; RealZooplaScraping; RealOnTheMarketScraping; LiveCollectionSelectionMatching |
| [tests/integration/test_run_pipeline.py](../../tests/integration/test_run_pipeline.py) | 6 | offline: 6 | The run coordinator from collection to delivery, with external services replaced. |
| [tests/integration/test_selection_persistence.py](../../tests/integration/test_selection_persistence.py) | 11 | offline: 11 | Selection, matching, and persistence contracts without the run coordinator. |
| [tests/integration/test_sort_verification.py](../../tests/integration/test_sort_verification.py) | 4 | live browser: 4 | Playwright tests verifying scraper sort parameters against real sites. |
| [tests/plugins/test_aiosqlite_leaks.py](../../tests/plugins/test_aiosqlite_leaks.py) | 4 | offline: 4 | The leak check closes a test's unclosed connections and leaves older ones alone. |
| [tests/plugins/test_schema_template.py](../../tests/plugins/test_schema_template.py) | 8 | offline: 8 | Schema-template activation, schema parity, fallback and resource ownership. |
| [tests/test_analysis/test_acoustic_context.py](../../tests/test_analysis/test_acoustic_context.py) | 2 | offline: 2 | Construction evidence reaches the actual evaluation request as acoustic context. |
| [tests/test_analysis/test_address_context.py](../../tests/test_analysis/test_address_context.py) | 6 | offline: 6 | Canonical location metadata reaches both native requests and checkpoint identity. |
| [tests/test_analysis/test_analysis_configuration.py](../../tests/test_analysis/test_analysis_configuration.py) | 62 | offline: 62 | How Settings resolve the analysis configuration, and what that configuration controls. |
| [tests/test_analysis/test_analysis_diagnostics.py](../../tests/test_analysis/test_analysis_diagnostics.py) | 3 | offline: 3 | Rejected model output exposes useful field paths without exposing its contents. |
| [tests/test_analysis/test_analysis_output_failures.py](../../tests/test_analysis/test_analysis_output_failures.py) | 57 | offline: 57 | Both native SDK phases must complete before an assessment is usable. |
| [tests/test_analysis/test_analyzer.py](../../tests/test_analysis/test_analyzer.py) | 55 | offline: 55 | PropertyQualityAnalyzer execution with a mocked SDK client. AnalyzeMergedProperties; VisualRequest; FloorplanNoteWiring; PhaseChaining; AnalyzeSingleMerged |
| [tests/test_analysis/test_area_scope.py](../../tests/test_analysis/test_area_scope.py) | 27 | offline: 27 | Keep external and unclassified floorplan areas out of internal arithmetic. |
| [tests/test_analysis/test_assessment_models.py](../../tests/test_analysis/test_assessment_models.py) | 80 | offline: 80 | Storage assessment models in models/quality.py: defaults, coercion, and historical readers. |
| [tests/test_analysis/test_attempt_settlement.py](../../tests/test_analysis/test_attempt_settlement.py) | 37 | offline: 37 | How each physical provider request is settled in the paid-attempt ledger. |
| [tests/test_analysis/test_badge_assembly.py](../../tests/test_analysis/test_badge_assembly.py) | 3 | offline: 3 | The analyzer reconciles each new assembly's badges after correcting listing facts. |
| [tests/test_analysis/test_badge_consistency.py](../../tests/test_analysis/test_badge_consistency.py) | 59 | offline: 59 | Badge reconciliation against the structured evidence of one new assembly. |
| [tests/test_analysis/test_evidence_grounding.py](../../tests/test_analysis/test_evidence_grounding.py) | 68 | offline: 68 | Evidence contradictions through native two-phase requests and durable reuse. |
| [tests/test_analysis/test_image_blocks.py](../../tests/test_analysis/test_image_blocks.py) | 7 | offline: 7 | Cache-only image blocks for the visual request (PropertyQualityAnalyzer._build_image_block). |
| [tests/test_analysis/test_image_review_coverage.py](../../tests/test_analysis/test_image_review_coverage.py) | 14 | offline: 14 | Coverage and uncertainty through native requests, caches and queue restarts. |
| [tests/test_analysis/test_input_reservation.py](../../tests/test_analysis/test_input_reservation.py) | 8 | offline: 8 | The input token reservation for the supported single-turn listing payload. |
| [tests/test_analysis/test_lifetime_spend_caps.py](../../tests/test_analysis/test_lifetime_spend_caps.py) | 3 | offline: 3 | A small run's lifetime and total-attempt caps hold across native calls and restarts. |
| [tests/test_analysis/test_listing_text.py](../../tests/test_analysis/test_listing_text.py) | 11 | offline: 11 | Bounded source inputs and protection against unsupported extracted bands. |
| [tests/test_analysis/test_malformed_output_retry.py](../../tests/test_analysis/test_malformed_output_retry.py) | 11 | offline: 11 | A completed but malformed visual response is retried once, as a separately paid attempt. |
| [tests/test_analysis/test_model_selection.py](../../tests/test_analysis/test_model_selection.py) | 19 | offline: 19 | Choosing a model per phase: supported settings, provenance, request contracts and rates. |
| [tests/test_analysis/test_outdoor_evidence.py](../../tests/test_analysis/test_outdoor_evidence.py) | 15 | offline: 15 | Outdoor absence requires evidence; unobserved type flags retain uncertainty. |
| [tests/test_analysis/test_prompts.py](../../tests/test_analysis/test_prompts.py) | 35 | offline: 35 | Prompt snapshot regression tests for quality analysis prompts. VisualSystemPromptSnapshot; EvaluationSystemPromptSnapshot; BuildUserPromptSnapshot; BuildEvaluationPromptSnapshot; ToolSchemaSnapshots |
| [tests/test_analysis/test_provider_transport.py](../../tests/test_analysis/test_provider_transport.py) | 10 | offline: 10 | Provider requests and responses through the real Anthropic SDK client. |
| [tests/test_analysis/test_quality_eval.py](../../tests/test_analysis/test_quality_eval.py) | 4 | live: 4 | Live structural checks of the two-phase analysis against the real Claude API. |
| [tests/test_analysis/test_rate_cards.py](../../tests/test_analysis/test_rate_cards.py) | 33 | offline: 33 | Rate cards: exact per-model prices and reservations, frozen with each paid attempt. |
| [tests/test_analysis/test_request_admission.py](../../tests/test_analysis/test_request_admission.py) | 60 | offline: 60 | What the spend middleware lets through to the provider, using the real SDK. |
| [tests/test_analysis/test_response_model_pairs.py](../../tests/test_analysis/test_response_model_pairs.py) | 40 | offline: 40 | Provider response models stay paired with the storage models they validate into. |
| [tests/test_analysis/test_static_gif_admission.py](../../tests/test_analysis/test_static_gif_admission.py) | 16 | offline: 16 | Static GIFs built from the cache must pass the native spending boundary. |
| [tests/test_analysis/test_token_usage.py](../../tests/test_analysis/test_token_usage.py) | 9 | offline: 9 | TokenUsage: the analyzer's in-memory token and cost estimate. TokenUsage |
| [tests/test_analysis/test_value_assessment.py](../../tests/test_analysis/test_value_assessment.py) | 11 | offline: 11 | Value-for-money against historical rental benchmarks (assess_value in analysis/analyzer.py). |
| [tests/test_analysis/test_visual_checkpoints.py](../../tests/test_analysis/test_visual_checkpoints.py) | 37 | offline: 37 | Restart and crash boundaries for paid visual observations, using the real SDK. |
| [tests/test_cli.py](../../tests/test_cli.py) | 56 | offline: 56 | CLI intent is validated before settings, paid work or process startup. |
| [tests/test_config.py](../../tests/test_config.py) | 24 | offline: 24 | Tests for application configuration. GetSearchAreas; GetFurnishTypes; GetSearchCriteria; DataDir; PropertyAnalysisTimeout |
| [tests/test_data/test_area_context.py](../../tests/test_data/test_area_context.py) | 66 | offline: 66 | Tests for area context data loading and accessor functions. DataLoading; MicroAreaValidation; Accessors; AcousticProfiles; NoiseEnforcement |
| [tests/test_data/test_location_mappings.py](../../tests/test_data/test_location_mappings.py) | 25 | offline: 25 | Tests for shared location mappings. RightmoveLocationsRegression; RightmoveOutcodesRegression; BoroughAreasRegression; SpotChecks; AliasCollisions |
| [tests/test_db/test_anchor_records.py](../../tests/test_db/test_anchor_records.py) | 12 | offline: 12 | Stored anchors: homes loaded for matching, and refreshing an anchor's sources. |
| [tests/test_db/test_backup.py](../../tests/test_db/test_backup.py) | 31 | offline: 31 | Backup proofs: live WAL, concurrent edits, interruption and isolated restore. |
| [tests/test_db/test_connection_lifecycle.py](../../tests/test_db/test_connection_lifecycle.py) | 9 | offline: 9 | Storage connection lifecycle: context manager, health check, reconnection and close. |
| [tests/test_db/test_dropped_properties.py](../../tests/test_db/test_dropped_properties.py) | 9 | offline: 9 | Homes dropped by the media admission policy are saved so later runs skip them. |
| [tests/test_db/test_floor_area_storage.py](../../tests/test_db/test_floor_area_storage.py) | 3 | offline: 3 | A home's listed floor area and its source survive storage and reach the dashboard query. |
| [tests/test_db/test_floorplan_origin.py](../../tests/test_db/test_floorplan_origin.py) | 12 | offline: 12 | Conservative migration and round-trip contracts for floorplan evidence. |
| [tests/test_db/test_legacy_notification_status.py](../../tests/test_db/test_legacy_notification_status.py) | 4 | offline: 4 | The legacy notification_status projection and the lists derived from it. |
| [tests/test_db/test_listing_text.py](../../tests/test_db/test_listing_text.py) | 9 | offline: 9 | Source text survives refresh, correction, persistence and retry paths. |
| [tests/test_db/test_location_backfill.py](../../tests/test_db/test_location_backfill.py) | 7 | offline: 7 | Ward and commute backfill for homes saved without them. Wards; CommuteBackfill |
| [tests/test_db/test_merge_safety.py](../../tests/test_db/test_merge_safety.py) | 33 | offline: 33 | A canonical match must not erase history or partially move source ownership. |
| [tests/test_db/test_migrations.py](../../tests/test_db/test_migrations.py) | 17 | offline: 17 | The versioned migration runner and the data repairs individual migrations make. |
| [tests/test_db/test_off_market.py](../../tests/test_db/test_off_market.py) | 19 | offline: 19 | Off-market state of homes and of their individual source listings. |
| [tests/test_db/test_pending_analysis_queue.py](../../tests/test_db/test_pending_analysis_queue.py) | 27 | offline: 27 | The pending initial-assessment queue: saving, loading, completing and resetting work. |
| [tests/test_db/test_pending_enrichment_queue.py](../../tests/test_db/test_pending_enrichment_queue.py) | 14 | offline: 14 | The pending enrichment queue: homes saved before their details or media arrived. |
| [tests/test_db/test_pre_analysis_grouping.py](../../tests/test_db/test_pre_analysis_grouping.py) | 2 | offline: 2 | Saving a matched group before analysis refuses to absorb members it must not retire. |
| [tests/test_db/test_price_history.py](../../tests/test_db/test_price_history.py) | 20 | offline: 20 | Price changes, their history, price-drop notification flags and value recomputation. |
| [tests/test_db/test_property_rows.py](../../tests/test_db/test_property_rows.py) | 8 | offline: 8 | Single-listing rows in the properties table, their images and dependent rows. |
| [tests/test_db/test_quality_analyses.py](../../tests/test_db/test_quality_analyses.py) | 4 | offline: 4 | Saving and reading the stored assessment of a home.  |
| [tests/test_db/test_reanalysis_queue.py](../../tests/test_db/test_reanalysis_queue.py) | 23 | offline: 23 | The reanalysis queue: requesting, loading and completing reassessment of a home. |
| [tests/test_db/test_run_repository.py](../../tests/test_db/test_run_repository.py) | 22 | offline: 22 | RunRepository (``storage.runs``): pipeline runs, scraper runs and property events. |
| [tests/test_db/test_source_membership.py](../../tests/test_db/test_source_membership.py) | 11 | offline: 11 | Source listings and the home each one belongs to. SeenListings; WritersLinkAbsorbedSources |
| [tests/test_db/test_spend.py](../../tests/test_db/test_spend.py) | 33 | offline: 33 | Spending admission must survive races, ambiguous outcomes and process exit. |
| [tests/test_db/test_status.py](../../tests/test_db/test_status.py) | 9 | offline: 9 | Owner status changes on a home and their recorded history. UpdateUserStatus; GetStatusHistory |
| [tests/test_db/test_transaction_ownership.py](../../tests/test_db/test_transaction_ownership.py) | 18 | offline: 18 | Interleaved storage operations must own their complete database work. |
| [tests/test_db/test_web_queries.py](../../tests/test_db/test_web_queries.py) | 35 | offline: 35 | Dashboard reads through WebQueryService: the grid, detail page, map and counts. |
| [tests/test_enrichment/test_commute.py](../../tests/test_enrichment/test_commute.py) | 39 | offline: 39 | Tests for advisory commute assessment with TravelTime API. CommuteAssessor; CommuteResult; GeocodeProperties |
| [tests/test_enrichment/test_detail_enrichment.py](../../tests/test_enrichment/test_detail_enrichment.py) | 35 | offline: 35 | Detail enrichment: acquiring detail text and media, and screening gallery images. |
| [tests/test_filters/test_availability.py](../../tests/test_filters/test_availability.py) | 96 | offline: 96 | Status evidence is about the requested listing, not neighbouring page text. |
| [tests/test_filters/test_availability_rightmove_indexed_model.py](../../tests/test_filters/test_availability_rightmove_indexed_model.py) | 16 | offline: 16 | Rightmove's indexed ``window.__PAGE_MODEL`` keeps the plain page model's status rules. |
| [tests/test_filters/test_availability_zoopla_linked_data.py](../../tests/test_filters/test_availability_zoopla_linked_data.py) | 35 | offline: 35 | Zoopla's JSON-LD listing node and its Offer must both belong to the requested listing. |
| [tests/test_filters/test_criteria.py](../../tests/test_filters/test_criteria.py) | 3 | offline: 3 | Tests for property criteria filtering. CriteriaFilter |
| [tests/test_filters/test_fit_score.py](../../tests/test_filters/test_fit_score.py) | 102 | offline: 102 | Tests for Marcel Fit Score and Lifestyle Quick-Glance Icons. Eligibility; ComputeFitScore; ComputeLifestyleIcons; SignalEffects; ComputeFitBreakdown |
| [tests/test_filters/test_fit_score_floor_area.py](../../tests/test_filters/test_fit_score_floor_area.py) | 10 | offline: 10 | Total floor area adds up to 15 hosting points, graduated from 30 to 55 m². |
| [tests/test_filters/test_location.py](../../tests/test_filters/test_location.py) | 9 | offline: 9 | Tests for LocationFilter. LocationFilter |
| [tests/test_filters/test_media_gate.py](../../tests/test_filters/test_media_gate.py) | 16 | offline: 16 | Media admission (filters.media): a source floorplan, an exempt source, or enough photos. |
| [tests/test_filters/test_off_market.py](../../tests/test_filters/test_off_market.py) | 17 | offline: 17 | Off-market spot checks: fetching, page assessment, batching and diagnostics. |
| [tests/test_filters/test_shadow_rating.py](../../tests/test_filters/test_shadow_rating.py) | 41 | offline: 41 | Keep the measured candidate reproducible without endorsing sparse evidence. |
| [tests/test_matching/test_canonical_selection.py](../../tests/test_matching/test_canonical_selection.py) | 13 | offline: 13 | Select canonical listing fields and floorplan evidence within a matched group. |
| [tests/test_matching/test_cross_platform_merge.py](../../tests/test_matching/test_cross_platform_merge.py) | 31 | offline: 31 | The deduplicator merges one home's listings across platforms and keeps other homes apart. |
| [tests/test_matching/test_floor_area_merge.py](../../tests/test_matching/test_floor_area_merge.py) | 4 | offline: 4 | Merging listings keeps the floor area of the highest-ranked portal that reports one. |
| [tests/test_matching/test_grouping_invariants.py](../../tests/test_matching/test_grouping_invariants.py) | 19 | offline: 19 | Match grouping keeps independent homes apart and never repeats a platform in a home. |
| [tests/test_matching/test_image_evidence.py](../../tests/test_matching/test_image_evidence.py) | 18 | offline: 18 | Image evidence distinguishes homes, and a merged home keeps one copy of each photo. |
| [tests/test_matching/test_match_score.py](../../tests/test_matching/test_match_score.py) | 26 | offline: 26 | A match score adds weighted signals for a pair of listings and grades the evidence. |
| [tests/test_matching/test_match_signals.py](../../tests/test_matching/test_match_signals.py) | 30 | offline: 30 | Signals that feed a match score: distance, rent closeness, postcode form, street and outcode. |
| [tests/test_matching/test_scoring_properties.py](../../tests/test_matching/test_scoring_properties.py) | 24 | offline: 24 | Hypothesis properties of match scoring and the signals it combines. |
| [tests/test_models.py](../../tests/test_models.py) | 34 | offline: 34 | Validation and serialisation of the listing, criteria and assessment models. |
| [tests/test_notifiers/test_delivery_recovery.py](../../tests/test_notifiers/test_delivery_recovery.py) | 38 | offline: 38 | Offline Bot API fault tests with real delivery transactions and restarts. |
| [tests/test_notifiers/test_enriched_listing_notification.py](../../tests/test_notifiers/test_enriched_listing_notification.py) | 5 | offline: 5 | Alerts for a fully enriched home: several portals, a gallery, a price range and concerns. |
| [tests/test_notifiers/test_notification_selection.py](../../tests/test_notifiers/test_notification_selection.py) | 34 | offline: 34 | Selection transactions, replay and delivery stop decisions using real SQLite. |
| [tests/test_notifiers/test_space_info.py](../../tests/test_notifiers/test_space_info.py) | 4 | offline: 4 | The alert's space line prefers a listed floor area over the analysis estimate. |
| [tests/test_notifiers/test_telegram.py](../../tests/test_notifiers/test_telegram.py) | 80 | offline: 80 | Telegram message formatting, photo resolution and the standalone send helpers. |
| [tests/test_notifiers/test_telegram_keyboard.py](../../tests/test_notifiers/test_telegram_keyboard.py) | 14 | offline: 14 | The inline keyboard sent with each discovery alert: status buttons, links and map. |
| [tests/test_pipeline/test_admin_cli.py](../../tests/test_pipeline/test_admin_cli.py) | 11 | offline: 11 | Administrative argument contracts; durable reconciliation is tested separately. |
| [tests/test_pipeline/test_analysis_timeout.py](../../tests/test_pipeline/test_analysis_timeout.py) | 7 | offline: 7 | Tests for per-property wall-clock timeout in run_concurrent_analysis. |
| [tests/test_pipeline/test_candidate_selection.py](../../tests/test_pipeline/test_candidate_selection.py) | 6 | offline: 6 | Which collected listings become candidates: criteria, seen listings and media admission. |
| [tests/test_pipeline/test_collection.py](../../tests/test_pipeline/test_collection.py) | 10 | offline: 10 | Collection across platforms: scraper coordination, area handling and known IDs. |
| [tests/test_pipeline/test_collection_commands.py](../../tests/test_pipeline/test_collection_commands.py) | 1 | offline: 1 | Scrape-only executes collection and prints observations without persisting homes. |
| [tests/test_pipeline/test_commute_outcomes.py](../../tests/test_pipeline/test_commute_outcomes.py) | 7 | offline: 7 | Advisory commute outcomes reach storage without false passes or lost homes. |
| [tests/test_pipeline/test_consolidation_image_cache.py](../../tests/test_pipeline/test_consolidation_image_cache.py) | 3 | offline: 3 | Consolidation copies a newcomer's cached images to the anchor it joins. |
| [tests/test_pipeline/test_consolidation_price_history.py](../../tests/test_pipeline/test_consolidation_price_history.py) | 5 | offline: 5 | A newly matched portal's quote can reveal a lower price for the stored home. |
| [tests/test_pipeline/test_consolidation_source_membership.py](../../tests/test_pipeline/test_consolidation_source_membership.py) | 9 | offline: 9 | Which candidates join a stored anchor, and when joining requests a reassessment. |
| [tests/test_pipeline/test_cross_run_consolidation.py](../../tests/test_pipeline/test_cross_run_consolidation.py) | 8 | offline: 8 | Consolidation across observations preserves identity and requests fresh assessment. |
| [tests/test_pipeline/test_dedup_existing.py](../../tests/test_pipeline/test_dedup_existing.py) | 4 | offline: 4 | run_dedup_existing (--dedup-existing): merge safe staging rows, report established homes. |
| [tests/test_pipeline/test_enrichment_admission.py](../../tests/test_pipeline/test_enrichment_admission.py) | 2 | offline: 2 | Bound enrichment without losing waiting homes or spending retries on the queue. |
| [tests/test_pipeline/test_enrichment_persistence.py](../../tests/test_pipeline/test_enrichment_persistence.py) | 9 | offline: 9 | Promoting queued discovery preserves enriched evidence across process restarts. |
| [tests/test_pipeline/test_enrichment_recovery.py](../../tests/test_pipeline/test_enrichment_recovery.py) | 6 | offline: 6 | Enrichment recovery grants: a retained anchor gets one more detail outcome without recreating its identity or resetting money. |
| [tests/test_pipeline/test_event_recorder.py](../../tests/test_pipeline/test_event_recorder.py) | 10 | offline: 10 | Tests for the property event recorder (buffered diagnostics). PropertyEvent; EventRecorder |
| [tests/test_pipeline/test_image_recovery.py](../../tests/test_pipeline/test_image_recovery.py) | 9 | offline: 9 | Tests for restoring missing analysis media from stored URLs. DownloadMissingImages; RestoreMissingAnalysisImages |
| [tests/test_pipeline/test_initial_assessment.py](../../tests/test_pipeline/test_initial_assessment.py) | 18 | offline: 18 | Initial assessment: completing a home, the assessment batch and estimated floor area. |
| [tests/test_pipeline/test_observation_accounting.py](../../tests/test_pipeline/test_observation_accounting.py) | 18 | offline: 18 | Real storage and orchestration; external effects are replaced at their boundaries. |
| [tests/test_pipeline/test_queued_work_progress.py](../../tests/test_pipeline/test_queued_work_progress.py) | 25 | offline: 25 | Existing work progresses without discovery and incomplete analysis never delivers. |
| [tests/test_pipeline/test_rating_shadow_report.py](../../tests/test_pipeline/test_rating_shadow_report.py) | 41 | offline: 41 | Saved crossings and read-only guarantees for the offline rating candidate. |
| [tests/test_pipeline/test_reanalysis.py](../../tests/test_pipeline/test_reanalysis.py) | 11 | offline: 11 | Tests for run_reanalysis() queue orchestration. RunReanalysisRequestOnly; RunReanalysisEarlyReturns; RunReanalysisSuccess; RunReanalysisErrorHandling |
| [tests/test_pipeline/test_reanalysis_queue_retention.py](../../tests/test_pipeline/test_reanalysis_queue_retention.py) | 2 | offline: 2 | A reassessment request stays queued until a reassessment completes. |
| [tests/test_pipeline/test_run_lock.py](../../tests/test_pipeline/test_run_lock.py) | 23 | offline: 23 | Cross-process exclusion and completion-relative scheduling, without provider I/O. |
| [tests/test_pipeline/test_run_observability.py](../../tests/test_pipeline/test_run_observability.py) | 73 | offline: 73 | Run accounting with real in-memory storage and synthetic work/provider boundaries. |
| [tests/test_pipeline/test_scraper_metrics.py](../../tests/test_pipeline/test_scraper_metrics.py) | 7 | offline: 7 | Tests for ScraperMetrics collection during scrape_all_platforms(). |
| [tests/test_pipeline/test_spend_admin.py](../../tests/test_pipeline/test_spend_admin.py) | 6 | offline: 6 | Operator spending commands: the read-only report and explicit reconciliation. |
| [tests/test_pipeline/test_spend_recovery.py](../../tests/test_pipeline/test_spend_recovery.py) | 7 | offline: 7 | Runner takeover frees execution without erasing potentially billed work. |
| [tests/test_pipeline/test_ward_lookup.py](../../tests/test_pipeline/test_ward_lookup.py) | 3 | offline: 3 | Ward acquisition selects useful evidence and persists only resolved locations. |
| [tests/test_resource_lifecycle.py](../../tests/test_resource_lifecycle.py) | 8 | offline: 8 | Network and provider clients close their resources when their async context exits. |
| [tests/test_scrapers/test_area_preflight.py](../../tests/test_scrapers/test_area_preflight.py) | 8 | offline: 8 | A misspelled area must fail before collection or location filtering. |
| [tests/test_scrapers/test_detail_fetcher_parsing.py](../../tests/test_scrapers/test_detail_fetcher_parsing.py) | 84 | offline: 84 | Unit tests for DetailFetcher parsing logic. FindDictWithKey; IsEpcUrl; RightmoveParsing; ZooplaParsing; ZooplaFloorplanFromHtml |
| [tests/test_scrapers/test_detail_identity.py](../../tests/test_scrapers/test_detail_identity.py) | 68 | offline: 68 | Wrong-advert responses must not escape as detail data or retained evidence. |
| [tests/test_scrapers/test_detail_transport.py](../../tests/test_scrapers/test_detail_transport.py) | 23 | offline: 23 | Detail transport attempt limits, backoff and independent throttle buckets. |
| [tests/test_scrapers/test_onthemarket.py](../../tests/test_scrapers/test_onthemarket.py) | 17 | offline: 17 | Tests for OnTheMarket scraper. OnTheMarketScraper; OnTheMarketParser; OnTheMarketEarlyStop |
| [tests/test_scrapers/test_onthemarket_contracts.py](../../tests/test_scrapers/test_onthemarket_contracts.py) | 63 | offline: 63 | Offline contracts for OnTheMarket envelopes, health and request bounds. |
| [tests/test_scrapers/test_onthemarket_detail_contracts.py](../../tests/test_scrapers/test_onthemarket_detail_contracts.py) | 18 | offline: 18 | OnTheMarket detail envelope drift must not erase enrichment or bypass identity. |
| [tests/test_scrapers/test_openrent.py](../../tests/test_scrapers/test_openrent.py) | 25 | offline: 25 | Tests for OpenRent scraper. OpenRentScraper; OpenRentParser; OpenRentScraperIntegration; OpenRentNoEarlyStop; OpenRent429Handling |
| [tests/test_scrapers/test_pagination.py](../../tests/test_scrapers/test_pagination.py) | 7 | offline: 7 | Retention and stop semantics, independent of any portal transport. |
| [tests/test_scrapers/test_parsing.py](../../tests/test_scrapers/test_parsing.py) | 26 | offline: 26 | Price, bedroom and postcode parsing shared by every portal's search scraper. |
| [tests/test_scrapers/test_rightmove.py](../../tests/test_scrapers/test_rightmove.py) | 23 | offline: 23 | Tests for Rightmove scraper. RightmoveScraper; RightmoveParser; RightmoveOutcodeResolver; RightmoveEarlyStop |
| [tests/test_scrapers/test_rightmove_contracts.py](../../tests/test_scrapers/test_rightmove_contracts.py) | 42 | offline: 42 | Retained search contract, visible parse failures and honest legacy detail support. |
| [tests/test_scrapers/test_rightmove_indexed_detail.py](../../tests/test_scrapers/test_rightmove_indexed_detail.py) | 31 | offline: 31 | Actual indexed detail fixture plus explicit malformed-graph mutations. |
| [tests/test_scrapers/test_rightmove_local_http.py](../../tests/test_scrapers/test_rightmove_local_http.py) | 5 | offline: 5 | Real HTTP search checks restricted to loopback, with no portal requests or signals. |
| [tests/test_scrapers/test_rightmove_transport.py](../../tests/test_scrapers/test_rightmove_transport.py) | 23 | offline: 23 | Search transport health, bounds, cleanup and application cancellation. |
| [tests/test_scrapers/test_scraper_location_leakage.py](../../tests/test_scrapers/test_scraper_location_leakage.py) | 15 | live: 7, offline: 8 | Search URLs for each area, plus an opt-in live report of out-of-area results. |
| [tests/test_scrapers/test_search_page_health.py](../../tests/test_scrapers/test_search_page_health.py) | 22 | offline: 22 | Collection health must describe received evidence, including partial failures. |
| [tests/test_scrapers/test_source_area.py](../../tests/test_scrapers/test_source_area.py) | 46 | offline: 46 | Reported units, ranges and decimal precision must survive parsing. |
| [tests/test_scrapers/test_zoopla.py](../../tests/test_scrapers/test_zoopla.py) | 59 | offline: 59 | Tests for Zoopla scraper. ZooplaScraper; ZooplaHtmlParser; ZooplaRscExtraction; ZooplaEarlyStop; CloudflareDetection |
| [tests/test_scrapers/test_zoopla_models.py](../../tests/test_scrapers/test_zoopla_models.py) | 37 | offline: 37 | Tests for Zoopla Pydantic models. ZooplaListingUris; ZooplaFeature; ZooplaImage; ZooplaPosition; ZooplaListing |
| [tests/test_scripts/test_audit_duplicate_candidates.py](../../tests/test_scripts/test_audit_duplicate_candidates.py) | 33 | offline: 33 | Synthetic offline coverage: review candidates never become identity decisions. |
| [tests/test_scripts/test_audit_image_cache.py](../../tests/test_scripts/test_audit_image_cache.py) | 28 | offline: 28 | Tests for scripts/audit_image_cache.py, run against the production schema. |
| [tests/test_scripts/test_reclassify_epc_images.py](../../tests/test_scripts/test_reclassify_epc_images.py) | 4 | offline: 4 | Tests for scripts/reclassify_epc_images.py. ReclassifyInDb; UndoDiskRenames |
| [tests/test_scripts/test_sync_to_fly.py](../../tests/test_scripts/test_sync_to_fly.py) | 3 | offline: 3 | The retired Fly sync script refuses to run, so it cannot replace a running database. |
| [tests/test_test_support/test_pytest_profile.py](../../tests/test_test_support/test_pytest_profile.py) | 3 | offline: 3 | Exercise the timing plugin in real serial and distributed pytest sessions. |
| [tests/test_utils/test_address.py](../../tests/test_utils/test_address.py) | 69 | offline: 69 | Address normalisation used for location checks and listing matching. |
| [tests/test_utils/test_circuit_breaker.py](../../tests/test_utils/test_circuit_breaker.py) | 17 | offline: 17 | Tests for shared circuit breaker utilities. CircuitBreaker; ConsecutiveFailureBreaker; APIUnavailableError |
| [tests/test_utils/test_cost_calculator.py](../../tests/test_utils/test_cost_calculator.py) | 54 | offline: 54 | Advert evidence cannot silently become a complete household budget. |
| [tests/test_utils/test_epc_detector.py](../../tests/test_utils/test_epc_detector.py) | 14 | offline: 14 | Tests for PIL-based EPC chart detection. DetectEpc |
| [tests/test_utils/test_floorplan_detector.py](../../tests/test_utils/test_floorplan_detector.py) | 12 | offline: 12 | Tests for PIL-based floorplan detection. DetectFloorplan |
| [tests/test_utils/test_http_evidence.py](../../tests/test_utils/test_http_evidence.py) | 5 | offline: 5 | Recognising a Cloudflare challenge page instead of the requested listing. |
| [tests/test_utils/test_image_cache.py](../../tests/test_utils/test_image_cache.py) | 64 | offline: 64 | Tests for image cache utilities. SafeDirName; GetCacheDir; UrlToFilename; IsPropertyCached; SaveAndReadImageBytes |
| [tests/test_utils/test_image_hash.py](../../tests/test_utils/test_image_hash.py) | 47 | offline: 47 | Tests for image hashing utilities. HashesMatch; FetchAndHashImage; FetchImageHashesBatch; HashFromDisk; HashCachedGallery |
| [tests/test_utils/test_image_processing.py](../../tests/test_utils/test_image_processing.py) | 44 | offline: 44 | Tests for image processing utilities. TransparentFloorplans; IsValidMediaType; ResizeImageBytes; DetectMediaTypeFromBytes; Constants |
| [tests/test_utils/test_negotiation.py](../../tests/test_utils/test_negotiation.py) | 11 | offline: 11 | Observation age and unsupported research cannot justify an offer level. |
| [tests/test_utils/test_postcode_lookup.py](../../tests/test_utils/test_postcode_lookup.py) | 9 | offline: 9 | Tests for postcodes.io client utility. LookupWard; ReverseLookupWard; BulkReverseLookup |
| [tests/test_web/browser/test_browser_e2e.py](../../tests/test_web/browser/test_browser_e2e.py) | 77 | local browser: 77 | Browser-based E2E tests using Playwright against a live FastAPI server. |
| [tests/test_web/test_access.py](../../tests/test_web/test_access.py) | 47 | offline: 47 | Access checks against the complete application, including mounted assets. |
| [tests/test_web/test_annotate.py](../../tests/test_web/test_annotate.py) | 6 | offline: 6 | Notes on the listing text.  |
| [tests/test_web/test_app.py](../../tests/test_web/test_app.py) | 9 | offline: 9 | Tests for the FastAPI application factory. CreateApp; SecurityHeaders; PipelineConfig |
| [tests/test_web/test_area_page.py](../../tests/test_web/test_area_page.py) | 8 | offline: 8 | Area page (GET /area/{outcode}): the full area guide linked from each detail page. |
| [tests/test_web/test_brief.py](../../tests/test_web/test_brief.py) | 11 | offline: 11 | The brief check and the lead line it gives a listing.  |
| [tests/test_web/test_cached_images.py](../../tests/test_web/test_cached_images.py) | 11 | offline: 11 | Cached listing images: the /images route and the local URLs pages use instead of CDN ones. |
| [tests/test_web/test_card_and_status.py](../../tests/test_web/test_card_and_status.py) | 16 | offline: 16 | The card partial (GET /property/{id}/card) and status changes (PATCH /property/{id}/status). |
| [tests/test_web/test_dashboard.py](../../tests/test_web/test_dashboard.py) | 23 | offline: 23 | Dashboard page (GET /): the property grid, its cards, the map data and the page shell. |
| [tests/test_web/test_dashboard_filters.py](../../tests/test_web/test_dashboard_filters.py) | 77 | offline: 77 | Dashboard filters and sorting: query parameters, chips, the Filters dialog and /count. |
| [tests/test_web/test_delivery_view.py](../../tests/test_web/test_delivery_view.py) | 3 | offline: 3 | Owner-only, bounded delivery metadata without payloads or raw failures. |
| [tests/test_web/test_detail_costs.py](../../tests/test_web/test_detail_costs.py) | 8 | offline: 8 | The detail page's costs section ("What it costs"), rebuilt from current text and rent. |
| [tests/test_web/test_detail_floorplans.py](../../tests/test_web/test_detail_floorplans.py) | 4 | offline: 4 | Floorplans on the detail page: repeated plans are set aside, never hidden. |
| [tests/test_web/test_detail_page.py](../../tests/test_web/test_detail_page.py) | 29 | offline: 29 | Listing detail page (GET /property/{id}, rendered by property_detail into detail.html). |
| [tests/test_web/test_diagnostics.py](../../tests/test_web/test_diagnostics.py) | 3 | offline: 3 | GET /diagnostics: the owner's view of pipeline state (protected by the access boundary). |
| [tests/test_web/test_filter_navigation.py](../../tests/test_web/test_filter_navigation.py) | 6 | offline: 6 | Real query/HTTP regressions for exact tags and complete dashboard URLs. |
| [tests/test_web/test_floor_area_badge.py](../../tests/test_web/test_floor_area_badge.py) | 2 | offline: 2 | Dashboard cards show a floor-area badge only when the area is known. |
| [tests/test_web/test_outdoor_evidence.py](../../tests/test_web/test_outdoor_evidence.py) | 17 | offline: 17 | Stored historical zeros must not become evidence of outdoor absence in the UI. |
| [tests/test_web/test_processing_visibility.py](../../tests/test_web/test_processing_visibility.py) | 32 | offline: 32 | Processing failures must not remove otherwise useful homes from owner review. |
| [tests/test_web/test_property_filter.py](../../tests/test_web/test_property_filter.py) | 65 | offline: 65 | PropertyFilter (home_finder.web.filters): query validation, chips and filter counts. |
| [tests/test_web/test_reanalysis_request.py](../../tests/test_web/test_reanalysis_request.py) | 5 | offline: 5 | POST /property/{id}/reanalyze queues an assessed home for the next --reanalyze run. |
| [tests/test_web/test_score_consistency.py](../../tests/test_web/test_score_consistency.py) | 26 | offline: 26 | Score eligibility and postcode context agree across actual consumers. |
| [tests/test_web/test_telegram_setup.py](../../tests/test_web/test_telegram_setup.py) | 4 | offline: 4 | Real webhook startup and callback acknowledgement with only the Bot API replaced. |
| [tests/test_web/test_telegram_webhook.py](../../tests/test_web/test_telegram_webhook.py) | 34 | offline: 34 | POST /telegram/webhook: status callbacks from the alert keyboard (st:{unique_id}:{status}). |
| [tests/test_web/test_template_filters.py](../../tests/test_web/test_template_filters.py) | 6 | offline: 6 | Jinja filters registered by the web routes. ListingAgeFilter |
| [tests/test_web/test_verdict.py](../../tests/test_web/test_verdict.py) | 6 | offline: 6 | The fits / watch out / find out lists that lead the detail page (build_verdict). |
| [tests/test_web/test_working.py](../../tests/test_web/test_working.py) | 17 | offline: 17 | The fit score with its working shown.  |

Research harnesses under `experiments/layout-inference` and `experiments/dedup` have separate dependencies and test configuration. They are outside root `testpaths` and were not rerun in this integration. No browser result here establishes live portal support or paid-model accuracy.
