# Deduplication quality: direct review of the 50-listing capture

**Reviewed:** 29 September 2026. **Application baseline:** `89d0f4c`. **Reviewer:** Codex, using direct visual and textual inspection, followed by offline scoring experiments. This is an agent development adjudication, not independent human ground truth.

The current matcher did a good job of joining syndicated advertisements that reuse photographs. I found no apparent wrong merge among its **15 accepted pairs**. It nevertheless left **three avoidable duplicate cards**: one Southborough Road home appeared twice, and one Wilmot Street home appeared three times. Applying only these supported consolidations would leave **32 provisional homes instead of 35**. One further Cheshire Street relationship remains uncertain; 32 is not a certified count of every distinct dwelling.

The practical recommendation is to retain conservative automatic matching, implement the missing identity/membership contract, and surface unresolved candidates for review. A larger photo-hash threshold is not a safe shortcut: an offline experiment created a demonstrably wrong Cheshire Street merge before it recovered either missed cross-portal Wilmot link. No live memberships, thresholds or application settings were changed.

## Evidence and method

The source is the [50-listing experiment](../plans/2026-09-29-fifty-listing-pipeline-experiment.md), comprising 25 Rightmove and 25 OnTheMarket listings. I reviewed all 50 listings through a local evidence deck: **200 original gallery photos and 35 source-declared floorplans**, together with descriptive text, features, rent, bedrooms, address and coordinates. Close cases were examined at the original image resolution. All 15 accepted pairs received a visual assessment; review also covered listings the matcher never compared.

Each image comes from its original source detail observation and original source cache. This matters because the pipeline copies images into a merged home's cache: comparing those augmented galleries would create circular evidence for the original merge. The review manifest records source URLs, original file paths and SHA-256 digests. Images, full advertising text, screenshots and generated reports remain private and outside Git.

The deck omits match scores and predicted memberships. **The review was not blind:** I had already seen the live results and two suspected misses. These labels are useful development cases and explainable judgments; they cannot qualify the independent reference set or a production accuracy claim.

A separate mechanical scan compared all **1,225 unordered listing pairs**, including same-portal and different-bedroom pairs. A deliberately broad review screen selected pairs sharing a normalized street, lying within 100 metres, or having an original-gallery pHash distance at most 12. This produced 51 pairs. Seven further pairs that became eligible in the relaxed-threshold experiment were also adjudicated.

The [versioned label file](2026-09-29-deduplication-labels.json) contains:

- **19 same-home pair judgments**, represented by 17 duplicate-home groups: sixteen pairs and one triple.
- **38 different-home pair judgments**, with reasons tied to layout, windows, kitchens, floorplans or other specific evidence.
- **One uncertain pair**, retained separately.
- Notes for the 15 listings without a confidently identified counterpart after the supported groups are formed.
- **1,167 unreviewed pair relationships**, which are deliberately not counted as true negatives. All 50 listings were inspected, but every possible pair was not independently adjudicated.

The broad screen is a search aid, not proof of complete recall. Misleading coordinates, alternate addresses, changed photos, inaccurate bedroom counts and missing unit numbers can still conceal a duplicate. The sample is a bounded, nonrandom capture from two portals and a narrow rent range. Four retained photos per listing are not the complete advertising gallery.

## Accepted pairs

All 15 accepted pairs are supported by several matching interior photographs, not solely an exterior, nearby coordinates or a common street. Each also has four gallery pHash matches under the current threshold; all have a minimum distance of zero. Where floorplans exist on both sources, their layouts corroborate identity. Matching marketing material is still evidence rather than authoritative proof of the physical unit.

Neutral IDs below map to exact provider listing IDs in the label file and private deck.

| Listings | Home | Specific corroboration beyond location |
| --- | --- | --- |
| R01 / O02 | Wilmot Street, £1,800 | Brick-feature kitchen, purple sofa, green rug and matching plan |
| R02 / O03 | Meath Crescent | Occupied kitchen/reception, balcony plan, reference CHPK3582527 |
| R03 / O04 | Allgood Street | Identical interiors and irregular third-floor plan; O04 supplies Flat 12 |
| R05 / O05 | Corfield Street | Kitchen, bedroom, exterior and ground-floor two-bedroom plan |
| R07 / O07 | Hackney Road, £1,800 | Pink sofas, arched windows, wooden chest and fitted kitchen |
| R08 / O10 | Millenium Place, £2,000 | Skylit kitchen, empty reception and second-floor plan |
| R09 / O01 | Hackney Road, £2,100 | Wooden kitchen, stairwell and split first/second-floor plan |
| R10 / O12 | Columbia Road | Gridded glazing, kitchen and long first-floor plan; CHPK0185159 |
| R11 / O13 | Wilmot Street, £1,800 | Lower-ground white kitchen and patio plan; CHPK3247417 |
| R12 / O14 | Queensbridge Road | Occupied rooms, patterned rug, plan geometry; CHPK3464168 |
| R15 / O20 | Cadogan Terrace | Occupied interior, first-floor plan and property reference 3009430 |
| R16 / O19 | Prince Edward Road | Black kitchen, balcony and matching second-floor plan |
| R17 / O21 | San House, Bradstock Road | Building plus cream kitchen, windows and balcony door |
| R21 / O24 | Sky Apartments | Occupied rooms and seventh-floor plan; CHPK3707955 |
| R22 / O25 | Chelmer Road | Kitchen, bedrooms, plan and property reference 2487475 |

Several details illustrate why identity and current property facts need separate treatment. Cadogan's text says the furnished photographs are from a previous tenancy and the flat will be unfurnished. Chelmer's availability dates differ by one day. Both Prince Edward Road sources use a floorplan headed Wallis Road. These facts should be preserved as source evidence; they do not outweigh the combined interior evidence supporting those pairs.

## Missed duplicates

### Southborough Road: two IDs on the same portal

**R19 `rightmove:92353701` and R20 `rightmove:92353155`: high-confidence same advertised home.**

Both show the same exterior and the same three occupied interiors, including green dining chairs, cushions, plants, kitchen objects and bedroom furnishings. Full postcode, coordinates, £2,250 rent and one bedroom agree. Descriptions differ principally by a disclaimer. This is much stronger than two flats sharing a building photograph.

The matcher excludes overlapping sources before scoring. Its merged URL and text projections are also keyed by portal, so simply deleting that guard could lose one of the advertisements. The required repair starts with multiple independently identified source listings per canonical home and reversible membership.

### Wilmot Street: one home photographed differently, on three advertisements

**R13 `rightmove:91938783`, O08 `onthemarket:20385686`, and O09 `onthemarket:20370518`: high-confidence same advertised home.**

The photographs show the same window and curtain pattern, projecting wall beside the kitchen opening, circular wire mirror, long framed mirror, wall artwork, lamps, white cabinet and rug. R13 and O08 also show the same cream kitchen with black tiles and the same cabinet/appliance layout from different positions. Both describe the second floor. O09 supplies closer views of the same sitting room and a bathroom photograph.

Furniture positions, a portable heater, lighting and floor appearance vary. Those changes deserve attention but do not explain away the combination of architectural geometry and distinctive objects. The decision does not rest on one reusable ornament or a generic furnished-room resemblance. There is no authoritative unit identifier in this capture, so independent review remains valuable.

| Pair | Closest original-gallery pHash distance | Current outcome |
| --- | ---: | --- |
| R13 / O08 | 24 | Non-image score 80.195; rejected for no shared image hash |
| R13 / O09 | 24 | Non-image score 82.299; rejected for no shared image hash |
| O08 / O09 | 20 | Excluded because both advertisements are on OnTheMarket |

Even if one cross-portal link were accepted, the current one-listing-per-portal constraint would prevent all three advertisements from belonging to the same home. This is both a representation limitation and a matching limitation.

## Correct separations and an unresolved case

The three Wilmot homes must not be collapsed into one street-level group. R01/O02 have a brick-feature grey kitchen and a different reception/plan; R11/O13 have a lower-ground garden layout with patio access; R13/O08/O09 have the second-floor black-tiled kitchen and distinctive sitting room. Some different units are only about 10 metres apart in the supplied coordinates. The five live image vetoes therefore comprise **two supported missed links and three correct rejections**. Removing that veto would discard useful protection as well as recover candidates.

Millennium Place R04 is a third-floor flat with a 37.53 m² plan; R08/O10 show a different second-floor layout of approximately 44.2 m² and a skylit kitchen. The shared postcode and building do not establish a shared dwelling. Nearby Corfield/Wilmot and San House/Granard House comparisons also demonstrate why the review must consider, but not blindly join, records outside the current bedroom blocks.

**Cheshire R06 versus O06 is a strong different-home case.** R06 has a 33.3 m² plan with an irregular entrance and narrow shower room. O06 has a 43.0 m² third-floor plan with a balcony and different entrance/bathroom geometry. The window and kitchen arrangements differ too. These are the two homes erroneously joined by the relaxed-threshold experiment below.

**Cheshire O06 versus O16 remains uncertain.** Their coordinates are only 1.81 metres apart and their modern interiors could be units in the same building. The advertisements say third floor versus first floor, while O06 discloses digitally enhanced marketing images. Different homes is plausible, but the retained material lacks a corroborated unit identifier. Keep them separate pending better evidence; neither nearly identical coordinates nor differences in staging should decide this automatically.

## What the numbers establish

| Measure | Result and interpretation |
| --- | --- |
| Accepted pairs inspected | 15/15 supported; zero apparent false merges in this batch |
| Known positive pairs recovered | 15/19 = **78.9%**, conditional on this agent's development labels |
| Cross-portal positive pairs recovered | 15/17 = **88.2%** |
| Same-portal positive pairs recovered | 0/2; current policy excludes them |
| Supported duplicate reductions recovered | 15/18 = **83.3%**; a triple needs two reductions, not three |
| Canonical cards | 35 saved; 32 after only the supported additional consolidations |

The three-member Wilmot group contains three positive pair relationships but requires two merges to become one home. That distinction explains the different denominators. Counts of scored pairs, accepted links, duplicate reductions and resulting homes must remain separate in observability.

These are descriptive cohort results, not estimated population precision/recall or a release gate. In particular, “15/15 supported” does not establish 100% production precision. The unreviewed and uncertain relationships prevent a defensible whole-cohort accuracy or specificity calculation. Do not hide this behind the large number of easy, assumed negatives.

## Offline threshold experiment

The experiment used the production pure scoring and greedy grouping functions on the original source galleries. Only the process-local pHash threshold changed; it was restored afterward. Source blocking, score threshold, source-overlap guard and grouping order stayed constant. The baseline exactly reproduced the saved live memberships. No network, model or database mutation was involved.

| pHash threshold | Qualifying same / different pairs | Final supported / wrong merges | Homes | Result |
| --- | --- | --- | ---: | --- |
| 8, current | 15 / 0 | 15 / 0 | 35 | Baseline |
| 10 | 15 / 0 | 15 / 0 | 35 | No recovery |
| 12 | 15 / 0 | 15 / 0 | 35 | No recovery |
| 16 | 15 / 0 | 15 / 0 | 35 | No recovery |
| 20 | 15 / 0 | 15 / 0 | 35 | Same-portal distance-20 pair is still excluded |
| 22 | 15 / 4 | 15 / 1 | 34 | Wrongly joins Cheshire R06/O06; recovers no known missed positive |
| 24 | 17 / 12 | 16 / 1 | 33 | Joins R13/O09, misses O08, and still wrongly joins Cheshire |

All qualifying pairs in this table have explicit labels. Several wrong qualifying links do not become final merges because a stronger true pair has already occupied a source slot. That incidental protection depends on which advertisements are present; inspecting only the final count would hide the growing risk. A count closer to 32 can still be a worse result.

The [ImageHash documentation](https://github.com/JohannesBuchner/imagehash) describes recognition of nearly identical images and the luminance structure used by pHash. It separately provides crop-resistant hashing. My inference from that documentation and this experiment is that pHash is useful evidence of a reused photograph, while a non-match is inadequate evidence of a different dwelling. Changing camera position is not simply a looser near-copy threshold problem.

The [Splink evaluation API](https://moj-analytical-services.github.io/splink/api_docs/evaluation.html) explicitly supports counting positives missed by blocking rules, and its [cluster evaluation guide](https://moj-analytical-services.github.io/splink/topic_guides/evaluation/clusters/overview.html) separates good links from complete, uncontaminated entity groups. Those principles support including same-portal misses in the denominator and inspecting the full Wilmot group. They do not imply that Home Finder needs a new linkage framework. Primary documentation checked on 29 September 2026.

## Additional pipeline defect found during inspection

**O15 `onthemarket:19983662`, Turin Street, has a bathroom photo incorrectly promoted to a floorplan.** The fourth source gallery image clearly shows a bath, basin and window. The source detail observation declares no floorplan. Nevertheless, the live event `floorplan_detected_by_heuristic` records image index 3 at confidence **0.665**, above the heuristic's **0.65** threshold; enrichment then reports three gallery images and a floorplan.

An offline call to the same detector reproduced `(True, 0.665)`. The promoted floorplan file is byte-for-byte identical to the bathroom gallery image; SHA-256 is `c76272dea9a498267c103035fe8f6c512885a05665ace07c4be716c234a122a7`.

This explains the difference between **36 reported enriched floorplans** and **35 actual source-declared plans** in the review. The original gallery file remains cached, so the dedup hash loader still compared four source images for this listing; this was not the cause of the identified dedup misses. It can, however, mislead floorplan availability/filtering and later model input selection. The batch stopped before this home's model assessment, so no downstream model error is claimed here. Preserve source asset roles separately from derived classifications and test white bathrooms as a real negative case. Raising this classifier's threshold on one example is not a validated repair.

## Sequential follow-up within the delivery plan

1. **PR07b identity and membership foundation:** support several source listing IDs on one portal without overwriting URLs, observations, text, prices or asset provenance. Add stable canonical identity and reversible merge/split decisions with aliases and preserved user/history state. Exercise Southborough's two Rightmove IDs and Wilmot's one Rightmove plus two OnTheMarket IDs. Keep automatic matching policy unchanged in this foundation slice.
2. **PR07b review decisions:** distinguish accepted identity, rejected identity and insufficient evidence. The two Wilmot image vetoes should produce an inspectable candidate with original photos and reasons; the different Wilmot units and uncertain Cheshire pair must stay separate. Record policy/input revisions, source-listing members, evidence references, image coverage, reason, reviewer and subsequent reversal. Repeated review/replay must not generate extra identities, model work or notifications. Do not silently equate zero shared hashes with conflicting room evidence.
3. **PR11 / PR14 / PR21 asset evidence:** preserve the source-declared gallery role and record heuristic classification separately. Carry explicit role/selection provenance into the model input manifest. Add the Turin bathroom case alongside real floorplans, blank/light photos and ambiguous images. A derived guess must not silently satisfy a reliable-floorplan requirement or conceal an original gallery photo. Resolve this through an abstaining, validated policy rather than a single tuned cutoff.
4. **PR19 reference qualification:** have an independent reviewer assess the development labels, especially the Wilmot triple and uncertain Cheshire pair. Add fresh same-building negatives, changed-angle positives, relistings and incomplete galleries. Split by complete identity group; all three Wilmot listings must stay together. Retain natural-capture and authored challenge cohorts separately. These already inspected examples must not later be reported as an untouched holdout.
5. **PR30 remains optional:** after identity safety and measured pilot incidents, compare bounded review suggestions from copy-detection or multimodal evidence against the rules baseline. Measure false merges, missed groups, abstentions, review burden and additional spend. Route only unresolved candidates to a more capable model under the existing budget/provenance contracts; this audit is not evidence for paying for an extra model pass on every home.

For a service serving tens to hundreds of users, the immediate gain is trustworthy home identity, fewer repeat cards and preserved personal decisions. Wrong merges can attach somebody's saved state or assessment to a different flat; missed duplicates waste attention and potentially later model/notification work. The observed three extra cards are worth fixing, but preserving correctness is more valuable than maximizing the merge count.

## Reproduction and handoff

Use a Python environment with the repository's application and development dependencies. Both Python 3.11.16 and 3.14.7 were exercised for the evaluator. Output directories must be new.

```sh
python scripts/build_dedup_review.py \
  --input data/smoke-runs/2026-09-29-fifty-01 \
  --output data/dedup-review-new

python scripts/analyze_dedup_review.py \
  --input data/smoke-runs/2026-09-29-fifty-01 \
  --labels docs/reviews/2026-09-29-deduplication-labels.json \
  --output data/dedup-analysis-new
```

The deck can be opened locally, or served exclusively on loopback:

```sh
python -m http.server 8837 --bind 127.0.0.1 --directory data/dedup-review-new
```

Open `http://127.0.0.1:8837/review.html`. The Compare field accepts IDs such as `R13,O08,O09`, `R19,R20` or `R06,O06,O16`. Image links open the retained original bytes. The deck uses a restrictive content policy, escaped listing text and local image links; it makes no portal requests. Keep the original cache tree available because images are symlinked, not redistributed.

Retained private evidence in this workspace:

- `data/dedup-review-2026-09-29/review.html`, `manifest.json`, and `catalogue.md`.
- `data/dedup-review-2026-09-29/analysis-final-314/report.json` and `analysis-final-311/report.json`: all pair profiles, labels, candidate/final groups at each threshold, input hashes and checks.
- `data/dedup-review-2026-09-29/floorplan-role-finding.json`: exact bathroom-role reproduction.
- `output/playwright/dedup-review/`: local browser evidence for all ten pages and close comparisons; ignored by Git.

Validation: all 50 source IDs accounted for; all 51 broad-screen pairs adjudicated; all counterfactual qualifying pairs adjudicated; baseline matches live memberships; original batch DB, event log, report and referenced images unchanged; process threshold restored. Both runtime reports passed their checks. Ruff lint and formatting passed for the two new scripts. The application matcher was not modified, so the earlier full 3,164-test runs are not presented as new tests for this review. No additional portal/model requests, provider charges, notifications, deployment or production writes were made.

The label file pins the original database and event-log checksums. The generated evaluator report additionally pins the label version/hash and all original referenced artifact hashes. Future agents should read this review and the label rationales before changing matching behavior; the main [delivery plan](../plans/2026-09-28-research-synthesis-and-delivery-plan.md) remains the sequencing authority.
