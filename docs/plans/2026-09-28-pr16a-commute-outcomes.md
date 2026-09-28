# PR16a: Honest commute outcomes and consistent advisory behaviour

**Date:** 28 September 2026. **State:** committed as `3e0c05d`; verified; not deployed.
**Parent:** [delivery plan, PR16](2026-09-28-research-synthesis-and-delivery-plan.md#pr16--correct-commute-semantics-and-version-personal-decision-policy-m).
**Predecessor:** PR17a, commit `b4cf2f9`. No schema migration, provider calls, collection or notifications.

## Problem and resulting behaviour

The controlled baseline produced `(0 minutes, within_limit=True)` after a mocked provider exception. A defensive 1,801-second response against a 1,800-second cap became a 30-minute pass through truncation. A stage with all routes unreachable returned no homes, whereas mixed successful/unsuccessful batches behaved as advisory. The latter also reported dropped homes that were actually retained.

The adapter now returns one typed outcome for each input and mode. Measured within-limit and over-limit journeys have exact integer seconds and an invariant tying the state to the limit. Missing coordinates, unresolved destination, provider failure, bounded-search unreachable and invalid/incomplete response are distinct unknown states with no duration. Errors cannot masquerade as a successful zero-minute measurement. A genuine measured zero is valid in the runtime model. Cancellation propagates rather than becoming an outcome.

Thresholds and best-mode selection compare seconds, with ceiling rounding only at the display/projection boundary: 1,799 and 1,800 seconds are within 30 minutes; 1,801 seconds is over the limit and displays as 31 minutes. The last is a defensive over-cap fixture, not a demonstrated normal provider response. Extra response IDs are ignored; missing, duplicate, conflicting or malformed outcomes stay unknown. Longitude zero is a valid coordinate.

The stage retains every home under an explicit advisory policy, including when all journeys are unknown or over the limit. Its within-limit counter counts actual measured matches, not admitted homes or a monotonic funnel. The existing event store records `commute_passed`, `commute_over_limit` or `commute_unknown`, mode states, advisory policy and retention. No credentials is a recorded not-configured reason. It no longer emits misleading commute-drop events. General early-return run-counter finalization remains PR09a.

The existing minute/mode projection is written only for measurements, including known over-limit journeys. Backfill follows the same best-measured selection; a provider outage leaves a missing value missing. There is no bulk historical rewrite, new migration or refresh of old estimates in this segment. Grid, detail, map popup and Telegram qualify positive saved values as estimates and present missing/legacy zero values as unknown. Fixed green/red threshold badges are removed because a stored minute alone cannot establish a current user's policy result.

## Primary-source check and limits

Checked 28 September 2026: the [TravelTime matrix reference](https://docs.traveltime.com/api/reference/travel-time-distance-matrix) specifies request maximum journey time and returned travel time in seconds, and an explicit unreachable-ID list. Its bounded search can omit reachable journeys outside the limit; unreachable does not supply a measured duration or establish that no journey is possible. Treating an absent response member as an invalid/incomplete response rather than an unreachable measurement is this application's conservative policy.

This early repair deliberately does **not** claim the complete PR16b contract. Stored minute/mode projections still lack versioned destination/profile, requested/resolved journey time, origin precision, source/result revision and expiry. The current request still uses the run's current UTC arrival time; reproducible future London weekday scenarios and DST handling remain PR16b. Existing postcode geocoding and cache topology remain. Full seconds are retained in the runtime outcome, not a new durable provider-payload archive. Operational outcome events are distinct from a complete reproducible route observation.

Actual TravelTime Order/account rights must be established before route-provider activation or expanded retention/cache reuse. This segment made no new entitlement assumption and used mocked requests only. Legacy positive values remain explicitly unverified; legacy zeros cannot distinguish old error sentinels from real measurements and therefore display as unknown. The existing manual backfill selects missing values only; active-candidate refresh, provenance and any zero repair belong with PR16b's permission/expiry policy.

## Verification

- Baseline reproductions: provider-error false pass, defensive second boundary, all-unreachable abort.
- First focused run: 304 tests passed across adapter, pipeline integration, backfill, DB persistence, web and Telegram.
- Added regressions cover 0/1,799/1,800/1,801 seconds, invalid negative/boolean/float/string durations, malformed/duplicate/conflicting/missing/foreign IDs, zero longitude, cancellation and mixed missing coordinates/provider outage.
- Real SQLite integration covers all-unreachable, all-error, no credentials, mixed measured/unknown, omitted adapter output, accurate persisted events/counters, retained homes and unchanged missing backfill values.
- Browser suite: 59 passed, including 375px and 1,280px commute estimate/readability checks; two existing WebSocket deprecation warnings. Local fixture tiles only.
- Full Python 3.11.16 suite: 2,486 passed; 86.35% branch-inclusive coverage; four existing deprecation warnings.
- Full Python 3.14.7 suite: 2,486 passed; 86.23% branch-inclusive coverage; four existing deprecation warnings.
- Ruff lint/format, strict mypy (70 source files), JavaScript syntax and diff checks passed.

Read-only inspection of the frozen 1,016-property snapshot found 929 positive saved minute values, 87 missing and **no zeros**. It therefore does not demonstrate historical persisted zero-minute corruption. No migration/initialization was run against it. SHA-256 remains `e50897b46ebefea0119fd62951ac277d764684f9590d52801e66c964b1352c63`. Logs and aggregate audit are under `/tmp/home-finder-pr16a/`.

Reviewed fixture screenshots: [mobile card](../reviews/2026-09-28-pr16a-commute-mobile.png) and [desktop card](../reviews/2026-09-28-pr16a-commute-desktop.png). The estimate qualification is visible at both sizes; fixture images are synthetic/unavailable rather than fetched from a portal.

## Recovery and next segment

There is no schema change. Retain this unknown-state/advisory handling in any protected compatible recovery build; restoring the old false-success/abort behaviour is not an acceptable presentation fallback. Existing schema-8 compatibility requirements from PR06a still apply.

**Next: PR08a**, repair current price ranges and target-listing availability parsing. Cross-source projection policy/history repair remains PR08b. Continue sequential verified commits; live acquisition, deployment, paid usage and public rollout retain their operational gates.
