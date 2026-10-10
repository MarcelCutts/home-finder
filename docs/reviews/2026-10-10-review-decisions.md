# Open PR review decisions and execution

Date: 10 October 2026. Baseline: live PR #35, `8bac7b681dc3bc9d86da1964221e6243136f381c`.
This reconciles Astra Ultra's `opus-ultra-findings.md` (the filename misidentifies
the reviewer) and `2026-10-10-open-pr-review.md`. Original working reviews remain
untouched. GitHub PR numbers, rather than stack positions, are used below.

## Decision

Retain the practical repairs. Do not merge the whole stack or expand the evidence
journal and reversible grouping programme to make it mergeable. A small hosted
service remains compatible with this decision; it does not require every proposed
subsystem now. Preserve existing work on its branches while preparing smaller
changes. Do not restore confident cost estimates from missing evidence.

Both reviews are useful, but neither is a current merge approval. Astra gives
strong, narrow reproductions and distinguishes an activation blocker from a live
failure. The second review finds more operational failure paths, but some headline
severities outrun its own caveats. In particular, conservative unknown availability,
missing spend authorization, and unknown-charge holds are not inherently defects.
Their poor diagnostics, retry consequences, and failure to process unrelated work
are the actionable problems. Agent count and passing tests are not evidence of
product value or independent human validation.

Initial live inventory: 34 open PRs, including 32 implementation drafts and two Dependabot
updates. Main was `a86e51e`; CI was `disabled_inactivity`; main had no required
checks. All implementation PRs lacked remote check results. The later #34 and
#35 already add the default-off regrouping gate and detail-response identity
binding, so Astra F1/F2 must be evaluated against those changes.

## Task list

- [x] Reconcile both reviews against live heads and inspect primary documentation.
- [x] Pause the old instruction to continue producing implementation PRs.
- [x] Prepare, validate and merge a narrow CI/release bootstrap from main (#36).
- [x] Extract and independently review owner access plus transaction ownership as a coherent main-based candidate.
- [x] Merge that candidate only after its full hosted checks pass (#38).
- [x] Fix health-route classification and prevent anonymous requests locking out valid credentials.
- [x] Preserve optional/paid bill inclusion and uncertain council-tax claims as unknown.
- [x] Accept ordinary dual-unit area rounding while retaining real conflicts.
- [x] Reject unrecovered rollback journals in snapshot export using shared validation.
- [x] Stop provider/account failures consuming property failure allowances.
- [x] Keep uncharged rejections out of paid lifetime caps; let in-flight calls settle.
- [x] Bound batch concurrency by configured spend capacity and explain disabled admission.
- [x] Repair interrupted collection and duplicate-enrichment recovery without a new work scheduler.
- [x] Normalize JSON-equivalent integer representations without accepting malformed assessments.
- [x] Run lint, formatting, strict types, and full offline coverage on the integrated fixes.
- [x] Preserve the remaining verified repairs on `codex/review-remediation` without merging its deferred ancestors.
- [x] Record exact results and merge only candidates supported by current checks.

## Deliberate holds

- #17–#23 and #27–#34: optional evaluation, evidence, and identity infrastructure.
  Keep regrouping disabled. Do not build projection consumers solely to activate
  an unneeded API. Retain exact-provider and response-identity correctness fixes
  when extracting useful runtime work.
- #24/#26: separate small parser/diagnostic repairs from smoke/batch tooling and
  research records. Prior bounded-run authorizations are historical; this review
  includes no new paid run.
- Observation retention and large-table backup scaling: real growth risks, not a
  reason to ship a new retention subsystem now. Do not activate the ledger train
  until its need and bounded storage contract are established.
- Provider/model quality qualification, live portal qualification and deployment:
  offline tests do not establish any of these. No paid model call is part of this review.
- Production access: only repository code has been repaired. These merges do not
  deploy or configure owner credentials, and no claim about protection of the
  running service is made. Deployment and live verification remain outstanding;
  existing production tokens have not been changed by this review. Consult the
  original private review for its operational findings before activation.
- Do not publish the private security review or raw listing research records as
  part of the remediation. Do not rewrite published history to remove them during
  this code review.

## Primary sources used

- [SQLite URI handling](https://www.sqlite.org/uri.html) and
  [rollback journal recovery](https://www.sqlite.org/lockingv3.html): immutable
  reads skip locking/change detection; a frozen file can still require recovery.
- [Anthropic error semantics](https://platform.claude.com/docs/en/api/errors):
  authentication, permissions, billing, rate limits and overload are provider or
  account outcomes, not evidence that a rental property is unsuitable.
- [JSON Schema numeric types](https://json-schema.org/understanding-json-schema/reference/numeric): `4.0` and `4` satisfy the same integer contract.
- [Anthropic billing](https://support.claude.com/en/articles/8977456-how-do-i-pay-for-my-claude-api-usage): failed requests and client timeouts have different charge implications.
- [ASGI request scope](https://asgi.readthedocs.io/en/latest/specs/www.html#http-connection-scope): `path` is the parsed request target, separate from headers.
- [Starlette release notes](https://www.starlette.io/release-notes/#101-may-21-2026): current dependencies already address the historical Host-header URL construction issue; route classification must still use the request target.
- [NIST unit conversion guidance](https://www.nist.gov/pml/special-publication-811/nist-guide-si-appendix-b-conversion-factors): converted measurements should reflect the precision of the reported value.
- [GitHub workflow activation](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows):
  disabled workflows need deliberate recovery; enablement alone does not repair
  the old workflow's schedule or deployment behaviour.

## Finding adjudication

| Review claim | Current judgment and action |
| --- | --- |
| Astra F1, returned detail belongs to another advert | Valid at reviewed head; #35 already validates requested/final/page/payload identity. Its regressions are included in the integrated suite. Do not duplicate this subsystem. |
| Astra F2, regrouping leaves stale assessments current | Valid for explicit reviewed regrouping; #34 defaults that API off. #33 does not finish consumer freshness. Hold activation rather than build more projections. |
| Astra F3, paid bills treated as rent-inclusive | Reproduced and fixed, with rendered dashboard regressions. Uncertain tax bands remain unknown. |
| Astra F4, hot-journal export | Reproduced with a crashed SQLite writer and fixed using the existing shared frozen-snapshot validator. |
| Astra F5, #7 search text stops refreshing | True at #7; actual receipt evidence fixes the normal path later. Do not land #7 alone without extracting that minimal handoff. Undated historical text must stay undated. |
| Astra F6, no independently visible validation | Confirmed; narrow replacement workflow opened as #36. The first hosted run caught an installer/Python mismatch that local cached validation missed; corrected on the next head. |
| Second review high 1, missing spend policy | Safe refusal was already intentional. Added actionable warnings and a safe example; do not invent spending authorization or prevent dashboard-only operation. |
| High 2–4, provider failures, retry exhaustion and cancellations | Fixed retry semantics and drain in-flight requests. Known zero-cost rejections do not consume paid-operation caps; unknown billing still blocks conservatively. |
| High 5, strict non-strict tool output | Accept mathematically integral JSON numbers at integer schema positions; preserve substantive required fields, strict types, and redacted diagnostics. Do not infer live pass rates from two responses. |
| High 6, ledger growth | Mechanism is real; the projected daily size is a synthetic estimate, not measured production growth. Hold the ledger train; no new retention system or blind removal of backup limits. |
| High 7 and historical Host bypass | Fixed credential lockout and use ASGI route paths. Current Starlette already repairs the historical malformed-Host construction; standalone security candidate also works on old Starlette. |
| High 8, Rightmove availability | Real encoded-parser gap, but stripped fixture does not establish whole-page status. Manual command fails conservatively. Hold activation/claims of live coverage pending target-bound status fixtures; do not turn HTTP 200 into available. |
| High 9 and pending donor retry | Fixed through existing guarded staging absorption, with two-retry and retry-into-new-group regressions. Protected history still blocks automatic absorption. |
| Interrupted collection stops at known page | Removed routine early-stop based only on receipt IDs. Existing page/rate caps remain; additional fetches are the explicit tradeoff to avoid skipping unseen pages. |
| Cross-portal price history | Retain an existing lower quote when a new source only provides a higher one, preventing a fabricated price drop on the next run. |
| Dual-unit area withheld | Fixed using overlap of rounding intervals implied by reported precision, not a loose percentage. |
| Model EPC/tax/bills facts discarded | Expanded supported advert wording and retain earlier bills claims visibly as unverified; unsupported model guesses still cannot establish costs. |
| False new state in advanced groups | Hold grouped-home routing with the rest of identity work; do not create a new personal-state projection policy in this pass. |
| Map disappears on tile failure | Usability limitation, lower priority than correctness. Defer provider/fallback design changes. |
| Over-reservation blocks admission | Correct financial circuit breaker after a broken bound. Reconciliation needs billing evidence; no automatic reset or erased audit history. |

The second review's suggestion to reopen product scope is unnecessary for these
repairs: a personal tool and a small hosted service both benefit from them. Its
suggestion to land the first fourteen positions as ordinary PRs is a direction,
not proof that every extraction is a small rebase. Several depend on fixes much
higher in the stack. Each extracted candidate still needs its own checks.

## PR disposition

| GitHub PRs | Disposition |
| --- | --- |
| #1, #2 | Closed as superseded by the pinned actions merged in #36. |
| #3 | Closed after operating repairs merged in #36; large delivery narrative retained on the old branch, not required for CI. |
| #4, #6 | Closed as superseded by merged #38, which combines owner access and complete transaction ownership with the access fixes and documented migration. Separate implementation commits preserve review boundaries; original branches retained. |
| #5, #9, #16 | Useful runtime/commute/backup work; retain for ordinary, independently checked extraction. Do not include maps or unrelated tooling merely because they are ancestors. |
| #7, #8, #10–#15, #25 | Useful repair train with verified corrections preserved on the remediation branch. Not approved at original heads; requires dependency-aware extraction and fresh hosted checks. |
| #17–#23 | Defer optional evaluation/ledger/replay infrastructure. Preserve small text and area correctness repairs for extraction where needed. |
| #24, #26 | Extract parser/diagnostic fixes if adopting their prerequisites; defer harnesses and research records. No new paid run authorized. |
| #27, #28, #30–#34 | Defer the broad identity/grouping train. Preserve exact provider identities and history safeguards; leave regrouping disabled. |
| #35 | Existing response-identity repair addresses Astra F1; retain for adapter extraction rather than merging all its ancestors. |

## Verification and execution results

- #36 merged as `b14875292abe8d0b44b16540e1bab80e2ac11ea7` after all four hosted checks passed on head `9de64d0`. [Hosted run](https://github.com/MarcelCutts/home-finder/actions/runs/38050709104): 2,378 tests passed, 85.18% coverage on Python 3.14.7. Local Python 3.13.11: 2,378 tests, 85.31%. Lint, formatting, strict mypy and clean package builds passed.
- #1/#2/#3 closed without deleting their branches. No deployment or scheduled live scraping was enabled.
- [Owner/transaction PR #38](https://github.com/MarcelCutts/home-finder/pull/38) merged as `4d557c689cea3f2db076280e8b92716128747f96` after all four [hosted checks](https://github.com/MarcelCutts/home-finder/actions/runs/38051382244) passed on head `0093b53`: **2,470 tests passed, 85.49% coverage**, with 167 existing TemplateResponse deprecation warnings on Python 3.14.7. Before submission: 320 focused owner-access/config/webhook/status/migration tests, 398 database/pipeline/access tests, and an independent 64-test access/concurrency pass; original Starlette 0.52.1. Rebase onto the CI merge changed no application/test files. Hosted test timeout increased to fifteen minutes after the measured nine-minute baseline; the expanded test job finished in eight minutes twelve seconds. #4/#6 closed without deleting their branches.
- [Remediation branch](https://github.com/MarcelCutts/home-finder/tree/codex/review-remediation) implementation commits: `14e696b` (access), `35c320b` (cost/area), `fd4c435` (data recovery), `634932d` (model/spend), `318fc0b` (explicit conflict fixture and connection cleanup). These are a correction workbench on #35, not approval to merge its ancestors. Access repairs were also extracted into merged #38; the other implementation fixes remain unmerged until their prerequisites are independently selected and checked.
- Integrated fixes on Python 3.14.7: **3,550 tests passed, 88.66% coverage**, with Ruff, formatting and strict mypy passing. The first final run exposed one ambiguous conflict fixture: bare “electricity included” no longer means “in rent”. It now explicitly claims inclusion in rent while preserving every conflict/link assertion. The 276-test affected slice and the complete rerun both passed.
- Declared minimum Python verified independently on **3.11.16: 3,550 tests passed, 88.73% coverage** after a clean locked dependency install into an isolated environment. Both full runs tested application commit `318fc0b` and retained pytest's network blocking.
- Dependabot opened five updates after the CI merge: #37 (upload-artifact), #39 (grouped minor/patch dependencies), #40 (structlog), #41 (Anthropic), and #42 (mypy). These are outside the original 34-PR snapshot and remain open for their own review, including major-version compatibility. The final inventory is therefore again 34 open PRs: 29 deferred original drafts and five new dependency updates. No automatic merge was enabled.
- Sandboxed aiosqlite could not wake its event loop even for `SELECT 1` on an in-memory database; an identical diagnostic succeeded outside the sandbox. Offline suites ran with approved escalation and pytest's network blocking retained.
- Automatic approval review rejected enforcing required checks/admin protection on main as a persistent repository-wide policy outside the explicit PR authorization. The exact proposed policy is ready and user approval was requested; branch protection remains unchanged pending that answer. Each actual merge is manually gated on its checked head.


