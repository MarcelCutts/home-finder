# PR17a: Honest tenant costs and rental guidance

**Date:** 28 September 2026. **State:** implemented and verified; not deployed.
**Parent:** [delivery plan, PR17](2026-09-28-research-synthesis-and-delivery-plan.md#pr17--make-costs-market-context-and-rental-guidance-evidence-based-m).
**Predecessor:** PR06a, commit `19d6f24`. No schema migration, paid calls, source collection or messages sent.

## Problem and resulting behaviour

A local reproduction with only £1,800 rent and `property_type=new_build` returned a “true monthly cost” of **£2,171–£2,421**. It assumed EPC D, fixed water/broadband costs and a £200–£450 leaseholder service charge. A blanket bills-included input still added council tax without establishing whether it was covered. Another reproduction combined a -3% numeric trend with “rising strongly” text and produced **“rising strongly (+-3.0% YoY)”**. Collector age of 200 days became “significantly stale” and strengthened a fixed-discount recommendation.

The dashboard now presents **Rent and bills**, an **advertised subtotal**, and an explicit **incomplete budget** label. This is useful even before an AI assessment exists. The current retained source text, rather than a model's financial guesses, determines named council-tax, gas, electricity, water and broadband inclusions/exclusions. Generic “bills included” or “bills excluded” wording alone does not specify coverage. Conflicts between providers remain visible and unpriced.

Only clearly advertised separate monthly bill amounts/ranges enter the subtotal. The intentionally small parser accepts explicit named-bill wording and an amount form such as `Water: £30–£40 per month payable separately`. It preserves source-labelled quotations for review. Questions, assumptions, conditional wording, nonmonthly amounts, inverted ranges, conflicting amounts and unrecognised language do not become a payment. This is conservative textual support, not a general natural-language parser, contract interpretation or legal finding that a payment is due. Unsupported wording stays in the source description for human review.

Service charge/ground rent mentions, including an earlier model-extracted amount, create a review question about tenant liability and rent inclusion. They are not added to rent. Unpriced utility costs are not zero. The view explains household/tariff/council-tax uncertainty and excludes deposits, moving costs, TV licensing and optional insurance from its monthly subtotal. It does not invent a generic utility range: the bundled rates have no effective period or adequate applicability metadata. Current sourced estimates and their household assumptions remain PR17b.

Both model prompts now withhold generic Band C/EPC D cost defaults. Listing-only extraction is separate from the explicitly unverified historical comparator and dated tenancy guidance. Fixed stock-type rent premiums/discounts and service-charge additions are removed from the cost instructions and schema description. EPC, like council tax, must have a single matching non-estimated textual band in the supplied listing inputs; conflicting, missing or potential ratings become unknown. This does not verify the portal's assertion against an official register.

The cost view reads current full retained source text, so a later correction can change the displayed band/bill treatment without a new image call. Older unsupported EPC/council-tax claims are labelled unverified rather than rewritten in storage. Cards and Telegram qualify old EPC claims; Telegram no longer presents service charges as a `+£…` tenant cost or generic bill inclusion as complete coverage. Historical AI prose remains available with an explicit assessment/evidence qualification. This slice is not a blanket audit of every historical sentence or a claim that all housing/acoustic research is now verified.

Offer preparation reports collector observations and recorded price changes without assigning negotiating strength, inferring continuous availability, or recommending a percentage discount. The dashboard sort, age labels and Telegram price-update age use first-observed wording. Numeric trend sign controls direction consistently; undated trends, research rent comparisons, council-tax tables and broad area context are labelled unverified. Existing deterministic value recomputation remains and its comparison is labelled accordingly; no London market median is inferred from the filtered catalogue.

## Primary-source checks

Checked 28 September 2026:

- [Government fee guidance](https://www.gov.uk/guidance/fees-you-can-charge-as-part-of-a-tenancy) distinguishes rent and permitted payments and requires agreement about separately charged utilities. Inclusion is not a basis to charge twice. The application does not infer that a service-charge label is a permitted separate tenant fee.
- [England tenancy overview](https://www.gov.uk/guidance/renters-rights-act-overview-for-tenants) and [assured periodic tenancy bidding guidance](https://www.gov.uk/assured-periodic-tenancies-tenants/rental-bidding) support the dated guidance: from 1 May 2026, covered landlords/agents must not invite or accept rent above the advertised amount. The UI names England private assured periodic tenancies and the different treatment of lodgers, social housing and some student accommodation; it does not decide an individual property's tenancy category.
- [Council-tax band lookup](https://www.gov.uk/council-tax-bands) and [how council tax is calculated](https://www.gov.uk/council-tax) separate the property's band, council rate and household relief. [Official EPC lookup](https://www.gov.uk/find-energy-certificate) is available for checking the actual certificate. The UI links the registers rather than presenting a portal/model claim as verification.
- [Ofgem unit rates and standing charges](https://www.ofgem.gov.uk/your-energy-supply/your-energy-bill/energy-price-cap-unit-rates-and-standing-charges) vary by region, payment method and effective period. A headline typical-household cap cannot supply this flat's monthly bill. No national headline figure or future quarter's figure is silently inserted into the budget.

The small guidance record has scope, effective date, check date and primary links. It needs review when applicable rules change. PR17b still owns a full versioned reference registry, expiry/application rules, household scenarios and replay of prior evaluations; this patch does not claim that larger contract is complete.

## Verification and data preservation

A fresh read-only audit of the frozen reference found **940 council-tax-band claims and 24 EPC claims in 1,001 analyses**, with **zero retained source descriptions** and no selected-input records. These counts establish absent retained support, not that every original claim was false or that historical model calls definitely lacked text. The app neither upgrades these claims to verified facts nor rebills the historical catalogue.

| Check | Result |
|---|---|
| First focused cost/model/route/Telegram/data subset | 613 passed; one expected old area-heading assertion subsequently corrected |
| New budget and prompt regressions | 76 passed; the remaining new test was corrected to preserve the existing deterministic price/value recalculation, then passed |
| Chromium suite after final labels/typography | 57 passed; two existing deprecation warnings; includes 375px/1280px budget readability, no horizontal overflow, guidance links |
| Ruff lint/format, strict mypy, diff checks | Passed; 173 formatted Python files, 69 source files checked |
| Final SDK/ambiguous-band/card subset | 37 passed; includes supported, absent and conflicting EPC evidence through mocked SDK HTTP requests |
| Full Python 3.11.16 suite | 2,453 passed; four existing deprecation warnings; 85.89% coverage |
| Full Python 3.14.7 suite | 2,453 passed; four existing deprecation warnings; 85.79% coverage |

The integration case writes source claims, renders the budget, changes rent, explicitly clears source text and renders again. The subtotal follows the current rent; cleared evidence becomes unknown. Text updates and page reads leave the saved AI assessment unchanged. The existing price-change path updates only its deterministic value comparison; retained listing extraction and visual observations remain unchanged. No model client is invoked by the budget code. Tests also cover conflicting providers, feature-only evidence, HTML, uncertainty, unsupported generic defaults and negative/invalid trend values.

Review images: [mobile](../reviews/2026-09-28-pr17a-budget-mobile.png) and [desktop](../reviews/2026-09-28-pr17a-budget-desktop.png), using synthetic browser fixtures with the guidance disclosure expanded. Local logs are under `/tmp/home-finder-pr17a/`. The frozen database was read through a read-only immutable connection after checking for a nonempty WAL; no initialization or migration occurred.

The final SHA-256 remains `e50897b46ebefea0119fd62951ac277d764684f9590d52801e66c964b1352c63`. Prompt/schema snapshot changes were reviewed. Obsolete tests asserting guessed costs and negotiation weights were replaced with evidence and unknown-state regressions; the lower test count is not a reduced coverage threshold.

## Recovery and next segment

No schema or persisted history change is required. A code rollback that restores the known misleading totals is not an acceptable presentation fallback; keep this budget view/unknown-state handling in a protected compatible recovery build. PR06a's schema-8 compatibility requirement still applies to any older build.

**Next: PR16a**, explicit commute unknown/error states and consistent advisory handling/accounting. Current route-provider entitlements, versioned profiles/scenarios, cache permission and reference estimates remain their later gates. Continue verified commits; this slice does not complete the hosted-service goal.
