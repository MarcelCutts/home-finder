# Architectural review of the Home Finder model bake-off

21 October 2026. Application baseline: `2dc7262925c33b520993b11399b9f5869561b21b`.

**My judgment: this is a useful exploratory experiment, with stronger engineering findings than model-selection conclusions.** GPT-6.1 Sol at low effort deserves a controlled trial. The evidence does not yet justify declaring it the better rental-search system or changing the production default. The highest-value work is making factual claims dependable and measuring whether assessments improve viewing decisions. At the stated volume, a provider migration needs a quality or operational justification; saving roughly $2 per 100 homes is insufficient by itself.

I read the supplied HTML, inspected the original experiment and its saved records, checked the application on a fresh worktree, and consulted current primary documentation. I independently reproduced the validity and consensus counts for all 27 arms, recomputed the main costs from usage, replayed the proposed JSON-decoding repair, and ran the existing notification-selection function over the saved assessments. No new paid model calls were made and no application code was changed.

The original comparison worktree now also contains subsequent prompt/schema/image edits and a separate `tightened` run. Those are outside the attached experiment. This review uses the 579 `core` records and 172 saved judge verdicts; the baseline prompt hashes match their provenance. The live report in that worktree is no longer byte-identical to the attachment. This distinction matters when interpreting later improvements.

**What Opus did well.** The experiment uses actual retained homes, application prompts, validation and reconciliation. It prices failures, includes smaller models and mixed phases, interleaves arms, repeats some cases, and exposes disagreements between judges. Its bootstrap resamples homes rather than treating the two verdicts on a home as independent samples. These are substantial improvements over choosing from vendor benchmark charts. The schema probes also separate compiler rejection from model-output failure, and the report correctly acknowledges that model consensus is not human truth.

The useful model signal is specific: with these inputs and this application contract, GPT-6.1 Sol low produced fewer judge-identified factual errors, passed validation consistently in the observed runs, and cost less than Sonnet. The direction is credible enough to investigate. It is also sensible to stop escalating effort by default: no material benefit was demonstrated here. That does not prove that more reasoning never helps a difficult plan.

**The headline conceals a material trade-off.** Here is the Claude judge's assessment of Sonnet versus GPT-6.1 Sol, reconstructed from the saved verdicts:

| Criterion | Sonnet wins | Ties | GPT-6.1 Sol wins |
| --- | ---: | ---: | ---: |
| Factual accuracy | 9 | 0 | 20 |
| Calibration | 12 | 0 | 17 |
| Usefulness | 12 | 8 | 9 |
| Viewing notes | 14 | 11 | 4 |
| Overall | 10 | 0 | 19 |

The overall result favours GPT-6.1 Sol, while usefulness and viewing notes remain strengths of Sonnet. “Both judges agreed” accurately describes the direction of the overall vote, but not agreement across these product qualities. The 19/29 win rate is 65.5%, with a descriptive 95% Wilson interval of approximately 47.3–80.1%. A two-sided exact sign/binomial test against equal preference gives p≈0.136. This is suggestive evidence, not a reason to insist on statistical certainty before experimenting, and not a demonstrated population win.

The headline 19% Sonnet preference pools this judge with a GPT judge that selected GPT-6.1 Sol on 28/29 homes. The pooling calculation is reproducible, but a bootstrap cannot remove a judge's systematic preference. Calling that preference *self-preference* is a plausible hypothesis rather than a demonstrated cause: quality, calibration, style, and shared blind spots can all contribute. Opposite order across two different judges does not test whether either individual judge reverses a judgment when its own A/B order is reversed. Research has documented position, verbosity and self-enhancement effects; a small human-calibrated, order-swapped check is more informative than simply adding another judge. [Zheng et al., LLM-as-a-Judge](https://arxiv.org/abs/2306.05685).

The reported error counts are judge allegations, capped by the prompt at six per assessment, without a severity-weighted independent adjudication. They should not be described as measured factual error rates. The Claude-only 2.34 versus 1.24 errors per home is a useful review queue, not ground truth.

**“98%” is agreement on a selected slice, not 98% accuracy.** There are only 12 homes with both flagship references. They produce 160 agreed field instances. GPT-6.1 Sol matches 157/160. Sonnet matches 137/148 across 11 valid homes, because one reference-covered home failed its first run. The chart therefore combines different valid-case denominators. On those same 11 homes, Sol still leads, 145/148 versus 137/148: the direction survives pairing. Unknown or disagreeing flagship answers never become labels; the hardest cases can disappear from the reference. The reference also tolerates a one-star rating difference, and its area comparison uses `space.total_area_sqm` without requiring that the value really was a printed floorplan total.

Several further qualifications matter:

- The 579 analyses are 499 initial arm/home runs plus 80 repeats, spread over only 30 distinct case IDs. Repeats improve knowledge of repeatability, not population coverage.
- Only 13/30 selected homes have declared floorplans; the sample includes one OpenRent and three Zoopla cases. Source/floorplan stratification is useful but does not guarantee representative layout difficulty, poor-condition examples or close decisions.
- “97% text facts” for GPT-6.1 Sol is 35 correct out of 36 answered regex-derived checks. Sonnet has 33 correct, one wrong and one abstention. This is sparse coverage, and the percentage excludes abstentions. Some checks also score application-corrected facts, so they evaluate the system rather than raw model extraction.
- The leave-one-out majority is not an independent panel: many votes come from related GPT variants, effort settings and service tiers. It is useful for locating disagreements, weak as an additional quality verdict.
- The reported 7–8% repeat variation counts fields that differ at least once across repeated runs. It is not a standard error or a minimum detectable between-model difference. A five-point advantage cannot simply be dismissed—or certified—by comparing it with that number.
- Neither cheap model was judged directly against Sonnet. Haiku versus Luna being inconclusive does not establish that either is sufficient for the application.
- “Never broke” means no observed failures: Sol has 30/30 valid initial runs and 50/50 including repeats; Sonnet has 29/30 and 48/50. Even with independent representative cases, zero failures in 30 gives a one-sided 95% upper failure-rate bound of about 9.5%. Repeated homes do not create 50 independent reliability cases.
- The expensive reference models are not independently quality-scored against human labels here, and some high-effort arms have only six or ten homes. “No gain demonstrated” is justified; “no gain exists” is not.

**The missing measurement is the decision the application makes.** The current default alert policy requires a fit score of at least 60. I replayed `select_discovery` with each home's saved bedrooms and postcode, holding owner state fixed at new/active. Sonnet and GPT-6.1 Sol each yield one alert out of 30, but for different homes. Among their 29 jointly valid cases, there are two threshold crossings: one Sonnet-only alert and one Sol-only alert. This does not establish which is correct. It identifies two especially valuable cases to adjudicate and shows why equal alert counts or high field agreement are insufficient.

This also exposes an architectural issue. `fit_score.py` gives positive weight to generated highlights such as “Open-plan layout”, “Period features” and “Recently refurbished”. Unsupported prose can therefore affect ranking and notification eligibility, not just presentation. The report's optimistic-badge observation deserves more attention than the token-speed chart.

There is a directly reproducible example: a saved Sonnet assessment contains both `pets_allowed: "no"` and a “Pets allowed” highlight. The existing badge reconciler handles bills, appliances, wardrobes and some other signals, but has no pets rule. A model switch may reduce this contradiction; an application invariant can prevent it across providers. Derive factual badges from supported canonical facts, and do not let a free-form curation step create ranking evidence. “Good hosting layout” is not evidence of an open-plan kitchen.

**The schema investigation is insightful, but its repair claims are too confident.** The saved compiler probes establish that this complete visual schema is rejected and some smaller variants compile. Anthropic documents both explicit limits and interacting internal grammar-size limits. It does not publish a universal 54–58-property ceiling. Optional fields, unions and other features remain relevant even though removing them alone did not rescue this particular schema. [Anthropic structured-output limits](https://platform.claude.com/docs/en/build-with-claude/structured-outputs#schema-complexity-limits).

Compiler rejection explains why strict mode was unavailable; it does not prove that schema size alone caused every subsequent malformed answer. Opus missing `overall_rating` could also reflect instruction adherence, required-field placement or the phase's mixed responsibilities. Its observed 8/21 failures across three configurations are a contract-compatibility result, not evidence that Opus is generally unreliable.

More concretely, **ordinary JSON decoding repairs neither saved Sonnet failure**. One flattens nested objects into XML-like fragments such as `<parameter name=...>`. The other stores `space` as JSON text missing the closing object brace. Both still fail the baseline validator after the proposed decoding attempt. Decoding an actually valid JSON-encoded object can be a narrowly defined normalization, followed by full validation, but that is not what these two receipts demonstrate. Do not reconstruct missing fields, parse arbitrary pseudo-XML or silently fill a missing rating merely to improve “validity”.

A single bounded retry after a settled, malformed response is reasonable to test. “A retry makes failures roughly zero” is unproven: repeats are not a conditional retry experiment, and correlated difficult cases or persistent contract defects violate the independence assumption behind squaring a failure rate. The application already has scheduled analysis recovery and successful-visual checkpoints. Any immediate retry must fit those existing attempt/spend limits, retry only the necessary phase, and preserve the rule against blindly repeating an uncertain paid dispatch.

**Normalize image inputs, but keep the claim precise.** The baseline resizer preserves alpha channels and returns small images unchanged; I confirmed that against cached floorplan bytes. The report's original-versus-white-background provider probe is a plausible and valuable explanation for its unreadable-plan example. I did not independently repeat that cloud experiment, and accepted PNG input does not by itself establish a universal alpha-compositing contract.

White-background compositing before the size early return is sensible for this document-like input. It needs to handle RGBA, LA and palette transparency, emit a matching media type, preserve the existing animation-admission rules, and avoid losing small labels through needless lossy encoding. Version the transform/input identity. Re-evaluate the affected case and any affected judge/reference inputs; fixing the pixels could change both scores and labels, so its effect on the model ranking cannot be assumed in advance. Retain adequate resolution for plans even if ordinary gallery photos later use a cheaper policy.

**The cost direction checks out; the latency explanation needs correction.** The important list rates in the experiment match current official documentation: Sonnet 5.5 and GPT-6.1 Sol are both $2/$10 per million standard input/output tokens; Haiku 5.5 and GPT-6 Luna are $0.10/$0.50 at the applicable short-prompt rates. The measured token usage, caching and output length create the task-cost difference. I independently obtain $0.059385 per valid Sonnet result and $0.040588 for GPT-6.1 Sol across all retained runs. [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing), [OpenAI pricing](https://developers.openai.com/api/docs/pricing).

The warm comparison includes repeated cases and warmed prefixes; a quiet scheduled service will have a different cache mix. The cold column is *per attempted home*, while the warm headline is *per valid home*. On the same valid-result denominator the cold estimates are about $0.0957 for Sonnet and $0.0582 for Sol. That is a reporting correction, not a reversal of the cost finding. At 100 homes, the warm difference is about $1.88 and the cold difference about $3.75. Reanalysis, retries and operating volume should enter a forecast separately.

The benchmark's “tokens per second” divides total output tokens by whole-request latency. It does not measure streamed decode speed: it includes prefilling/time to first token, reasoning and serialization; provider token units also differ. The observation that some arms finish faster is valid. The causal statement that OpenAI is slower *because* it generates tokens at one-third Claude's speed is not established by this measurement.

Flex is the clearest operational counterexample. **Three of its 30 phase calls required retries**, with attempt counts 2, 3 and 3. The shim resets its timer for each attempt and retains only the successful attempt's latency. Failed attempts and backoff disappear from the reported 41-second provider metric. Full wall time also contains queueing, so it is not an isolated tier comparison either. Thus “half the price with no slowdown in this run” is not a sound operational conclusion. OpenAI explicitly documents slower responses and occasional unavailability for Flex, although the 50% price discount is real. Use standard service initially for time-sensitive listing alerts; consider Flex for offline reanalysis when its deadline/fallback behaviour is measured. [Flex processing](https://developers.openai.com/api/docs/guides/flex-processing), [GPT-6.1 Sol pricing notes](https://developers.openai.com/api/docs/models/gpt-6.1-sol).

**A production adapter is more than the experiment shim.** The shim is useful proof of request mapping, but it replaces the native SDK path and therefore bypasses the durable per-attempt spend middleware. It overwrites output caps with 16,000, uses 600/900-second request timeouts and up to four transport attempts. The baseline runtime uses 8,192 output caps, 240/60-second phase timeouts and zero SDK retries. The main low-effort arms' recorded outputs fit below 8,192, so the cap mismatch does not itself invalidate their observed ranking; it still leaves production behaviour unqualified.

More importantly, the experiment's embedded application provenance identifies Sonnet for the OpenAI runs too; the outer experiment configuration carries the real identity. That is workable for a one-off harness, but cannot be copied into production checkpoint or reanalysis keys. OpenAI also receives strict JSON response formatting while Anthropic emits a non-strict visual tool call. This measures the two available configurations of this application, not provider-neutral intrinsic model ability or either provider's best attainable prompting.

If the trial earns a switch, introduce a small explicit provider boundary around phase requests, validated domain output and usage receipts. Keep actual provider/model/effort/service tier, effective wire-schema and prompt hashes, image-transform identity, request IDs and SDK version truthful. Extend pricing, cache-write accounting, input reservations, typed rejection/refusal/truncation handling, cancellation and unknown-outcome handling together. Keep successful visual checkpoint reuse. Do not build a provider marketplace or let a compatibility wrapper masquerade as Anthropic all the way into accounting. The current docs also distinguish cache reads and writes by lifetime, so a new adapter should pin the supported policy and reject unpriced modes. [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).

**Architectural alternatives worth considering.** The visual phase currently produces observations, a desirability rating and summary. The evaluation phase sees those observations plus text, not the images, so it is not an independent visual verifier. Several schema fields duplicate or derive from others; meanwhile, useful concepts such as observable room access are less explicit. Reducing the contract should follow product meaning rather than aiming at a particular property count. Some apparent duplicates may have different scope: a primary-bedroom wardrobe and wardrobes anywhere are not automatically interchangeable. Likewise, an advertised floorplan total, a room-derived estimate and an accepted internal total need distinct meaning even if their representation can be simplified.

| Option | Judgment for this application |
| --- | --- |
| Keep Sonnet; fix image handling and factual badge invariants | Best immediate work. Existing integration remains useful while the known correctness gaps are addressed. |
| GPT-6.1 Sol low for both existing phases | Best challenger for a controlled, standard-tier trial. Test accurate facts and useful viewing notes separately. |
| Luna low for both phases | Worth a direct, labelled comparison if volume becomes important. Its 91% consensus and excellent observed cost are insufficient evidence for use as a gate that silently rejects homes. |
| Strong visual model plus cheap evaluator | Technically feasible, but the second phase creates badges and advice that still need evidence. Savings are small at current scale; the existing mixed arm has no direct human-quality comparison. |
| Shrink observations; derive simple facts/badges; synthesize decisions separately | Best longer-term direction if review shows recurring semantic problems. Preserve unknowns and evidence references for material claims; move policy/suitability out of shared observations when necessary. Implement incrementally. |
| Split the visual schema into two strict calls | Fallback if a smaller useful contract still cannot compile. Each extra image pass introduces cost, latency, partial failure and reconciliation work. Two successful half-schema probes do not qualify the complete two-call system. |
| Higher effort, flagship models, broad new-provider sweep, or an ensemble on every home | No present justification. Broader experiments will not resolve ambiguous labels or unsupported application-derived claims. Use expensive review selectively on material uncertainty if later evidence supports it. |

**Next steps, in order.**

1. Preserve this experiment as exploratory evidence and use the corrected counts/interpretations above. Keep the later `tightened` experiment separate. Do not select a winner by pooled judge votes alone.
2. Finish the small image-normalization and badge-consistency fixes, coordinating with the existing comparison worktree rather than duplicating its active edits. Include the two actual malformed receipts as private regression cases if changing recovery. Do not treat a successful parse as semantic correctness.
3. Review material claims on the existing cases: start with the two alert crossings, explicit contradictions, unreadable plans and judge disagreements, then include a random sample of agreements to catch shared errors. Define what “separate office”, “usable living area”, “open plan” and acoustic uncertainty mean before tuning. Ten homes are a useful calibration session, not confirmation of a general winner. The original checkout already has uncommitted offline evidence/review tooling; assess and reuse it rather than starting another evaluation platform.
4. Freeze the resulting rubric and compare repaired Sonnet with GPT-6.1 Sol low on a fresh held-out sample—roughly 30–50 homes is a practical next tranche, not a promised statistical-power threshold. Retain exact effective requests, inputs, usage and failures. Sample whole homes; keep near-duplicate adverts/photo families together. Use the same processing policy and actual production limits, with a small repeated subset.
5. Decide using material unsupported-claim rate, supported-fact coverage, missed promising homes, unnecessary alerts, and property-specific viewing-note utility. Grade failures separately and include them in end-to-end completion and cost. Review high-severity disagreements manually; bootstrap paired differences by home. Adopt the challenger only if the improvement is meaningful to those decisions and there is no unacceptable regression in advice or operations. Otherwise keep the repaired baseline.

The near-term decision is **repair and validate, then trial**. The report gives enough evidence to make GPT-6.1 Sol the leading challenger; it does not make model choice the main architectural problem.

**Verification record.** The accompanying `2026-10-21-model-bakeoff-audit.py` reads retained JSON and a SQLite snapshot in read-only mode, uses the baseline validator and notification selector, and writes aggregate JSON to stdout. Its saved output is `2026-10-21-model-bakeoff-audit.json`. Ruff lint and formatting pass. Independent aggregate checks match all 27 saved summary arms for validity and consensus. Application behaviour was not modified, so no production deployment, live-provider qualification or full-suite pass is claimed.

Reproduction from this worktree, using installed project dependencies:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run python \
  docs/reviews/2026-10-21-model-bakeoff-audit.py \
  /home/marcel/projects/lab/home-finder/.claude/worktrees/sonnet-openai-comparison-9ee7f3/experiments/model-comparison
```

Attachment SHA-256: `556a21e67d6ae8ac88ca205ef7fa9f4a761c6a119d18137f6b51f696d21265b1`.
Core-record aggregate SHA-256: `26314ec361342115566112319d334ca2d50d5d7ecc233612aa41669c8fbcba27`.
Judge-record aggregate SHA-256: `2efc4eab435b43536f016131a25fd15d6a39353abb3980a0b76bbcc5284f1936`.
The aggregate hash construction is defined in the audit script. Real adverts, photos and provider responses remain in the private experiment; they are not copied into this review.

## Follow-up: review and implementation of the correctness fixes

Opus's subsequent uncommitted image change correctly composites transparent pixels onto white before the small-image early return and before resizing. This worktree adopts that approach, closes image handles, avoids lossy re-encoding for transparent WebP, and retains animated bytes for admission to reject. Tests cover the observed black-ink LA/RGBA floorplan failure, palette and colour-key transparency, partial alpha, opaque passthrough and static/animated formats. A mocked native request test verifies the actual PNG payload behind a misleading `.jpg` URL, leaves the cached source unchanged, invalidates the old visual checkpoint, and reuses the new checkpoint on repetition.

Opus had not extended the badge reconciler. The saved `cbfa2bd6c46` receipt contradicts itself on pets and overstates both bathroom finish and overall condition. The saved `c66220bd66a`, `c3416f2470a` and `c7815468aad` receipts advertise open-plan layouts without supported kitchen/living connectivity. Minimal regression fixtures reproduce those fields without including private adverts or images. They failed against the baseline through native mocked requests for both supported Sonnet configurations.

New assemblies now require matching structured evidence for pets, modern/dated kitchen and bathroom finishes, excellent condition, and open-plan layout. One optional visual observation, `space.kitchen_living_layout`, distinguishes `open_plan`, `separate` and `unknown`. Its schema requires visual support for a continuous kitchen/living room; a combined living/dining room or a good hosting judgement is insufficient. Missing older observations default to unknown. The guard removes unsupported badges without generating replacements or parsing model prose as facts. Historical loading does not run the guard. The visual schema fingerprint changes automatically; the evaluation contract is unchanged.

These changes enforce consistency, not independent factual truth: an incorrectly extracted positive fact can still support an incorrect badge. A held-out semantic review remains necessary. Newly assembled analyses that omit the connectivity observation conservatively lose the open-plan badge; stored historical badges are not rewritten.

The added schema field exposed an existing admission-fixture limit at one to three images. The runtime text/schema allowance increases from 110,000 to 111,000 tokens while keeping the per-image bound, spend caps and attempt limits intact. This increases the conservative visual reservation by $0.00250; it does not make a provider call or grant new spending permission. Explicit configured limits remain explicit. Operations documentation and pinned ledger expectations match the new reservation.

Validation: the initial regression run failed on all nine saved-case/native-request checks. The related filter, model and notification sweep passed 1,776 tests and exposed three admission-bound failures, fixed as above. Final focused runs cover 264 image/badge/schema/transport/checkpoint tests and 57 configuration/admission tests, all passing. Ruff lint, formatting and strict mypy pass. Tests use mocked providers; no live-provider qualification is claimed.

At this initial implementation checkpoint, the other changes were left for separate review. The broader follow-up below adopts the reviewed recovery changes and records which policy changes remain experimental.

## Broader review of Opus's work today

Scope: the Home Finder work dated 21 October 2026. Remote PR search found one
existing PR, [#75](https://github.com/MarcelCutts/home-finder/pull/75), alongside
the uncommitted model-comparison worktree. Older dirty files in the main checkout
were excluded. Original experiment data and Opus's working files were preserved.

### Recovery: adopt with stronger validation

Nested JSON text can be decoded safely when the schema expects an object or
array and the decoded value validates. Ordinary prose remains prose; evaluation
output keeps its stricter contract. However, Opus's implementation accepted
conflicting duplicate keys, NaN, infinity and overflowing exponents inside those
strings. Six targeted tests reproduced that acceptance. The reviewed decoder
rejects all of them, shares duplicate-key handling with evaluation parsing, and
logs only schema-owned paths. Python's default decoder accepts repeated names
and nonstandard numeric constants, so an ordinary successful `json.loads` is
insufficient here. [Python JSON documentation](https://docs.python.org/3/library/json.html#standard-compliance-and-interoperability).

The two observed malformed shapes remain failures: a truncated `space` JSON
string and pseudo-XML with nested fields spilled into the parent. We do not
invent missing syntax or infer where repeated root fields belong. Minimal
regressions retain these structural failures without private advert text.

One identical retry is now included for a *completed malformed visual response*.
Each dispatch has a separate admission and settlement, both usages are counted,
and a checkpoint is written only for a valid response. Refusals, truncation and
uncertain dispatches are not retried by this path. Tests cover both actual shapes
recovering and remaining malformed, an exhausted attempt budget, and absence of
invalid checkpoints. They verify behavior, not a claimed real-world recovery
percentage. Non-strict calls can fail the schema; strict output support does not
remove the need to handle refusals or incomplete output.
[Anthropic structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs).

### Policy and prompt changes: useful direction, not ready to adopt

The deterministic rating is a reasonable shadow experiment. It separates
observations from a repeatable policy, but its calibration target is other
models, not renter decisions. Running the proposed function gives **3 for `{}`**
and **5 when the only observation is a modern kitchen**. Its test explicitly
expects some missing evidence to increase the score. That is a policy choice,
not validation that the five-star scale has retained its meaning. Keep the
existing production rating contract for this PR. Before adoption, define an
evidence-coverage rule, review alert-threshold crossings on held-out homes,
version the policy, and show the proposed score alongside the current score.
Do not replace the formula with another set of unvalidated weights.

The hosting checklist is more explicit, but the four checks are neither equally
important nor always observable. A standing-capacity proxy does not establish
that eight guests can dine comfortably. Record each check as yes/no/unknown
before choosing an aggregation policy; do not let an unseen check become a
negative by subtraction.

The office rule weakens the existing evidence requirement: seeing a door in a
photo does not prove that the room has independent access or is not a through-room.
An unmeasured, partly shown room can also leave desk fit unknown. Retain those
unknowns instead of restricting them to only two scenarios.

Changing “top” from fifth-floor-or-higher to the building's top floor repairs a
real semantic mismatch with the fit score's acoustic interpretation. However,
changing only the prompt silently mixes old and new meanings in persisted data.
Prefer separate observations for vertical level and whether a dwelling is above
the flat; alternatively version the interpretation and stop awarding that
benefit to legacy ambiguous values. Direct garden access alone is also not proof
that the accommodation is on the ground floor.

### Dashboard and experiment tooling

PR #75's count, label and layout changes are useful. Review found a pair of
floorplans with different dimensions but identical perceptual hashes. The PR now
retains alternatives in a collapsed section and full-size gallery, preserves
their source status, supports keyboard opening of the selected version, and
survives a cache eviction race. “Potential road or rail noise” preserves the
uncertainty lost in the original wording. The PR contains the walkthrough,
screenshots and checks in `docs/reviews/2026-10-21-ui-pr-75-review.md`.

The experiment harness also had accounting defects: outstanding reservations
were process-local, judge JSON failures discarded already reported costs, and
cancellation released potentially billable work. A separate experiment-review
worktree contains shared atomic reservations, specific settlement tokens,
retained uncertain charges and no automatic retry of an ambiguous dispatch.
Warm and cold report columns now use the same valid-result denominator,
including costs of failures. Its small historical reservation estimates are
**not validated upper bounds on a provider invoice**; qualify actual request
cost bounds before another paid trial. The harness remains exploratory, and
none of its private data or retained reports were copied into source control.

The next decision remains a controlled, human-labelled comparison after these
correctness repairs. Shipping more deterministic labels, or reducing run-to-run
variation, is not a substitute for checking whether the labels are right.

Final application verification for this worktree: **4,105 default tests pass**,
with **90.84% branch-inclusive coverage** (75% required). Ruff lint, formatting
and strict mypy pass. The earlier recovery-specific run passed 260 tests; the
four saved-shape retry tests first failed without recovery, and six strict-JSON
tests first failed against Opus's proposed decoder. PR #75 was independently
verified with 610 related tests and 64 Chromium tests. Provider calls were mocked;
there is no new live-model quality or paid-budget qualification claim.
