# PR06b-6: Preserve source area quantities without inventing exact totals

**Date:** 29 September 2026. **Predecessor:** raw references `9d41a13`.
**Commit:** `7361f43`.
**Scope:** structured area evidence, decimal parsing, and the existing scalar supplied to analysis. No historical data repair or new source acquisition is implied.

## Defects and resulting behavior

The Rightmove detail parser previously selected `maximumSize` from a range such as 650–700 sq ft and presented its converted value as a total. With only `minimumSize`, it also presented the minimum as a total. Zoopla converted a fractional `sizeSqft` to an integer before conversion. OnTheMarket's `minimumAreaSqFt` likewise became a total. These losses would make a durable evidence ledger preserve an interpretation stronger than the source field.

`SourceAreaReading` now retains the original numeric quantity as a decimal, source unit, normalized unit, source field and separate lower/upper bounds. Its kind is point, range, minimum or maximum; its measurement basis is explicitly unspecified. A minimum cannot establish gross internal, net internal or usable area. Bounded malformed or unsupported optional quantities produce an explicit `unsupported_quantity` omission while preserving valid description/features and other detail evidence.

Rightmove retains up to ten `sizings` entries in their original order, supporting the existing `sqft` and native `sqm` shapes. Zoopla retains the supported RSC `taxonomy.sizeSqft` value, including fractional digits. OnTheMarket retains the supported minimum-square-feet field as a minimum. This does not add unverified maximum/alternative field names, infer areas from descriptions or images, or claim a complete inventory of portal measurement formats. If one Rightmove entry is unsupported, the optional set is omitted as a whole rather than presenting a partial set as complete.

JSON decimal parsing occurs before quantity interpretation. The indexed Rightmove decoder accepts decimal scalar leaves but still requires integer references; a `3.0` node reference remains invalid. Native source quantities accept exact decimal strings/integers/decimals and reject binary floats, booleans, nonfinite/negative/zero values, more than eight decimal places and values exceeding 100,000 source units. The original JSON lexical notation is available only when raw bytes were retained; normalized decimals preserve numeric value and scale, not every original character of the numeric token.

Conversion uses **1 sq ft = 0.09290304 m²**, derived from the exact international foot of 0.3048 m. The human-reference `PrintedArea` contract shares that constant without changing its label schema or values. [NIST international-foot definition](https://www.nist.gov/pml/us-surveyfoot), [NIST area conversion tables](https://www.nist.gov/pml/us-surveyfoot/revised-unit-conversion-factors).

## Evidence, display and model inputs

Detail observations retain the structured readings before image work through the existing atomic ledger sink. An observation's exact bounds survive JSON serialization, database retrieval, deterministic projection and offline replay. Replay also serializes the detail result using decimal strings and preserves its verified raw reference when available. Parser fingerprints include the new quantity model and mapper.

The legacy scalar `floor_area_sqm` is produced only when all retained readings are point quantities and agree after rounding to 0.1 m², using decimal half-up rounding. Any range, lone minimum/maximum or disagreement with another unit representation withholds the scalar. The prior 100–5,000 sq ft plausibility gate still applies to this display/prompt value; otherwise valid source quantities outside that range remain in the ledger. This is a display-selection rule, not verification that the property's measured area is correct.

The prompt no longer labels a bare stored area as a source-reported total. It calls it a rounded recorded figure and states that provenance and measurement basis are unspecified in that input. This matters for old rows labelled `estimated` too: passing their scalar to an existing reanalysis must not turn a previous model estimate into verified listing evidence. New range/minimum readings are retained but are not yet rendered as rich area facts in the dashboard or separately supplied to the model. That presentation and dependency work belongs with PR08b/14.

Historical scalar columns, saved assessments and fit scores are not rewritten or automatically rebilled. An immutable read-only query of the verified PR05a backup found **143 non-null scalar areas among 1,016 properties: 39 Rightmove, 21 OnTheMarket and 83 estimated**. Their original units/bounds are unavailable in the snapshot; these counts do not prove which individual values are wrong. The source DB checksum remained `e1494adf0f5b174cd9a2e142f50aa25b8ffd5a85ba3f4d66383a3e8762cea639`. The query program/report are `/tmp/home-finder-pr06b/area_historical_audit.py` and `/tmp/home-finder-pr06b/area-historical-audit.json`. PR08b/14 must treat retained legacy scalar provenance separately from fresh quantity evidence and invalidate/recompute dependents deliberately.

## Verification and limits

New tests cover exact conversion, fractional values and trailing zero scale; lower/upper/range distinction; native metric units; compatible and incompatible dual-unit readings; malformed optional fields; bounds; immutable ledger round trips and field projection; decimal indexed leaves versus invalid decimal references; enrichment persistence before the first media call; scalar withholding at the prompt boundary; and offline raw-linked replay. Existing floor-area tests now assert the corrected range/minimum behavior rather than accepting the old upper-endpoint selection. The blind human-reference model tests remain unchanged and pass.

The focused set passes **178 tests** on Python 3.14 and **78** area tests on 3.11. The final quantity-specific suite passes **29 tests** after the prompt provenance correction. The broader parser/quality run passed 837 tests with one newly written enrichment fixture failing because it contained no images; the fixture was corrected to exercise successful enrichment with a mocked media boundary. Early decimal expectations had two arithmetic transcription errors, corrected against the exact conversion. The first full matrices were deliberately interrupted before changing the prompt, and are not reported as completed verification.

The final full suites pass **3,066 tests on each of Python 3.11 and 3.14**, with branch-inclusive coverage **88.58% / 88.50%**. Ruff checks/format verification pass across 226 files; strict mypy passes across 98 source files. Final logs and coverage XML are `/tmp/home-finder-pr06b/area-stable-{full,coverage}{311,314}.*`; earlier interrupted matrices remain separate. Source/test files stayed unchanged during the final full runs.

The numeric parser scenarios are controlled fixtures, including the new positive indexed sizing example. The retained real Rightmove detail response has an empty `sizings` list; it does not qualify a fresh positive-size indexed format, and no new live request was made for this slice. Full source/target-page qualification remains PR10/11/18. Decimal preservation cannot establish that a claimed area belongs to the correct flat, measurement standard or relisting.

## Compatibility and next contracts

No database migration or dependency change is required. The observation field is optional and excluded when absent, preserving old payload hashes. The omission vocabulary gains a quantity-specific reason; invalid field/reason combinations are rejected. Compatible readers must accompany rollback after new evidence is written; do not strip quantity fields or rewrite immutable payloads to satisfy an older strict reader.

This completes another local evidence contract, not the operational authority cutover. Check remaining receipt scope/material-revision requirements before moving to PR07's identity and merge/split decisions. PR09b owns complete query/page coverage; PR08b owns non-text current projections; PR14 owns versioned artifact dependencies. Remote authenticated transport remains conditional on collector placement, while retention scheduling and source-use qualification remain activation gates. No paid call, notification, production write or source request occurred here.
