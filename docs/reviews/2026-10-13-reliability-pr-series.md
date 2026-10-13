# Reliability PR series

13 October 2026. Review and merge guide for the completed reliability work arising
from the [architecture assessment](2026-10-13-architecture-assessment-and-tasks.md).
The [goal execution record](../plans/2026-10-13-goal-driven-implementation.md) explains
the decisions, and the [completion audit](2026-10-13-core-reliability-completion-audit.md)
maps the requirements to code and evidence. This record distinguishes the published
series from the larger original working directory.

## Review order

The series starts at `main` revision
`7605ced6fd9b2a1cc42b0d3d3caaf13509593aac`, which matched remote `main` when packaging
began. At publication every PR was a draft: PR 1 targeted `main`, and each later
PR targeted its predecessor so GitHub showed only that behavior change. The table
records the original implementation commits; the merge-review corrections below
are additional signed commits, not replacements for that history.

| Order | PR | Requirements | Review scope | Original commit |
| --- | --- | --- | --- | --- |
| 1 | [#65: Runtime and startup](https://github.com/MarcelCutts/home-finder/pull/65) | T02/T03 | Strict paid-output assembly, runtime overrides, provider rejection classification, CLI intent | `03f6942` |
| 2 | [#66: Search health](https://github.com/MarcelCutts/home-finder/pull/66) | T04 | Explicit page outcomes, bounded failed pagination, shared area preflight | `f54eeec` |
| 3 | [#67: Processing recovery](https://github.com/MarcelCutts/home-finder/pull/67) | T04/T05 | Fair enrichment admission, independent merge groups, atomic cache recovery, lifecycle migrations 13–14 | `a94327f` |
| 4 | [#68: Spend recovery](https://github.com/MarcelCutts/home-finder/pull/68) | T06 | Task-owned runner fencing, retained financial liability, frozen v1 and coupled v2 reservations | `122cb6f` |
| 5 | [#69: Scores and alert selection](https://github.com/MarcelCutts/home-finder/pull/69) | T07 | Fit v3 normalization, durable discovery/price decisions, migration 15, historical selection review | `71d69d6` |
| 6 | [#70: Delivery visibility](https://github.com/MarcelCutts/home-finder/pull/70) | T08 | Owner-only read view, safe progress projection, ordinary Details URL buttons, synthetic screenshots | `840f6a1` |
| 7 | [#71: Dashboard navigation](https://github.com/MarcelCutts/home-finder/pull/71) | T09 | Exact tags, complete query state, history/reset behavior, morphing, browser evidence | `ed579de` |
| 8 | [#72: Architecture assessment and completion audit](https://github.com/MarcelCutts/home-finder/pull/72) | T01–T17 decisions; G1 execution | Judgments, justifications, remaining tasks and this review/validation record | `9609c24` |

Review and merge in this order. PR #65 merged as `8845217`. Each subsequent signed
integration commit uses the preceding integration commit as its first parent and
the original or corrected PR head as its second parent. The PR branch updates
are fast-forwards, preserving every original commit and the review fixes. Inspect
each incremental tree and run the required checks on the exact candidate.

Before advancing `main`, retarget that PR to `main`, mark it ready, confirm that
`main` still equals the expected predecessor and that its required checks pass.
Fast-forward protected `main` to the reviewed merge commit; confirm GitHub records
the PR as merged. This satisfies the strict up-to-date requirement without making
a different merge commit after validation. If protection rejects that update,
use GitHub's merge operation and update/revalidate each following branch against
the resulting `main`. Do not force-push, bypass protection or change its rules.
The series does not enable auto-merge or dispatch a release.

Enrichment admission and merge/recovery changes share persistence transitions and
schema fields, so PR 3 keeps that boundary together. PR 5 similarly keeps score
eligibility and the persisted selection decision together: an alert must use the
same assessed evidence the dashboard presents. Operating notes accompany the
relevant implementation PR; the final PR provides the broader architectural record.

## Scope preserved during packaging

An isolated checkout was used; the original `codex/goal-reliability-repairs` working
directory was not switched, reset, staged or committed. The source snapshots and
an inventory of workspace hashes were retained before splitting. At the original
packaging checkpoint, all 97 selected source, test, README and configuration files
matched the completed implementation byte for byte after excluding pre-existing
edits. The subsequent review fixes intentionally change that packaged tree; they
were also made in isolated checkouts, preserving the original working directory.

The series intentionally leaves these earlier, separate changes in the original
workspace:

- The offline evaluation prototype: its Python modules, review UI, fixtures,
  scripts and usage document, together with its existing README paragraph.
- The final-drain metric rename in `main.py`, its operations explanation and its
  additional observability test. The existing observability fixtures receive only
  the policy adjustment needed by the alert-selection change in this series.
- Other local research, experiment output, logs, private database snapshots and
  cached property evidence. The input refactor report is identified as local
  context in the assessment rather than published incidentally.

Six committed screenshots use synthetic local fixtures. They are available in
`docs/reviews/assets/reliability/` and linked from the relevant PRs. Historical
selection evidence is aggregated or deidentified; the underlying database copies
and detailed temporary review projections are not committed.

## Original validation of the isolated series

Every implementation branch passed Ruff lint, Ruff formatting, strict mypy and
`git diff --check` before publication. Focused tests ran against that intermediate
branch, rather than assuming that a passing final tree proved each split sound.
The counts below overlap; they must not be summed as unique tests.

| PR | Focused verification |
| --- | --- |
| 1 | CLI dispatch, runtime defaults, configuration, strict output, provider errors, quality, native transport and app-factory suites passed. |
| 2 | 684 scraper, configuration, location and observation-accounting tests; 72.37 seconds. |
| 3 | 287 processing, merge isolation, migration, cache, delivery recovery and cross-run integration tests; 27.33 seconds. |
| 4 | 328 runner, spend, restart/cancellation, cache-control, frozen-rate-card and delivery-recovery tests; 28.78 seconds. |
| 5 | 730 notification, score, lifecycle, price-history, transaction, migration, dashboard and pipeline integration tests; 63.91 seconds. |
| 6 | 422 web, owner-access and Telegram URL-button tests; 28.16 seconds. |
| 7 | 417 offline web tests; 29.56 seconds. All 63 Chromium browser tests; 27.85 seconds. |

The browser suite reports two upstream WebSocket deprecation warnings. One focused
cross-run fixture attempted DNS and was blocked by pytest-socket; this establishes
no live-provider result. Default tests continue to exclude slow/browser markers
and block network sockets. SQLite tests ran outside the environment's filesystem
sandbox because its worker threads stall there; pytest's socket restrictions
remained active.

**Original isolated default suite: 4,006 passed in 492.46 seconds, with 90.75%
branch-enabled total coverage**, above the 75% gate. The five warnings were blocked
socket attempts in existing integration fixtures; no external service result is
inferred from them. There were no waived failures. This run covered the original
`ed579de` implementation tree, before the review fixes below. PR #72 remains
documentation-only; the corrected implementation has separate validation.

Reproduction from the appropriate branch with the locked development environment:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest -n 4 --cov --cov-report=term
uv run pytest -n 0 -m browser tests/test_web/test_browser_e2e.py
uv build --offline
```

The isolated checkout reused the existing Python 3.14.7 environment with its own
`src` directory explicitly on `PYTHONPATH`; no dependency or lockfile change was
needed. At that checkpoint, strict mypy checked 106 source files and formatting
checked 265 Python files. The wheel and source archive build succeeded offline.
The wheel includes all eight new runtime modules/templates; the source archive
contains no local database or filled `.env`. Neither distribution includes the
separate evaluation prototype.

The original G1 workspace result was 4,100 default tests at 90.94% coverage, plus
63 browser tests. It includes the pre-existing evaluation work and observability
test. It remains valid evidence for that workspace, but is not presented as the
test count of these PRs. The completion audit also retains the historical-copy
upgrade/restart checks: schema 6/12 to 15, preserved original rows except derived
score fields, stable repeat initialization and unchanged original file hashes.

Local packaging logs and assembly metadata are under
`/tmp/home-finder-pr-packaging/`; the committed record carries the durable outcomes.
GitHub checks remain authoritative for each published SHA. Browser and live-source
jobs are opt-in in the existing CI; the browser result above was established locally.

## Merge-review corrections and final validation

Independent review reproduced two regressions that the original passing suites
did not cover. Both fixes retain the existing architecture and persistence rules:

- PR #67: [`e046f3a`](https://github.com/MarcelCutts/home-finder/commit/e046f3a767a282809f64e8b26222a01782acea81)
  preserves enriched observations when a queued discovery becomes either an
  analysis candidate or a floorplan-dropped home. Measured floor area and its
  provenance remain paired; accepted source URLs, price bounds, location,
  availability and commute survive promotion and restart. Regression scenarios
  exercise actual detail enrichment, dashboard size filtering, multi-source
  recovery and sparse observations while retaining owner, attempt and spend history.
- PR #69: [`7b82f57`](https://github.com/MarcelCutts/home-finder/commit/7b82f5746dcd012fb1fee49bd9686e4983e89efd)
  retains Telegram's recipient delay when an archived, rejected or off-market
  home suppresses a waiting alert. Tests preserve its plan and receipts, reopen
  storage, verify another recipient can proceed and check the original exact
  expiry before permitting the next alert to the delayed recipient.

The corrected source candidate is
[`d56a79d`](https://github.com/MarcelCutts/home-finder/commit/d56a79db826a0e54991635c7a73dcc363b7436a0).
Ruff lint and formatting pass (266 Python files); strict mypy passes for 106 source
files. `uv build --offline` produces the wheel and source distribution; an independent
artifact check finds all 138 runtime files byte-identical to the reviewed source,
with no runtime data or credentials included. Independent Chromium verification
passes all 63 browser tests in 27.57 seconds, with the two upstream WebSocket
deprecation warnings.

**Corrected default suite: 4,018 passed in 346.09 seconds, with 90.79% branch-enabled
total coverage**, above the 75% gate. The seven warnings are blocked socket attempts
in existing integration fixtures: six `socket.socket` attempts in commute cases
and one `getaddrinfo` attempt in a cross-run case. No live-provider result is
inferred. The run retained the default slow/browser exclusions and pytest-socket
restrictions, using the existing environment with `PYTHONPATH=src` and
`python -m pytest -n 6 --cov --cov-report=term`; aggregate JSON coverage is retained
at `/tmp/home-finder-pr-fixes/coverage.json`. The original area-loss reproductions
now retain `57.3` square metres with `rightmove` provenance, and the dropped-home
case retains both accepted source URLs and enriched location. Independent review
also verified the exact incremental trees and commit signatures.

Required GitHub checks remain a separate gate for every exact integration head
before its protected `main` update; a local result does not replace that gate.

Review also reproduced a pre-existing, nonblocking dashboard edge case:
`No built-in wardrobes` is accepted in a bookmark but absent from the filter
checkboxes, so Apply drops it. The same behavior occurs before PR #71. Its tested
custom bedroom and score restoration remains valid; completing that tag control
is a separate follow-up.

## Remaining release and product work

Merging this series is separate from deploying or enabling collection. The existing
release workflow requires explicit dispatch from `main`; feature-branch pushes and
draft PR creation do not deploy. G2 remains the next operating goal: protected
deployment, explicit active/dormant mode, backup plus media restoration and useful
operating signals. Do not uncordon the old exposed release merely to regain
availability.

Inspect retained delivery intents before enabling collection: the new threshold
does not reclassify existing queues. The minimum fit of 60 remains provisional.
Uncertain Telegram parts have no automatic retry allowance, and unknown paid
requests retain their liability until reconciled. Live source access, paid-provider
behavior and private/group Telegram transport remain separate qualifications.

G3 evidence/relevance work and G4 invited-user ownership remain queued in the
execution record, with detailed possible tasks and justifications in the assessment.
The reliability series does not introduce a new scheduler service, queue, ORM,
frontend framework or distributed persistence model.
