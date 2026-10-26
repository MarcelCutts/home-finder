# Cleanup audit — 26 October 2026

A read-only audit of `main` at `51dd9c6`. It looks for code, tests, configuration and documents
that can be removed, merged or corrected. Every finding is categorised and graded by the strength
of its evidence. Nothing in the application was changed. A separate worktree trial applied the
32 best-evidenced deletions (net −1,881 lines) and ran the project's own gates against them.
Everything passed and nothing needed downgrading (see [trial results](trial.md)).

**Contents:** [summary](#summary) · [how findings were graded](#how-findings-were-categorised-and-graded) ·
[why the residue exists](#why-the-residue-exists) · [bugs found on the way](#bugs-found-on-the-way) ·
[cleanup plan](#cleanup-plan) · [owner decisions](#decisions-only-the-owner-can-make) ·
[looks dead, must stay](#looks-dead-must-stay) · [limits](#limits-of-this-audit) ·
[finding index](index.md) · [method and sources](method.md) · per-area findings in [findings/](findings/)

## Summary

Nine auditors covered the code areas, the test suite, the repository and cross-cutting patterns.
They produced **189 findings**, about 150 distinct issues once overlaps between areas are merged.

| Confidence | Findings | Meaning |
|---|---:|---|
| CERTAIN | 55 | Mechanically verified: no reference anywhere searched, or a document contradicted by quoted code |
| HIGH | 93 | No production caller, and superseded by a cited commit or prior decision; small residual doubt |
| MEDIUM | 30 | Probably removable, but there is a plausible invisible caller, stored data, or a product choice |
| LOW | 11 | Judgment calls, reported only where useful |

42 findings need an owner decision. By category: dead code 34, stale documentation 26,
duplication 21, test-only production code 20, superseded implementations 17, over-building 16,
compatibility shims 16, configuration drift 15, repository hygiene 9, agent residue 8, test hygiene 7.

The application is in better shape than the count suggests. Copy-paste between modules is rare
(the AST and pylint scans found four small exact-duplicate groups), the three `listing_text` and
three `spend` modules are legitimate layering, and the October reliability work (spend ledger,
delivery outbox, merge safety, checkpoints) is intact. The residue is concentrated in a few
predictable places, listed under [why the residue exists](#why-the-residue-exists).

**The ten most valuable changes**, in suggested order:

1. **Make the agent instructions true.** `CLAUDE.md` has about 20 claims the code contradicts.
   Examples: `--serve` alone does not start collection; the model is Sonnet 5.5 with 8,192 tokens,
   not Sonnet 4.6 with 21,000; Rightmove does not use Crawlee; `filters/quality.py` and `models.py`
   no longer exist; and the documented description-escaping pattern reintroduces a fixed bug.
   `CLAUDE.md` is also listed in `.gitignore`, so ripgrep-based search (including agents' Grep)
   silently skips it. Every agent session loads this file, so it has the highest cost of any stale
   text in the repository. (REPO-1–4, ANALYSIS-1, PIPELINE-3–5, SCRAPERS-17, FILTERS-10, WEB-8, TESTS-17)
2. **Cut the test suite's runtime by about four fifths.** An autouse fixture runs `gc.collect()`
   and scans the whole heap after each of the 4,251 tests. The cheap thread scan in the same
   fixture covers the stated problem on its own. Measured on the same 4-CPU machine with the same
   command, the full suite took 586 s on `main` and 124 s in the trial tree. With only this fixture
   changed, the DB and web tests went from 91–96 s to 21–22 s. One catch: the scan was hiding a
   leaked stdlib `sqlite3` connection, which now shows up as a fifth warning. Fix that before turning
   leak warnings into errors. (TESTS-1)
3. **Delete code with no references at all.** This covers enquiry and viewing-message accessors,
   `utils/union_find.py`, unused constants, dead CSS and JS for the replaced fit-breakdown
   visuals, an orphaned template partial, unread route context, dead model members and more. The
   trial applied this batch (−558 lines) and every gate passed. (Batch B)
4. **Drop Crawlee and declare what is actually used.** No source file imports Crawlee since 15ae399.
   Beautiful Soup and lxml arrive only as Crawlee's transitive dependencies. Its test fixtures and
   a lint exemption exist only for it. `respx` and `dirty-equals` are also unused. In the trial the
   lockfile went from 132 to 117 packages, with nothing added and no version changes. (SCRAPERS-1, TESTS-5, TESTS-16)
5. **Stop testing code that production cannot reach.** About 20 wrappers exist only because their
   tests call them. Examples: `analyze_merged_properties`, `deduplicate_and_merge_async`,
   `compute_fit_breakdown`, the scrapers' "compatibility readers" and `run_dry_run`. Retarget
   those tests to the production entry points, then delete the wrappers. (Batch E)
6. **Retire the pre-outbox notification writers and Telegram senders.** `PropertyStorage.mark_notified`
   and its siblings write delivery state directly, bypassing the outbox. The old senders
   (`send_batch_notifications` and others, about 330 lines) and an unreachable 280-line text-only
   alert format have no production caller. Both survive because the tests use them as seeding
   helpers, so add `tests/support/seed.py` first. (DB-1, DB-6, FILTERS-1, FILTERS-2, TESTS-6, TESTS-8)
7. **Remove the plumbing of switched-off features.** Early-stop pagination is hard-disabled
   (`known_ids_by_source = None`), yet it still threads through `--full-scrape`, four scrapers and a
   storage query. The `enable_image_hash_matching` setting has no effect on normal runs. The
   council-tax and energy prompt arguments are always `None` across four layers. (Batch G)
8. **Retire maintenance scripts that mutate by default.** `scripts/audit_merges.py` deletes homes and
   assessments with raw SQL and foreign keys switched off. `audit_image_cache.py --reflag-partial`
   quietly queues paid reanalysis. Several other scripts are tied to February cache layouts. (PIPELINE-27, REPO-7–9)
9. **Make what lives outside `src/` importable again, and check it in CI.** The rename in 41f499e
   (25 October) broke both experiments, draft PR #77 and a review audit script. Nothing outside
   `src/` and `tests/` is import-checked. A small AST import smoke test would have caught all of
   these. (REPO-18–20, FILTERS-11)
10. **Archive the February planning prose.** Twenty-eight files (about 12,200 lines) of February
   plans, tickets and A/B/C reviews have no inbound links and describe deleted modules. Move them
   to `docs/archive/2026-02/` behind a short `docs/README.md` index; delete nothing. (REPO-17)

## How findings were categorised and graded

The full rubric, the research behind it and the sources are in [method.md](method.md). In short:

**One category per finding.** DEAD (unreferenced) · TEST-ONLY (only tests call it) · SUPERSEDED (an
older path beside its replacement) · COMPAT (shims, aliases, tolerant legacy readers) · DUP ·
STALE-DOC · HYGIENE · CONFIG · OVERBUILT (guards for impossible states, needless indirection) ·
AI-ISM (narration of PR or review history, wording like "new" or "now", mutant-specific
commentary) · TEST-HYGIENE.

**Confidence comes from evidence, not impressions.** Each finding lists the searches and commands
behind it. The evidence comes in families:

| Family | What counts | Strength |
|---|---|---|
| Static references | `git grep` across src, tests, scripts, experiments, docs, templates, JS and CSS, plus draft PR #77's branch; string and `getattr` uses count as references | Any unexplained hit blocks deletion |
| Static semantics | vulture at 100%, `mypy --warn-unreachable`, ruff F-rules, no route or registration path | Strong |
| Tool leads | vulture at 60% or 90%, deptry, ruff ERA/ARG/BLE | Lead only; vulture calls these percentages "very rough estimates" |
| Dynamic | Full suite with per-test coverage contexts (4,251 tests, 91.1% line coverage); a [query](method.md#reproducing-the-evidence) gives the tests that execute any line | Shows *which* tests keep code alive |
| History | `git log -S` for the commit that removed the last caller; prior review decisions A1–A20 and B1–B9 | Supports, never sufficient alone |
| Owner | Operator habits, local untracked work, real stored rows | Only the owner can say something is *unwanted* |

**Grades:** CERTAIN means mechanically verified. HIGH means no production caller plus a cited
supersession, or prior decision. MEDIUM means there is a plausible invisible caller, stored data
or a product choice. LOW means a smell.

The research changed three rules:

- **Tests are not evidence of use.** Google's Sensenmann treats a library and its tests as one unit
  that lives or dies together. A TEST-ONLY finding therefore names the code *and* the tests that
  exist only for it. Shared tests are retargeted, not deleted.
- **Unused is not the same as unnecessary.** Recovery, migration and error paths can go unexecuted
  and still be needed. They default to *keep and document* or to an owner question.
- **Compatibility readers of stored data are removed in stages.** First query the real database,
  then log loudly when the legacy branch fires, then delete. Code search cannot prove the absence
  of an old row.

**Verification.** CERTAIN items and test-only items whose tests are exclusive to them were applied
in a separate worktree. The [trial](trial.md) then ran ruff, strict mypy, the full suite and a
coverage regression check: no function covered on `main` may lose all its tests. Findings that
failed a gate would have been downgraded; none did. 4,213 tests passed (the 38 removed were exactly
the deleted code's exclusive tests), coverage rose from 91.13% to 91.41%, all eight operator entry
points still answered `--help`, and vulture's list shrank from 343 to 315 lines. The browser suite
and the Docker build were not run; the container has neither a browser nor Docker.

**Cross-checking between auditors** caught at least one wrong claim. CROSS-3 says the off-market
checker fetches Rightmove with httpx and no User-Agent. FILTERS-8 is right instead: `rightmove`
is in `_CURL_SOURCES` (`filters/off_market.py:59`), so the httpx branch can never run. The stale
docstring at line 86 misled the first auditor.

## Why the residue exists

These six patterns explain most of the findings. Each one also suggests a guard, so the residue
does not come back.

1. **Refactors move code but not its consumers outside CI's view.** 41f499e moved
   `filters/quality.py` to `analysis/analyzer.py` and the prompts to `analysis/prompts.py`, moved
   detail enrichment into `enrichment/`, and removed the `CommuteFilter` class. It updated `src/`
   and `tests/` but not `CLAUDE.md`, the experiments, the review scripts or the open draft PR.
   *Guard:* an import smoke test for scripts, experiments and docs scripts, plus a rule that a
   rename updates `CLAUDE.md` in the same PR.
2. **A replacement lands beside the old path, and the old path's tests keep it alive.** The outbox
   replaced `mark_notified`; strict evaluation parsing replaced `_extract_eval_json`; graduated
   scores replaced `coordinates_match`. Each time, the tests kept calling the old function, so it
   never looked dead. *Guard:* when a PR supersedes a function, it moves that function's tests to
   the new entry point in the same PR.
3. **Features are switched off without being removed.** Early-stop was disabled with a hard-coded
   `None`, image-hash dedup with a hard-coded `True`, and council-tax defaults with constant `None`
   returns. The parameters, CLI flags, docs and tests remain and still describe the old behaviour.
4. **Agent instructions drift fastest.** `CLAUDE.md` copies content from README, `docs/pipeline.md`
   and `tests/README.md` and has fallen behind all three. README is the most accurate top-level
   document. *Guard:* keep `CLAUDE.md` to agent guidance (scope, the owner's preferences, gotchas,
   the ADR guide) with links to the authoritative documents, under the 200 lines Claude Code
   recommends.
5. **The same policy is written several times and drifts.** Anti-bot HTTP fetching exists in six
   places, which differ on what is retried, how a 403 is handled, the User-Agent and the proxy.
   Floor-area plausibility exists in three, and the display path does not apply the gate that
   persistence does. The score working recomputes the scorer's mean, and one rounding drift has
   already been fixed (959984f).
6. **The test suite accretes defensive machinery.** Examples are the heap scan per test, live tests
   that cannot reach the network (`--disable-socket` applies to `-m slow` as well) and so skip or
   pass vacuously, Crawlee reset fixtures for a library no longer used, 497 redundant
   `@pytest.mark.asyncio` markers under `asyncio_mode=auto`, and an identical `storage` fixture
   copied into 18 files.

## Bugs found on the way

These change behaviour and are not cleanup. They should be fixed on their own, not mixed into
deletion PRs.

| Finding | Problem | Confidence |
|---|---|---|
| WEB-7 | A listing whose only details are EPC, service charge, bills or council tax shows an empty "Listing Details" heading. Those rows moved to the cost section in 3570e26, but the guard still counts them | HIGH |
| WEB-8 | `CLAUDE.md` documents `\| e \| replace("\n", "<br>") \| safe`, which renders a literal `&lt;br&gt;`. An agent following it would reintroduce the bug 5d36b40 fixed | CERTAIN (verified with Jinja) |
| PIPELINE-9 | `--serve --debug` loses debug logging, because `create_app` reconfigures logging at INFO | CERTAIN |
| CROSS-7 | A Claude floor-area estimate rejected by the 9–465 m² persistence gate is still shown by the dashboard and detail-page fallbacks | HIGH |
| DB notes | If `save_quality_analysis` fails to serialise, it logs and writes nothing, but `complete_analysis` still queues a discovery alert for a home with no assessment | MEDIUM (needs a test) |
| SCRAPERS-13 | Zoopla's private challenge detector differs from the shared `is_challenge`. A challenge page served with status 200 is neither retried nor counted as a block, despite the comment and `docs/sources/zoopla.md` | MEDIUM |
| SCRAPERS notes | The dedup hero-image hash fallback uses plain httpx against CDNs that need curl_cffi, so it probably fails quietly for three of the four sources | MEDIUM |
| PIPELINE-24 | `Settings.data_dir` is `"."` for in-memory databases, so tests create `image_cache/` in the working directory. One appeared in the repository root during this audit's test run | MEDIUM (directory observed) |
| PIPELINE-13 | Source-row retention and price-change detection run up to three times per listing. They are idempotent today, so the cost is wasted queries and a log line that can never fire | HIGH |
| REPO-7 | `scripts/audit_merges.py` deletes by default, with foreign keys off, leaving dangling `merged_id`s. Its "will re-appear on the next scrape" promise is false | HIGH |
| TESTS-3 | The live Claude evaluation test builds the analyzer with no spend policy, so it cannot pass. Fixing it naively would spend money | HIGH |

## Cleanup plan

The batches follow the research's ordering: documents and hygiene first, then certain dead code,
then test-only code, then superseded and compatibility code in stages, and duplicate merges last.
Each batch is one category in one subsystem where practical. None mixes in behaviour changes.
Each must pass ruff, strict mypy and the full suite, and must not leave any retained function
without a test. Finding IDs link to the per-area files.

| Batch | Contents | Grade | Evidence state | Approx. size | Owner? |
|---|---|---|---|---|---|
| **A. True instructions and docs** | Rewrite `CLAUDE.md` as agent guidance plus links, and un-ignore it and `.python-version` (REPO-1–4 and others listed above); fix the `--serve` claim in `operations.md` (REPO-5); fix `.env.example` reservation figures and the default search areas (PIPELINE-6–8); fix README, CONTRIBUTING and `docs/ci.md` (REPO-6, WEB-9, PIPELINE-10); fix `docs/sources` (SCRAPERS-18); add ADR 002 superseding ADR 001 (REPO-16) | CERTAIN | Verified against code | ~200 doc lines changed | no |
| **B. Certain dead code** | DB-2, DB-10, DB-12, DB-14, DB-17, SCRAPERS-4 (`_extract_listings_from_parsed`), SCRAPERS-6, SCRAPERS-7, FILTERS-8, ANALYSIS-5, 7, 10, 13 and 14, WEB-1–5 (certain parts), PIPELINE-19 | CERTAIN/HIGH | Trial: all gates passed (browser suite not run) | −558 lines | no |
| **C. Dependencies** | Remove Crawlee and add `beautifulsoup4` and `lxml`; remove the Crawlee fixtures and E402 ignore; remove `respx` and `dirty-equals` (SCRAPERS-1, REPO-12, TESTS-5, TESTS-16). Coordinate with Dependabot PR #86 | CERTAIN | Trial: all gates passed; 15 packages removed, none added, no version changes | −342 lines, mostly lockfile | no |
| **D. Test-suite speed and hygiene** | TESTS-1 (heap scan), TESTS-10/11 and SCRAPERS-15 (unused fixtures and files), TESTS-14 (markers, add `--strict-markers`), TESTS-21 (redundant asyncio markers), TESTS-12/13 (shared fixtures into `tests/support`), TESTS-18, TESTS-15, PIPELINE-24 (cache directory in the working directory) | CERTAIN/HIGH | Trial: TESTS-1, TESTS-10 and TESTS-11 passed (−135 lines); the rest static | ~1,100 test lines | no |
| **E. Retire test-only APIs** | Retarget tests, then delete: SCRAPERS-3 (floorplan heuristic), FILTERS-5, ANALYSIS-3, SCRAPERS-11, PIPELINE-11/12 and DB-4 (trial verified); ANALYSIS-2, FILTERS-6/7/9, SCRAPERS-4/5/9/21, WEB-6, DB-3/8/9, CROSS-10 (need retargeting) | HIGH | Trial: the first seven passed (−846 lines); the rest need retargeting | ~600 src and ~550 test lines removed; ~150 test call sites retargeted | no |
| **F. Superseded writers and senders** | Add `tests/support/seed.py`, then delete the old storage writers (DB-1, DB-6, TESTS-6) and the old Telegram senders and unreachable text format (FILTERS-1/2/3, TESTS-8) | HIGH | Coverage-mapped | ~970 src and ~970 test lines removed; ~500 seeding call sites rewritten | **yes**: accept the outbox qualification route (A7) |
| **G. Switched-off feature plumbing** | Early-stop and `--full-scrape` (PIPELINE-1, SCRAPERS-2, CROSS-1, DB-5, TESTS-9); image-hash setting (PIPELINE-2, SCRAPERS-16); always-`None` prompt arguments (ANALYSIS-4, CROSS-8, ANALYSIS-11); triple retention (PIPELINE-13); ignored arguments (PIPELINE-18, CROSS-9) | HIGH | Prompt bytes unchanged (pinned snapshot) | ~200 src and ~350 test lines | **yes**: drop the CLI flag; choose an image-hash policy |
| **H. Scripts, experiments, live tests** | Retire mutating scripts (PIPELINE-27, REPO-7–9, PIPELINE-20); fix or archive the experiments and add an import smoke test (REPO-18–20, FILTERS-11); delete or replace the live tests with an explicit smoke procedure (TESTS-2–4, REPO-15, A20); TESTS-20 | HIGH | Import failures reproduced | ~1,800 script and live-test lines removed; experiments (~7,700 lines) fixed or archived | **yes**: which scripts the owner still runs |
| **I. Repository hygiene** | Root leftovers: `output.txt`, `crontab`, `mutmut_config.py`, `dedupe.json` (REPO-10; root `main.py` is in Batch B); move `output/playwright` and the research note (REPO-11); archive the February documents (REPO-17); tidy config drift (REPO-14, 22, 23); delete the 40 merged remote branches (REPO-21) | HIGH | Reference-checked | ~12k lines moved | **yes**: branches |
| **J. Small structural fixes** | DB-15 (image SQL ×3), DB-20 and CROSS-4 (db imports FastAPI), CROSS-5/6, ANALYSIS-16, FILTERS-12 and SCRAPERS-8 (leftover lazy imports and re-exports), CROSS-7 (one floor-area helper), CROSS-12/13 and SCRAPERS-10 (media-type and small helpers), ANALYSIS-12/19, DB-7/11, WEB-10/12/13/17, PIPELINE-21, FILTERS-13, CROSS-14/17, ANALYSIS-17, SCRAPERS-22 | HIGH | Static | ~500 lines touched | no |

**Deferred until data or a decision exists.** The stored-row JSON repair and the `json-repair`
dependency (ANALYSIS-9), the remaining legacy readers (DB-19) and the startup legacy-delivery
classification (DB-18) each need a read-only count on the real database first; the query is in
ANALYSIS-9. HTTP transport consolidation (CROSS-3, SCRAPERS-13, 19, 20) needs an intended
per-source policy and live verification, which the delivery scope keeps manual. The others in this
group are the B4/B5/B6 command questions (PIPELINE-14–16), the legacy Sonnet 4.6 defaults
(ANALYSIS-6), `TokenUsage` beside the ledger (ANALYSIS-15), parser fallbacks without fixtures
(SCRAPERS-14), the venue data and social metadata (ANALYSIS-8, WEB-11), the alias vocabulary
(ANALYSIS-18, CROSS-16), and the rest of the MEDIUM and LOW items in the [index](index.md).

## Decisions only the owner can make

1. Is the delivery outbox qualified enough to delete the old Telegram senders (A7)? FILTERS-1 suggests
   rewriting `scripts/test_notification.py` to qualify the durable `deliver()` path instead.
2. Can `--full-scrape` go? It currently does nothing.
3. Should image-hash dedup be configurable? If so, wire the setting through consolidation; if not,
   delete it.
4. Which maintenance scripts are still run locally: `audit_merges`, `audit_image_cache --fix`,
   `--purge-disk` or `--reflag-partial`, `backfill_wards`, the EPC scripts?
5. Delete the live `slow` tests and CI job, or replace them with an explicit smoke procedure that
   enables sockets and fails rather than skips (A20)?
6. Run the read-only data checks in ANALYSIS-9 and DB-19 against the real database, so the tolerant
   readers and `json-repair` can be removed?
7. Should the bundled venue and creative-scene data be shown on the listing page or dropped
   (ANALYSIS-8)? Music events matter to the search, so this is a product call.
8. Delete the 40 merged branches? Replace the 31 closed stack branches with one tag at
   `codex/review-remediation`? Their commits are already kept in GitHub's `refs/pull/*/head`.
9. Does any local `.env` or Fly secret still set `MAX_ANALYSIS_PER_RUN` (PIPELINE-17)?
10. Is there untracked work on your machine (experiments data, evaluation scripts) that calls any
   method marked for deletion? Nothing tracked does.

## Looks dead, must stay

Tools flagged these; the auditors checked them and they are live. Each per-area file has a
complete "Verified keep" list.

- `import httpx2` and the `httpx2` dependency: the Anthropic SDK's transport, used by tests directly.
- `jinja2` and `python-multipart` (deptry): FastAPI's implicit runtime dependencies.
- Visual and evaluation schema fields vulture lists (`has_worn_fixtures`, `storage_rating` and so
  on): sent to the model, part of the checkpoint hashes, and read by templates.
- All Pydantic validators, `model_config`, FastAPI route functions and Jinja filters vulture lists.
- `scripts/sync-to-fly.sh`: a deliberate failing tombstone, documented and tested.
- `scripts/audit_duplicate_candidates.py`, `scripts/generate_icons.py`, and the seven
  `python -m home_finder.pipeline.*` operator commands. Their zero coverage comes from subprocess
  tests and untested CLIs; they are not dead.
- The migrations, including migration 012's frozen copy of the delivery SQL (done in 71d69d6).
- `area_delay`, `should_skip_remaining_areas` and the curl helpers: live, but always mocked in tests.
- The `archive/*` branches: a recorded preservation decision.

## Limits of this audit

- **No production evidence.** There are no run logs, operational coverage or tombstones, and no
  access to the real database. "Unused" here means unused by tracked code and tests.
- **Untracked local work is invisible.** The prior assessment warned that substantial research and
  evaluation work lives untracked on the owner's machine.
- **Shallow clone.** History starts at 23 February 2026, so some "superseded in" commits are the
  earliest visible ones.
- **Python 3.13, not 3.14.7.** The pinned interpreter was unavailable in the container. All 4,251
  tests passed on 3.13.
- **No network to portals, Anthropic, Telegram or Fly.** Anti-bot behaviour claims are from code
  reading only.
- **AI authorship is context, not evidence.** A preregistered trial found no downstream
  maintainability penalty for AI-assisted code. Each item stands on its own evidence.
