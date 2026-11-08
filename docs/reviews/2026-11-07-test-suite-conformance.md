# Test-suite PR conformance review

7 November 2026. Reviewed [PR #111](https://github.com/MarcelCutts/home-finder/pull/111)
at `24bdcb05ee878daaec300f367ae668850d6c84c3` against the requested combined audit,
implementation, performance experiments, report and automatic worker selection.
The corrections below are a follow-up to that head. This is a requirements and
evidence review, not a second independent audit of every assertion in the suite.

The implementation conforms to the principal requirements. It provides a suite
inventory, responsibility-based layout, measured coverage, performance evidence
and working CI observability. It does **not** establish complete action coverage
or exhaustive equivalence of the consolidated tests. Those remain explicit limits.

## Requirement checks

| Requirement | Evidence and assessment |
| --- | --- |
| Explain each suite's purpose and boundary | The [current inventory](2026-11-07-integrated-test-inventory.md) accounts for 4,281 collected cases in 193 modules: 4,160 offline, 77 local browser, 40 live and four live browser cases. `tests/README.md` explains responsibilities and distinguishes staged integration from a full run. |
| Assess code, user and pipeline coverage | The [coverage table](2026-11-07-integrated-test-coverage.md) and [action matrix](2026-11-07-test-suite-integration.md#user-and-pipeline-action-coverage) distinguish execution evidence from external-service correctness. Coverage numerators, denominators and percentages agree with the saved evidence. Python coverage includes 123 package files; scripts, templates and JavaScript are outside that denominator. |
| Structure complementary tests with clear intent | Suites split by responsibility; reusable builders and transports live in support modules. No test-module imports from other test modules were found. Sampled infrastructure, transport, browser and path-guard tests reach their claimed production decisions. The live enrichment assertion defects below were corrected. |
| Account for consolidation | All relocation destinations exist, and referenced test classes/functions resolve. The [ledger](2026-11-07-test-suite-consolidation.md) accounts for 1,369 absent baseline identifiers: 474 unchanged-body moves, 172 same-name moves/revisions, 719 contract-family consolidations and four retired helper checks. Contract-family mappings do not establish per-assertion or per-parameter equivalence. |
| Combine supported findings from both investigations | Verified schema-template activation/fallback/ownership, connection cleanup, socket markers, bounded IDs, subprocess coverage, retry policy and shared artifact isolation. Recorded source hashes match the reviewed files. Historical reports are marked as snapshots and the integration report supersedes their recommendations. |
| Add observability and experiment under variable load | Repeated interleaved worker/template measurements record wall time, CPU and load. All 14 saved profiles have successful exits. Hosted CI produced its timing summary, profile/JUnit and coverage artifacts. Occupancy, inclusive fixture time and process CPU are defined separately. |
| Let the machine select workers | The default remains uncapped `-n auto --dist=loadgroup`, with no custom worker-count hook. It selected 16 workers locally and two on hosted CI. This discovers physical cores at startup; it does not continuously adapt worker count to competing sessions. That matches [xdist's documented behavior](https://pytest-xdist.readthedocs.io/en/stable/distribution.html). |
| Keep terminology and claims accurate | Corrected the documentation issues below. The report distinguishes local fast, local coverage and hosted CI measurements, and statement/branch coverage from coverage.py's combined score. |

## Findings corrected

**Incorrect postcode preservation contract.** The pre-existing live enrichment
test required every postcode to remain unchanged. Production intentionally
backfills absent postcodes and outcodes from detail evidence. The assertion now
preserves price, bedrooms and an existing complete postcode while permitting
backfill. Existing offline tests separately prove both backfill and complete
postcode preservation; no application behavior changed.

**Incomplete partition assertions.** Set comparisons in the live probes could
pass if an enriched home appeared twice, including in both result groups. The
partition and concurrency checks now compare ID multiplicities, and the canonical
data check also verifies the result count. These assertions reject duplicate
results as well as missing homes.

**Misleading instructions and performance wording.** Plain pytest is described
as the default offline suite; `-n 0` disables xdist rather than application
threads. Python socket blocking is distinguished from native curl. Schema
templates are reused per worker, without claiming that concurrent first callers
cannot build spare candidates. Browser examples no longer imply that
`--timeout=120` controls a test marked `timeout(0)`; the documentation describes
Playwright and CI job timeouts. Marker precedence and zero disabling the timeout
are confirmed by the [pytest-timeout documentation](https://pypi.org/project/pytest-timeout/).
The startup/shutdown bottleneck claim is now scoped to the measured local
16-worker profile; hosted two-worker CI spends most of its time executing tests.

## Verification

- Hosted [run 42407477245](https://github.com/MarcelCutts/home-finder/actions/runs/42407477245)
  at the reviewed head passed lint, types, offline coverage and the cold build.
  All 4,160 offline cases passed in 72.51 seconds. Its timing summary reported two
  workers and 94% phase occupancy. Coverage, test-observability and distribution
  artifacts were present. Optional browser/live jobs were skipped as configured.
- After the assertion corrections, all 35 offline detail-enrichment tests passed.
  A temporary plugin ran the four live test bodies against deterministic scraper
  and detail-fetch boundaries, retaining real conversion and enrichment: 12 cases
  passed across missing, outcode and complete-postcode inputs. No portals or paid
  APIs were contacted by these checks.
- A deliberate duplicate-result mutation failed all nine partition/preservation
  checks; the three description-only checks still passed. This verifies the added
  assertions' sensitivity without claiming a live portal run.
- Repository Ruff checks and formatting checks passed; `git diff --check` passed.
  The follow-up changes only assertions in the optional live lane and documentation.
  Prior local evidence remains 4,160 offline cases and 77 Chromium cases passed;
  those full local runs were not repeated for these changes.

## Remaining assurance limits

The largest unresolved test-preservation question is the 719 contract-family
consolidations. The ledger establishes traceability, not full equivalence; higher
aggregate coverage cannot prove that every removed edge case survived.

Coverage still misses maintenance-script execution, several dashboard failure and
rendering paths, some CLI variants and real assessment quality. The integration
report prioritizes these follow-ups. Live access, provider accuracy and actual
Telegram delivery remain outside the offline suite's evidence. Hosted CI does not
run the optional browser/live lanes by default; the existing browser evidence is
local, and its 27 third-party deprecation warnings remain visible.

These limits prevent a claim of complete coverage. They do not invalidate the
verified suite organization, automatic workers, measured improvements or working
observability delivered by this PR.
