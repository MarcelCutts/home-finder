# Offline duplicate-candidate audit

`scripts/audit_duplicate_candidates.py` creates a private review queue of
metadata-similar **distinct canonical homes**. It includes homes whose listing
platforms overlap, including two already-merged groups sharing the same portals.
It makes no identity decisions and changes no deduplication thresholds.

## Run

Use an existing, closed, standalone SQLite snapshot with writers stopped for the
entire audit. The current canonical-home table is `properties`; memberships come
from `source_listings.merged_id`, not the legacy `sources`/`source_urls` JSON.
Both tables must exist. There is no migration or schema-repair step.

With the project dependencies already installed, run from the repository root:

```bash
uv run --offline --no-sync python -B scripts/audit_duplicate_candidates.py \
  --database /private/offline-snapshot/properties.db \
  --output /private/reviews/duplicate-candidates-001.json \
  --max-homes 1000 --max-comparisons 100000 --max-pairs 100
```

The output parent directory must already exist. The JSON file is created with
mode `0600`; existing paths, including symlinks, are refused. Choose a new output
name for each run. It contains addresses, URLs, coordinates, canonical IDs and
source-listing memberships, so keep it private and out of version control.
Stdout is one JSON object containing aggregate counts, truncation flags and the
`unconfirmed`/`uncalibrated` labels. Failures exit nonzero without printing private
row values or paths. A `FileExistsError` means the output name is already taken;
an `OperationalError` can indicate an incompatible schema or unreadable snapshot.

SQLite is opened with `mode=ro&immutable=1`, `query_only=ON`, and in-memory temp
storage. Immutable mode prevents SQLite sidecar writes and assumes the file will
not change. **Do not use this command against a live database.** The CLI rejects
any `-wal`, `-shm` or `-journal` sidecar, even an empty one. Do not delete sidecars
to satisfy the check; create a consistent standalone backup using the procedure
in [operations](../operations.md#back-up-and-migrate). Copying only a live `.db`
file can lose committed WAL contents. Lack of sidecars does not prove that a file
is safe to read as immutable; keeping writers stopped is the caller's responsibility.

The audit does not load credentials, run the pipeline, contact portals or models,
fetch/hash images, inspect copied post-merge caches, modify the database/cache,
merge homes, or change any production configuration. Its only application output
is the requested JSON file. The `-B` invocation also disables Python bytecode writes.

## Selection and limits

- Homes load in canonical ID order. Invalid canonical metadata is skipped and
  counted. All lifecycle states are included; this is not an availability check.
- Only different canonical homes are paired. Members linked to one home are never
  compared against each other; unlinked/dangling source listings are excluded.
  Every linked member is retained, including repeated platforms and the
  `is_backfilled` flag. Homes without membership rows still use their canonical
  platform. Membership does not independently corroborate identity.
- The existing pure `calculate_match_score` runs with no image hashes. Candidates
  need equal bedroom counts, positive prices with a nonzero graduated price score,
  and either a full-postcode score, a coordinate score, or both street and outcode
  scores. With current scoring constants, this means price difference below 6%
  of the pair's average and coordinate separation below 100 m when relying on
  coordinates. Exact street/outcode or full-postcode agreement can qualify a pair
  despite distant coordinates. The audit does not use `is_match` or `confidence`.
- At most `max_comparisons` unordered home pairs are examined, including pairs
  rejected by the metadata gates. The best `max_pairs` among that prefix are
  retained by raw score, with canonical IDs breaking ties. This is deterministic,
  not a random sample or a globally best ranking when truncated.
- Defaults are 1,000 homes, 100,000 comparisons and 100 output pairs. Hard ceilings
  are 5,000 homes, 1,000,000 comparisons and 1,000 output pairs; each must be positive.
  `homes_truncated`, `comparisons_truncated`, and `candidates_truncated` show which
  bounds affected the result. Counts describe the scanned subset, not the database.

## Interpretation

Every pair is labelled **identity unconfirmed** and **score uncalibrated**. The
score is a sum of correlated metadata signals, not a probability. Different
flats in one building can share price, bedroom count, postcode, street and pin,
and therefore score highly. Missing or stale canonical metadata and the bounded
scan can also hide true duplicates. An empty report does not establish that the
database has no duplicates, and this tool cannot detect incorrect within-home
merges.

Independently review original source evidence before proposing an identity
change. Post-merge gallery directories can contain copies from absorbed sources;
comparing those copies would not supply independent evidence. This command
intentionally supplies no image evidence, model verdict, merge action, precision
or recall estimate. Paid trials and any subsequent identity changes require a
separate workflow.
