# Anthropic vs OpenAI for the quality analysis: comparison plan

Written 2026-10-21. Prices and model facts were checked online the same day (sources in `mc/rates.py`
and the final report). The original budget was about $20 per provider, judging
included. Review found that the ledger's fixed reservations were estimates, not
hard upper bounds. See the corrected accounting limitations and offline checks
in `README.md`; qualify per-request bounds before another paid run.

## What is being compared

The only LLM workload in home-finder is the two-phase quality analysis in
`src/home_finder/filters/quality.py`:

1. **Visual**: up to 20 gallery photos plus the floorplan plus listing text go in, and structured
   observations come out (a tool schema with about 60 fields).
2. **Evaluation**: text only. Phase 1 JSON plus listing text go in, and value, viewing notes,
   highlights and the one-liner come out (a strict JSON schema).

Production today runs `claude-sonnet-5-5`, with the visual phase on adaptive thinking at `low`
effort and the evaluation phase on `between_tools` at `low`. In the 2026-10-10 operating trial this
cost about $0.062 a home.

## Fidelity: the app's own code, with the provider swapped

`mc/shim.py` stands in for `AsyncAnthropic` inside an unmodified `PropertyQualityFilter`:

- The filter builds its exact production request and the shim sends it on: to Anthropic with the
  configured model and effort, or translated to the OpenAI Responses API. The translation keeps the
  same system prompt, the same labelled image blocks and the same schema (OpenAI's is made
  strict-compatible).
- The shim returns an Anthropic-shaped message, so the app's own validation, floorplan-area
  reconciliation, badge reconciliation and merge code all run unchanged.
- Images are the app's own 1568 px resizes for every model. OpenAI gets `detail: high`, which does
  not downscale at that size.
- System prompts are cached on both providers. OpenAI uses an explicit breakpoint so the per-home
  images are not written to cache at 1.25x.

## Test set

- **Source:** the 2026-10-10 operating trial, snapshotted read-only into `data/snapshot/`. That is
  93 homes, 91 of them with photos, all with retained listing text and cached images (about 7.5
  photos a home), 56 with floorplans.
- **Sample:** a deterministic stratified order by floorplan presence and source (`mc/cases.py`).
  Every run uses a prefix of that order, so smaller runs nest inside larger ones and all
  comparisons are paired.
- **Labels:** there are no human labels. The existing reference packs are all unreviewed.

## Configurations

The arms are price-matched across providers. Prices are list prices in $/M input/output tokens.

| Price tier | Anthropic | OpenAI |
|---|---|---|
| $10 / $50 (ceiling) | Fable 5.1 | GPT-6 Astra |
| $4 / $20 | Opus 5.5 (also fast mode at 2x) | GPT-5.6 Sol |
| $2 / $10 (production tier) | **Sonnet 5.5** (low / medium / high) | GPT-6.1 Sol (low / medium / high, plus flex, fast and low-detail images), GPT-6 Sol (low / none), GPT-5.6 Terra ($2/$12) |
| $0.10 / $0.50 | Haiku 5.5 (low / medium / no thinking) | GPT-6 Luna (low / medium / none), GPT-5.6 Luna ($0.20/$1.20) |

There is also a split arm: Sonnet 5.5 for the visual phase and Haiku 5.5 for the evaluation phase.
Effort is always set explicitly. The evaluation phase runs at the lowest sensible effort on both
providers.

## Protocol

1. **Pilot:** 3 homes × all 27 arms. It checks that every arm is accepted by its API and measures
   real cost per home, to size the rest.
2. **Main sweep:** 30 homes for the production-tier and cheap-tier arms, and 10–15 homes for the
   expensive or speculative arms. Arms are interleaved home by home in a seeded random order, so
   time-of-day load and provider incidents fall evenly across them. Concurrency is 6 per provider.
3. **Repeats:** the baseline and its best OpenAI counterpart run 3 times on the same 10 homes. This
   measures run-to-run noise: field flip rate and rating spread. Any between-model difference
   smaller than that noise is not a difference.

## Metrics

**Speed**
- End-to-end seconds per home (visual plus evaluation, run sequentially as in production) and
  per-phase latency, each reported at p50 and p90.
- Output tokens per second.
- Retries are counted, and so are failures.
- Calls are non-streaming, as in production. Time to first token is not measured because the
  pipeline is a batch job.

**Cost**
- Dollars per home from each response's usage at list price: cached reads and writes, reasoning
  and thinking tokens, and processing-tier multipliers.
- Cost per *successful* home includes the spend on failed attempts.
- Runs are mostly warm-cache, which is what a batch run sees. A cold-cache figure is derived
  separately.

**Quality.** No single metric is trusted; each covers a different failure.

1. **Reliability:** the share of homes that come back as a complete, valid assessment under the
   app's own contract. Truncation, refusal, malformed output and outages all count as failures.
2. **Text-grounded facts (deterministic):** where the listing text states a fact explicitly, the
   model's raw output is checked against it. Facts covered include hob type, dishwasher, washing
   machine, furnished status, EPC, council tax, pets, floor level, outdoor space and parking. Both
   accuracy and the rate of contradicting the text are scored.
3. **Floor area:** compared against the source's own floor area where the portal publishes one.
   Scored as the share within 5% and the abstention rate.
4. **Cross-family consensus on visual fields:**
   - Silver labels come from the price-matched flagships, Opus 5.5 at medium and GPT-5.6 Sol at
     medium, wherever the two agree.
   - Every other arm is scored on about 15 categorical fields: condition, kitchen, hob, light,
     floor level, glazing, bath, outdoor, office separation and others. The overall rating is
     scored within ±1.
   - Leave-one-out majority agreement across all arms is reported as a second view.
5. **Blind pairwise judging:**
   - Two judges from different families, Opus 5.5 and GPT-5.6 Sol, each see the photos, the
     floorplan, the listing text and two anonymised, canonically rendered assessments. They give a
     per-criterion verdict on factual accuracy, calibration (honest "unknown"), usefulness for the
     owner's priorities, and overall.
   - Every pair is judged in both orders. A verdict that flips with the order counts as a tie.
   - Judges are reported separately, so any self-preference by family shows up.
   - Matchups are each candidate arm against the production baseline. The listing prefix is cached
     to keep judging affordable.

**Statistics**
- The design is paired by home. Confidence intervals use a cluster bootstrap over homes (repeats
  and judge orders resampled together).
- Win rates are reported with ties.
- With n of 30 or fewer, only large gaps (roughly 65/35 or more) are detectable. Smaller
  differences are reported as "no detectable difference", not as wins.

## Known limitations

- No human gold labels. The judges and silver labels are models and can share blind spots. The
  report lists per-field disagreements for a quick owner spot-check.
- The judges are also contestants, from the Opus 5.5 and GPT-5.6 Sol tiers. Self-preference is
  checked by comparing the two judges, not eliminated. No third-family judge was available.
- There is a single day of latency measurement, from one location.
- OpenAI's GPT-6 and GPT-5.6 IDs have no dated snapshots, so behaviour can change under the same
  name.
- Costs are estimates from usage × list price. The provider dashboards are authoritative.
