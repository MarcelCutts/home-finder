# Research Prompt: E2 Comprehensive Profile

I'm researching the **E2 postcode area** (Bethnal Green, Haggerston, Shoreditch, Cambridge Heath) in London for a rental property finder. E2 sits primarily in **Tower Hamlets** with some parts in **Hackney**. I need structured data covering 6 specific topics. Return everything as a single JSON object matching the schemas below exactly.

```json
{
  "noise_enforcement": {
    "Tower Hamlets": {
      "process": "How to report noise complaints, out-of-hours service details",
      "threshold_info": "What constitutes a statutory nuisance, any specific dB thresholds",
      "response_time": "Typical response times for noise complaints"
    }
  },

  "hosting_tolerance": {
    "E2": {
      "rating": "high | moderate | low",
      "notes": "2-3 sentences on noise/hosting tolerance in E2. Mention Shoreditch nightlife context vs quieter Bethnal Green residential streets.",
      "known_friendly_areas": ["Areas where hosting/music events is more tolerated"],
      "known_sensitive_areas": ["Areas where neighbours are more likely to complain"]
    }
  },

  "creative_scene": {
    "E2": {
      "rehearsal_spaces": ["Name (location/distance context)"],
      "venues": ["Name (location/distance context)"],
      "creative_hubs": ["Name (location context)"],
      "summary": "2-3 sentences on E2's creative/music scene. Shoreditch is a major nightlife and arts hub — cover that plus the quieter creative spaces in Bethnal Green."
    }
  },

  "days_on_market": {
    "E2": {
      "avg_days": 0,
      "note": "Brief note on market speed and negotiation potential"
    }
  },

  "environmental_risks": {
    "E2": {
      "flood_risk": {
        "zone": "1 | 2 | 2-3 | 3",
        "affected_streets": ["Specific streets with elevated flood risk"],
        "history": "Any recent flooding events",
        "mitigation": "Risk context — ground floor vs upper, proximity to canals/rivers"
      },
      "air_quality": {
        "rating": "good | moderate | poor",
        "worst_roads": ["Specific roads with worst NO2/PM2.5"],
        "notes": "Context on distance from major roads, ULEZ impact"
      },
      "other_risks": ["Any land contamination, noise pollution, or other environmental concerns"]
    }
  },

  "energy_and_costs": {
    "tower_hamlets_council_tax_monthly": {
      "A": 0, "B": 0, "C": 0, "D": 0
    },
    "tower_hamlets_rent_trend": {
      "yoy_pct": 0.0,
      "direction": "rising | stable | falling"
    }
  }
}
```

## Context for answering

- **Hosting tolerance:** The user runs music events and wants to host without being antisocial. Warehouse/industrial conversions and areas near nightlife are more tolerant.
- **Creative scene:** Focus on music rehearsal spaces, live music venues, and creative/maker hubs within cycling distance.
- **Days on market:** Use any available data on average time-to-let in E2 from Rightmove, Zoopla, or estate agent reports.
- **Environmental risks:** Be specific about streets. E2 has parts near Regent's Canal — check flood risk there. Shoreditch has major roads (A10 Kingsland Road, Great Eastern Street).
- **Council tax:** Use current 2025-2026 Tower Hamlets rates.
- **Rent trends:** Use latest ONS or Homelet rental index data for Tower Hamlets.

Be specific and cite sources where possible. I need real data I can put directly into a reference database.
