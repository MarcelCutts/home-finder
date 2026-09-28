# PR20a: Native Anthropic SDK transport with the same analysis requests

**Date:** 28 September 2026. **State:** committed as `03e3e4c`.
**Predecessor:** Rightmove captured-detail repair, `9aba945`.
**Parent:** [PR20 specification](2026-09-28-research-synthesis-and-delivery-plan.md#pr20--isolate-sdk-transport-phase-configuration-and-output-contracts-m).

## Change and compatibility evidence

The locked Anthropic SDK moves from **0.81.0 to 1.8.0**. The temporary `<1` requirement becomes `>=1.8.0,<2`. Version 1 uses `httpx2` for HTTP transport; passing the previous `httpx.Timeout` to the new client reproduced a constructor `TypeError`. The application now uses the SDK's native `Timeout` type at client creation and both request sites, and exposes an optional native async client at the same constructor boundary used in production.

The SDK migration guide documents the native client/timeout types and warns that mocks instrumenting old `httpx` no longer observe SDK traffic. The existing HTTP tests now inject `httpx2.MockTransport` into the real SDK, exercising client construction, request serialization, response parsing and exceptions. The application uses no process-wide import alias. Its other HTTPX consumers retain their existing transport. [Official v1 migration guide](https://github.com/anthropics/anthropic-sdk-python/blob/v1.8.0/MIGRATION.md), [SDK 1.8.0 release](https://github.com/anthropics/anthropic-sdk-python/releases/tag/v1.8.0).

Before changing requests, both SDKs processed the same authored inputs and existing golden HTTP responses through their respective native mock transports. **The complete two request JSON objects, parsed visual/evaluation dictionaries and token counters are equal.** No paid calls were made. The request hashes are now regression assertions in `tests/test_filters/test_quality_transport.py`:

| Phase | Canonical JSON SHA-256 |
| --- | --- |
| Visual | `05007bbf2d261d843c31919c2a52daba72e6f3fb6ec202379413aca80323788b` |
| Evaluation | `1be5ce938e29adea05be23dc2e1609b528301e535ef2b267af7e91709fc37511` |

The baseline remains `claude-sonnet-4-6`, with the same prompts, images/text composition, cache prefixes, adaptive visual reasoning, tool output and native evaluation JSON schema. Output limits remain 21,000 / 4,096, read timeouts 240 / 60 seconds and connect timeout five seconds. SDK retries remain one. These read/connect limits are not whole-call deadlines; the existing pipeline property deadline still bounds its complete analysis including retries. PR13 will own admitted and metered attempts.

The replayed usage remains **2,415 input, 499 output, 2,000 cache-read and zero cache-write tokens**. Native tests also exercise cache creation usage through the existing suite. Closing the filter closes an injected client even if no API client was ever needed. Cancellation propagates through an in-flight native request without retry or circuit-breaker failure.

Private baseline, candidate and aggregate files are `/tmp/home-finder-pr20/baseline.json`, `after-transport.json` and `transport-compatibility.json`. They contain authored fixture requests, not fresh model assessments. The committed hashes and response fixtures provide the durable regression checks.

## Dependencies and audit

The new lock contains `httpx2` and `httpcore2` **2.13.1**, conditional `httpx2-jsfetch` **1.0**, and `truststore` **0.10.4**. The required `idna` resolution moves **3.15 → 3.20**; `distro` **1.9.0** is removed. No unrelated application dependencies, Python policy or scraper version change. `httpx2>=2,<3` is explicit because it is now part of the application's injectable SDK boundary.

Fresh candidate environments installed successfully on Python 3.11.16 and 3.14.7. Locked offline sync succeeds. Wheel and source distribution build under `/tmp/home-finder-pr20/dist/`, carry the new SDK requirements, and exclude local databases/logs/environment files. Both dashboard and collector container targets build successfully. With networking disabled, the dashboard image runs CLI help and constructs/closes the native SDK client; it reports SDK 1.8.0, httpx2 2.13.1, UID 1000 and SQLite 3.53.1.

Two package audits were checked rather than treating a scanner's summary as complete coverage:

- CI-equivalent `pip-audit==2.10.1` reports no known vulnerabilities among packages it could audit. It skips the unpublished Crawlee 1.3.2b4 and local `home-finder` package.
- `uv audit --locked --python-version 3.14` checks 129 packages and reports two records that alias the **same existing Crawlee sitemap SSRF**: `GHSA-3r75-xc34-5f44` / `PYSEC-2026-2430` / `CVE-2026-46497`, fixed in 1.7.0. This is the previously recorded PR03/PR11 issue, not two new defects. No SDK/new-transport advisory is returned by these checks. The application does not invoke the affected sitemap-loading path; the existing transport qualification/replacement work remains open.

No fresh container scan or deployment is claimed. The existing Debian findings in the [PR03 record](2026-09-28-pr03-runtime-and-maps.md) remain applicable. Raw package-audit results and logs are under `/tmp/home-finder-pr20/`.

## Verification and remaining PR20 work

The focused quality/recovery suite passes **307 tests**. The additional native transport suite and converted HTTP suite pass **17 tests**, including actual SDK 429/500 retry, exhausted native read timeout, per-phase timeout extensions, cancellation, client cleanup and baseline request hashes. The complete suite passes **2,697 tests on each of Python 3.11.16 and 3.14.7**, with **87.87% / 87.77%** combined line/branch coverage and no reported warnings. Tests block real network sockets. Ruff lint/format pass (192 files), strict mypy passes (78 source files), and `git diff --check` passes. No dashboard changes require a browser rerun.

This is the transport slice. **PR20 remains open** for explicit per-phase model/provider/reasoning/effort/limit/timeout/output-contract configuration, recorded provenance, and typed refusal/truncation/malformed-output outcomes. In particular, current evaluation code can fall back from invalid structured output; this commit preserves that baseline so its correction is reviewable separately. PR13/14 must later persist per-call accounting and extraction/attempt keys. No live model quality comparison, new human label or production model promotion is implied.

Follow-up: [PR20b phase configuration/provenance](2026-09-28-pr20b-analysis-configuration.md), then explicit output-failure handling and PR13 spend admission. Roll back this segment by reverting the dependency lock/requirements and native transport/tests together; it introduces no schema migration.
