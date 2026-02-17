# Scraping beyond the Big Four: is it worth it for East London rentals?

**The coverage gap from the four major aggregators is real but narrow.** Rightmove and Zoopla together capture roughly 90–95% of agency-listed rental properties, and adding OnTheMarket and OpenRent closes most of the remaining gap. The genuinely missing listings fall into two small but actionable categories: properties in the 0–48 hour window before agents syndicate to portals, and private landlord listings on SpareRoom that never reach aggregators at all. For a focused East London search, **adding 3–5 carefully chosen agent scrapers plus SpareRoom would meaningfully improve speed without a proportionate maintenance burden**, while scraping beyond that hits diminishing returns fast.

---

## The aggregators already cover more than you'd expect

Rightmove dominates UK lettings with roughly **70% of portal-driven instructions** and claims that 3 in 4 tenancies agreed originate from a Rightmove enquiry. Zoopla sits at an estimated 50–60% coverage. Together they capture the vast majority of agency-listed rentals. OnTheMarket adds a 24-hour exclusivity window on around 4,000 new listings per month nationally (its "Only With Us" feature), giving a small but genuine speed advantage. OpenRent now accounts for **~17% of all new UK lettings instructions** (TwentyEA data, Q2 2025), up 189% since pre-pandemic — and crucially, landlords on its free tier list only on OpenRent, not on Rightmove or Zoopla.

The real coverage gap is smaller than many renters assume. An academic study (Taylor & Francis, 2024) found that **96% of UK renters found their property via just four platforms**: Rightmove, SpareRoom, Zoopla, or Gumtree. For the £1,400–2,200/month 1–2 bed range in East London, true off-market lettings are rare — that phenomenon is concentrated in the £3,000+ corporate/diplomatic relocation segment. Agents like John D Wood explicitly market "discreet marketing" for high-end landlords, but as one relocation consultant put it: "There isn't an exclusive list of off-market rental homes in London for you to tap into — unless your budget puts you in the same league as a Russian oligarch."

What *does* exist is a **timing gap**. Agents routinely share new instructions with their registered applicant database before uploading to portals. Typical delay is 0–48 hours, sometimes up to a week. This is deliberate — agents use "I'm calling you before it's on Rightmove" as a value proposition. The delay is entirely at the agent's discretion; Rightmove's technology allows listings to go live within minutes. No evidence suggests East London agents systematically avoid portals, but the density of small independents in Hackney and Stoke Newington means the pre-portal window is where scraping agent sites adds genuine value.

---

## Which East London agents are worth scraping

The table below ranks agents by three factors: lettings volume in the target areas, likelihood of having exclusive or early listings, and how scrapable their websites are. Two agents — Daniel Henry (Northern Ireland only) and Pedder (South East London only) — from the original query list are not relevant and are excluded.

### Tier 1: High volume, strong area coverage

| Agent | Type | Website | Key areas covered | Lettings search | Syndication | Scrapability |
|-------|------|---------|-------------------|-----------------|-------------|-------------|
| **Keatons** | Independent chain | [keatons.com](https://www.keatons.com/lettings/) | Hackney, Dalston, Clapton, Stamford Hill, Stoke Newington, Stratford, Leyton, Walthamstow | Yes — filters, price range, bedrooms | Rightmove ✓ Zoopla ✓ | Medium — WordPress front-end, Reapit CRM backend; search results may need JS rendering |
| **Dexters** | Major chain (80+ offices) | [dexters.co.uk](https://www.dexters.co.uk/property-lettings/properties-to-rent-in-hackney) | Hackney, Dalston, Clapton, Finsbury Park, Leyton | Yes — excellent URL-based filters, pagination, area pages | Rightmove ✓ Zoopla ✓ | **Best** — Starberry platform, clean server-rendered HTML, structured property cards |
| **Castles** | Independent chain (est. 1981) | [castles.london/rent](https://www.castles.london/rent) | Stoke Newington, Hackney, Harringay, Tottenham | Yes — area-based browsing, clean pages | Rightmove ✓ Zoopla ✓ | Good — WordPress, clean area-segmented pages (`/rent/area/hackney`) |
| **Foxtons** | Public chain | [foxtons.co.uk](https://www.foxtons.co.uk/contact/foxtons_hackney_estate_agents) | Hackney, Dalston, Clapton, Stoke Newington, Leyton | Yes — full search system | Rightmove ✓ Zoopla ✓ | **Poor** — React SPA with session management; everything already on portals anyway |
| **Winkworth** | Franchise chain | [winkworth.co.uk](https://www.winkworth.co.uk/branches/stoke-newington/properties-to-let) | Stoke Newington, Hackney, Clapton, Dalston | Yes — area filtering, pagination (lazy-load) | Rightmove ✓ Zoopla ✓ | Medium — some JS lazy-loading for results |

### Tier 2: Medium volume, good area fit

| Agent | Type | Website | Key areas covered | Notable details |
|-------|------|---------|-------------------|-----------------|
| **Anthony Pepe** | Independent (est. 1987) | [anthonypepe.com](https://www.anthonypepe.com) | Harringay, Finsbury Park, Manor House, Seven Sisters | Dominant on the Green Lanes / N4 corridor; 7 offices; 2,254+ Google reviews |
| **Hunters** | Franchise chain | [hunters.com](https://www.hunters.com/office/stoke-newington/) | Stoke Newington, Stamford Hill, Clapton, Dalston | Syndicates to Rightmove, Zoopla, and OnTheMarket |
| **Homefinders** | Independent (est. 1988) | [homefinders.net](https://www.homefinders.net) | Dalston, Hackney, Stratford | Named Best Estate Agency in E8 (2024 & 2025); offers guaranteed rent — large managed portfolio |
| **Bigmove** | Independent (est. 2007) | [bigmove.uk.com](https://www.bigmove.uk.com) | Dalston, Finsbury Park, Hackney, Clapton, Manor House, Stoke Newington, Stamford Hill | Broadest area overlap of any single independent agent |
| **Felicity J. Lord** | Chain (Spicerhaart) | [fjlord.co.uk](https://www.fjlord.co.uk/branch-finder/stoke-newington/) | Stoke Newington, Clapton, Hackney | 138 properties let in last 12 months; notably lists on Rightmove and OnTheMarket but **not Zoopla** |
| **Alex Crown** | Independent (est. 2013) | [alexcrown.co.uk](https://www.alexcrown.co.uk) | Finsbury Park, Manor House (peripherally) | Primary focus is Archway/Holloway/N19; peripheral to target areas |

### Tier 3: Hyper-local independents with exclusive potential

| Agent | Type | Website | Key areas | Why they matter |
|-------|------|---------|-----------|-----------------|
| **New Space** | Lettings specialist | [newspaceuk.com](https://www.newspaceuk.com) | Hackney, Dalston, Clapton, Stoke Newington | Lettings-only specialist in Haggerston — highest chance of pre-portal exclusives |
| **Oakwood** | Independent (est. 1994) | [oakwoodestateagents.com](https://oakwoodestateagents.com) | Stoke Newington (N16) | 30+ years hyper-local; Gold Sales Winner 2025 & 2026 |
| **Michael Naik & Co** | Independent (est. 1985) | [michaelnaik.com](https://www.michaelnaik.com) | Stoke Newington (N16) | British Property Award winner for N16; uses Boulevard platform |
| **Bennett Walden** | Independent (est. 1994) | [bwofhackney.co.uk](https://www.bwofhackney.co.uk) | Hackney (E8) | 30 years as Hackney specialist; strong lettings/management |
| **Wild & Co** | Independent | [wildandco.uk](https://wildandco.uk) | Clapton, Hackney | Family-run, Lower Clapton office |
| **Next Move** | Independent (est. 1987) | [nextmove.com](https://nextmove.com) | Stoke Newington, Clapton | Long-established N16 agent |

### Tier 4: Walthamstow / Leyton / Stratford specialists

| Agent | Type | Website | Key areas |
|-------|------|---------|-----------|
| **Stow Brothers** | Independent | [stowbrothers.com](https://www.stowbrothers.com) | Walthamstow, Leyton, Stratford |
| **Kings Group** | Chain | [kings-group.net](https://www.kings-group.net/branches/walthamstow/) | Walthamstow, Leyton, Stratford |
| **Victor Michael** | Independent | [victormichael.com](https://victormichael.com) | Stratford, Leyton, Walthamstow |
| **Wonderlease** | Independent (est. 1988) | [wonderlease.co.uk](https://www.wonderlease.co.uk) | Walthamstow, Leyton, Stratford |
| **E10 & E17 Homes** | Independent | [e10ande17homes.co.uk](https://www.e10ande17homes.co.uk) | Leyton, Walthamstow |
| **Robert Alan Homes** | Independent (est. 2002) | [robertalanhomes.com](https://robertalanhomes.com) | Hackney, Clapton, Leyton, Walthamstow |
| **Living London** | Independent | [living.london](https://www.living.london) | Tottenham, Seven Sisters, Tottenham Hale |

**Not relevant:** Daniel Henry operates only in Northern Ireland. Pedder operates only in South East London (SE postcodes). "Living in London" at living-london.net is a Canada Water agent — the Tottenham agent is "Living London" at living.london.

---

## Alternative platforms beyond agent websites

**SpareRoom is the standout non-aggregator source.** It lists approximately **2,200 studio/1-bed flats across London** at any given time, with strong coverage in the £1,400–2,200 range. Many are posted by private landlords or departing tenants who never cross-post to Rightmove or Zoopla. The site is highly scrapable — public pages, clean URL parameters (`spareroom.co.uk/flatshare/east_london?showme_1beds=Y`), and no login required for browsing. Despite its reputation as a flatshare site, SpareRoom's "Studio/1 bed flats" and "Whole properties" categories contain substantial numbers of independent units. For East London zones 3–4 specifically, around **330–590 relevant listings** were visible at the time of research.

**Gumtree still has London rentals but the signal-to-noise ratio is poor.** A Hackney search returned ~113 listings, of which only 17 were private (non-agency). Most agency listings duplicate what's already on Rightmove/Zoopla. Scam risk is well-documented — Action Fraud recorded **5,751 rental scam cases nationally in 2023** with £9.4M in losses, and Gumtree is frequently cited as a vector. OpenRent also auto-distributes some listings to Gumtree, further reducing the unique content. Worth monitoring at low priority but the engineering effort may not justify the handful of genuinely exclusive listings.

**Facebook groups contain unique landlord-direct listings but are essentially unscrappable.** Private landlords and departing tenants post in area-specific rental groups, and these listings often appear nowhere else. However, Facebook requires login, groups require membership approval, and Facebook's terms of service prohibit scraping. This is a manual-monitoring channel, not an automated one.

**Several platforms from the query are defunct or irrelevant.** Movebubble appears to have shut down or pivoted away from rentals — the domain no longer shows rental content. Mashroom sold its property management portfolio in 2024 and pivoted to agent lead generation. Ideal Flatmate is still active but focused on flatmate matching, not whole-property lettings. Spotahome, HousingAnywhere, and Nestpick target the furnished/short-term/expat segment at price points above the target range. Council lettings portals (ELLC ChoiceHomes for Hackney/Waltham Forest, HomeConnections for Haringey) are social housing allocation systems — a professional renter at £1,400–2,200/month would not qualify.

---

## The main value is speed, not coverage

The data points clearly toward **speed over coverage** as the primary benefit of scraping agent websites. With Rightmove + Zoopla + OnTheMarket + OpenRent, you're already capturing well over 95% of rentals that will ever be publicly listed in these areas. The properties you'll miss entirely are a small number of private landlord listings on SpareRoom and an even smaller number on Gumtree/Facebook — perhaps **3–5% of the total market**.

The speed advantage, however, is tangible. In a market where desirable East London 1–2 bed flats receive dozens of enquiries within hours, seeing a listing **24–48 hours before it hits Rightmove** is a meaningful competitive edge. This is where agent website scrapers deliver value: not by finding hidden inventory, but by catching listings during the pre-syndication window when competition is lower.

The strongest speed advantage comes from scraping **independents with high lettings volume** — agents like Keatons, Castles, and Homefinders who manage large rental portfolios and routinely share properties with their own database before portal upload. Major chains like Foxtons and Dexters syndicate more quickly (often same-day) and have less exclusive pre-portal inventory, making them lower priority for scraping despite higher volume.

---

## Tiered recommendation for what to build

**Definitely add** (high yield relative to engineering effort):

- **SpareRoom** — the single highest-value addition. Significant unique listings in the target price range, excellent scrapability, and zero overlap with your existing aggregators. Filter to studio/1-bed/2-bed whole properties in East London.
- **Dexters** (dexters.co.uk) — clean Starberry HTML, URL-based filtering, high volume across Hackney and Finsbury Park. The best ratio of scrapability to listing volume of any agent site.
- **Castles** (castles.london) — WordPress site with clean area-segmented pages. Strong coverage of Stoke Newington, Hackney, Harringay, and Tottenham. Easy to build and maintain.
- **Keatons** (keatons.com) — the dominant independent in Hackney with broad area coverage. Reapit CRM backend may require some JS rendering, but worth the effort given their volume.

**Consider adding** (moderate yield, justify if you have engineering capacity):

- **Anthony Pepe** (anthonypepe.com) — essential if Harringay/Finsbury Park/Manor House are high-priority areas, as they dominate that N4 corridor and no other agent covers it as thoroughly.
- **Homefinders** (homefinders.net) — guaranteed-rent model means they manage properties that may appear on their site before portals. Good Dalston/Stratford coverage.
- **Bigmove** (bigmove.uk.com) — covers nearly all target areas from a single independent office. Website is basic, which may mean simpler scraping.
- **Stow Brothers** (stowbrothers.com) — best independent coverage of Walthamstow/Leyton/Stratford if those areas are high priority.
- **Gumtree** (gumtree.com) — only if you filter aggressively to private listings and implement scam-detection heuristics. The 15–20 private Hackney listings at any given time are the only unique content.

**Probably not worth it** (low marginal yield given existing coverage):

- **Foxtons** — very high volume but everything syndicates to Rightmove/Zoopla same-day, and the React SPA is the hardest to scrape. Near-zero exclusive listings.
- **Winkworth** — moderate volume but syndicates promptly; lazy-loading adds scraping complexity.
- **Tier 3 hyper-local independents** (Oakwood, Michael Naik, Bennett Walden, Wild & Co, Next Move, New Space) — each has only a handful of lettings at any time. The collective maintenance burden of 6+ scrapers for perhaps 1–3 exclusive listings per month is hard to justify unless you can build a generic scraper framework that handles multiple sites cheaply.
- **Tier 4 Waltham Forest agents** (Kings Group, Victor Michael, Wonderlease, E10 & E17 Homes) — same logic as Tier 3 unless Walthamstow/Leyton is your primary search area.
- **Facebook groups** — unscrappable without violating TOS; manual monitoring only.
- **Movebubble, Mashroom, Ideal Flatmate** — defunct, pivoted, or irrelevant for whole-property search.
- **Council lettings portals** — social housing; not applicable at £1,400–2,200/month.
- **Spotahome, HousingAnywhere, Nestpick** — wrong market segment (furnished/expat/short-term).

## Conclusion

The four aggregators already provide excellent coverage for East London rentals. The genuine gap is narrow: **SpareRoom for unique private listings, and 3–4 high-volume independent agent sites for the 24–48 hour speed advantage before portal syndication.** Dexters, Castles, and Keatons offer the best combination of volume, area coverage, and scrapability. Beyond this core set of additions, each extra scraper delivers diminishing returns — the engineering cost of building, monitoring, and maintaining scrapers for small independents with 2–3 active lettings rarely justifies the marginal gain. A pragmatic approach is to build your "definitely add" tier first, measure how many genuinely new or early listings you capture over 4–6 weeks, and let that data guide whether to expand further.