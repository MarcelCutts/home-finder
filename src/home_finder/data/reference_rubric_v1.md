# Home Finder reference rubric v1

Version: `home-finder-reference-v1`. Purpose: a small development reference for a
rental decision aid. This pack is historical evidence, not a current availability
check, an untouched holdout, or a statistically representative rental survey.
All labels start **unreviewed**. No generated assessment is an answer key.

## Review procedure

1. Open `index.html` locally. Review only the retained case material and this rubric.
   Open images at full resolution if needed. Do not inspect candidate model output,
   the application's previous ratings, or other reviewers' labels before your first
   pass. If you have already seen an answer, record that exposure outside the labels
   and keep the case in development; do not sign an independence declaration you
   cannot make. The owner may be the reviewer; a hired team is not required.
2. Mark each factual field supported, unknown or conflicting. Unreviewed means no
   review has happened, not that the fact is unknown. Supported requires a value
   and evidence; unknown requires a reason; conflicting requires a reason and at
   least two evidence citations. An unreadable plan is unknown, not a small home.
3. Cite the image/text identifier, a location such as "lower-right total" or
   "paragraph 2", and exact printed wording for advertised/printed facts. Preserve
   negation, conditions and units. A citation must belong to this exact case.
4. Optional owner preferences belong in the separate owner-judgment section, with
   an explicit profile version and rationale. Unknown facts cannot become a low
   suitability score. The profile should define the intended workspace and hosting
   requirements; a score without that context is not comparable across reviewers.
5. Download the first-review JSON and put it in a private label directory. The
   page starts blank on reload and does not save progress automatically. It offers
   two citations per fact; the JSON contract permits up to eight. Never publish
   the pack or labels in git. Validate with the CLI before treating a review as
   complete. Validation checks structure and provenance, not human identity or truth.
6. For ambiguous critical cases, request an independent second human check. Preserve
   their original annotation separately. Resolve disagreement with a documented
   rationale; otherwise keep unknown/conflicting. An adjudicator ID by itself is
   only a declaration and is not proof that this second check occurred.

## Exact field meanings

| Field | Supported reference | Unknown/conflicting examples |
| --- | --- | --- |
| Printed total area | Exact decimal printed as a total floor area, original sq ft or sq m, and the printed gross internal/net internal/gross external definition; use unspecified_total if no definition is printed. | Room dimensions alone; a computed room sum; a plan without a legible total. Incompatible totals/definitions are conflicting. |
| Bedrooms labelled on plan | Count rooms explicitly labelled bedroom, including labels such as bedroom/study. Zero is supported only by a complete legible plan showing no bedroom labels. | Marketing bedroom count without a plan; cropped or illegible plan; incompatible plans. This is a transcription, not a judgment of legal bedroom suitability. |
| Advertised EPC | Explicit current A–G band in retained source text or a legible certificate/plan image. | Potential rating only, inferred efficiency, generic area estimate, stale registry facts brought in from outside this pack. |
| Advertised council tax | Explicit band A–H in retained listing evidence. | Local authority's typical cost or a budgeting Band C default. This is an advertised claim, not a verified legal liability. |
| Explicit pets permission | True for unqualified permission; false for an explicit ban. Quote the statement verbatim. | "Considered", "subject to approval", silence, or conflicting source statements. Do not infer the legal effect of the advertised restriction. |
| All utility bills included | True for explicit "all utility bills included" (or equivalent); false for explicit exclusion of all utility bills. | Only water, internet or council tax listed; bare "bills included" without scope; conflicting statements. Preserve the quote for later, more detailed cost labels. |
| Bathtub visible | True when a bathtub is clearly visible in an actual photograph or unambiguous labelled fixture in a floorplan. False only if a complete legible plan explicitly identifies every bathroom as shower-only and retained photos do not contradict it. | Cropped bathroom photos, a shower that could be over a bath, an unexplained plan symbol, or lack of a bath photo. |

Keep gross floor area, room dimensions and usable living area distinct. Convert only
the transcribed value: the international foot is exactly 0.3048 metres, so 1 sq ft
is exactly 0.09290304 sq m. For example 500 sq ft is 46.45152 sq m; its measurement
definition remains unchanged. Do not silently compare a gross total with usable
living space, or an EPC area with a plan using a different date or method.

No photograph proves sound insulation, broadband speed, legal compliance, current
availability, the completeness of a listing, or permission to share its contents.
Historical descriptions may be absent or backfilled. File recovery time is not
the time the listing or image was originally observed.

## Preference scale and critical errors

For an explicitly named owner profile, use workspace and hosting scores 1–5:
1 clearly unsuitable, 2 important compromises, 3 workable with stated compromises,
4 good fit, 5 especially good fit. Explain the evidence and tradeoffs in the
rationale. `retain_for_review` means worth further human review, not a viewing
recommendation or a financial decision. Use insufficient_evidence when the whole
judgment cannot be made; do not fill the missing scores with ones.

Critical errors include inventing an unsupported factual answer; reversing a
negation or conditional permission; confusing sq ft and sq m; passing off a room
sum as a printed total; treating potential EPC as current; silently merging
conflicting sources; and rejecting a promising home on an unsupported assumption.
References must preserve correct abstention. Candidate evaluation later measures
unsupported assertions, missed evidence and abstention separately, plus schema
failures and the total cost of a useful complete assessment.

## Versioning, coverage and holdout gate

Freeze input bytes, rubric and the evaluation protocol before a comparison. Store
original reviewer files as immutable copies with checksums. A correction has an
incremented revision and the prior annotation's SHA-256 in `supersedes_sha256`;
retain that prior file and record the reason in the changed field. The CLI checks
the revision declaration but does not reconstruct or certify its audit chain.
Corrections must not be silently made after looking at candidate answers.

The supplied pack uses one canonical example per connected group from aliases,
provider identity/URLs and identical cached image bytes. These conservative groups
reduce leakage; they do not establish distinct physical homes or detect every
crop, re-encoding or relisting. Source, area, bedroom bucket, floorplan presence
and gallery size guide development selection. Media quality, tenure/room type,
price bands, address uncertainty and text polarity still need human coverage review.
Missing historical text cannot qualify fresh extraction of amenities or costs.

This first pack is development-only because prior exposure is not established.
Create an exposure-checked holdout with separate connected groups and fresh,
appropriately retained text before a promotion decision. Forty to sixty cases are
a practical first review, not a precision guarantee. No model agreement, OCR output
or agent-generated label substitutes for independent human verification.

References: [NIST international-foot definition](https://www.nist.gov/pml/us-surveyfoot),
[grouped validation guidance](https://scikit-learn.org/stable/modules/cross_validation.html#cross-validation-iterators-for-grouped-data),
[dataset documentation](https://arxiv.org/abs/1803.09010).
