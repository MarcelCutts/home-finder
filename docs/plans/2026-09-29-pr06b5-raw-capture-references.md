# PR06b-5: Verifiable raw references, replayed observations and explicit expiry

**Date:** 29 September 2026. **Predecessor:** current text `e5f1a9e`.
**Scope:** offline association of retained PR18a responses with normalized observations, bounded reference resolution, and an explicit raw-body retention operation. Normal collectors still retain normalized observations; this slice does not enable collection or a second database writer.

## Result

Version-2 replay reports now include normalized `SourceObservation` records when a usable captured response has an explicit observation time. These point to the exact original manifest checksum, record index, response checksum, byte length and acquisition mode. Search and detail use their application parsers and observation normalizers, including source metadata such as a reported floorplan count of zero. Replaying undated input still produces useful extraction output, but exports no invented dated observation.

Replay preserves the original capture time and records its own execution time separately. Event identities are stable for the same manifest, record position and parser implementation. Repeating the replay therefore inserts one event into an isolated ledger and then records an idempotent repeat. A parser implementation change gets a different identity; it does not overwrite an earlier interpretation. Equal-time disagreements between interpretations can remain conflicts under the existing projection policy and require a reasoned correction. Source/model implementation hashes, replay/verifier code and shared identity helpers contribute to the fingerprint; this is not a claim that a hash captures the entire runtime environment.

Fixture observations carry `capture_mode=fixture` and a distinct producer namespace. No export is automatically imported into the application database. A future authenticated ingestion boundary must qualify the producer and reject development fixtures; the producer string or manifest checksum is not authentication or proof that the operator's acquisition claim is true.

## Verification and availability contract

`pipeline.capture_evidence` supplies one verifier for replay, reference resolution and inspection. It bounds manifests to 256 KB, responses to the declared maximum of at most 8 MB, and captures to twelve planned requests. It checks record identity, allowed response headers, aware timestamps, body length and SHA-256. Duplicate JSON keys and nonfinite constants are rejected explicitly: Python's default JSON decoder accepts both unless configured otherwise. [Python JSON interoperability](https://docs.python.org/3.14/library/json.html#standard-compliance-and-interoperability).

Files are opened relative to an anchored directory descriptor. Each directory component and input file rejects symlinks; inputs must be regular bounded files. Nonblocking opens prevent a supplied FIFO from hanging inspection. These are Linux/POSIX operations matching the application environment. The private capture directory remains a trusted local administration boundary; advisory locks and checksums do not protect against an administrator concurrently replacing its contents. [Python descriptor-relative file operations](https://docs.python.org/3.14/library/os.html#os.open).

A running capture is not an immutable replay input. Truncated, failed, challenged and non-200 responses cannot export positive normalized observations. Missing body files, elapsed retention deadlines and invalid references are distinct from available bytes. The ledger retaining a raw checksum does not make a deleted, unavailable or altered file readable. Inspection exposes metadata and availability, not page content or listing URLs.

Replay allows at most 100 extracted listings per response, 4 MB of normalized observations across the report and a 16 MB final report. An excessive record count has an explicit parser failure; exceeding the aggregate byte bound fails before publishing an output artifact. Export directories/files remain private (`0700`/`0600`) and publication uses the existing atomic, fsynced writer.

## Retention policy and operator procedure

New live capture plans require `raw_retention` containing `policy_version: 1`, a `policy_reference` and an aware `retain_until`. The deadline must be future and within 90 days at acquisition; this is an engineering ceiling, not a legal entitlement or recommended universal retention period. The existing source acquisition/retention qualification is still required independently. Capture stops before beginning another request if its raw deadline has elapsed.

Existing captures and offline fixtures without this declaration are labelled `legacy_unmanaged`. They are inspectable but cannot be expired by the new tool. This avoids assigning a deletion policy retroactively to the owner's retained evidence. A managed capture becomes unreadable through normal replay/reference resolution at its declared deadline, even if an operator has not yet removed its files.

Inspect a specific private capture first:

```sh
uv run python -m home_finder.pipeline.capture_evidence /private/capture-directory
```

After its declared deadline, an operator can remove that capture's raw bodies with its reviewed manifest checksum:

```sh
uv run python -m home_finder.pipeline.capture_evidence /private/capture-directory \
  --expire --expected-manifest-sha256 MANIFEST_SHA256
```

Expiry requires a terminal capture and the same namespace lock as acquisition. It verifies all remaining bodies before deleting any, acts only on the manifest's fixed body filenames, and leaves unrelated files and the original manifest unchanged. A private, fsynced `raw-expiry.json` receipt records intent before removal and completion afterward. A process exit between removals is recoverable: another invocation checks the remaining files and records already absent files separately. It does not falsely claim that an absent file was removed by the current invocation. Corruption stops expiry for audit instead of deleting changed content.

No expiry scheduler has been enabled. Operational rollout must schedule and monitor this command for managed captures. It removes raw body files only; immutable normalized observations, replay exports, annotations, backups and other copies have separate retention/access lifecycles to settle before hosted rollout. The offline report records the raw policy but is not itself an automatic deletion mechanism. Raw response bodies are opaque private source content, not sanitized public fixtures; do not commit or serve them. Headers remain allowlisted, with no request credentials or authentication headers introduced.

## Checks and retained-capture rehearsal

The focused regression set passes 160 tests on Python 3.14; 78 capture/replay tests pass on 3.11. New cases cover stable duplicate replay and a real ledger round trip, all four detail adapters, unknown capture times, parser revisions, reference mismatches, symlink/ancestor/FIFO rejection, malformed manifests, byte/record bounds, elapsed deadlines, lock contention, tampering, idempotent expiry and a real child-process exit between removals. Expiry tests use temporary synthetic captures only. Initial test failures were incorrect assumptions about the saved fixtures' description/branch fields and an attempted mutation of a frozen test property; the assertions/context were corrected without adding unsupported fixture facts.

Full suites pass **3,037 tests on each of Python 3.11 and 3.14**, with branch-inclusive coverage **88.47% / 88.39%**. Ruff checks and format verification pass across 223 files; strict mypy passes across 96 source files. Final logs/coverage are `/tmp/home-finder-pr06b/raw-{full,coverage}{311,314}.*`. No source/test files changed during those runs.

The original terminal curl journal has manifest SHA-256 `997855fb5ba79e748e248983c4a2c5e4035a8eb50e1efc5ec3be36fd85e907b2` and raw body SHA-256 `5a8f6defa750357dd9af2e1398cf90e8cec9eb4d5482d9c7fcb784e08bc6cc7f`. It is explicitly a **reference-only** request, so direct replay correctly exported no detail observation. The rehearsal preserved that journal and made a separate, clearly labelled fixture of the same bytes, supplying only the detail parser's request identity. Its placeholder search title/price/address were not promoted into detail facts.

Two replays of that derived fixture produced the same observation, SHA-256 `0e5b01ea1321caae0b861af2372b227c90eb46cb62eb7714d65bf1fd7353cce7`, retaining the original `2026-09-28T18:09:49.986563Z` capture time, 396 description characters, eight features, nine selected gallery URLs, the reported branch ID and zero floorplans. Ingestion into a new isolated database inserted one observation and then replayed it without duplication. Both original files retained their exact checksums. No new portal request, model call, notification, production write or deletion occurred.

The private report is `data/raw-replay-2026-09-29-v2/verification.json`; probe code is `/tmp/home-finder-pr06b/raw_replay_probe.py`. The derived raw copy has a seven-day development deadline; the original remains unmanaged and unchanged. Earlier reference-only replay outputs remain separately recorded in `data/raw-replay-2026-09-29/`. These are development artifacts, not human labels or source-access qualification.

## Compatibility and next segment

There is no database migration. Optional `capture_mode` is omitted from old observation payloads when absent, preserving their existing hashes. New strict readers understand the extension; rollback must keep that reader compatibility and the immutable evidence rather than stripping fields. Replay output explicitly advances to schema version 2. Capture manifests remain version 1 with an optional plan field; new live capture deliberately fails when the deadline is omitted. No new dependency is required.

Next address exact source floor-area quantities and their interpretation before promoting them into durable evidence. The current parser can collapse a range to its upper endpoint and lose its source unit; that must not become a fabricated exact total. Non-text current projections remain PR08b, identity decisions PR07, and artifact dependency/freshness PR14. Remote observation transport remains conditional on measured collector placement and is not activated here.
