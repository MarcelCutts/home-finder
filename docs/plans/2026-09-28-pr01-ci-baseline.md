# PR01: CI baseline and explicit release activation

**Date:** 28 September 2026. **State:** reviewed and accepted for local commit; not pushed, merged or deployed.
**Branch:** `codex/pr01-ci-baseline`, based on `a86e51e`.
**Parent:** [delivery plan, PR01](2026-09-28-research-synthesis-and-delivery-plan.md#pr01--restore-a-reproducible-ci-baseline-and-control-release-activation-s).
**Review boundary:** segment 1 was completed and the goal paused. The owner subsequently instructed: “commit each step and continue through the tasks, continuing to use goals.” Proceed with sequential verified commits; the earlier pause requirement is superseded.

## Goal and review protocol

The owner requested online research into Codex goals, then implementation with a pause after every segment. [OpenAI's current guidance](https://learn.chatgpt.com/docs/long-running-work) recommends an outcome, explicit constraints and verifiable completion criteria, keeping related work in one conversation. A goal retains the existing permissions; it does not grant additional access. Separate checkouts are recommended when independent chats write in parallel.

Applied here:

1. One persistent goal follows the delivery plan's execution queue for the confirmed small hosted service. Its first segment is PR01, not the entire backlog in one uninterrupted run.
2. Each segment must implement its bounded behaviour, run relevant checks, record remaining rollout conditions and be committed before continuing. The initial PR01 review pause was fulfilled; subsequent per-segment pauses are no longer requested. A passing local test suite does not establish a successful deployment or elapsed canary.
3. The goal retains the historical pause wording; the owner's later instruction and updated delivery plan/`CLAUDE.md` supersede it. Native goal resumption is controlled by the user. Keep corrections and decisions in this conversation without resetting progress merely to change that wording.
4. No token budget was specified, so none was invented. No parallel agents or background continuation into the next segment are needed.
5. PR01's boundaries exclude deployment, collection resumption, paid application APIs and notifications. All existing unrelated workspace changes remain separate.

The installed CLI reports `codex-cli 0.158.0`; the session's native goal tools created the goal and recorded the requested PR01 pause. That checkpoint did not complete the hosted-service objective.

## Implemented behaviour

### CI and releases

- Replaced missing `setup-uv@v8` and moving action tags with full commit pins verified against each upstream repository. Set uv to 0.9.29, matching the existing Docker build tool.
- Retained separate Ruff/format, strict mypy and default pytest/coverage jobs. Coverage remains branch-inclusive with the existing 75% floor.
- Added a cache-free locked install and source/wheel build job. Coverage XML and distributions are seven-day artifacts. Replaced PR-comment automation with the coverage artifact and terminal report, removing the test job's `pull-requests: write` permission without removing a coverage check.
- Kept weekly dashboard browser tests, explicitly excluding `slow` tests. Manual browser/live-test inputs default to false. Live portal tests require explicit opt-in; the live job installs Chromium because some existing portal-sort tests have both markers.
- Removed deployment from CI. The separate `Release dashboard` workflow accepts only a manual dispatch, validates only `main`, reuses CI at the same revision, and deploys only after validation succeeds. It checks out the triggering SHA, not a later branch head. Release checks have a distinct concurrency group from ordinary CI and are not cancelled by pushes.
- Validation receives no inherited secrets. The deploy job uses the `production` environment and a new `FLY_PRODUCTION_API_TOKEN` secret, mapped to Fly's expected variable only on the deploy step. It cannot fall back to the old repository secret. Environment/credential setup is still a rollout prerequisite below.
- Replaced `setup-flyctl@master` with the official flyctl 0.4.108 Linux x86-64 archive, pinned by SHA-256. The upstream action's latest tagged release was still on Node 20; directly verifying the CLI archive avoids depending on that runtime or a moving branch. Download, checksum, extraction and `flyctl version` were checked locally without deploying.
- Changed Dependabot from the `pip` to `uv` ecosystem so it maintains the actual lockfile.

Verified upstream pins:

| Component | Release | Commit / SHA-256 |
|---|---|---|
| `actions/checkout` | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `astral-sh/setup-uv` | v10.2.0 | `c18668ad3cf93ea998bef934396af7bb5c839dc7` |
| `actions/upload-artifact` | v7.0.1 | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` |
| flyctl Linux x86-64 archive | 0.4.108 | `38173df9dadd6a1f413ce435b6745b29ce2893c0ba918af17d7a1eced8089844` |

The pins were resolved through GitHub's release and Git-ref APIs, including annotated-tag dereferencing. Action definitions at those commits were read. `setup-uv`'s `v8` ref returned 404. This establishes upstream artifact resolution, not a completed GitHub-hosted workflow run.

### Dependency and package boundaries

The Anthropic requirement is now `>=0.81.0,<1`; the locked SDK remains 0.81.0. PR20 owns the deliberate SDK/transport migration. All 127 locked package records, versions, sources and distribution hashes are unchanged. Only the root package's requirement metadata changed in `uv.lock`.

A normal `uv lock` attempt upgraded Crawlee from 1.3.2b4 to 1.10.3b2, upgraded impit and added proclimits. Those changes were discarded because they belong to PR11, and the narrow root requirement metadata was updated explicitly. `uv lock --check --offline`, a fresh cache-free install and `uv pip check` then passed. The old Crawlee release's index absence does not prevent installing its already-locked wheel; the fresh install actually downloaded and installed it. Future re-resolution still requires deliberate Crawlee qualification, not blindly accepting the resolver's latest prerelease.

The SDK maintainer's published advisories were checked. The two memory-tool advisories returned by the repository API affected `>=0.86.0,<0.87.0`, not the locked 0.81.0, and their 0.87.0 fix remains within the temporary bound. This was a scoped compatibility check, not the complete dependency/container audit required by PR03. The bound is not a reason to defer a necessary security fix.

Building from this dirty workspace revealed that Hatch's default source archive included an unrelated existing root log. The source-distribution configuration now explicitly selects `src`, `tests`, README, licence and dependency files; Hatch also includes its required metadata and the existing `.gitignore`. The rebuilt archive excludes unrelated root logs, research, credentials and database exports. All 92 tracked application files, including templates, static assets and reference data, were compared byte-for-byte with the wheel and preserved.

### Operating documentation

Updated README, `CLAUDE.md` and source notes to reflect:

- The gradual hosted target and the present single-owner implementation.
- Enrichment before geocoding/commute, source-listing persistence, price-change detection, enrichment retries, and later reanalysis/price-drop work with current early-return limitations.
- Phase two's native JSON-schema `output_config`, cached base64 images for every source, and missing description/feature propagation as an outstanding repair.
- Writable dashboard controls and pending access protection; `--no-pipeline` is not read-only mode, and `--dry-run` is not a free/offline collection mode.
- The dated, incomplete cost cohort: 15 metered February runs, 200 counted analyses and $32.1247557 estimated model cost. It is not a universal per-home price or a full service invoice.
- The serve loop's completion-relative delay and jitter, the old cron's alternating 55/5-minute gaps, and the absence of exclusion across separate processes.
- Historical parser/transport assumptions as unqualified current contracts, plus the unsafe database-replacement semantics of the legacy sync script.
- The advertised Python 3.11+ versus CI/Fly 3.14 mismatch, to be resolved in PR03.

No application source or test code changed. The pre-existing `.env.example` edit and other research/log/experiment files were preserved.

## Verification and limits

Local environment: Linux, CPython **3.14.7**, SQLite **3.53.4**, downloaded checksum-verified uv **0.9.29**, Ruff **0.14.14**, mypy **1.19.1**, pytest **9.0.2**, actionlint **1.7.12**. The clean environment and diagnostic outputs are under `/tmp/home-finder-pr01-20260928/`; those temporary files are not committed or published.

| Check | Result |
|---|---|
| Fresh `uv sync --locked --all-extras --no-cache` into a new environment | Passed; 125 packages installed, including Crawlee 1.3.2b4 and Anthropic 0.81.0 |
| `uv lock --check --offline` | Passed; 127 lock entries retained |
| `uv pip check` against the fresh environment | Passed; all 125 installed packages compatible |
| Ruff check / format check | Passed; 163 files already formatted |
| `mypy src` | Passed; 63 source files |
| Default pytest with `HYPOTHESIS_PROFILE=ci`, coverage and four workers | **2,378 passed**, 152 warnings, **85.18%** branch-inclusive coverage, 190.99 seconds |
| `uv build --no-cache` | Passed; source archive and wheel built, then rebuilt and inspected after the archive-content fix |
| Wheel/source contents | All 92 tracked application files preserved in wheel; source archive contains 212 expected files, without unrelated root logs/research/credentials/DB exports |
| actionlint on both workflows | Passed |
| Fly CLI installer artifact and version | Checksum/extraction passed; reports flyctl 0.4.108, no deployment |
| `git diff --check` | Passed |

Test reproduction, using a fresh `/tmp` environment rather than altering the existing `.venv`:

```bash
UV_PROJECT_ENVIRONMENT=/tmp/home-finder-pr01-20260928/venv \
  /tmp/home-finder-pr01-20260928/tools/uv sync \
  --locked --all-extras --no-cache --python .venv/bin/python

HYPOTHESIS_PROFILE=ci PYTHONUNBUFFERED=1 \
UV_PROJECT_ENVIRONMENT=/tmp/home-finder-pr01-20260928/venv \
UV_CACHE_DIR=/tmp/home-finder-pr01-20260928/check-cache \
  /tmp/home-finder-pr01-20260928/tools/uv run --locked --offline pytest \
  --cov --cov-report=xml:/tmp/home-finder-pr01-20260928/coverage.xml \
  --cov-report=term -n 4
```

The tests retained the repository's default socket blocking, marker exclusions and 75% coverage floor. They ran outside the filesystem sandbox because aiosqlite worker-thread execution had stalled inside it during the earlier baseline review. Four workers bounded local resource use; CI retains `-n auto`. No new implementation-mirroring tests were introduced for these configuration/documentation changes.

Workflow routing was inspected alongside actionlint:

| Event | Default checks/build | Dashboard browsers | Live portal tests | Deployment |
|---|---|---|---|---|
| Pull request / push to main | Yes | No | No | No |
| Weekly schedule | Yes | Yes, excluding `slow` | No | No |
| Manual CI, defaults | Yes | No | No | No |
| Manual CI, selected options | Yes | If requested | If requested | No |
| Manual Release dashboard, main | Yes, reused at the same revision | No | No | Only after validation succeeds and production credentials are configured |
| Manual Release dashboard, another ref | Skipped | No | No | Skipped |

The GitHub-hosted execution, Docker image build, browser suite and live portal suite were **not** run. CI still selects the Python 3.14 series rather than an exact interpreter build; local 3.14.7 results do not prove every patch/platform. Build-backend resolution and container bases also remain outside a fully hermetic build claim. Runtime alignment and broader security upgrades are PR03. Existing warnings include the already-known template API deprecations; this segment does not fix them.

## Rollout, rollback and next segment

Remote read-only inspection established that CI is `disabled_inactivity`, that `FLY_API_TOKEN` exists as a repository secret, and that the required `production` environment is absent. No remote setting or credential was changed or read by value.

Before activating these workflows:

1. Review PR01's local changes. Keep its files separate from the owner's existing `.env.example` and research changes when committing. The delivery plan and this handoff are new documents and must accompany their new relative links when published.
2. Satisfy O1's private-access/backup requirement before publishing the exposure material or releasing an application change. This segment has not protected the existing live dashboard.
3. Configure the `production` GitHub environment to allow only `main`, provision the app-scoped `FLY_PRODUCTION_API_TOKEN` there, and retire the old repository-wide credential. Branch guards in YAML alone do not protect a repository secret from a PR that edits the workflow. Do not claim this external protection is complete before inspecting it.
4. Merge the reviewed release split, re-enable CI and obtain a hosted green run before depending on the repaired gates. Check branch-protection requirements if the new build gate should be mandatory. Do not enable live-test inputs merely to verify CI.
5. A later explicitly selected manual release deploys the dashboard with `--no-pipeline`. Record its exact image/revision and retain a previously verified release for rollback. No known-good Fly image was selected or redeployed in this segment.

No schema or data migration is involved; the database, collector configuration and notification state are unchanged. For a local rollback, revert only PR01's workflow/manifest/docs edits. Do not undo unrelated workspace changes. After publication, keep release activation manual even if another part of this segment must be reverted; reintroducing the old push-triggered deployment would restore the original hazard.

**Next eligible implementation segment:** PR02, owner-only dashboard/mutation/Telegram callback protection, with O1 operational containment still outstanding. PR03 follows; PR04's transaction repair remains next in the queue after initial access/runtime protection. O2 source feasibility and O3 operating-mode decisions are also outstanding. No canary, paid benchmark, production restart or user onboarding has occurred.

## Primary references checked

- [OpenAI: long-running work and goals](https://learn.chatgpt.com/docs/long-running-work) — objective, constraints, verification, conversation continuity and permissions.
- [GitHub: secure use of actions](https://docs.github.com/en/actions/reference/security/secure-use) — full commit pins and least required permissions.
- [GitHub: reuse workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows) — same-commit local workflow references and explicit secret passing.
- [GitHub: supported Dependabot ecosystems](https://docs.github.com/en/code-security/reference/supply-chain-security/supported-ecosystems-and-repositories) — `uv` ecosystem and SHA-comment update behaviour.
- [Astral: uv in GitHub Actions](https://docs.astral.sh/uv/guides/integration/github/) — explicit tool version, Python selection and locked sync.
- [Hatch: build file selection](https://hatch.pypa.io/latest/config/build/#file-selection) — explicit source-archive contents.
- Upstream releases: [checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1), [setup-uv v10.2.0](https://github.com/astral-sh/setup-uv/releases/tag/v10.2.0), [upload-artifact v7.0.1](https://github.com/actions/upload-artifact/releases/tag/v7.0.1), [flyctl v0.4.108](https://github.com/superfly/flyctl/releases/tag/v0.4.108).
- [Anthropic SDK maintainer advisories](https://github.com/anthropics/anthropic-sdk-python/security/advisories) — scoped check for the temporary SDK compatibility bound.
