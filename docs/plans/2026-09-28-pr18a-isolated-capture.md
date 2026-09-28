# PR18a: Isolated response capture, parser replay and historical export

**Date:** 28 September 2026. **State:** tooling and offline exercise committed as `02a75f1`, verified. Live acquisition and the later elapsed canary remain unqualified.
**Predecessor:** PR12a, `b5d9859`.
**Parent:** [delivery plan, PR18](2026-09-28-research-synthesis-and-delivery-plan.md#pr18--run-a-controlled-collection-restart-and-qualify-source-coverage-m--elapsed-canary).

## What the production copy contributes

The owner confirmed that a production database copy is available. The supplied 27 September copy contains 1,016 properties, 3,267 source-listing projections, 1,001 quality analyses and 15,872 image references. It is useful for storage regression, recovery, price/identity cases and constructing a development sample. It is not a source-response archive or independently labelled model evaluation set.

Read-only SQL checks found **zero nonempty descriptions in either the property or source-listing tables**, and zero nonempty description maps. That is a concrete limit on evaluating historical model conclusions: the evidence needed to check many text-derived assertions is absent. Image URLs alone also cannot reproduce what a model saw; cached bytes would need their own verified retention and hashing step.

The new `--snapshot` operation exported 24 cases, chosen deterministically by `first_seen, unique_id`, into a private directory. This deliberately biased development sample contains 54 source projections, 24 unverified model outputs and 639 image references. No human labels are assigned, and it is not a representative benchmark or holdout. The export omits user notes/contact tables and uses explicit column allowlists so future private fields are not silently included.

The original copy remains unchanged:

```text
e50897b46ebefea0119fd62951ac277d764684f9590d52801e66c964b1352c63
```

Local artifacts are under `/tmp/home-finder-pr18a/historical/`, with aggregate evidence in `historical-summary.json` and `text-audit.json` in the parent directory. These paths are local working artifacts, not committed data or durable production backups.

The exporter opens an existing, quiescent copy with `mode=ro&immutable=1` and `query_only`, refuses a nonempty WAL, and compares the file hash before and after export. It never initializes `PropertyStorage`, migrates schemas or changes queue state. Immutable mode is appropriate only for a frozen copy: SQLite explicitly skips locking/change detection in this mode. Do not point it at an active database. [SQLite URI documentation](https://www.sqlite.org/uri.html).

## Capture contract

`python -m home_finder.pipeline.capture` is independent of the application pipeline. Offline fixture input is the default. Each invocation writes a new private namespace with a plan, request journal, retained body hashes, parser/model source-file hashes, runtime dependency versions and outcomes. An advisory process lock excludes a second collector using the same output root. Directories use mode `0700`; artifacts use `0600`, atomic replacement and local fsync.

A plan supplies explicit URLs and scope, with a hard ceiling of 12 requests, 8,000,000 retained bytes per response and 60 seconds per request. Smaller budgets are supported. The CLI limits plan files to 128,000 bytes. Detail parsing requires the actual retained search-listing input with matching source and URL; the tool does not invent a dummy property to satisfy a parser.

Live mode requires `--live` plus a dated, unexpired acquisition/retention qualification reference for that source. This records an operator decision; supplying a string does not itself establish permission. It allows only HTTPS URLs on the selected portal's exact configured hosts, rejects URL credentials and common credential query parameters, and enforces at least one second between requests. No source is newly qualified by this implementation.

Every live request is reserved in the journal before sending. This path performs one direct HTTPX request per planned URL, using an identifying fixed user agent, no inherited proxy/auth settings, and a fresh cookie state for each request. It does not discover detail URLs or make hidden typeahead/warm-up requests. It does not retry, follow redirects, download images, rotate transports, operate a challenge, run models/commute services or send notifications.

Redirects, non-200 responses, recognised challenges, timeouts, exceptions and oversized bodies stop the run. Cancellation retains the received prefix and a terminal journal before releasing the lock. The CLI returns nonzero for a stopped capture. A hard process kill can leave a reserved attempt with unknown completion and no final body: it is not silently classified as successful or automatically replayed as a new request. Durable per-observation checkpointing remains PR06b/09b.

Bodies are the **HTTP-decoded parser bytes**, not packet captures; fixture bytes are retained as supplied. The body limit bounds stored content, not the exact number of network octets buffered by the transport. Response headers are limited to content type, Retry-After and Cloudflare mitigation status; cookies and unrelated headers are omitted. Sent headers are recorded when a response is obtained. [HTTPX streaming and response cleanup](https://www.python-httpx.org/async/#streaming-responses), [redirect behaviour](https://www.python-httpx.org/quickstart/#redirection-and-history).

## Offline parser replay

`--replay` validates the manifest plan, request identity, file confinement, body length and SHA-256 before parsing. It never changes the original capture. It writes a separate result with the source-manifest hash, current parser source hashes, extraction outcomes and no human labels.

Search replay invokes the existing portal parser functions. Detail replay invokes `DetailFetcher.fetch_detail_page` with its complete fetch boundary replaced by the retained response; client/session creation and media acquisition are disabled. No application database is opened. Generated `Property.first_seen` defaults are excluded from replay output; a supplied observation date is preserved separately, and an absent date stays unknown.

`extracted` means the parser returned fields. It is not proof of target identity, correct query scope, model truth or current source support. Empty output is `empty_or_unrecognised`; rejected/partial bodies remain unusable/incomplete evidence. The old OnTheMarket HTML search fixture currently produces the ambiguous outcome because its HTML-only shape does not match the adapter's `__NEXT_DATA__` contract. The harness reports this instead of declaring healthy empty inventory.

This transport is intentionally recorded as `httpx_direct_no_session`. It is **not** equivalent to the production Crawlee/curl adapters with their headers/session behaviour. Results must not be used to claim those adapters are qualified or broken. PR10/11 need retained successful examples through the relevant application adapter before promotion.

## Commands

After installing the locked development environment:

```bash
uv run python -m home_finder.pipeline.capture \
  --snapshot data/production-review/2026-09-27T122222Z/properties.db \
  --output-root data/captures/historical --max-cases 24
```

Example offline plan (`data/capture-plan.json`; this is a legacy fixture, not a current live observation):

```json
{
  "source": "rightmove",
  "max_requests": 1,
  "max_response_bytes": 1000000,
  "request_timeout_seconds": 30,
  "delay_seconds": 3,
  "requests": [{
    "url": "https://www.rightmove.co.uk/property-to-rent/find.html",
    "kind": "search",
    "scope": "legacy repository search-parser fixture",
    "fixture": "rightmove_search.html"
  }]
}
```

```bash
uv run python -m home_finder.pipeline.capture \
  --plan data/capture-plan.json --fixture-root tests/fixtures \
  --output-root data/captures/fixtures

uv run python -m home_finder.pipeline.capture \
  --replay data/captures/fixtures/REPLACE_WITH_CAPTURE_DIRECTORY \
  --output-root data/captures/replays
```

For qualified live use, remove fixture fields, supply the approved explicit endpoint scope and add a `qualification` object containing `source`, a non-secret decision `reference`, `acquisition: true`, `retention: true` and an aware ISO `expires_at`. Use `--live` without `--fixture-root`. No live command was run during this slice. Storage/model-processing/hosted-display rights are separate decisions; this tool does not grant them.

## Verification and remaining gates

The focused suite passes 40 cases: destination and request-budget validation; source/listing binding; missing, expired and mismatched qualification; private artifacts; process exclusion; symlink escape; no offline HTTP client; durable reservation before sending; 301/403/429/challenge stop behaviour; no cookie propagation; bounded/truncated bodies; timeout/cancellation prefix retention and stream closure; all four existing search/detail parser paths; tamper rejection; read-only schema-compatible historical export with private-field exclusion; and CLI failure status.

Full supported-runtime verification: **2,623 tests pass on each of Python 3.11.16 and 3.14.7**, with combined line/branch coverage of **87.43% / 87.33%** and four pre-existing warnings per run. Ruff lint and formatting checks pass (187 files); strict mypy passes (76 source files). Logs and coverage reports are under `/tmp/home-finder-pr18a/`. This slice does not change dashboard behaviour, so browser tests are not repeated.

| Source | What is established here | Still needed before fresh qualification |
| --- | --- | --- |
| Rightmove | Offline legacy search/detail replay and retained failure classification | Successful current search/detail/typeahead inputs; verify reported new payload contract and complete adapter |
| Zoopla | Offline legacy search/detail replay | Current search/detail inputs, full session/transport comparison and query coverage |
| OpenRent | Offline legacy search/detail replay | Current inventory/pagination reconciliation, rooms/leakage and full adapter |
| OnTheMarket | Legacy detail replay; explicit unrecognised legacy HTML search outcome | Retained current structured search/detail inputs and full adapter |

PR18a's **tooling and historical sample** can proceed independently of source acquisition. Actual live capture, source permission/support decisions and PR18b's seven-day authoritative canary remain open. Next: inspect retained successful research artifacts for PR10/11; implement only evidenced parser repairs. If essential source examples are absent, record the narrow dependency and continue the independent PR20 SDK/configuration work. PR19/21 still require suitable retained evidence and independent human labels before model promotion.
