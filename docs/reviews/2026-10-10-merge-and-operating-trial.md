# Reliability merge review and operating trial

Home Finder is an owner-facing rental discovery service with a later path to a
small hosted service. The immediate qualification is whether useful homes survive
partial failures, evidence stays honest across persistence, paid work resumes
without unnecessary repetition, and notifications have explicit delivery state.
The operating trial uses a separate database and a cumulative US$25 model budget.
It does not activate the deployed scheduler or replace the production authority.

## Fresh review decisions

| Change | Purpose | Fresh review finding and disposition |
| --- | --- | --- |
| #44 CI | Complete the normal hosted suite without weakening its acceptance gates. | No new defect found. Preserve coverage, network blocking, Hypothesis profile and manual release. Merged as `4564fcb`. |
| #45 Visibility | Let the owner inspect pending/image-less homes while keeping explicit filters truthful. | Fallback values could match quality filters; unknown outdoor space could mean no outdoor space. `9e7e4da` requires assessment evidence and explicit negative outdoor evidence, while source floor area remains usable. 752 web/database tests passed. |
| #46 Floorplans | Preserve source designation and avoid turning a gallery heuristic into certainty. | Bulk pipeline writers discarded origin, which also changed prompts after restart. `c13746b` centralizes image upserts inside the caller's transaction. 455 database/enrichment/upgrade tests passed, including eight new bulk/restart/refresh cases. |
| #47 Checkpoints | Retain a validated successful visual result while evaluation recovers. | A confirmed charge could invalidate a retained successful provider receipt. `8a8da3e` accepts explicit reconciliation only with the retained validated successful provider receipt; `4e0a237` tests real restart reconstruction for declared plans and sent reanalysis. 155 integrated recovery/spending tests passed. |
| #48 Delivery | Freeze intended parts, persist receipts and resume only known unfinished work. | A price drop during partial discovery could lose its known-unsent status and enter historical review. `2b64b39` persists revision intent atomically, gates dispatch until discovery completes, and preserves immutable receipts/latest-revision semantics. 680 notification/database/pipeline tests passed. |

Independent review used fictional data, real storage and SDK boundaries, and
mocked external responses. It reconstructed queue items after closing/reopening
storage; reusing the original in-memory object had concealed the provenance
failure. The earlier green suites were useful but did not cover these boundaries.

## Patterns that fit this application

| Pattern and primary source | Application decision |
| --- | --- |
| [Transactional outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html) | Persist business state and delivery intent in one SQLite transaction. Dispatch only committed work. A receipt can advance a known part; an ambiguous remote outcome remains a review hold. A broker is unnecessary for the current topology. |
| [Bounded retries and jitter](https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/) | Keep retry ownership explicit. Do not multiply SDK, phase and whole-pipeline retries. Stop on ambiguous paid/send outcomes; preserve known rate delays. Raise sample size gradually while watching latency, rejection and retry counts. |
| [Airflow task practices](https://airflow.apache.org/docs/apache-airflow/stable/best-practices.html#creating-a-task) | Make repeated work depend on identified inputs and committed results. Content identities and transaction-scoped upserts serve that purpose here. Test actual reconstructed state, including image order and provenance. This does not require adopting Airflow. |
| [Scrapy job persistence](https://docs.scrapy.org/en/latest/topics/jobs.html) | Isolate each crawl's browser/request storage. Do not confuse visited-request caches with durable business outcomes. Treat clean restart and interrupted dispatch as different cases. |
| [Scrapy AutoThrottle](https://docs.scrapy.org/en/latest/topics/autothrottle.html) | Observe per-source failures and concurrency. Fast error responses must not accelerate collection. Retain modest existing throttle/concurrency bounds and stop repeatedly blocked sources; an adaptive throttle is a later measured improvement if fixed bounds prove inadequate. |
| [SQLite WAL](https://www.sqlite.org/wal.html#concurrency) | Keep transactions short, preserve one host/authority and the runner lock, and inspect database wait time. Concurrent readers do not imply concurrent writers. A move to another database needs measured contention or topology requirements. |

These are design judgments for Home Finder, informed by the cited patterns.
The operating evidence must establish whether the existing implementation meets
them. New workflow infrastructure, observation journals and multi-user routing
remain separate decisions.

Telegram's [album method](https://core.telegram.org/bots/api#sendmediagroup)
returns sent message objects, and its
[error parameters](https://core.telegram.org/bots/api#responseparameters) can supply
a retry delay. The documented send parameters do not provide an idempotency key.
The resulting design judgment is to persist acknowledged parts and hold ambiguous
outcomes for review; this application does not promise exactly-once delivery.

## Sonnet qualification

Anthropic's [Sonnet 5.5 specification](https://platform.claude.com/docs/en/models/sonnet-5-5/overview)
and [pricing](https://platform.claude.com/docs/en/about-claude/pricing) list
`claude-sonnet-5-5` at US$2/US$10 per million input/output tokens, with cache writes
at US$2.50 for five minutes or US$4 for one hour, and cache reads at US$0.10,
all per million tokens. The configured account returned the model
successfully from the non-generating Models API. Subsequent live runs qualified
generation, native usage settlement and checkpoint consumption. Neither check
establishes the provider's remaining credit balance.

The [migration guide](https://platform.claude.com/docs/en/models/sonnet-5-5/migration-guide)
requires changes at this application's boundary: forced tool selection is rejected,
`between_tools` replaces disabled thinking, and the image/token reservation must
cover the newer model. PR #49 adds this explicit adapter and preserves old
provenance and frozen rate cards. Its native contracts passed offline checks before
the live canary. PR #55 selects 5.5 for new runtime settings after those trials;
historical configuration parsing remains unchanged.

## Trial gates

1. Verify every final PR head and the complete stack with required hosted checks,
   then land the combined stack through #48. Preserve individual PR commits and
   their review boundaries. Qualify the separate Sonnet adapter in #49, then update local main. Preserve unrelated files, dependency PRs and production data.
2. Use `data/operating-trials/2026-10-10/properties.db` for every paid trial. The
   lifetime allowance is US$25 including held reservations; changing sample size
   or run date cannot reset it. Preserve unresolved attempts.
3. Begin with one assessment, then several, then target 50 and 100 selected
   listings across sources if previous results and remaining budget justify it.
   Distinguish discovered observations, selected listings, grouped homes and
   completed assessments in the results.
4. Exercise repeat discovery, pending recovery and cross-source grouping. Review
   actual model outputs against retained source text and a small image sample.
5. Retain JSON logs, heartbeat cost/hold counts, attempt receipts and before/after
   database audits. Check foreign keys, integrity, group memberships, floorplan
   origin, checkpoint consumption, terminal outcomes and cumulative cost.
6. Use Telegram fault tests for delivery qualification in this round. Live sends
   are not necessary to prioritize collection/assessment; do not claim live
   Telegram delivery has been qualified.

`--dry-run` suppresses Telegram but still collects, saves and spends. At the start
of this trial it omitted durable pipeline-run/event rows, so the runner supplied
its own identity, JSON logs and snapshots. PR #60 repairs that omission for later
runs while preserving notification suppression. Earlier runs are not backfilled
as though those application records had existed.

## Verification before live operation

The integrated repair tree `292abd1` passed 3,134 default offline tests at
88.85% branch-inclusive total coverage, with sockets blocked; Ruff, formatting, strict mypy and
wheel/sdist builds passed. The one warning was an attempted DNS lookup blocked by
pytest-socket in an existing cross-run integration test. These checks exercise
faults and persistence but do not establish current portal or provider health.

The separate Sonnet implementation `00b09b7` received an independent accept
review after 231 locked-runtime tests and 89 minimum-SDK tests, plus additional
base-identity and invalid-usage probes. After integration with the reviewed
repairs, 207 focused native-transport/accounting/checkpoint tests passed. The final hosted heads for #49–52 all passed; their merge is `22c9205`. The post-merge main run also passed 3,366 tests (89.24% branch-inclusive total coverage), with lint, typing and build green.

The retained trial runner is
`data/operating-trials/2026-10-10/runner-instrumented.py` (private, ignored from Git).
Every run retains the exact runner bytes in its own directory and
records its hash, exact application commit, Python version, clean-tree status,
redacted effective settings and budget in `plan.json`. It runs the real pipeline,
not a simulated entry point. Each label owns its crawl cache and output directory;
the shared database and image cache preserve recovery/grouping/accounting across
labels. The initial baseline uses 4.6 while the separate modern adapter completes
its protected checks; subsequent model qualification uses 5.5.

The configured operating scope is £1,800–£2,250, 1–2 bedrooms, destination
N1 5AA, a 40-minute advisory limit, cycling/public transport, and the existing
floorplan-or-photo eligibility gate. These runs measure journeys at their request
time; they are not a reproducible weekday commuting benchmark. Telegram is
suppressed throughout. No credentials or model response bodies are added to Git.

## Baseline observation and defects

On merged main `468bccd`, `baseline-46-01` observed 13 Rightmove listings,
selected/enriched three and completed one 4.6 assessment through two settled
requests. Both travel modes returned measurements for all three homes. Cost was
$0.110730750 with zero holds; integrity/FKs passed. Two homes remain queued.

The crawl stalled for 133.7 seconds with high CPU despite a single page. A plain
GET and local parsing were fast. Independent localhost replay reproduced an
empty-queue polling loop; the lifecycle also swallows cancellation and SIGINT.
The attempted graceful stop therefore ended crawling but allowed the already
authorized pipeline to proceed into its bounded model assessment. This run is
useful evidence, not a clean unattended success. PR #50 repaired this lifecycle after the localhost reproduction.

Independent review of the retained output found that maximum room dimensions
were treated as usable rectangular areas: 60.1 m² of named rooms exceeds the
printed 52.6 m² whole-flat total. It also found unsupported certainty about bills,
heating, integrated appliances and street frontage. Useful visual observations
and viewing questions remain, but the score cannot be treated as independently
validated. These findings justify a narrow consistency/uncertainty repair before
broad assessment qualification. The original evidence snapshot is retained so
later reanalysis cannot erase the baseline.

Browser verification on isolated real data confirmed three visible homes, two
clearly pending, and one result after applying an explicit minimum fit score.
Anonymous dashboard requests return 401; health returns 200. Map display is
unavailable because this trial has no configured browser tile key; the grid and
external location links remain available. Browser tests use temporary local owner
credentials and neither schedule collection nor register a Telegram webhook.

## Repairs from the first operating probes

The original reliability stack #44–48 is merged at `468bccd`. Review preserved
every component commit and verified each final head before the combined landing;
no required checks were bypassed. All final heads #49–52 passed required hosted checks before their combined merge
at `22c9205`. The separate model adapter #49 passed the full
local suite (3,216 tests; 88.90% branch-inclusive total coverage). Its first hosted run exposed
four MagicMock usage fixtures that were corrected to native SDK Usage values;
accounting validation was not weakened.

The source repair #50 replaces Rightmove's Crawlee search wrapper with an
area-scoped HTTPX client and preserves application cancellation. The known
[Crawlee queue-loop issue](https://github.com/apify/crawlee-python/issues/1598)
aligns with the localhost reproduction, but the cancellation fault required its
own probe. Whole-request deadlines supplement HTTPX inactivity timeouts. Final
review also stopped terminal errors multiplying across pages and blocked areas.
A bounded live unpaid probe completed in 0.526 seconds: one HTTP 200 page, 22
unique observations, three selected, no retries or parse errors. It does not
substitute for the full pipeline run.

The evidence repair #51 distinguishes printed totals and confirmed rectangular
areas, reconciles incompatible arithmetic before evaluation, and applies the
same checks to reused visual checkpoints. Raw paid evidence remains intact.
Silent bills information becomes unknown; both prompts qualify claims about
hidden appliances, heating, fitted furniture and frontage. This is consistency
checking and better uncertainty handling, not independent measurement. Both
floorplan views now use white image backgrounds. A 390×844 local browser check
confirmed transparent labels are readable. Real-data screenshots remain private
local artifacts after automatic approval review rejected publishing them.

The integrated #49–51 tree passed 3,269 default tests at 89.03% branch-inclusive total coverage.
Nine warnings were blocked socket/DNS attempts in existing commute/cross-run
integration fixtures; no test had external network access. A signal-probe fixture
was fixed to delegate non-SIGINT handlers so it works with pytest's default
signal timeout, rather than requiring a special test mode.

An unpaid OnTheMarket discovery attempt initially reported two healthy empty
areas. The saved search payload contained 11 records under
`props.pageProps.initialReduxState`; the old parser defaulted a missing legacy
path to an empty list. A retained detail page has the same moved envelope. PR #52 covers search and enrichment, distinguishes valid empty results from
missing/malformed data, retains valid partial observations and bounds blocked
requests. Next.js [documents that page props are present in initial HTML](https://nextjs.org/docs/pages/building-your-application/data-fetching/get-server-side-props);
the exact source contract is established by these retained responses, not by a
generic framework guarantee.

## Unpaid source qualification on the reviewed candidate

After the final candidate `63a5761` passed 3,366 default local tests at 89.24%
branch-inclusive total coverage, two bounded runs exercised real collection, enrichment, commute
and grouping with native paid admission disabled. Hosted merge checks were still
running, so these are explicitly candidate runs, not merged-main paid trials.

| Run | Selected | Newly enriched | New canonical homes | Newly formed cross-source groups | Duration | Model spend |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `repaired-discovery-10` | 10 | 6 | 4 | 2, including one old anchor update | 12.099 s | $0 |
| `repaired-discovery-50` | 50 | 42 | 31 | 11 | 77.009 s | $0 |

The first run observed 23 unique source adverts; one selected out-of-area advert
was correctly rejected. Both commute modes returned usable measurements for all
new candidates in both runs. All 300 recorded images were present on disk, all 22
dedicated plans were source-declared, and database/FK and spending checks passed.
The 38 retained homes include 13 two-source groups. The native spending guard
deferred queued work without admitting requests; lifetime spending remained
$0.110730750, with zero holds.

Independent review of the first 10 selections verified both accepted pairs using
original photos, plan/text/location evidence and eight matching gallery images
per pair. All 20 selected cross-source combinations were considered; no missed
match was demonstrated. Nearby same-street homes remained correctly separate.
Promising pairs seen only outside the selection are unqualified candidates, not
false negatives. The larger review follows separately.

The dedup summary's `multi_source_count` counts genuinely new homes only; the
updated old anchor is recorded separately. The trial report uses actual persisted
source memberships to avoid undercounting. Image-cache audits also distinguish
original source evidence and copied merged assets from final database image rows;
extra files are not proof of a false merge. The 476 `thumb_` files are derived
thumbnails; no cleanup was performed.

A read-only SQLite backup plus asset capture and isolated restore passed using the
application's backup CLI. All 996 assets (60,225,077 bytes) and logical database
inventories matched; schema 12 had zero FK violations. The restored database hash
was `a84fa4f750905a418d92d5adb9b19310f5e684dfc8bd4feda58ab7284178a219`.
The restore remains a verification copy and never admits work. This validates the
trial backup, not current production access or a production cutover.

The first assessed home acquired a second source during discovery. The subsequent
5.5 canary therefore tests the repaired model boundary, prompts and enriched inputs
together; it is not a controlled model-only comparison against the 4.6 baseline.

## Deduplication limit exposed by larger evidence

The larger visual review found a high-confidence missed duplicate represented by
two separate two-portal groups. Different agents used different camera positions
and room dressing for the same distinctive kitchen/rooms. Original photos share
appliance labels, fixtures and tile details, and both plans share the unusual
second-floor topology. Plans still disagree slightly on dimensions and room
labels; these remain source claims rather than independently measured facts.

Two existing policies explain the miss: a zero-pHash-match veto and one advert
per platform in a group. The latter also exists in `MergedProperty.source_urls`,
row reconstruction and donor-retirement protections. Removing the guard alone
could lose individual URLs/evidence. The immediate improvement is a bounded
read-only candidate audit, followed by an explicit multi-advert membership design
if the owner workflow needs confirmed coalescing. Automatic threshold relaxation
is not supported by the adjudicated positives and nearby negatives. The expanded
review below found five likely missed clusters, reinforcing the representation
problem without supplying a calibrated automatic merge threshold.

[ImageHash's own rationale](https://github.com/JohannesBuchner/imagehash) concerns
images that look nearly identical; a photograph comparison is not a physical-home
identity test. [Splink distinguishes linking datasets from deduplicating within
them](https://moj-analytical-services.github.io/splink/topic_guides/splink_fundamentals/link_type.html).
The application currently handles the former more completely. These distinctions
inform the proposed design; adopting either a new classifier or Splink itself is
not required. No real listings were merged, deleted or marked by the audit.


## Larger collection and current-source checks

The merged-main `repaired-discovery-100` run selected 100 adverts (50 per portal),
rejected four out-of-area results, and enriched all 67 newly eligible adverts
without an enrichment failure. Together with 38 previous anchors, these produced
89 canonical records: 51 new records and 16 new two-source groups. Duration was
115.822 seconds, with zero paid requests, failed pages or parse errors. Source
observations total 128; 118 adverts link to canonical records. All 67 cycling
outcomes were measured within the advisory limit; public transport returned 65
measured outcomes and two unknowns. The aggregate logs do not distinguish the
unknowns' individual reason, so they must not be described as measured journeys
above the limit or as confirmed provider errors.

The small `other-sources-discovery-10` probe selected five OpenRent and five Zoopla
adverts from 45 observations. Six remained after common filters and all six
were enriched; all received measured journeys in both modes. Two adverts joined
existing homes and four new canonical records were retained. It finished in
25.392 seconds without paid requests or failed pages. The combined collection
has 93 canonical rows and 31 two-source groups. Two rows are deliberately dropped
by the existing eligibility policy; dropped records are not pending assessments.

These are canonical-record counts, not a count of unique physical flats. The
independent review inspected all 16 newly accepted Rightmove/OTM pairs and both
new Rightmove/Zoopla pairs using original source images, carrying forward the
13 previously reviewed unchanged pairs. Every accepted pair had corroborating
interior evidence; none was shown incorrect. The larger audit considered 6,903
unordered source-advert combinations, but did not visually adjudicate all of
them, so it does not establish overall precision or recall.

Five disjoint, high-confidence missed identity clusters contain 18 adverts in
13 canonical records. If these judgments are adopted, they imply at least eight
extra canonical records. An exterior-only same-building candidate remains
unresolved and is excluded from that count. Differing floorplans/room geometry
also supplied useful nearby negatives. Cross-agent camera angles, staged rooms,
photo collages and within-portal adverts explain why whole-image pHash and the
one-advert-per-platform grouping model miss these cases. Raising one global
threshold would leave representation limits intact and introduce false matches.

PR #53 adds a private offline candidate queue, not an automatic resolver. Against
the verified 38-record backup it examined all 703 pairs and returned two candidates;
the independently identified likely missed pair ranked first. The database and
cache remained untouched, and the JSON file had mode 0600. The CLI explicitly
requires a closed standalone snapshot; immutable SQLite reads must never target
an active database, even if its sidecars happen to be absent.

Against a fresh verified 93-record backup, the same CLI examined all 4,278 pairs
and returned 21 unconfirmed candidates, including 15 with overlapping platforms.
Every one of the five previously reviewed likely identity clusters appeared in
that queue; all 12 within-cluster canonical pairs were surfaced. The other nine
candidates have not been adjudicated by this check. This is recovery of selected
known cases, not a global recall/precision estimate or calibration of the score.
The backup's database hash remained unchanged and the report was created with
mode 0600. The backup also retained 2,397 assets (147,855,077 bytes), 62 settled
paid attempts, 26 assessments and zero FK violations before the evidence repairs.

A separate read-only availability probe checked seven existing URLs across all
four portals (only one linked OpenRent advert was available). All returned HTTP
200 and initially parsed as unknown. Deeper retained-page review found explicit
Zoopla `InStock` offers tied to the requested listing by `mainEntityOfPage`; the
reader only recognized `url`. Those two pages justify a narrow identity-path
repair. Publication flags, rental-channel labels and move-in dates on the other
five pages do not prove current letting availability and remain unknown. No records were removed or marked
active by this probe. The investigation also found that Rightmove's availability
reader did not understand the indexed detail envelope already supported by its
detail extractor; the focused follow-up reuses that bounded decoder while keeping
the observed generic flags unknown. The Zoopla linked-data repair requires a
target-bound RealEstateListing identity and rejects contradictory identities;
[Schema.org defines mainEntityOfPage](https://schema.org/mainEntityOfPage) as the
page for which the entity is the primary subject. The source payload, not a
generic HTTP success, supplies the explicit availability claim. Availability persistence and conflicting-source
aggregation remain covered by offline tests, not by this read-only network probe.

## Paid qualification and evidence quality

The first 5.5 canary on merged main completed both phases in 26.961 seconds for
$0.095249, preserving the printed 52.6 m² total and leaving unsupported room areas
and bills unknown. It still wrongly promoted a nearby amenities street into the
headline. Inspection showed neither model phase received the actual listing
address. PR #54 supplies bounded, escaped canonical address/postcode metadata to
both phases, explicitly as unverified source claims, while retaining conflicting
marketing text. Existing checkpoint hashing includes the rendered metadata.

The corrected candidate canary completed in 25.242 seconds for $0.096017. It kept
the location distinction in its notes and no longer asserted the amenities street
in its headline. This is evidence for this example, not proof that prompting can
eliminate unsupported claims. Independent review remains necessary for material
observations and ratings.

The next five-property batch completed ten model requests in 112.735 seconds for
$0.288019. All five new assessments persisted; successful visual checkpoints were
consumed and there were no unresolved charges. All six then-current assessments
passed the mechanical arithmetic checks. Three existing room-sum warnings were
misleading: they compared partial known room areas with the whole-flat total.
A smaller partial sum is not evidence of inconsistent arithmetic. This justified
a focused warning correction alongside the badge consistency repair.

The canary also exposed a 'Poor hosting layout' badge beside a structured 'good'
rating. The follow-up removes badges that contradict supported structured values,
including bills after canonical evidence correction. It does not invent new facts,
turn missing data into negative evidence or rewrite historical assessments.

PR #55 makes Sonnet 5.5 the default only for new runtime configurations. Historical
configuration parsing, saved provenance and frozen tariffs remain unchanged;
explicit old/partial JSON and explicit null retain their documented meaning.
The runtime profile uses low effort, 8,192 output tokens and no SDK retries.
It preserves the 20-image setting and enforces conservative payload bounds.
The profile canary used all nine available images within the 20-image allowance,
completed both phases in 22.915 seconds for $0.0616598, and released all holds.
Twenty-image payloads were exercised offline through the native SDK, rather than
claiming that this real advert contained twenty distinct input images.

The next run assessed 20 additional homes with concurrency two and an eight-image
cap. All 40 requests succeeded in 250.235 seconds for $1.17713; repeated discovery
selected 50 known adverts without creating new homes or image rows. All 26 then-
current assessments passed mechanical arithmetic/provenance checks: 19 retained
a source-printed total and seven left the total unknown. These checks establish
internal consistency, not correct interpretation of every room or photograph.
Ten partial-room-sum warnings were the same false alarm repaired in #56. Two
other warnings concerned genuinely small, source-printed whole-flat totals;
those numbers remain visible with their uncertainty rather than being altered.
The recorded 336 write scopes had approximately 2.02 ms p95 lock wait and
6.37 ms maximum wait; maximum write duration was 16.87 ms. This local,
two-worker owner trial shows no database bottleneck. It is not a capacity test
for independent hosted users or evidence for changing database topology.

An independent review then inspected all 50 linked gallery photographs and four
plans for the five newly assessed examples in the smaller batch. Printed totals
and readable dimension labels matched the four plans, while the no-plan example
kept area estimates unknown. Material defects still remained:

| Finding | Why it matters | Bounded response |
| --- | --- | --- |
| Bathroom access was classified as non-ensuite despite the visible route through a bedroom. | A valid response/schema does not establish spatial accuracy. | Add generic door-tracing guidance and a viewing check for ambiguity; retain this case in the evaluation corpus. |
| Explicit private patio evidence coexisted with four false outdoor flags. | Filtering and fit scoring could hide a useful home. | Add explicit yes/no/unknown availability, include patios/courtyards in terrace semantics, and preserve historical unknowns. |
| A bathroom photograph lay beyond the seven-gallery-plus-plan input cap. | The assessment accused the advert of missing bathroom images when the model had reviewed a subset. | Retain actual reviewed/available counts in both prompts and the stored result; suppress unsupported completeness penalties. |
| A small second room was called a dedicated office without clear evidence of a closable, non-through layout. | Bedroom count is insufficient to establish a usable separate workspace. | Require visible/source-supported room access; treat doors, daylight and ventilation as viewing questions when unclear. |
| Unknown wardrobe evidence became a negative badge; explicit bills bullets were lost during HTML normalization. | Concise labels contradicted the evidence shown beside them. | #56 applies removal-only badge consistency and preserves HTML bullet boundaries for the strict bills reader. |

The review also found an unsupported acoustic inference from a lower-ground floor
label. Construction, overhead occupancy, real noise and market-value judgments
remain advisory inferences. The application has not measured them independently.
This selected review is not a population accuracy estimate.

A controlled recheck kept the same retained advert and image limit and changed
visual effort from low to medium. Both attempts repeated the bathroom-access
error. Low effort took 21.958 seconds and $0.090995; medium took 24.142 seconds and
$0.0611478, with different cache usage. This does not justify raising effort
globally or interpreting the lower cached charge as a cheaper medium-effort model.
An offline replay of the actual image encoder returned the 542×764 RGB floorplan
byte-for-byte unchanged, so client resizing or transparency does not explain that
specific error.
Larger paid runs were held until the outdoor and photo-coverage contract repairs
passed their review gates.

### Targeted rechecks after the evidence repairs

Four bounded two-request reanalyses used the same isolated authority after #58
and #59 passed review. They retained actual gallery counts, distinguished source
area from earlier model estimates, and used the modern runtime profile. All eight
requests settled successfully, every checkpoint was consumed, and integrity,
foreign-key, reservation and no-notification checks passed.

| Retained case | Gallery images reviewed / available | Duration | Additional cost | Result and limit |
| --- | ---: | ---: | ---: | --- |
| Patio, eight-image allowance | 7 / 8, plus a plan | 23.684 s | $0.0968070 | Patio becomes explicit outdoor access. Partial-review metadata prevents typed missing-room/few-photo penalties; prose describes the subset. Separate-office certainty remains excessive. |
| Ambiguous bathroom access, twenty-image allowance | 15 / 15, plus a plan | 21.371 s | $0.0721426 | Access becomes unknown with a viewing check instead of a confident hallway assertion. Both the prompts and supplied image count changed; this is not a prompt-only comparison. |
| Explicit bills, twenty-image allowance | 7 / 7; no plan | 21.828 s | $0.0512186 | Bills remain explicitly excluded and unknown wardrobe evidence no longer creates a negative wardrobe badge. Other source/model judgments remain advisory. |
| Patio, twenty-image allowance | 8 / 8, plus a plan | 25.911 s | $0.0645166 | The previously omitted bathroom photograph is assessed; patio access remains positive. The office enum still overstates what the cautious access notes establish. |

Cumulative accounted model cost after these checks was $2.265633150 across 70
settled requests, with no outstanding hold. Full supplied-gallery review means
all retained gallery inputs were sent successfully; it does not establish that
the portal exposed every photograph, the model attended to every detail, or its
interpretation is correct. These examples support the deterministic repairs but
also show why prompt compliance and spatial ratings need a separate measured
quality corpus.

### Follow-up PR boundaries and review evidence

| PR | Purpose and final behavior | Verification and explicit limit |
| --- | --- | --- |
| #53 | Produce a bounded private duplicate-candidate queue from a closed SQLite snapshot. | Synthetic CLI checks plus immutable 38- and 93-record replays. No network, database writes, automatic identity changes or calibrated probabilities. |
| #54 | Supply canonical address/postcode context to both analysis phases. | Bounded escaped metadata and rendered-prompt identity checks; the retained canary no longer confuses an amenities street with the address. Source metadata remains unverified. |
| #55 | Use Sonnet 5.5 for newly created runtime profiles. | Native SDK, minimum-SDK/Python 3.11, frozen-history and all 1–20 image-bound tests. Explicit old configuration retains its meaning; paid policy is still required. |
| #56 | Remove contradictory badges, retain explicit bills bullets and stop false partial-room-sum alarms. | Structured-value/bills/source-boundary tests and live targeted rechecks. Guards only remove unsupported labels; they do not invent replacement facts. |
| #57 | Recognize explicit availability in current Rightmove and Zoopla envelopes. | Retained seven-page replay gives two explicit active claims and five proper unknowns. Malformed, conflicting and wrong-listing identities cannot borrow unrelated evidence. |
| #58 | Preserve yes/no/unknown outdoor access and include patios/courtyards. | Storage/filter/map/fit/UI parity, historical-unknown and positive-badge contradiction tests. Independent review's explicit-negative/positive-badge defect is fixed. Old silent flags remain unknown; raw historical assessments are not rewritten. |
| #59 | Persist application-owned supplied-gallery coverage and prevent subset omissions becoming advert defects. | Native prompt-identity, checkpoint-reuse and typed-result tests; model-estimated prior area is excluded from source-area context. All 1–20 image bounds pass after prompt compaction. Free-form ratings and misplaced prose are not guaranteed by these typed guards. |
| #60 | Record dry-run runs/events, correlated phase logs and terminal outcomes. | Combined 3,753 tests pass at 89.68% branch-inclusive coverage; 635 focused checks and static gates pass. Independent review reproduced and verified repairs for both primary-error masking and cancellation arriving during cleanup, on Python 3.11 and 3.14. |

The complete local candidate is `81dd45c392ceb0f1bdf5291cffd5f3aca98f9e73`.
Its five test warnings were blocked socket/DNS attempts, not successful network
access. Independent cancellation review covered exact repair `27c8a3c`; the
reviewed helper, pipeline, recorder and repository code is unchanged by the
integration merge. Synthetic desktop/mobile screenshots are committed with #58
and #59; real trial screenshots remain local.

The observability repair deliberately keeps existing storage tables. Durable run
rows do not identify dry/live mode; logs do. Dry runs can become the latest
diagnostic run and count toward the next live retention window. Run-level cost
totals cover returned main-analysis usage, may be incomplete after interruption,
and exclude reanalysis: the paid ledger remains authoritative. Successful phase
execution is distinct from every property having a successful assessment.

### Fifty-property operating batch

On the clean reviewed candidate `81dd45c`, `evidence-batch-50` selected 100 known
source adverts, admitted up to 50 of the 65 pending homes, and used three concurrent
model operations with a twenty-image allowance. It finished in 401.380 seconds:
31.387 seconds for acquisition and 369.979 seconds for assessment/persistence.
Discovery created no new homes, memberships or image rows, and had no failed pages
or parse errors.

Forty-eight valid assessments persisted through 96 successful model requests.
One additional visual request returned a wrong boolean type and omitted a required
field; validation rejected it and settled its known $0.036886 charge without saving
a successful assessment. Another home was deferred before dispatch because its
valid, single-frame GIF plan was supported by the image builder but excluded by
the reservation format whitelist. Its eight JPEG gallery images and GIF plan were
otherwise valid. That case incurred no model charge. The fifteen homes outside
the run cap were deliberately left queued. Seventeen homes therefore remain for
recovery; these distinct outcomes must not be reported as fifty successes.

The batch cost $3.003109600; cumulative accounting reached $5.268742750 across 167
settled requests, with zero holds, unresolved attempts or over-reservations. All
74 current assessments pass the mechanical arithmetic/provenance checks. Every
one of the 48 new assessments records complete supplied-gallery coverage of five
to eight photos; the twenty-image allowance was not exhausted. Their
outdoor claims are 20 yes, 24 unknown and four no. These counts do not validate
the factual quality of those claims.

The application now records the completed run, two scraper summaries and 346
property events. Both Telegram phases were explicitly skipped, and all 48 new
delivery intents remain pending. Integrity and foreign-key checks pass; no visual
checkpoint remains after the successful assessments. Of 747 write scopes, p95
lock wait was 2.08 ms and maximum wait 6.85 ms; maximum write duration was 18.04 ms.
These local three-worker measurements again provide no case for replacing SQLite.

The remaining format/schema problems have bounded responses. Anthropic documents
[static GIF support](https://platform.claude.com/docs/en/build-with-claude/vision);
admission can accept verified single-frame GIFs while retaining decoded MIME,
byte-size and animation restrictions. Its
[structured-output contract](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
provides strict tool inputs, subject to schema-complexity and response-stop limits.
The current visual schema has 10 optional and eight union parameters, within the
documented explicit limits of 24 and 16, and all twelve objects already prohibit
additional fields. Qualifying that option is a small response to the observed
type/missing-field failure. Local validation and stop-reason checks must remain;
schema correctness does not establish visual accuracy.

Anthropic's [vision guidance](https://platform.claude.com/docs/en/build-with-claude/vision-coordinates)
describes limits to spatial reasoning and recommends visual spot checks; small
details can also lose precision after downscaling. Its
[evaluation guide](https://platform.claude.com/docs/en/test-and-evaluate/develop-tests)
separates task fidelity, consistency, latency and cost and includes ambiguous
cases in task-specific tests. Applied here, the next quality corpus should score
material fields against retained evidence, distinguish unknown from wrong, and
include independently reviewed held-out examples. Those recommendations inform
the method; they do not validate this application's present accuracy.

Lower observed cost is partly due to prompt-cache hits. These sequential runs
also changed prompts, source coverage and image limits; their charges are not a
controlled model-only price or quality comparison. Costs below are usage-based
accounting at the frozen supported tariff, not a claim to have queried the
provider's final invoice or remaining account credit balance.

## Operational boundaries and next tasks

The operating authority is the one isolated trial database. Every paid attempt,
including reanalysis and every code revision, shares its US$25 lifetime allowance.
Restored copies are marked as rehearsals and never admit work. No trial task
replaced production data, enabled its scheduler or sent Telegram messages.
Live Telegram is deliberately unqualified in this round; the acknowledged-part,
retry-delay, partial-album and ambiguous-delivery paths have offline fault tests.

The owner dashboard passed local anonymous-denial/health checks, pending-listing
and explicit-score filtering, and desktop/mobile inspection. Floorplan transparency
is readable on white backgrounds. Real-data screenshots remain local because
automatic approval review rejected their publication. Synthetic UI evidence is
already in the repair PRs. A missing trial browser tile key limits map verification.
The deployed service's owner access, current authoritative database and fresh
production backup with assets still need a separate rollout check; the successful
trial restore is not a production cutover rehearsal against today's authority.

The application has enough recovery structure for an owner pilot: one authority,
bounded worker concurrency, attempt receipts, checkpoints and durable delivery
parts. The observed faults justify improving its adapters and evidence contracts.
They do not justify reintroducing a general event journal, broker, Temporal/Airflow
runtime, distributed leases or a multi-user routing framework.

Recommended sequential follow-ups, each with a reviewable completion gate:

1. **Owner rollout qualification.** Identify the authoritative database, fence
   writers and verify a fresh backup/restore including assets before cutover.
   Deploy a separately authorized revision/configuration with scheduling and
   sending disabled. Then verify anonymous denial on the public origin, owner
   access, mutation/callback identity and the one-worker authority. Activate only
   the separately authorized bounded operating policy, beginning with a small
   scheduled sample and review of delivery holds. This trial's $25 is not
   continuing authorization; any live Telegram trial needs its own authorization.
2. **Material assessment evaluation corpus.** Freeze original source inputs,
   supplied-image coverage, prompts/configuration and independently reviewed
   verdicts for room access, separate office suitability, outdoor access, bills
   and printed dimensions. Include positive, negative and genuinely unknown
   cases, plus held-out examples. Report field errors separately from schema,
   spend and latency success. The office enum/notes contradiction is a concrete
   starting case; do not treat prompt-only compliance as a deterministic guard or
   solve it with uncalibrated prose matching. Require measured improvement before
   another prompt/model-wide change or paid historical refresh.
3. **Multi-advert identity representation.** Document and test how one home keeps
   several adverts from one platform, including original URLs/text/image provenance.
   Migrate platform-keyed projections without losing history or changing identity
   based solely on a score. Prove old rows and existing sent/price/spend receipts
   survive reconstruction and rollback from a verified backup.
4. **Reviewed canonical coalescing.** Build a bounded operator action using frozen
   candidate evidence and explicit conflict handling. Preserve assessment history,
   owner decisions and every advert; stale evidence must invalidate approval.
   Retain audit-only behavior until transaction, restart and delivery-receipt tests
   demonstrate that coalescing cannot silently resend or discard work.
5. **Candidate recall evaluation.** Extend the offline adjudicated corpus with the
   five positive clusters, nearby negatives and unresolved exterior-only examples.
   Evaluate within-portal blocking and different-view image signals independently
   of the final merge rule. Report subgroup errors and review workload; do not
   turn a heuristic score or this selected sample into a calibrated probability.
6. **Reproducible commute scenarios and diagnostics.** Replace request-time arrival
   with an explicit weekday/time scenario and persist its identity with outcomes.
   Log transport mode and per-state counts, including unreachable/invalid/missing
   results. The [TravelTime matrix contract](https://docs.traveltime.com/api/reference/travel-time-distance-matrix)
   ties outcomes to arrival/departure time and a travel-time bound. Saturday-night
   results are useful operational evidence, not a weekday commuting benchmark.
7. **Owner ingestion.** Add manual URL ingestion through the same
   identity/enrichment/spend path if portal coverage limits the owner workflow.
   Repeated submission must find the same advert, respect pending/held work and
   retain source provenance; it must not create a parallel analysis or delivery
   path. Expose assessment uncertainty rather than treating ratings as truth.
8. **Hosted-service expansion.** Introduce user isolation before a second independent
   user. Measure database contention, queue delay and operational needs before
   choosing a different database or worker topology. Tens to hundreds of users
   remain the intended gradual destination, not a reason for a platform rewrite now.

The trial runner retains exact code/config/runner identities, JSON logs, cost
heartbeats, database snapshots and terminal audits alongside the ordinary run
records introduced by #60. Periodic stack diagnostics use `stacks.log`, keeping
JSONL events parseable. The diagnostics page does not retroactively cover trials
performed before the dry-run observability repair.
