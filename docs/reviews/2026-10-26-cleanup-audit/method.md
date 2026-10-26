# Method: categorising cleanup candidates and grading confidence

This note records the research behind the [cleanup audit](README.md)'s rubric, the rubric changes it led to, and how to reproduce the evidence. Sections 1–6 are the research note as written during the audit. Its section 6 proposals were partly adopted: the README uses the evidence families and the three rules (tests are not evidence of use, unused is not unnecessary, staged removal of compatibility readers), but not the numeric point weights, which are uncalibrated.


## 1. What industrial deletion systems do

- **Google Sensenmann** [1]: liveness comes from the build graph plus **execution logs** for each binary. Something with no recorded run "for a long time" (the post's example is "over a year") gets a deletion CL. **Test runs are explicitly not liveness.** Each library and its test are joined into one strongly connected component so they live or die together. Sensenmann keeps a blocklist for examples and code that sends no log signal. Owners review every CL, and the CL description has to give the reviewer enough to judge.
- **Meta SCARF** [2–5]: it adds runtime usage (endpoint logs, script invocations, Django template hooks) to the static graph and deletes unreachable *subgraphs*, which catches cycles. Textual search is a **conservative blocker**: any by-name or string reference stops deletion, and Meta accepts the false negatives this causes. The rule is "If metrics can be found for an asset, it is in use". Data removal is staged: notify the owner, **block reads and writes as a dry run**, monitor, then delete. Changes land automatically only in languages where Meta trusts the analysis.
- **Uber Piranha** [6,7]: a flag counts as stale after it has gone unmodified for a team-set period (e.g. 8 weeks). The flag's author reviews each diff. Even so, only 65% landed unchanged and only "over 85%" compiled and passed tests. Expect some well-evidenced deletions to still need edits.
- **Research** [8]: Haas et al. separate *dead* (unreachable), *unused* (not executed within a time window) and *unnecessary* (no stakeholder wants it). They point out that error-handling, recovery and migration code can be unused and still necessary. Their history heuristics (old, rarely changed, peripheral code) were confirmed unnecessary only **34%** of the time, so history is a weak signal on its own. Dead code measurably hurts comprehension [9].

## 2. Python tooling: what the numbers mean

| Evidence | Strength | Caveat |
|---|---|---|
| vulture 100% (unreachable code, unused args) | strong | Applies only "within the analyzed files" [10] |
| vulture 90% (imports) / 60% (everything else) | lead only | The README calls these "very rough estimates" based on the kind of code, not on evidence. Matching ignores scope and uses names only, so a name collision hides dead code and dynamic use shows up as a false positive. Re-run after each deletion because removals cascade. |
| `mypy --warn-unreachable` (not covered by `strict`) | strong for types | Run now: 6 hits, saved in the audit scratch evidence. Untyped JSON or SQLite input can still reach a branch the types call impossible. |
| ruff ERA001 / ARG / BLE001 / S110 / TRY203 / PIE790 | leads | ERA001 is documented as "prone to false positives" [12,16]. ARG flags fire on interface-mandated signatures. |
| deptry DEP002 | medium | Misses implicit or plugin use (jinja2, python-multipart) [11] |
| coverage `--cov-context=test` + `coverage json --show-contexts` | dynamic | Shows *which* tests execute each line [13]. This is how to find "exclusive" tests. |
| Operational coverage (`coverage run --context=ops -m home_finder …`, owner-run) | strongest available | Plays the role of Sensenmann's run logs. It only sees the paths that actual run exercised. |
| Tombstones (a log marker on suspect code; wait N real runs; one that fires is a "vampire") [17] | strong over time | Slow, and changes production code |
| DB/file shape query on a copy of the data | decisive for COMPAT | SCARF's data rule |
| pylint R0801 / symilar | DUP lead | Finds exact blocks only, default 4 lines [15] |

## 3. Patterns in AI-written code (evidence quality varies)

- Duplication rises and reuse falls. GitClear (vendor data, method unpublished): cloned lines went from 8.3% to 12.3% between 2021 and 2024, and "moved" (refactoring) lines fell from 25% to under 10% [18].
- Sonar's static-analysis study: "dead/unused/redundant code" was 15–17% of the smells found in Claude models' code. Empty placeholder classes and generic exceptions were frequent, and the authors attribute both to missing project-wide context [19]. The Sonar blog reports high comment density (16.4% for Claude 3.7 Sonnet) and verbosity.
- After teams adopt Cursor, static-analysis warnings and complexity rise and stay up [20]. Agent refactorings are mostly low-level and do not reduce smell counts [21], so design-level duplicates and superseded code need human-directed cleanup.
- Counter-evidence: a preregistered RCT found no significant downstream maintainability penalty [22]. Grade each item on its own evidence, not on the fact that an AI wrote it.
- **Unsourced:** I found no study measuring "comments narrating review history" or "defensive checks for impossible states" in agent code. Both are practitioner observations.

## 4. Classification and prioritisation

Fowler's quadrant [23] classifies where debt came from. That helps prevention (agent instructions), not ordering. SQALE [24] contributes the useful split between **remediation cost** (effort) and **non-remediation cost** (what keeping the debt costs). For an agent-maintained repo, stale instructions have outsized non-remediation cost: CLAUDE.md is loaded into every session, contradictions make Claude "pick one arbitrarily", and the target size is under 200 lines [27]. This repo's CLAUDE.md is 236 lines.

## 5. Staged removal, verification, batching

- **Staging:** use expand → migrate → contract [23]. Keep migrations. For compat readers: query the stored data first, then make the legacy branch log loudly (a tombstone or soft block) for N runs, then delete [4,17].
- **Gates per batch:**
  1. Work in a git worktree.
  2. ruff, mypy strict, warn-unreachable delta, vulture re-run, deptry.
  3. Full test suite, plus `-m browser` if templates or static files changed.
  4. **Coverage-context diff:** no retained source line may lose all its covering tests. Delete a test only if it is exclusive to the deleted code; otherwise retarget it. This mechanises the "keep unique regression scenarios" rule.
  5. `mutmut run "mod.func*"` on the surviving function of any MERGE or SIMPLIFY. Surviving mutants must not increase [14].
  6. Owner smoke test: every CLI and `python -m` entry point, `--dry-run --max-per-scraper 1`, and dashboard routes.
  7. The PR body carries the evidence and a revert note [1].
- **Batching:** one category × one subsystem per PR. Keep refactoring and behaviour changes in separate PRs. Aim for about 100 and at most about 400 reviewable lines; a whole-file deletion counts as roughly one line [25]. Each shard must be independently green and revertible, and only a few should be open at once [26]. Suggested order: docs, AI-isms and hygiene first; then CERTAIN dead code; then TEST-ONLY (the code and its exclusive tests together); then SUPERSEDED and COMPAT via staging; then DUP merges. Once a category is clean, add a guard against it coming back [26].

## 6. Suggested rubric changes

1. **Add a `Claim:` field**: UNREACHABLE / UNEXECUTED(window) / REDUNDANT-WITH(X) / CONTRADICTS-CODE / UNWANTED(owner). Confidence attaches to the claim. Only the owner can establish UNWANTED.
2. **Add `Evidence:` codes and points**, mirroring the repo's own dedup rule of 60 points plus at least 2 signals. These weights are a proposal and have not been calibrated:
   - S-SEM +30: vulture 100%, mypy unreachable, ruff F-rules, or no route or registration path.
   - S-REF +30: zero name or string hits across the tree and the PR #77 branch.
   - D-TEST +20: only exclusive tests execute it.
   - D-OPS +30: not executed in an ops run, or its tombstone stayed silent.
   - D-DATA +30: no stored legacy shapes.
   - H-SUP +20: the replacement commit or a prior review item is cited.
   - H-STAB +10.
   - O +40: owner confirmation.
   - vulture 60% scores **0**; it is only a lead.
3. **Thresholds**:
   - CERTAIN: at least 80 points, including S-SEM, or S-REF plus a D-code, with gates 1–4 passed.
   - HIGH: at least 60 points from at least 2 families (S/D/H/O).
   - MEDIUM: at least 30 points, **or any blocker**. Blockers: an unexplained textual hit, a getattr/registry/template/JS path, execution in an ops run, live data, or possible local untracked work.
   - LOW: everything else.
4. **TEST-ONLY:** tests are not liveness evidence. Report the deletion unit as the code plus its exclusive tests, and list shared tests that need retargeting.
5. **Unused ≠ unnecessary:** recovery, migration and error paths that never execute default to KEEP-BUT-DOCUMENT or ASK-OWNER.
6. **Add `Value` (1/3/9) and an `agent-facing` flag**, then rank by `V·P / (E + R·(1−P))`. P is 0.98 for CERTAIN, 0.9 for HIGH, 0.6 for MEDIUM and 0.3 for LOW. V, E and R each take 1, 3 or 9. Worked examples: a CERTAIN stale CLAUDE.md line scores ≈ 8.6; a MEDIUM compat reader of stored data scores ≈ 0.27.
7. **New Actions:** TOMBSTONE (log, then wait N runs) and DATA-CHECK (owner queries a DB copy).
8. **AI-ISM and OVERBUILT findings must name a concrete harm**: the thing misleads, contradicts the code, or is impossible by types or data. Style preference is not enough.
9. **Add a `Verified-by:` line per batch** listing which of gates 1–7 ran.

## Reproducing the evidence

All commands run from the repository root with dev dependencies installed (`uv sync --all-extras`).
`vulture` and `deptry` were added to the virtual environment for the audit only.

```bash
# Static leads (treat 60%/90% as leads, never as evidence on their own)
uv pip install vulture deptry
uv run vulture src scripts --min-confidence 60 --sort-by-size
uv run vulture src --min-confidence 60 > /tmp/v_src.txt                      # src alone
uv run vulture src tests scripts --min-confidence 60 | grep '^src/' > /tmp/v_all.txt
comm -23 <(sort /tmp/v_src.txt) <(sort /tmp/v_all.txt)                       # used only by tests/scripts
uv run deptry src
uv run ruff check src tests scripts --select ERA,ARG,PIE,BLE001,S110,PLC0415,T20,RET --statistics --exit-zero
uv run mypy src --warn-unreachable                                           # not included in strict

# Dynamic: which tests execute which lines (about 14 minutes on 4 CPUs)
PYTHONUNBUFFERED=1 HYPOTHESIS_PROFILE=ci uv run pytest --cov --cov-context=test --cov-report=json
# Do not export contexts to JSON (it reached 6 GB); query the .coverage SQLite file instead:
sqlite3 .coverage "select distinct c.context from arc a
  join context c on c.id = a.context_id join file f on f.id = a.file_id
  where f.path = 'src/home_finder/notifiers/telegram.py'
    and (a.fromno between 988 and 1010 or a.tono between 988 and 1010);"

# History: the commit that removed the last production caller
git log -S'.mark_notified(' --oneline -- src

# Every reference, including the draft PR branch and non-Python files.
# Use git grep, not rg: CLAUDE.md and .python-version are gitignored, so rg skips them.
git grep -n 'name_here'
git grep -n 'name_here' origin/codex/review-opus-experiments
```

Per-area auditors also used small AST scripts (duplicate-function hashing, pytest-fixture usage,
CSS selector extraction against templates and JS). Each per-area file in [findings/](findings/)
describes its method at the top.

## References
1. Norman, "Sensenmann: Code Deletion at Scale" (2023) https://testing.googleblog.com/2023/04/sensenmann-code-deletion-at-scale.html
2. Meta, "Automating dead code cleanup" (2023) https://engineering.fb.com/2023/10/24/data-infrastructure/automating-dead-code-cleanup/
3. Meta, "Automating product deprecation" https://engineering.fb.com/2023/10/17/data-infrastructure/automating-product-deprecation-meta/
4. Meta, "Automating data removal" https://engineering.fb.com/2023/10/31/data-infrastructure/automating-data-removal/
5. Shackleton et al., FSE 2023 (abstract only) https://doi.org/10.1145/3611643.3613871
6. Uber, "Introducing Piranha" https://www.uber.com/blog/piranha/
7. Ramanathan et al., ICSE-SEIP 2020 https://2020.icse-conferences.org/details/icse-2020-Software-Engineering-in-Practice/16/Piranha-Reducing-Feature-Flag-Debt-at-Uber
8. Haas et al., TOSEM 2020 https://teamscale.com/hubfs/26978363/Publications/2020-unnecessary-code-tosem.pdf
9. Romano et al., IEEE TSE 2020 (abstract only) https://doi.org/10.1109/TSE.2018.2842781
10. vulture README https://github.com/jendrikseipp/vulture
11. deptry rules https://deptry.com/rules-violations/
12. Ruff ERA001 https://docs.astral.sh/ruff/rules/commented-out-code/
13. coverage.py contexts https://coverage.readthedocs.io/en/latest/contexts.html ; pytest-cov https://pytest-cov.readthedocs.io/en/latest/contexts.html
14. mutmut https://github.com/boxed/mutmut ; diff-cover https://github.com/Bachmann1234/diff_cover
15. pylint duplicate-code https://pylint.readthedocs.io/en/stable/user_guide/messages/refactor/duplicate-code.html
16. Ruff rules index https://docs.astral.sh/ruff/rules/
17. Tombstones: scheb/tombstone (credits Schnepper, Velocity 2014, https://www.youtube.com/watch?v=29UXzfQWOhQ) https://github.com/scheb/tombstone
18. GitClear 2025 https://www.gitclear.com/ai_assistant_code_quality_2025_research
19. Sonar, arXiv 2508.14727 https://arxiv.org/abs/2508.14727 ; https://www.sonarsource.com/blog/the-coding-personalities-of-leading-llms/
20. He et al., MSR'26, arXiv 2511.04427 https://arxiv.org/abs/2511.04427
21. Horikawa et al., arXiv 2511.04824 https://arxiv.org/abs/2511.04824
22. Borg et al., "Echoes of AI", arXiv 2507.00788 https://arxiv.org/abs/2507.00788
23. Fowler: https://martinfowler.com/bliki/TechnicalDebtQuadrant.html ; https://martinfowler.com/bliki/ParallelChange.html ; https://martinfowler.com/bliki/StranglerFigApplication.html
24. SQALE (Letouzey, via Cutter) https://www.cutter.com/article/managing-technical-debt-sqale-method-490726
25. Google, "Small CLs" https://google.github.io/eng-practices/review/developer/small-cls.html
26. *Software Engineering at Google* ch.15 https://abseil.io/resources/swe-book/html/ch15.html and ch.22 https://abseil.io/resources/swe-book/html/ch22.html
27. Claude Code memory docs https://code.claude.com/docs/en/memory

**Not verified:** Sensenmann and SCARF publish no numeric time windows. I read only the abstract of the SCARF paper. The SmartBear "200–400 LOC" review figure exists only in secondary sources, so I left it out. The weights in §6 are a proposal, not calibrated.
