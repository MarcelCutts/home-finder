# Operating Home Finder

This is a small rental-search service with one owner's preferences, shortlist and
Telegram identity. It is not a multi-tenant service. Collection, assessment,
dashboard actions and delivery share one authoritative SQLite database and image
cache. Do not replace a live database with a file from another collector.

## Start with the dashboard

Install the locked dependencies with `uv sync --locked --all-extras`. Configure a
random ASCII `HOME_FINDER_WEB_OWNER_PASSWORD` of at least 24 characters. Hosted
access requires HTTPS and the canonical `HOME_FINDER_WEB_BASE_URL`. Configure the
webhook secret, Telegram chat and owner user before accepting callbacks; group
chats require an explicit owner user ID. Start with:

```bash
uv run home-finder --serve --no-pipeline
```

`--serve` alone also starts only the dashboard. Use `--serve --with-pipeline`
to explicitly activate recurring collection on an appropriately configured host.
Scheduler modifiers require `--serve`; pipeline, maintenance and serving modes
cannot be combined. Reanalysis modifiers (`--all`, `--outcode`, `--request-only`)
require `--reanalyze`.

Verify that anonymous dashboard/assets/diagnostics requests are rejected, valid
owner credentials work, and cross-origin mutations are rejected. `/health` is
liveness only. Never put a password in a URL or commit a filled `.env`.

The `dashboard` and `collector` Docker targets share the locked runtime and run
as an unprivileged user. They differ in their startup commands: the dashboard
disables the pipeline. The Fly configuration selects that dashboard command;
activating collection or moving the authority is a separate operational decision.

## Back up and migrate

The core repairs added retained listing text and paid-attempt accounting in
schemas 8–9. The focused repair stack adds image provenance (10), visual
checkpoints (11) and bounded delivery records (12). Earlier schemas migrate
sequentially. Existing property IDs, owner status, notes,
Telegram update IDs, images and history stay in place. Older binaries refuse a
newer schema, so rollback requires a verified backup, not an older binary pointed
at the upgraded file.

Before activation, use the new version's backup command against the old database.
It opens the source read-only and uses SQLite's backup API, including live WAL
contents. Choose a new private destination each time:

```bash
uv run python -m home_finder.pipeline.backup create \
  --database data/properties.db --assets data/image_cache \
  --destination /tmp/home-finder-backup
uv run python -m home_finder.pipeline.backup verify \
  --package /tmp/home-finder-backup
uv run python -m home_finder.pipeline.backup restore \
  --package /tmp/home-finder-backup --destination /tmp/home-finder-restored
```

Omit `--assets` only when intentionally excluding the image cache. A database
snapshot is consistent; assets are copied across a time window and are not an
atomic database/filesystem snapshot. `--frozen` is only for an offline immutable
file with no nonempty WAL or rollback journal. Never delete sidecars to make a
check pass. Backups refuse overwritten destinations, unsafe asset paths, invalid
inventories and excessive size/time bounds. Restore writes a new isolated
location and does not switch production. Rehearse the upgrade there, inspect
owner state, history and images, then stop writers before an authority switch.
A pre-upgrade backup does not contain owner decisions made afterward; fence writes
during cutover, and reconcile any later writes before choosing a rollback copy.

`scripts/sync-to-fly.sh` intentionally fails: its former file replacement could
erase remote owner decisions and lose live journal contents.

For a bounded metadata review of possible duplicate canonical homes, including
groups sharing listing platforms, see the [offline duplicate-candidate audit](audits/offline-duplicate-candidates.md).
It produces a private review queue from a standalone snapshot without changing
home identities or using image/model evidence.

## Paid analysis

An API key alone does not authorize paid work. Set exact decimal-string caps in
`HOME_FINDER_ANALYSIS_SPEND_POLICY`; the commented `.env.example` authorizes zero.
Choose per-call, daily and monthly amounts and a concurrency limit. Optional
`lifetime_usd` and `max_total_attempts` bound a one-off exercise. USD caps describe
provider usage for the supported global standard Sonnet 4.6 and Sonnet 5.5 paths;
they do not cover other billing accounts or independently issued requests.

Historical or explicit profiles without an input limit reserve the full context
and can exceed $6 per attempt. A $1 per-call cap defers such a request. Per-phase
`input_token_limit` values enable conservative admission of supported text/static
image payloads. For Sonnet 4.6, setting both phases to 140,000 input tokens and
8,192 maximum output tokens reserves $0.96288 **per attempt**; it does not guarantee
a complete property fits a $1 daily cap. There are two phases and potentially a
retry. Unsupported/oversized payloads are deferred. These are local admission
bounds, not a provider-enforced dollar cap. Actual settled usage releases unused
reservation; a bound violation stops new dispatch for reconciliation.

### Runtime model defaults

When `HOME_FINDER_ANALYSIS_CONFIG` is absent, new Settings use Sonnet 5.5 in both
phases at low effort, 8,192 maximum output tokens and zero SDK retries. Visual
reasoning follows `HOME_FINDER_ENABLE_EXTENDED_THINKING` (adaptive by default);
evaluation uses the lowest thinking mode. The existing gallery setting remains
20 by default and still controls image selection.

The new runtime factory sets the visual input allowance to 110,000 tokens plus
5,500 for each configured image and one spare floorplan slot. This covers the
current text/schema/framing and image payloads in offline fixtures across all
supported image counts. The actual serialized request must still fit the bound;
unusually large text, including Unicode-heavy payloads, can defer before dispatch.
Evaluation has a 140,000-token input allowance. At 20 images the reservations
are **$0.98392 visual** and **$0.64192 evaluation per attempt**. At 8 images the
visual reservation is $0.71992. These reservations are not predicted charges or
new spend authorization. Explicit caps are still required; repeated application
attempts remain subject to the existing durable attempt and budget limits.

The runtime factory is separate from versioned `AnalysisConfig` parsing. Saved
JSON, provenance and operation/checkpoint identities are not rewritten. New-run
phase JSON merges onto the current runtime profile: `{}` retains the defaults,
and `{"visual":{"effort":"medium"}}` changes only visual effort. An omitted model
therefore stays Sonnet 5.5; an omitted input bound retains the configured image-based
allowance. Top-level JSON `null` is rejected with a configuration error. To disable
analysis, use `HOME_FINDER_ENABLE_QUALITY_FILTER=false` instead.

An explicit phase field overrides its runtime default, including reasoning, model
or input bound. An explicitly null `input_token_limit` deliberately requests the
legacy full-context allowance and can raise a new reservation above $4. Historical
`AnalysisConfig` decoding retains its original defaults, independently of Settings.
To pin the complete new 20-image runtime profile against later default changes:

```dotenv
HOME_FINDER_ANALYSIS_CONFIG={"version":1,"visual":{"model":"claude-sonnet-5-5","reasoning":"adaptive","effort":"low","input_token_limit":225500,"max_output_tokens":8192,"sdk_retries":0},"evaluation":{"model":"claude-sonnet-5-5","reasoning":"disabled","effort":"low","input_token_limit":140000,"max_output_tokens":8192,"sdk_retries":0}}
```

Legacy reservations and stored rate-card versions are unchanged; each attempt
stores its own model and complete frozen tariff. Reservations include the highest
cache-write rate and all possible output tokens.

Published global standard prices, checked 10 October 2026, in USD per million tokens:

| Model | Input | Output (including thinking) | 5m cache write | 1h cache write | Cache read |
|---|---:|---:|---:|---:|---:|
| Sonnet 4.6 | 3 | 15 | 3.75 | 6 | 0.30 |
| Sonnet 5.5 | 2 | 10 | 2.50 | 4 | 0.10 |

Source: [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing).
Lower token prices do not guarantee a cheaper assessment: Sonnet 5.5's tokenizer
can produce about 30% more text tokens and uses the higher-resolution image tier.
The existing input policy reserves twice the serialized text byte count plus
framing, and 5,000 tokens per static image, covering the documented 4,784-token
maximum. [Vision limits](https://platform.claude.com/docs/en/build-with-claude/vision).

On Sonnet 5.5, the application setting `reasoning: "disabled"` sends
`thinking: {"type": "between_tools"}` (no up-front thinking); adaptive reasoning
remains available. Visual requests always use `tool_choice: {"type": "auto"}`.
New-run phase JSON inherits `low` effort when omitted. Historical phase decoding
retains its original model-dependent defaults. Supported overrides are `low`, `medium`, and
`high`. No thinking blocks are replayed between these independent
single-turn phases. [Migration guide](https://platform.claude.com/docs/en/models/sonnet-5-5/migration-guide).

The visual phase retains its existing non-strict tool schema and validates the
returned fields locally. Missing, duplicate or invalid tool output is a billed
failure, not a completed assessment or reusable checkpoint. Evaluation retains
its JSON schema and local validation. A visual model change invalidates its
checkpoint; changing only the evaluation model can reuse validated observations.
The runtime profile has offline SDK coverage; operating compatibility does not
establish better assessment quality. Larger quality checks still gate rollout.
Regional/priority
pricing, batch, fast mode, server tools, fallback models, and unknown billing
units remain unsupported and fail closed.

### Accounting and recovery

Provider/account errors leave homes pending rather than spending their output
failure allowances. Proven uncharged rejections remain in the audit but do not
consume the paid-operation retry cap. Timeouts/disconnects may have been billed,
so unknown attempts continue holding capacity. Inspect without loading secrets
or running migrations:

```bash
uv run python -m home_finder.pipeline.spend_report --database data/properties.db
uv run python -m home_finder.pipeline.spend_admin --help
```

Reconcile uncertain charges only against provider billing evidence. Only a
reservation that was never dispatched can be released without that evidence.
Stop the runner before operator reconciliation. Do not erase accounting rows to
re-enable calls. Offline tests establish code contracts, not live model quality.

Successful visual observations are checkpointed in the same transaction as their
usage receipt. Recovery reuses them only when the actual ordered image bytes,
rendered visual prompt, model, schema and relevant configuration match. Evaluation
still passes normal spending admission, including uncertain-charge holds and its
attempt limit. A changed evaluation configuration alone need not repeat vision.
The matching checkpoint is consumed atomically when assessment completes; paid
attempts remain. There is at most one checkpoint per property, rather than a
history of model responses. Changed visual inputs replace it only after success.

The spending report includes checkpoint identifiers and creation time, without
observations or images. Follow `visual_checkpoint_saved`, `visual_checkpoint_loaded`,
`visual_checkpoint_input_changed` and `visual_checkpoint_reused` events alongside
paid-attempt logs. An invalid receipt/payload or unavailable ledger defers work;
it must not silently trigger another paid call. Migration 11 adds this state;
rollback requires the verified pre-upgrade database, including all accounting.

## Collection and delivery

Qualify permitted source access before live collection. A challenge, parser
failure or unknown availability never means an available advert or a successful
empty search. Each accepted detail page must match the requested portal advert.
Fresh search receipts are persisted before downstream filtering; current detail
text is saved before optional media work. Historical text has no invented fetch
date. Page/rate caps still bound collection; a page of known IDs does not end the
normal scrape because unseen work may follow it.

Collection preflight rejects unknown borough names before constructing scrapers.
An explicitly empty `HOME_FINDER_SEARCH_AREAS` still disables collection. Outcodes
are checked for syntax; borough coverage uses approximate outcode sets, not exact
administrative boundaries. A failed request stops pagination after the source's
own retry budget. Stored scraper metrics distinguish recognized challenge pages,
unrecognized layouts, invalid records and exhausted transport failures. An
unexpected page callback exception is recorded as `page_error` without guessing
whether transport or parsing caused it. Valid listings from a partial page remain
available even when the run is unhealthy.


One supported active layout is a single Linux host with the locked runtime
(`uv sync --locked --no-dev`), the owner dashboard and one database:

```bash
HOME_FINDER_DATABASE_PATH=/srv/home-finder/data/properties.db \
HOME_FINDER_WEB_HOST=127.0.0.1 uv run home-finder --serve
```

Only run that command after source, notification and paid-analysis settings have
been qualified. The serve process owns both the dashboard and its scheduled
pipeline; a configured HTTPS reverse proxy can expose the authenticated dashboard.
The current Fly dashboard stays collection-inactive until an operator deliberately
chooses and verifies a co-located collector layout or moves the authority. Running
a separate local collector and copying its DB to Fly is not a supported layout.

Use one application worker and one scheduler for an authority. The runner lock
excludes overlapping local processes. The serve loop waits from completion,
with configured jitter; do not combine it with cron. Running `--dry-run` suppresses
Telegram delivery, but can still fetch portals and spend on enabled analysis.
For offline verification use the test suite, not a dry run with real credentials.

Commute failures remain unknown and advisory. Budget panels show supported costs
and explicit missing evidence; paid optional bills packages are not rent-inclusive.
Duplicate staging rows can merge only before user, delivery, analysis or linked
history exists. Saved owner decisions must not be reassigned automatically.

## Delivery recovery

The live pipeline stores discovery/price-revision intent with the readiness
transition, then fixes the recipient and intended parts before its first send.
Each acknowledged photo, album, follow-up or venue is saved before proceeding.
Recovery uses that saved plan. Rate limits persist a retry time for the recipient;
timeouts, interruption or a lost receipt stop for review. Telegram has no send
idempotency parameter that would establish exactly-once delivery after response
loss. [Bot API receipts and parameters](https://core.telegram.org/bots/api#sendmediagroup).

A completed discovery updates sent state with its final receipt. A price alert
marks only its own current revision delivered. Reassessment never resets sent
state. Initial intent without images can pick up newly enriched images before a
plan exists; after planning, missing/changed cached files require repair and an
explicit retry. Remote URL media remain dependent on their source host.

Migration holds untracked legacy pending/failed outcomes for review, preserves
old sent homes, and creates no historical Telegram IDs. Do not bulk-reset these
records. Owner-only `/diagnostics` and this read-only command expose bounded
metadata, acknowledged IDs, retry timing and reasons without loading secrets,
running migrations or sending anything:

```bash
uv run python -m home_finder.pipeline.delivery_admin --database data/properties.db
uv run python -m home_finder.pipeline.delivery_admin --help
```

Stop the runner before reconciling one exact `event_key`. Use `--delivery KEY`
and a short non-secret `--evidence-ref REF` with exactly one action:

- `--retry-confirmed-undelivered`: only after checking that the current part was
  not delivered, or correcting a known rejection/asset problem. Earlier receipts
  remain; only the unfinished part can retry. For legacy records this explicitly
  accepts starting a new plan after owner review.
- `--message-ids 123,124,125`: acknowledge the current uncertain part using IDs
  actually observed in the configured chat (all album IDs are required).
- `--suppress`: stop the remaining delivery without fabricating message receipts
  or a successful send. The record remains available for review.

These actions acquire the application runner lock and never themselves send.
Changing the bot/chat cannot reuse another recipient's receipts. Multi-user
routing is outside the owner workflow. Logs include `notification_delivery_planned`,
`notification_part_dispatched`, `notification_part_acknowledged`,
`notification_part_waiting`, `notification_part_uncertain`,
`notification_part_blocked`, `notification_delivery_deferred` and
`notification_delivery_complete`. Correlate their delivery/property keys and part
indices; acknowledgements include message IDs and elapsed time. No exception
body, payload, image bytes or bot token is logged by this recovery path.

## Release

Main requires current lint, formatting, type, test/coverage and build checks;
administrators are subject to the gate. The manual **Release dashboard** workflow
checks the selected main revision and uses the main-only `production` environment.
An operator must configure its `FLY_PRODUCTION_API_TOKEN` before releasing.
No release, source qualification or paid model exercise is triggered by a PR
merge. Verify owner access and the migrated state after any authorized deployment.
