# Three paid tests say leave the analysis prompts alone until there is an eval with real answers

An audit of the two analysis prompts found 21 things that no longer fit Sonnet 5.5, but two paid tests on the October bake-off homes showed that editing the prompt text does not improve the output on any measure available, and that each round of edits brought its own side effects. The first patch left the factual answers where they were, but it flipped "feels spacious" to false on 7 of 15 homes and listed a third fewer concerns. A revised patch repaired that and then over-corrected: it put the red-flag line on 14 of 15 alerts instead of 8, and moved the office-separation answers away from the reference answers. Two of the audit's own claims were also wrong: removing the example tagline did not vary the taglines, and adaptive thinking did nothing at low effort. No prompt patch is recommended, and no prompt or schema change has been applied. Tracing the contract findings into the code then changed their order. The red-flag count reaches only Telegram alerts, and a count computed in code would vary between repeat runs more than the model's own number, so it is parked. The yes/no field "feels spacious" is the one that reaches the listing page and the score: the prompt tells the model to answer false when unsure, and the page then says "Compact" and the score breakdown says "Feels cramped". A third test let that one field answer "unknown". It disturbed nothing else, but the model then stopped answering "not spacious" altogether, and three flats of 43 to 51 m² that it and three other models had called not spacious became "Spacious". That change was reverted too. Three changes were tested in one day and none held, so the next step was an eval with real answers, not a fourth change. The first such eval checks what the floorplans print, and there the pipeline is already right: every printed total area and all 75 room dimensions were read correctly. A second eval against the advert text found no stated fact reported wrongly. Its one soft spot is the property type, which Sonnet fills in for about a third of the homes whose advert does not name one. A code check for that is the one change to the pipeline that came out of this work.

*Method: audited on 12 November 2026 following Anthropic's prompt-audit procedure (inventory, git provenance, pattern scan, keep list), against the model the code runs, `claude-sonnet-5-5` at low effort. Findings were first checked against 48 valid saved outputs from the October bake-off, whose prompt hashes equal today's. The first test ran the 30 bake-off homes on a patch and the first 15 of them on the unpatched prompts, same code and same day, through the bake-off's harness ($2.67). The second ran a revised patch on the same 15 homes ($0.96), and the third a single-field change to `feels_spacious` ($0.89), for $4.51 in all. Every tested change, all outputs and the comparison scripts are in the private folder `data/prompt-audit-2026-11-12` in the main checkout.*

## What the tests showed

"Control" is the unpatched prompts. All counts are out of the same 15 homes, run on the same code on the same day.

| Measure | Control | First patch | Revised patch |
|---|---|---|---|
| Valid results | 15 | 15 | 15 |
| Factual answers that differ from the control | 6.0% (control against October) | 6.1% | 7.4% |
| Agreement with two flagship models' shared answers (143 answers): agree, differ, unknown | 133, 8, 2 | 132, 6, 5 | 131, 9, 3 |
| "Feels spacious" answered true | 7 | 0 | 8 |
| Listing concerns per home | 2.60 | 1.67 | 2.80 |
| Listing concerns about costs or tenancy terms, per home | 0.73 | 0.00 | 0.07 |
| Homes whose alert would carry the red-flag line | 8 | 8 | 14 |
| Condition concerns per home | 1.80 | 1.33 | 1.47 |
| Office separation answered "dedicated room" | 1 | 2 | 4 |
| Homes rated 4 stars (the rest are 3) | 1 | 5 | 3 |
| Taglines opening with "Bright" | 7 | 7 | 7 |
| Evaluation calls that used thinking | 0 of 14 | 1 of 14 | 0 of 14 |
| Median cost per home, cache warm | $0.0562 | $0.0573 | $0.0578 |

**Neither patch is more accurate.** The factual answers move about as much as they do between two runs of the same prompt, and agreement with the flagship answers is level or slightly worse. The first patch was also run on all 30 homes, all valid.

**Wording in a field description or an example steers the output strongly.** Three results show it:
- The first patch described "feels spacious" as "true only when generous room sizes are shown", and all 7 true answers became false. Restoring the meaning restored them.
- The revised patch named three examples of listing concerns: a mismatched address, staged photos and a missing floorplan. By keyword match, concerns mentioning an address rose from 4 to 10, photos from 6 to 17 and floorplans from 7 to 11, and the model's red-flag count rose from 1.53 to 2.40 per home.
- The rewritten office-separation description produced "dedicated room" on 4 homes where the control and the October run say "shared space" on 3 of them. Against the flagship answers it agrees on 2 of 6, the control on 4 of 6.

**The intended effects did appear.** Costs and tenancy terms left the photo-analysis output in both patches, and price words went from 3 homes to none.

**The rating shift is small and unproven.** With the value clauses removed from the rating criteria, 4 homes rose from 3 to 4 stars in the first test and 2 in the second, and none fell. The flagship models were run with the old criteria, so they cannot say whether that is better.

**One response needed the retry.** In the revised run one photo-analysis response failed validation and the built-in retry recovered it. One in 15 is in line with the October rate and cannot be pinned on the patch.

**Attribution is inferred.** Each patch was tested as a bundle, so which edit caused which change is my reading of the wording, not an ablation.

### Third test: letting "feels spacious" answer unknown

One field was changed and nothing else: the tool schema allowed null, and the prompt said null means the images do not show enough to judge.

| "Feels spacious" on the 15 homes | Control | Changed |
|---|---|---|
| True, shown as "Spacious" | 7 | 11 |
| False, shown as "Compact" | 8 | 0 |
| Unknown, nothing shown | 0 | 4 |

- **No side effects:** the other factual answers differed from the control by 4.6%, below the 6.0% re-run noise, and cost was unchanged. Changing one field at a time does contain the damage.
- **The answer itself got worse:** all 7 true answers stayed true, but the 8 false answers split into 4 unknown and 4 true, leaving no home "not spacious". Three of the four that turned true are flats of 43, 51 and 51 m² that the control and three other models all called not spacious.
- **Where unknown was right and wrong:** one home has only two exterior photos, and the control still called it "Compact"; unknown is the honest answer there. Unknown also went to a 32 m² flat with a floorplan, where "not spacious" was the supported answer.
- **Reading:** the old rule, "false when unsure", was also holding back a lean towards calling well-lit photos spacious. Removing it released that lean.

## First eval with real answers: what the floorplans print

The first eval checks saved outputs against the floorplan images themselves, with no model call. Fourteen of the 30 homes have a floorplan: 13 declared by the listing and one sitting in the gallery as an ordinary photo. For each, the labels record the printed total area, every printed room dimension (47 rooms), the printed floor level and any balcony, terrace or shared garden drawn on the plan. Almost every figure is printed in both metres and feet, and wherever both appear they agree, for all 47 rooms and 13 totals, which guards against misreading. The other 16 galleries were looked at image by image and hold no floorplan.

| What the plan prints | Sonnet 5.5 low, October | Sonnet 5.5 low, today's code | Haiku 5.5 low | GPT-6.1 Sol low | GPT-6 Luna low |
|---|---|---|---|---|---|
| Total floor area reported correctly | 22 of 22 | 14 of 14 | 23 of 23 | 23 of 24 | 23 of 24 |
| Room dimensions transcribed correctly | 75 of 75 | 47 of 47 | 73 of 73 | 75 of 77 | 73 of 77 |
| Floor level correct | 19 of 20 | 13 of 13 | 16 of 20 | 21 of 21 | 19 of 21 |
| Balcony, terrace or shared garden shown | 9 of 9 | 5 of 5 | 9 of 9 | 9 of 9 | 9 of 9 |
| Area given for a home with no floorplan | 0 of 26 | 0 of 16 | 0 of 24 | 0 of 26 | 0 of 26 |

Counts are runs, and some homes were run more than once in October. The "today's code" column is the 30-home run of the first patch, the only run today that covers every floorplan home; the unpatched control covers 7 of them and scores 7 of 7 on totals and 23 of 23 on rooms.

- **The floor area and room sizes on the listing page can be trusted as printed.** Sonnet 5.5 made no error on a total or a dimension, invented no room, and never gave an area where there was no plan.
- **It found the floorplan hidden in a gallery** and read it correctly, in every run.
- **Two findings are not supported by this evidence:** higher-resolution floorplans (F16) and moving the floorplan arithmetic into code (F14). At today's 1,568 px the plans are read without error, and the living-room area matches the printed dimensions in 13 of 14 rectangular cases.
- **This eval guards against regressions; it does not rank options.** Everything scores at or near the top, so it would catch a change that broke floorplan reading but cannot say which prompt is better.

## Second eval: what the adverts state

The second eval checks the listing facts against the advert text, again with no model call. All 30 adverts were read and every fact they state was recorded, among the ten the pipeline extracts: 50 stated facts in all (furnishing 13, deposit 8, EPC 6, bills 6, parking 6, property type 5, pets 4, council tax band 2). No advert states a service charge or a broadband type. A fact the advert does not state should come back unknown.

| | Sonnet 5.5 low, October | Sonnet 5.5 low, today's code | Haiku 5.5 low | GPT-6.1 Sol low | GPT-6 Luna low |
|---|---|---|---|---|---|
| Stated facts reported correctly | 73 of 76 | 24 of 25 | 68 of 70 | 74 of 78 | 76 of 78 |
| Stated facts reported wrongly | 0 | 0 | 0 | 0 | 0 |
| Stated facts left unknown | 3 | 1 | 2 | 4 | 2 |
| Unstated facts left unknown | 391 of 404 | 122 of 125 | 399 of 400 | 421 of 422 | 420 of 422 |
| Unstated facts filled in | 13 | 3 | 1 | 1 | 2 |

Counts are runs. "Today's code" is the unpatched control on 15 homes.

- **No model reported a stated fact wrongly.**
- **The misses are cautious, not wrong.** Two adverts say bills are excluded in wording the code's deliberately strict bills check does not accept ("(Excluding Bills)" and "Bills not included (£200-£400/month)"), and one says "No Pets" in one portal's copy only.
- **Every fact filled in without support is a property type, and Sonnet does it most.** In October it named a building type for 8 of the 25 homes whose advert names none, and today for 3 of 14. The answer is not stable: one home run three times was "ex-council" twice and "purpose built" once, another "new build" twice and "purpose built" once. The evaluation prompt says to take the type only from the advert.
- **That one reaches the page.** The property type is a badge on the card, the "Type" line on the listing page and a dashboard filter; it enters the fit score; and it selects the sound-insulation profile shown for the home. Council tax band and bills already have a code check that keeps the model's answer only when the advert supports it. Property type had none when this eval was run; one has since been added (see the recommendation).
- **Deposits given in pounds are treated inconsistently.** In October they were converted to weeks in 6 runs and left empty in 6. Both are accepted here.

**Limits of both evals.** The labels were read by Claude and have not been spot-checked by a person; each records the printed text or a short quote so a check takes seconds. They say what the plan prints and what the advert states, not what is true of the home. Thirty homes, fourteen with a floorplan, is a small set.

**Files.** Labels: [2026-11-12-eval-floorplan-labels.json](2026-11-12-eval-floorplan-labels.json) and [2026-11-12-eval-advert-labels.json](2026-11-12-eval-advert-labels.json). Scorer: [2026-11-12-eval.py](2026-11-12-eval.py), run as `python docs/audits/2026-11-12-eval.py RUN_DIR [RUN_DIR ...]` against any folder of saved runs.

## Recommendation

1. **Apply no prompt or schema change now.** Three were tested and none held. The prompts as they stand are stable from run to run.
2. **Inferred property types now have a code check**, added in the same change as this report. `supported_property_type` in [listing_text.py](../../src/home_finder/analysis/listing_text.py) keeps the model's type only when the advert names it, as the checks for council tax band and bills do. Applied to all 249 saved runs across nine model settings, it removes all 29 unsupported types and keeps all 29 stated ones. That result is narrower than it sounds: only 5 of the 30 adverts state a type, so the 29 kept answers repeat five wordings. A code review then found phrasings the first version got wrong in both directions ("A Victorian ground floor garden flat" was dropped; "close to the Georgian Sutton House" was kept), and the rules were reworked. They were then run over every advert in the bake-off snapshot: of 137 sentences containing a type word (45 distinct wordings), 107 are accepted and 30 passed over, and each wording was read and judged right. The rules remain wording heuristics that lean towards unknown; an advert phrased in a way they do not expect loses its type, not gains a wrong one. The check runs when a home is analysed, so analyses already stored keep their inferred type until the home is analysed again. Homes whose advert names no type will no longer carry a type badge or a sound-insulation profile.
3. **Get the owner's answers for the judgement facts.** What a document prints, the pipeline already reads correctly. Condition, "feels spacious" and similar judgements from photos are where it could still be wrong, and no document prints them, so they need a person's answers on a handful of homes.
4. **Leave "feels spacious" as it is** (F13). The field is an impression that does not track size: in the saved runs it is true for a 39 m² flat and false for a 51 m² one. If the "Compact" label matters, decide in code when the page may show it, for example only beside a measured floor area. That is a product choice, not a wording fix.
5. **Park the red-flag count** (F12). It only feeds Telegram alerts, and the owner's priority is the listing page. An earlier version of this report recommended computing it in code; the repeat runs do not support that.
6. **Change one field at a time if anything is changed later**, with a same-day control. That contained the side effects in the third test, though it did not make the change right.

## Scope, target and what was read

| Surface | Location | Size |
|---|---|---|
| Photo-analysis system prompt | [prompts.py](../../src/home_finder/analysis/prompts.py) lines 11–228 | 13,209 characters |
| Evaluation system prompt | prompts.py lines 232–318 | 4,885 characters |
| Per-listing prompt builders | prompts.py lines 321–575 | assembled per home |
| Photo-analysis tool and field descriptions | [analyzer.py](../../src/home_finder/analysis/analyzer.py) lines 231–565, 708–714 | 77 fields; 21 answer fields have no description |
| Evaluation output schema | analyzer.py lines 567–632 | 21 fields; 9 answer fields have no description |
| Request settings | [config.py](../../src/home_finder/config.py) lines 62–79, [models/analysis.py](../../src/home_finder/models/analysis.py) lines 33–38 | model, effort, thinking, output cap |
| Image preparation | [image_processing.py](../../src/home_finder/utils/image_processing.py) lines 20–22 | size cap |

Agent configuration files (`CLAUDE.md` and similar) were out of scope. The code calls only Anthropic.

**Provenance.** Of the 308 system-prompt lines, 137 still date from the 12 February 2026 two-phase commit (`adb791f`), when the model was `claude-sonnet-4-5-20250929`, and about 70 more from later February commits. About 100 come from the 10 October 2026 evidence repairs, written while the default was `claude-sonnet-4-6`; Sonnet 5.5 became the default the same day (`fe31547`). History shows no line written against observed Sonnet 5.5 behaviour.

**Counts.** Dated prompt text: 11 findings. Agent configuration files: not applicable. Tool descriptions: 3. Request settings and architecture: 7.

## Prompt-level findings and what the tests did to them

Each entry gives the place, the pattern it matches, the edit that was tried, and the result. The observations about the text stand; the tests show that acting on them did not help.

### F1 · Evaluation runs with thinking switched off
- **Where:** config.py line 73, `reasoning="disabled"`, which models/analysis.py line 38 turns into `thinking: {"type": "between_tools"}`.
- **Why it was raised:** Anthropic's Sonnet 5.5 guide says to use adaptive thinking for tasks without tools that need working out, and to add "Think the problem through before you answer."
- **Result:** with both changes the evaluation call used thinking in 1 of 14 calls, and value ratings were identical on all 15 homes. The photo-analysis call, which already runs adaptive, used no thinking tokens in any arm. This matters only if effort is raised above low.

### F2 · Sentences that force JSON the grammar already guarantees
- **Where:** prompts.py line 318, "Always return your assessment as JSON matching the provided output schema.", and lines 572–573.
- **Pattern:** scaffold replaced by an API feature; `output_config.format` constrains the output.
- **Result:** removed in both patches with no visible effect. Harmless either way.

### F3 · The one example tagline
- **Where:** prompts.py lines 305–307, `(e.g. "Bright Victorian flat with period features and a modern kitchen")`.
- **Why it was raised:** 22 of 48 saved taglines open with "Bright" and 41 contain "with".
- **Result:** the claim was wrong. With the example removed, 7 of 15 taglines still open with "Bright" in both patches, the same as the control.

### F4 · "Do NOT include EPC rating here" cannot be violated
- **Where:** prompts.py line 300. Highlights are an enum of allowed tags and none mentions EPC.
- **Result:** removed in both patches with no visible effect.

### F5 · Rating criteria ask for the value judgement the prompt rules out
- **Where:** prompts.py lines 208–217, for example "5 = … good or excellent value. Rare find.", against lines 14–17, "A separate evaluation step will handle value assessment".
- **Pattern:** patch accretion; February criteria survive beside the October rule.
- **Result:** rewriting the criteria to condition and liveability moved 4 homes, then 2, from 3 to 4 stars. Not shown to be better.

### F6 · Pricing claims about building types conflict with the October rule
- **Where:** prompts.py lines 57–58, 70, 79 and 177 ("Premium pricing (especially E9 canalside)" and similar), against line 75 ("Do not assume a rent discount from stock type").
- **Result:** with the four remarks removed, price words went from 3 homes to none.

### F7 · `<listing_signals>` asks for things this phase has no field for
- **Where:** prompts.py lines 84–96: EPC, service charge, bills, rent-free weeks, licensing.
- **Pattern:** unenforced instruction; the evaluation prompt already extracts these.
- **Result:** both rewrites removed costs and tenancy terms from the photo-analysis output. The first also dropped genuine listing concerns; the second, which named examples of concerns to keep, inflated exactly those examples. A rewrite that does neither has not been found.

### F8 · Field rules sit in a roll-call in the system prompt while the fields lack descriptions
- **Where:** prompts.py lines 19–33, a paragraph naming 25 fields; analyzer.py, where 21 photo-analysis fields and 9 evaluation fields have no description.
- **Pattern:** under-described tool parameters.
- **Result:** moving the rules onto the fields raised "unknown" answers from 40.2% to 43.2%, then 41.4%, and one description broke "feels spacious". Descriptions carry more weight than the same rule in the system prompt, so adding 30 at once was the wrong way to do it.
- **Size constraint found on the way:** the worst-case request has only about 300 bytes of headroom under the input allowance when one image is configured. Adding text to the prompt or the schema defers those homes unless something else shrinks.

### F9 · Two field descriptions predate and contradict the October access rules
- **Where:** `is_ensuite`, analyzer.py lines 420–427, against prompts.py lines 158–161; `office_separation`, analyzer.py lines 457–466, against prompts.py lines 168–170.
- **Pattern:** two copies of a rule that disagree; the prompt copy is newer.
- **Result:** `is_ensuite` barely moved. The `office_separation` rewrite made the answers less stable and less aligned with the flagship answers. The contradiction is real, but the old description plus the prompt rule works better than my replacement.

### F10 · Evaluation is told its input comes from "a detailed property inspection"
- **Where:** prompts.py lines 233–235.
- **Result:** with the framing changed to "made from marketing photos, not a visit", questions for the agent rose from 8.9 to about 10 per home. Whether that is useful was not judged.

### F11 · The tool description is one line
- **Where:** analyzer.py lines 708–710.
- **Result:** expanded in both patches. The tool was called on every run in every arm, so there was nothing to fix.

## Findings that need a contract or code change

These change stored data, validation or image handling. Neither patch touched them; F13 was then tested on its own in the third test.

| # | Finding | Evidence | Proposed action | Confidence |
|---|---|---|---|---|
| F12 | `red_flag_count` is a tally the model computes with no stated rule, and it drives the "⚠️ N red flags" line in alerts ([telegram.py](../../src/home_finder/notifiers/telegram.py) lines 626–628) | It equals none of four plausible tallies in 39 of 48 saved runs, and it moved from 1.53 to 2.40 under the revised patch. But only Telegram uses it; the listing page lists each flag and shows no number. Across 10 homes run more than once, the model's count was identical in 8 and a count of the listed items in 4, so a code tally would be the noisier number | Parked. One small alert defect remains: the detail message hides its "Not seen in supplied photos" line when the model's count is 0 (4 of 48 saved runs), fixable by dropping the count from that check at telegram.py line 419 | Low priority |
| F13 | Seven yes/no fields are told to "use false when uncertain" (prompts.py lines 29–31), so false means both no and unknown; October added `availability` to recover the difference for outdoor space | "Feels spacious" is false in 23 of 48 saved runs. The listing page shows every false as "Compact" (`_quality_card.html` line 69) and the fit score adds a "Feels cramped" factor ([fit_score.py](../../src/home_finder/filters/fit_score.py) lines 323–325). The answer is stable across repeat runs, so how many of the 23 are "could not tell" is not visible in the data. The outdoor and photo flags only show when true, so they do not make negative claims. Only 2 of the 23 false answers have no interior evidence at all | Tested and not kept: with null allowed, the model never answered false (see the third test). Leave the wording; any fix belongs in code that decides when "Compact" may be shown | Tested, negative |
| F14 | The model converts units, multiplies dimensions, sums areas and checks totals (prompts.py lines 120–142), and code then re-checks and withholds | The October repairs added the code checks | Not needed on current evidence: the floorplan eval found every printed dimension transcribed correctly and the living-room area right in 13 of 14 rectangular cases | Low |
| F15 | The evaluation schema has a field named `reasoning` (analyzer.py line 614) | Anthropic lists reasoning fields in JSON output as a refusal risk on Sonnet 5.5; no refusal occurred in 60 test runs | Rename the wire field to `explanation`; low priority | Low |
| F16 | Every image, including the floorplan, is capped at 1,568 px, and the comment cites a 2,000 px limit | Anthropic's vision page gives a 2,576 px tier for newer models and a 3,000 px limit for requests over 20 images; [operations.md](../operations.md) already says Sonnet 5.5 is on the higher tier | Not needed on current evidence: at 1,568 px the floorplan eval found no misread total or dimension on 14 plans. Correcting the stale comment is still worth doing | Low |

## Flagged without an edit

| # | What | Why it is only a flag |
|---|---|---|
| F17 | `<analysis_steps>` is a numbered list with the summary and rating as steps 5 and 6 of 13 | The content is domain knowledge, not a script, and the order of the output is set by the schema, not by this list |
| F18 | Numeric caps nothing enforces: 14 of 48 saved summaries have three sentences against "1-2 sentences"; 2 of 48 highlight lists fall outside 3–5 | Either enforce in code or accept; a product choice |
| F19 | The acoustic figures appear both in the prompt and in `src/home_finder/data/area_context.json` | The two copies agree today; only one of 48 saved outputs cites them |
| F20 | One renter's brief is built into the observation phase: "hosting 8+ guests", "office AND hosting 8+ people", E9, E15, N17. `is_spacious_enough` was never true in 48 saved runs or 60 test runs | Whether to pass the brief in per search is a product decision |
| F21 | `condition_concerns`, `concern_severity` and the concerns list overlap; 4 of 48 saved runs list concerns with severity "none" | Derivable in code, but needs a rule for what counts as significant |

## What fits and should stay

- **Stability:** the unpatched prompts gave valid results on all 15 control homes, with factual answers 6.0% different from the October run on older code.
- **Volume:** there is almost no capital-letter pressure language.
- **October prohibitions:** each names an observable failure and most give a reason. The tests give no reason to touch them.
- **Image handling:** images come before the text, each with a "Gallery image N:" label, which is what Anthropic's vision page recommends.
- **Caching:** the tool and system prompt are cached and contain nothing that changes between homes; no photo-analysis call missed the cache after the first home.
- **Tool steering:** naming the tool in the prompt is the documented replacement for forced tool choice, and the tool was called on every run.

## If any of these edits is taken up later

- **Tests:** prompt and schema text is pinned by snapshot tests. The revised patch broke 12 of 3,669 unit tests, all of them pins: nine in `tests/test_analysis/test_prompts.py` and one each in `test_analyzer.py`, `test_model_selection.py` and `test_provider_transport.py`.
- **Reanalysis cost:** any change to the prompt or tool changes their hashes, so saved photo-analysis checkpoints stop matching and a home analysed again pays for that call again.
- **Running a comparison:** the bake-off harness no longer imports against `main` because modules moved. The adapted runner in the evidence folder works, and a 15-home arm costs about $0.90.

## What a GPT-6.1 Sol version would change

OpenAI's guidance is to state each rule once, avoid conflicting rules, and reserve "always" and "never" for true invariants. A Sol version would also send the whole schema in strict mode, which makes field descriptions the main carrier of the rules, and these tests show how strongly a description steers an answer. That argues for building the eval before writing a second prompt, not after.

## Not verified

- **Which edit caused what:** both patches were tested as bundles.
- **Quality of the notes:** viewing notes, summaries and concerns were counted, not judged; no judge model or person rated them.
- **Sample:** 15 paired homes is small, and several differences are two or three homes. The flagship comparison covers 12 homes, and the flagship answers were produced with the old prompt.
- **The failed response:** the saved record does not show why the one photo-analysis response failed validation.
- **Vision page:** part of Anthropic's page was read as a summary, including the table that assigns models to resolution tiers.

Sources: [Prompting Claude Sonnet 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5), [Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices), [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs), [Vision](https://platform.claude.com/docs/en/build-with-claude/vision), [OpenAI prompt guidance](https://developers.openai.com/api/docs/guides/prompt-guidance-gpt-5p6), [October bake-off review](../reviews/2026-10-21-model-bakeoff-review.md).
