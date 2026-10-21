# Review of the experiment source

This branch preserves Opus's 21 October exploratory source separately from the
application fixes in [PR #76](https://github.com/MarcelCutts/home-finder/pull/76).
The input was the uncommitted `claude/sonnet-openai-comparison-9ee7f3` worktree,
based on `2dc7262`. Private snapshots, model responses, credentials, generated
reports and spending records are excluded.

The model comparison has useful paired cases, repeated runs, cross-family
judging and stored responses. It is exploratory evidence: model agreement is
not human truth, repeated records are not independent homes, and changed
rubrics or inputs must not be pooled as if they were the original experiment.
The application PR contains the detailed architectural review and independently
recomputed results.

## Reproduced defects and changes

Four regressions failed against the original source before the accounting repair:

1. A reservation in one process did not prevent a second process from exceeding
   the shared reservation limit. Reservations now live in the locked ledger,
   with atomic file replacement and specific settlement tokens. Python's
   [file-locking interface](https://docs.python.org/3/library/fcntl.html#fcntl.flock)
   protects the transaction; the regression actually starts another process.
2. A judge response with reported usage but invalid JSON left recorded spending
   at zero. Reported costs are now settled even if interpretation fails.
3. Cancellation released a potentially billable request. Unknown outcomes now
   retain their reservation and stop further work for that provider. The shim
   also stops repeating ambiguous connection/server failures automatically;
   reported rate-limit rejections can retry with fresh admission. This is a
   conservative experimental accounting policy, not a claim that every network
   exception is billed. Provider [error classes](https://platform.claude.com/docs/en/api/errors)
   distinguish rejections from failures but do not provide a complete invoice.
4. Another process could silently overwrite the configured caps. A mismatch
   now requires explicit reconciliation.

Additional checks cover non-finite/negative settlements, settlement after restart,
duplicate settlement, costs above their estimate, cancellation and connection
failures in the shim, priced post-processing failures, and rate-limit recovery.
Report tests verify that both warm and cold cost per valid result include failed
attempts in the numerator. Historical per-attempt keys remain available for
compatibility; new report columns use the same denominator.

New call records identify their timing scope and include queueing/backoff. The
old timings capture the last attempt. Neither is an isolated decoding-speed
measurement, and they should not be pooled without accounting for that difference.

## Status

**16 offline tests pass.** The new ledger and regression tests pass Ruff lint;
the seven touched Python files pass formatting, and all experiment Python
sources parse. Provider calls were mocked. No experiment was rerun against a
paid service, and private historical artifacts were not regenerated.

The fixed reserve amounts still are estimates rather than defensible maximum
request charges. This branch must not be promoted as hard budget enforcement.
Before another paid trial, qualify effective requests and pricing modes or reuse
the application's durable admission machinery through a truthful provider
adapter. Preserve effective provider, model, effort, schema, prompt and image
identities; the compatibility shim's application provenance is not sufficient.
