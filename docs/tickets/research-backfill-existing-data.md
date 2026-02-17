# Research Prompt: Backfill Existing Data Gaps (E2 + E8)

I'm building a London rental property finder with a static JSON reference data file. Two postcodes — **E2** (Bethnal Green, Haggerston, Shoreditch, Cambridge Heath) and **E8** (Hackney Central, Dalston, London Fields) — are missing from several data structures. I need you to produce entries matching the exact schemas below.

**Important:** E2 straddles Tower Hamlets and Hackney boroughs. For `outcode_borough`, use the primary borough (Tower Hamlets — most of E2 is in Tower Hamlets). Note the split in the area context text.

Please return all data as a single JSON object with the following structure:

```json
{
  "area_context": {
    "E2": {
      "overview": "2-3 paragraphs covering: rental market position relative to neighbours, transport links, character, value pockets, notable issues. Write for someone choosing between E2 and nearby E8/E3/E1. Mention the Tower Hamlets/Hackney borough split.",
      "micro_areas": {
        "Bethnal Green": {
          "character": "1-2 sentences on neighbourhood character",
          "transport": "Nearest stations and key bus routes",
          "creative_scene": "Local creative infrastructure",
          "broadband": "FTTP/fibre availability",
          "hosting_tolerance": "high | moderate | low",
          "wfh_suitability": "good | moderate | poor",
          "value": "Relative value within E2"
        },
        "Haggerston": { "...same fields..." },
        "Shoreditch": { "...same fields..." },
        "Cambridge Heath": { "...same fields..." }
      }
    },
    "E8": {
      "overview": "Same format as E2 — market position, transport, character, value pockets. E8 is Hackney borough.",
      "micro_areas": {
        "Dalston": { "...same fields..." },
        "London Fields": { "...same fields..." },
        "Hackney Central": { "...same fields..." },
        "Hackney Downs": { "...same fields..." }
      }
    }
  },
  "outcode_borough": {
    "E2": "Tower Hamlets",
    "E8": "Hackney"
  },
  "crime_rates": {
    "E2": {
      "rate": "(crimes per 1,000 residents/yr — use latest ONS/Met Police data)",
      "vs_london": "above | below | around",
      "risk": "low | low-medium | medium | medium-high | high",
      "note": "Optional context — e.g. 'Shoreditch nightlife area inflates stats; residential streets are calmer'"
    },
    "E8": { "...same fields..." }
  },
  "rental_benchmarks": {
    "E2": {"1": 0, "2": 0, "3": 0},
    "E8": {"1": 0, "2": 0, "3": 0}
  }
}
```

For `rental_benchmarks`: provide realistic median monthly rents (PCM) for 1-bed, 2-bed, and 3-bed flats in each outcode as of late 2025. Use Rightmove, Zoopla, or ONS rental data.

For `crime_rates`: use the latest Met Police / ONS neighbourhood crime data. London average is approximately 85 crimes per 1,000 residents per year.

Be specific and data-driven. I need real numbers, not vague assessments.
