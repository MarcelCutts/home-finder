# Score consistency and provisional alert selection

13 October 2026. Evidence for T07 of the [architecture assessment](2026-10-13-architecture-assessment-and-tasks.md), tracked under G1/M4 in the [execution plan](../plans/2026-10-13-goal-driven-implementation.md).

**Decision: use 60 as the provisional default discovery threshold.** A home must first have a valid rated assessment without a recorded analysis failure. Archived, rejected or recorded off-market homes do not generate new alerts. Preserve every dashboard record and record the policy, score and reason for each notification decision. This is a product starting point, not a validated accuracy claim. Score normalization and durable notification selection are implemented; the later section records the policy replay.

## What changed in the score

Fit policy v3 supplies one shared input boundary to persistence, backfill, price refresh, dashboard cards/detail/map data, Telegram formatting and delivery-plan construction. It requires an integer rating from 1 to 5 and no analysis failure. Raw dictionaries pass through the existing tolerant historical decoder, so legacy literal coercions match typed consumers; a shape that decoder rejects gets no recommendation. The decoder receives a deep copy because its historical literal coercion can modify nested dictionaries. The original evidence remains unchanged. The score derives area context from the property, rather than trusting a field embedded in assessment JSON.

Noise-note presence and absence no longer affect sound points, confidence or hosting warnings. The notes combine favorable, unfavorable and uncertain observations. An empty default list is not proof of quiet. Typed construction, glazing and hosting-risk fields remain inputs; missing hosting risk appears as unknown. Area hosting guidance remains part of the existing owner score, but its factors now explicitly say **unverified**. Revisiting the underlying geography/research belongs to T14; consistency does not validate that research.

Score wrappers and breakdowns use one calculation. Unrated/failed records have no recommendation or lifestyle icons. SQL assessment filters and map data use the resulting eligibility rather than treating any non-null rating as sufficient. A v3 backfill updates only derived score/version fields. It does not rewrite assessment JSON, personal status, delivery history or notification intent.

## Historical replay and denominators

Both snapshots were opened using SQLite `mode=ro` and `PRAGMA query_only=ON`. Their SHA-256 values were identical before and after inspection. No application initialization, schema migration, notifications or provider requests were used.

| Snapshot | Homes | Assessments | Rated under v3 | Without a score |
| --- | ---: | ---: | ---: | ---: |
| September 27 historical export | 1,016 | 1,001 | 976 | 40: 25 unrated assessments and 15 without an assessment |
| October 10 operating trial | 93 | 91 | 91 | 2 without an assessment |

The September replay produced identical scores from raw saved JSON and historically decoded models for all **1,001 assessments**. Compared with the saved score cache, v3 changes 47 September rows and 14 October rows. This comparison includes the removal of invalid recommendations; it does not imply that every changed number is a measured quality improvement.

These are hypothetical *first-discovery* selections over retained evidence. Previously delivered homes must not be alerted again. Neither snapshot establishes current availability: both have zero recorded off-market flags. These counts are not a forecast of today's market or a live per-run alert rate.

| Minimum score | September score-qualified / 976 rated | September after excluding archived | October score-qualified / 91 rated | Historically pursued retained / 7 |
| --- | ---: | ---: | ---: | ---: |
| 60 | 455 (46.6%) | 446 | 4 (4.4%) | 7 |
| 65 | 342 (35.0%) | 333 | 2 (2.2%) | 6 |
| 70 | 221 (22.6%) | 212 | 0 | 6 |
| 75 | 130 (13.3%) | 122 | 0 | 6 |
| 80 | 50 (5.1%) | 46 | 0 | 5 |

“Pursued” here means four currently interested, one enquired and two offered homes. Their scores are 62, 78, 80, 81, 82, 83 and 87. That is a very small, selected set. The 999 September homes still marked `new` are not negative labels. Nine of ten archived homes score at least 60; archive is therefore an explicit personal decision to respect, not a reliable training label for poor property quality.

## Mixed sample review

I reviewed **24 cached assessment records**: all seven pursued September homes, three archived examples, five new September homes near useful score boundaries, all four October homes at least 60, four lower-scoring October examples, and one October home without an assessment. I checked the retained structured observations and summaries, not the original photos, floorplans or current listings. The judgments below concern the suitability of a notification rule on this evidence; they are not new property assessments.

Identifiers are the snapshot prefix (`S` or `O`) plus the first ten hexadecimal characters of SHA-256 of the property's stored unique ID. No addresses, source links or personal notes are copied into this document.

| Sample | Score; saved decision | Review and implication |
| --- | --- | --- |
| S-a7573a42c8 | 83; offered | Large living area and separate work area make an alert reasonable; traffic/impact observations still need attention. |
| S-2d67aa1569 | 87; enquired | Dedicated workspace, useful living area and strong light support surfacing it. Several “noise indicators” are favorable construction/glazing observations. |
| S-4ea6563a68 | 81; interested | Workspace and living area support an alert. Adjacent loading activity remains a specific viewing question. |
| S-c58131641d | 78; interested | Dedicated workspace and usable living area support an alert despite an unknown hob. |
| S-ac0cee5fb7 | 62; interested | Practical two-bedroom layout and office separation remain useful despite electric cooking/heating and weaker light. This is the concrete false omission from a 65/70 cutoff. |
| S-31b1add073 | 82; interested | A retained dedicated-study observation makes a one-bedroom property relevant; bedroom count alone should not reject it. |
| S-b5111327a5 | 80; offered | Large retained living-area estimate supports hosting, while the narrow bedroom and road exposure remain material drawbacks. A high score cannot verify the area estimate. |
| S-09aa775847 | 32; archived | Compact/awkward hosting and shared workspace support dashboard-only selection independently of the archive decision. |
| S-30f15287e6 | 79; archived | Attractive light/space coexist with rail exposure and shared workspace. Suppress because it is archived; do not reinterpret the archive as a universal score label. |
| S-33d8698468 | 80; archived | Favorable retained features still do not override the owner's archive decision. |
| S-ca4d35a703 | 40; new | Serious condition concerns and weak workspace support dashboard-only treatment; the gas hob/garden do not erase the concerns. |
| S-4324694619 | 59; new | Dedicated office potential competes with compact, awkward living space. Keep visible as a near-threshold case for review rather than deleting it. |
| S-bc9434f89f | 60; new | Light, kitchen and two bedrooms justify a provisional alert, but living area is unknown and workspace remains shared. An alert must not claim those questions are resolved. |
| S-3416f2470a | 69; new | Useful workspace and living area justify inclusion while glazing remains uncertain. A 70 boundary has no strong evidence-based distinction here. |
| S-461d0402fb | 70; new | Practical workspace and hosting evidence support an alert; it need not receive a different eligibility rule from the 69 example. |
| O-e24989553c | 61; new | Two bedrooms, dedicated workspace and gas cooking support review; living area/spaciousness are unknown. |
| O-55e9f4df50 | 60; new | Strong kitchen/workspace observations support provisional inclusion. Unknown dimensions and glazing require qualification. |
| O-3200b11c42 | 65; new | Two-bedroom office potential, light and double glazing support review; living area remains unknown. |
| O-c19b21c425 | 65; new | A retained 22.8 m² living area and workspace support inclusion, with road exposure and electric cooking as caveats. |
| O-6a9db682ca | 20; new | Compact, awkward one-bedroom layout and unclear desk space are a weak fit for this owner's hosting/workspace priorities. |
| O-bfa2bd6c46 | 29; new | Shared workspace and typed high hosting-noise risk support dashboard-only selection. |
| O-323e7fa099 | 40; new | Attractive refurbishment does not resolve shared workspace and high hosting-noise risk. |
| O-57d11067db | 50; new | Good finishes/light contrast with unclear dimensions and shared workspace. Suitable for dashboard review without assuming a strong match. |
| O-6583a76a2e | Unscored; new | No assessment supports a recommendation; do not produce a fallback fit or alert from default dimensions. |

The October sample has substantially more explicit uncertainty about dimensions and spaciousness. Its lower score distribution cannot be attributed solely to worse properties: different selected homes, retained evidence and analysis behavior are confounded. This is another reason not to claim cross-snapshot calibration or automatically raise the threshold to reduce volume.

## Policy implementation requirements

1. **Discovery:** default minimum fit 60, configurable and recorded with a policy version. A successfully completed but below-threshold/unassessed home receives a durable dashboard-only decision, not a fake “sent” status or an endless delivery retry. Existing processing retries for incomplete analysis retain their separate rules.
2. **Personal and availability decisions:** archived and recorded off-market homes do not start alerts. Recheck current state before dispatch so a user's archive action can stop queued work. Preserve any receipts already obtained.
3. **Price changes:** treat an update to an already announced or explicitly tracked home separately from a new recommendation. Respect archive/off-market state, keep one event per price-history revision, and do not replay a previous price drop after restart. Final implementation must make the exact prior-interest rule explicit in its versioned decision.
4. **Existing delivery state:** keep frozen plans and acknowledgements. A new score version cannot rewrite a partially delivered message or silently retry an uncertain part. Freeze migration 12's legacy classification before changing runtime selection.
5. **Shadow evidence:** provide a mode/report that records candidate decisions without sending or creating new delivery intent. The replay above is offline historical evidence; it does not satisfy a future claim about live alert volume or transport qualification.
6. **Calibration follow-up:** review new inclusions and dashboard-only cases with the owner, especially near 60 and where evidence is sparse. Record denominators, dates and reasons. Revisit the threshold/profile in G3 from those decisions, rather than treating seven positive examples or an automated review as accuracy measurement.

Snapshot file identities for reproduction:

```text
data/production-review/2026-09-27T122222Z/properties.db
SHA-256 e50897b46ebefea0119fd62951ac277d764684f9590d52801e66c964b1352c63

data/operating-trials/2026-10-10/properties.db
SHA-256 3c1e9c9f33fd97a412991d55c505bff107cd86b61a8880622906f3e077a5ed9f
```

The read-only replay joins `properties` to `quality_analyses`, calculates `compute_fit_score(json.loads(analysis_json), bedrooms, postcode=postcode)` where an assessment exists, and groups by candidate thresholds and saved `user_status`. Do not initialize these snapshots through `PropertyStorage` for a replay; initialization intentionally backfills derived scores. The temporary detailed review projection is `/tmp/home-finder-notification-review-sample.json`; the table and snapshot identities above are the durable review record.

## Implementation and reproducible shadow report

The new runtime policy is `owner_notifications_v1`, with configurable minimum 60.
It records one discovery decision atomically with assessment completion, including
re-enrichment, and retains suppressed homes as `dashboard_only`. It preserves
previous decisions across reanalysis, configuration changes and restart. Existing
delivery plans and receipts are never rebuilt merely to adopt this cutoff.

Price updates qualify through prior discovery or explicit pursuit (`interested`,
`enquired`, `viewing_booked`, `viewed`, `applied`, `offered`), independently of fit.
A queued discovery delays a price alert until the discovery completes unless the
owner has explicitly tracked the home. Archived/rejected/off-market state stops
known unsent work and is rechecked at dispatch. Existing uncertainty remains for
operator reconciliation. Migration 15 adds nullable decision metadata; it does
not replay or reclassify historical alerts.

The production policy function also powers this credential-free read-only command:

```bash
uv run python -m home_finder.pipeline.notification_report \
  --database data/production-review/2026-09-27T122222Z/properties.db \
  --minimum-fit-score 60
```

Its October 13 replay reproduced these denominators using the exact hashes above:

| Snapshot | Retained homes | Valid scores | Eligible | Below minimum | Assessment unavailable | Archived |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| September | 1,016 | 976 | 446 | 520 | 40 | 10 |
| October | 93 | 91 | 4 | 87 | 2 | 0 |

The columns from eligible through archived are mutually exclusive decisions, so
each row sums to retained homes. Both file hashes remained unchanged. Output is
aggregated without addresses, notes or listing IDs. This is a counterfactual
application to every retained home, including already delivered ones, and creates
no delivery records. The actual send queue remains governed by prior decisions,
processing readiness, availability and delivery state. Live qualification remains
pending. Detailed regression evidence is recorded in the execution plan.
