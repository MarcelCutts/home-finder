# Reading and extending the tests

Start with the contract you want to understand. The [pipeline guide](../docs/pipeline.md)
explains the production sequence and vocabulary.

| Location | What it demonstrates |
| --- | --- |
| `test_pipeline/` | Coordination, recovery queues, run accounting, and resource ownership |
| `test_matching/` | Match scores, grouping invariants, canonical selection, and image evidence |
| `test_enrichment/` | Detail/media acquisition and advisory commute assessment |
| `test_analysis/` | Provider requests, response validation, evidence assembly, and paid-attempt behavior |
| `test_filters/` | Criteria, location, fit scoring, and selection policies |
| `test_db/` | Persistence, transactions, and durable state transitions |
| `test_notifiers/` | Message planning, transport receipts, and delivery recovery |
| `integration/` | Contracts that connect several subsystems; each module states its boundary |
| `support/` | Reusable builders, fake transports, and storage inspection helpers |

`integration/test_run_pipeline_e2e.py` calls the run coordinator with external
services replaced. `integration/test_candidate_preparation.py` exercises candidate
preparation and persistence. `integration/test_selection_persistence.py` connects
selection, matching, and storage. The `e2e` marker currently means several pipeline
stages; it does not imply live services or complete run-lifecycle coverage.

## Names describe the observed contract

Include the distinguishing condition and observable outcome when useful:

- `test_new_source_preserves_assessment_and_requests_reanalysis`
- `test_repeat_observations_do_not_request_another_reanalysis`
- `test_disabled_analysis_persists_home_and_suppresses_discovery_delivery`

A test claiming failure recovery must inject a failure. A test claiming a round
trip must perform both directions. Preserve precise compatibility names when
testing existing settings or stored fields, even if newer code uses clearer terms.

Use `batch`, `summary`, and `analyzer` for those objects. Distinguish an initial
assessment from a requested reanalysis. A processed count may include skipped
analysis; it does not establish a provider-call count.

## Keep the important boundary real

Call production consolidation rather than copying its anchor-selection algorithm
into a helper. Where recovery is the contract, assert durable state and reopen
storage when restart behavior matters. Compare the saved assessment before and
after a merge, since persistence can normalize the original input.

For notification lifecycle tests, keep the real planner and delivery repository
and fake the Bot API using `support/delivery.py` or `notifier_helpers.py`. Callback
invocation, selection eligibility, and confirmed delivery are separate events.
Assert suppression reasons or receipts as appropriate. The legacy
`notification_deferred` diagnostic also covers suppressed deliveries; its metadata
and the durable delivery row identify the actual state.

Synthetic stage summaries and callback fakes remain useful for narrow accounting
tests. Name that boundary explicitly. Private access can also be useful when the
contract is ownership, cancellation, or a particular provider wire format.

## Shared setup and organization

Put reusable builders and fakes in `support/`, with dependencies on application
code or other support modules. Test modules should not provide helpers to other
tests. Keep resource fixtures in the nearest relevant `conftest.py`, and keep the
values that distinguish a scenario visible in the test itself.

Split modules by responsibility. Matching tests separate grouping rules, canonical
selection, and image evidence. A multi-step restart scenario can legitimately be
long; snapshot size and assertion count alone are not reasons to split a test.

Comments explain a non-obvious constraint, fixture choice, or ordering requirement.
Keep them synchronized with the assertions. Avoid repeating the next function
call, and replace temporary labels such as “new fields” or numbered plan steps
with the lasting domain concept. Use explicit event coordination in concurrency
tests when ordering matters.

## Verification

Run from the repository root:

```sh
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest --cov --cov-report=term
```

Default tests block network sockets and exclude `slow` and `browser` cases. Run a
focused module while changing its contract, then run the default suite when shared
fixtures or import boundaries change.
