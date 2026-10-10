# Keep reviewed regrouping disabled while derived consumers are incomplete

Status: implementation, author review and local checks complete; published as [draft #34](https://github.com/MarcelCutts/home-finder/pull/34). Application commit: `25a5fe84b914beedda9869d3acbe4f7c8c25ad71`; the publication follow-up changes documentation only. Base: `fc7b466971984a15f69a886eadfe3cd621ee8cf7`, [draft #33](https://github.com/MarcelCutts/home-finder/pull/33). Branch: `codex/stack/31-regrouping-gate`, position 31 in native stack #29. This implements the first item in the revised corrective queue. Schema remains 015.

## Problem and bounded outcome

The independent review demonstrated that splitting a reviewed group can leave an assessment describing a departed member displayed as current, without scheduling reanalysis or blocking paid admission. The preceding listing-input lifecycle retains evidence and detects changed inputs, but does not complete assessment, price, availability or asset consumers. An application caller could nevertheless apply another reviewed merge, split or reversal.

`MembershipRepository.apply()` now refuses **new reviewed decisions by default** with `MembershipConflict("reviewed_regrouping_disabled")`. This also covers changing the representative through a reviewed merge. The refusal occurs inside the existing owned transaction before the journal, ownership, routes, generations or invalidations change. Rebuilding and acknowledging the `listing_inputs` component does not open the gate.

Read-only inspection, preparation and audit remain available. An exact retry of an already committed decision returns its existing receipt without repeating the mutation, including after restart or a subsequent split. Reusing that decision ID with a different payload is still rejected. Ordinary source linking, conservative automatic deduplication, staging absorption and cleanup retain their existing preservation restrictions and do not use this reviewed-operation method.

This is a narrow activation guard, not completion of the regrouping feature. Its practical value is preventing accidental use of unfinished behavior while the next changes repair the core rental-finding flow. It adds no service, scheduler, database table, configuration flag or user-facing control.

## Explicit copy rehearsals

Existing disposable-copy experiments can opt in at each mutation:

```python
await copy_storage.memberships.apply(
    reviewed_decision,
    allow_incomplete_projections=True,
)
```

Only the literal boolean `True` opts in; truthy strings or integers do not. The keyword is not serialized into the reviewed decision or persisted as a database setting. A preceding opted-in call does not enable the next call. No application environment setting, CLI argument or web/Telegram request supplies it.

The override is deliberately named for the incomplete invariant it accepts. It is intended for isolated disposable-copy rehearsals and **does not prove that the caller selected a copy**, grant operator approval, or make the resulting projections safe. It is an application guard, not a security boundary against direct Python/SQL access. Do not pass the keyword from untrusted request data or add an ordinary activation setting before the remaining consumers are complete.

Previously committed regrouping is retained, including any stale derived state it already created. This prospective guard does not repair or hide that history. An existing regrouped database still needs the outstanding freshness work before it can be considered ready for active use. No production regrouping has been performed by these implementation segments.

## Review and verification

Reviewed the public mutation entry point, all application callers, automatic membership writers, decision validation, committed retry ordering and refusal logs. The override is checked only for a genuinely new reviewed decision, after validating the payload and distinguishing an exact committed replay. This preserves recovery after an uncertain commit without permitting a new mutation.

The existing `membership_decision_refused` event records the decision ID and bounded reason `reviewed_regrouping_disabled`. Tests also check that the human review reason is not leaked into the refusal logs. No new raw listing or personal data is logged.

All 63 existing test calls that intentionally construct reviewed groups now opt in explicitly. An AST comparison against the base verifies that removing those keyword arguments restores the previous test code exactly; no assertion, fixture behavior or test control flow was weakened. Eight new cases cover default merge/split/reversal/representative-change refusal, whole-database row preservation even after rebuilding listing inputs, inspection and committed replay across split/restart, mismatched payload refusal, and strict opt-in values.

Both Python **3.14.7** and **3.11.16** pass:

- **149 affected tests:** the new gate, membership, group routing, listing projections, exact listing consumers, related web tests and read-only projection audit.
- **355 ordinary-work tests:** merge preservation, spending, automatic deduplication, existing-listing handling and default offline integration scenarios.

That is **504 relevant tests per interpreter** with external sockets blocked. The new change is not claimed to have rerun the entire suite or measured fresh global coverage. The parent's 3,406-test full suites remain dated evidence for #33. Ruff lint/format (268 Python files), strict mypy (120 source files), diff checks and release archive verification pass. No UI behavior changed, so no new browser run is claimed.

A fresh copy of the retained 50-listing database exercises the actual Southborough and Wilmot cases. Default merge, split and reversal are refused with every application row unchanged. Explicit copy-only merges and reversals preserve all original rows and issued links; committed merge replay after reversal is a no-op. Restart, journal replay, SQLite integrity and foreign keys pass, and the original input file hash is unchanged.

The independent offline 50-listing pipeline replay checks ordinary matching with the gate left at its default. Both same-run and split-run shapes retain the same 35 groups and all 50 selected members, generate no new work for known listings and make no model attempts. All original database/image artifacts remain unchanged. These are regression experiments, not a new live or model-quality trial.

The [verification record](2026-10-10-reviewed-regrouping-gate-verification.json) contains counts and hashes. Private logs, copies, replay outputs and source manifests are in `.worktrees/evidence/31-regrouping-gate`, excluded from Git. This is an author review, not independent approval. The independent review established the underlying activation problem; this segment addresses its default-use path, not the deferred freshness implementation.

## Stack and next action

The immediate Git base is #33. The semantic dependency is the reviewed mutation API from #30 and its routing semantics from #31; the new input component from #33 is exercised to prove its receipts cannot accidentally open the gate. The implementation is small enough to assess independently of the optional remaining projection programme. Existing stack heads are preserved.

Proceed to **returned-detail identity validation**: reject mismatched final URLs, embedded listing IDs and generic pages before retaining observations or using their text/images, with supported aliases checked explicitly. Then repair qualified bill inclusion and frozen-export validity. Keep further reconciliation conditional on product need and retain the current automatic matching thresholds.

Remote CI remains disabled by inactivity; no remote passing check is claimed. This segment changes no workflow setting, release state, schema, model policy or spending authorization. No active-data mutation, portal fetch, paid model call or Telegram send occurred. The delivery goal remains active.
