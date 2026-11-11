# Model comparison: Anthropic vs OpenAI for the quality analysis

See `PLAN.md` for the method. Everything under `data/` is private and git-ignored: the trial
snapshot, the run outputs and the spend ledger.

```bash
cd experiments/model-comparison
uv sync
uv run python run.py preflight                     # keys found? tiny Haiku ping, list OpenAI models
uv run python run.py cases --n 30                  # write the stratified case manifest
uv run python run.py sweep --run core --n 30 --configs sonnet55-low,sol61-low
uv run python run.py sweep --run repeat --n 10 --repeats 3 --configs sonnet55-low,sol61-low
uv run python judge.py --run core --n 30 --pairs sonnet55-low:sol61-low
uv run python analyze.py --run core
```

Keys come from the main checkout's `.env` (`HOME_FINDER_ANTHROPIC_API_KEY` or `ANTHROPIC_API_KEY`;
`OPENAI_API_KEY`). Base URLs are pinned because this shell exports `ANTHROPIC_BASE_URL` for
Claude Code itself.

- `mc/shim.py`: stands in for `AsyncAnthropic` inside the unmodified `PropertyQualityFilter`, and
  translates requests to the OpenAI Responses API.
- `mc/configs.py`: the arms.
- `mc/rates.py`: list prices as of 2026-10-21.
- `mc/ledger.py`: shared estimated spending and durable outstanding reservations.
- `mc/facts.py`: deterministic facts stated in the listing text.
- `judge.py`: blind pairwise cross-family judging.
- `analyze.py`: metrics with cluster-bootstrap confidence intervals.

## Review corrections, 21 October 2026

This copy preserves source only. Private case snapshots, responses, credentials,
ledgers and generated HTML are excluded. No new paid runs were made during review.

- Outstanding reservations are now shared under the file lock, survive process
  exits and settle by token. Changing an existing ledger's caps is rejected.
- Judge parsing failures still account for reported usage. A cancellation or
  uncertain dispatch retains its reservation and blocks further calls for that
  provider until reconciled. The shim retries reported rate limits only; SDK
  retries remain disabled. New timings include queueing/backoff, unlike the
  historical last-attempt timings: do not pool them as identical measurements.
- Warm and cold cost columns both divide total attempted cost by valid results.
  Results include repeated runs; they are not necessarily distinct homes.
- A budget stop between phases or cancellation retains the partial case, including
  completed and unresolved calls. Resuming skips that saved attempt; retry under
  a new run name after reconciliation so earlier spending remains inspectable.
- The existing `RESERVE` amounts are historical estimates, not maximum costs.
  **This harness does not guarantee a hard invoice cap.** Before another paid
  trial, bound effective requests and supported pricing modes, including cache
  writes, and reconcile the old ledger with provider receipts. The application's
  durable spend-admission machinery is the preferred basis for a production adapter.

Offline checks, from the repository root (also run by CI):

```bash
uv sync --locked --project experiments/model-comparison
PYTHONPATH=experiments/model-comparison:src \
  uv run --locked --project experiments/model-comparison python -m pytest \
  -c pyproject.toml experiments/model-comparison/tests -n 0 --no-cov
```

`preflight`, `sweep`, `judge.py` and the `probe_grammar*.py` scripts make provider
calls. The preflight ping and grammar probes bypass this ledger. The example commands above
describe the original experiment; they are not part of the offline test workflow.
The reviewed rating and prompt proposals remain candidates for a held-out,
human-labelled evaluation, not changes to the production rating policy.
