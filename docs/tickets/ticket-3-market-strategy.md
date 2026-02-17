# Ticket 3: Rental Market Strategy Dashboard Section

**Depends on:** Nothing (fully independent, parallel-safe)
**Blocks:** Nothing

## Goal

Add a "Market Intelligence" reference section to the web dashboard giving Marcel strategic rental advice. This is informational content, not per-property data — it sits alongside the property search as a standalone page. It's entirely decoupled from the analysis pipeline.

---

## 3A. Add market intelligence data

### New file: `src/home_finder/data/market_intelligence.json`

```json
{
  "seasonal_patterns": {
    "best_months": ["November", "December", "January"],
    "worst_months": ["August", "September"],
    "explanation": "Competition drops 30-40% in winter as most tenancies start in summer. Landlords listing in Nov-Jan are more likely to negotiate — they're trying to avoid void periods over the holidays. August/September is peak demand from professionals relocating and students, creating bidding wars."
  },
  "negotiation": {
    "success_rate": "10-15% of London rentals accept some negotiation, rising to 25-30% for properties listed 14+ days",
    "signals": [
      "Listed 14+ days (check first_seen date or listing history)",
      "Price reduction already applied (shows landlord flexibility)",
      "Relisted after being taken off market",
      "Multiple similar properties available in the building",
      "End of month listing (landlord wants to fill before next void month)",
      "Winter listing (Nov-Feb — lower competition)",
      "Agent-managed vs direct landlord (agents more likely to negotiate)"
    ],
    "tips": [
      "Lead with your strengths: stable self-employed income, excellent references, long tenancy intent",
      "Offer a longer tenancy (18-24 months) in exchange for lower rent — landlords value stability",
      "Offer to pay 3-6 months upfront if cash flow allows — reduces landlord risk",
      "Don't negotiate below 5% unless multiple signals align — small asks get rejected",
      "Time your offer for Friday afternoon — decision makers want to close before the weekend",
      "If rent is firm, negotiate on other terms: break clause timing, decoration rights, pet clause"
    ]
  },
  "self_employed_tips": {
    "documentation": [
      "2-3 years of SA302 tax calculations from HMRC (most agents require 2 minimum)",
      "Accountant's reference letter confirming income stability",
      "Company accounts if operating through a limited company",
      "Bank statements showing consistent income (6-12 months)",
      "Proof of current/past tenancy with landlord reference",
      "Business insurance documentation (shows legitimacy)"
    ],
    "positioning": "Frame as 'director of a software consultancy' rather than 'freelancer'. Emphasise: WFH full-time (always present, property well-maintained), stable recurring revenue from long-term clients, higher effective income than gross salary suggests due to tax efficiency. Offer guarantor or rent deposit scheme if asked."
  },
  "days_on_market": {
    "E3": {"avg_days": 10, "note": "High demand for canal-side living"},
    "E5": {"avg_days": 14, "note": "Moderate demand, good for negotiation on older listings"},
    "E8": {"avg_days": 8, "note": "Fastest-moving market in East London"},
    "E9": {"avg_days": 12, "note": "Varies by proximity to Hackney Wick vs Homerton"},
    "E10": {"avg_days": 16, "note": "Leyton — slower market, more negotiation room"},
    "E15": {"avg_days": 14, "note": "Stratford — variable by development vs established"},
    "E17": {"avg_days": 15, "note": "Walthamstow — popular but more supply"},
    "N15": {"avg_days": 18, "note": "Seven Sisters — slower, most negotiable in search area"},
    "N16": {"avg_days": 9, "note": "Stoke Newington — high demand, limited supply"},
    "N17": {"avg_days": 20, "note": "Tottenham — emerging, most room for negotiation"}
  },
  "platform_strategy": {
    "fastest_listings": "OpenRent — direct from landlords, often listed before agents get involved",
    "most_listings": "Rightmove — aggregates most agency listings, best for comprehensive search",
    "best_for_value": "OpenRent — no agent fees means landlords can price lower",
    "notes": "Zoopla and OnTheMarket have overlapping but not identical listings to Rightmove. Worth monitoring all four. Properties appear on Rightmove 1-3 days after listing on agency websites. OpenRent listings often appear same-day."
  }
}
```

### New file: `src/home_finder/data/market_intelligence.py`

```python
"""Static market intelligence data for London rental strategy.

Loaded from market_intelligence.json — informational reference data for the
dashboard, not injected into per-property analysis.
"""

import json
from pathlib import Path
from typing import Any, Final

_DATA_PATH = Path(__file__).resolve().parent / "market_intelligence.json"
_DATA: dict[str, Any] = json.loads(_DATA_PATH.read_text())

SEASONAL_PATTERNS: Final[dict[str, Any]] = _DATA.get("seasonal_patterns", {})
NEGOTIATION: Final[dict[str, Any]] = _DATA.get("negotiation", {})
SELF_EMPLOYED_TIPS: Final[dict[str, Any]] = _DATA.get("self_employed_tips", {})
DAYS_ON_MARKET: Final[dict[str, dict[str, Any]]] = _DATA.get("days_on_market", {})
PLATFORM_STRATEGY: Final[dict[str, str]] = _DATA.get("platform_strategy", {})
```

---

## 3B. Add web route and template

### File: `src/home_finder/web/routes.py`

Add a new route. The existing router is defined as `router = APIRouter()` and mounted on the FastAPI app. Add after the existing routes:

```python
from home_finder.data.market_intelligence import (
    SEASONAL_PATTERNS,
    NEGOTIATION,
    SELF_EMPLOYED_TIPS,
    DAYS_ON_MARKET,
    PLATFORM_STRATEGY,
)

@router.get("/market-intelligence", response_class=HTMLResponse)
async def market_intelligence(request: Request) -> HTMLResponse:
    """Market intelligence reference page."""
    return templates.TemplateResponse(
        "market_intelligence.html",
        {
            "request": request,
            "seasonal": SEASONAL_PATTERNS,
            "negotiation": NEGOTIATION,
            "self_employed": SELF_EMPLOYED_TIPS,
            "days_on_market": DAYS_ON_MARKET,
            "platform_strategy": PLATFORM_STRATEGY,
        },
    )
```

### New file: `src/home_finder/web/templates/market_intelligence.html`

This should extend the base template pattern used by `dashboard.html`. Look at the existing template for the HTML skeleton, nav, and style includes.

```html
{# Market Intelligence — standalone reference page #}
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Market Intelligence — Home Finder</title>
    {# Copy existing <style> from dashboard.html or link shared stylesheet #}
    <style>
        {# Include base styles from dashboard — body, nav, cards, badges, etc. #}
        {# Add page-specific styles: #}
        .mi-section { margin: 2rem 0; }
        .mi-section h3 { border-bottom: 2px solid var(--border, #e2e8f0); padding-bottom: 0.5rem; }
        .signal-list { list-style: none; padding: 0; }
        .signal-list li { padding: 0.5rem 0; border-bottom: 1px solid var(--border, #e2e8f0); }
        .signal-list li:last-child { border-bottom: none; }
        .dom-table { width: 100%; border-collapse: collapse; }
        .dom-table th, .dom-table td { padding: 0.5rem; text-align: left; border-bottom: 1px solid var(--border, #e2e8f0); }
        .dom-bar { display: inline-block; height: 1rem; background: var(--accent, #3b82f6); border-radius: 4px; }
        .tip-card { background: var(--card-bg, #f8fafc); border-radius: 8px; padding: 1rem; margin: 0.5rem 0; }
        .month-good { color: #16a34a; font-weight: 600; }
        .month-bad { color: #dc2626; font-weight: 600; }
    </style>
</head>
<body>
    <nav>
        <a href="/">Dashboard</a> &middot;
        <strong>Market Intelligence</strong>
    </nav>

    <main style="max-width: 900px; margin: 0 auto; padding: 1rem;">
        <h1>Market Intelligence</h1>
        <p>Strategic reference for London rental market navigation. Data compiled from market research — not updated in real-time.</p>

        {# Seasonal Timing #}
        {% if seasonal %}
        <section class="mi-section">
            <h3>Seasonal Timing</h3>
            <p>{{ seasonal.explanation }}</p>
            <p>
                Best months:
                {% for m in seasonal.best_months %}<span class="month-good">{{ m }}</span>{% if not loop.last %}, {% endif %}{% endfor %}
                &nbsp;&middot;&nbsp;
                Worst months:
                {% for m in seasonal.worst_months %}<span class="month-bad">{{ m }}</span>{% if not loop.last %}, {% endif %}{% endfor %}
            </p>
        </section>
        {% endif %}

        {# Negotiation #}
        {% if negotiation %}
        <section class="mi-section">
            <h3>Negotiation</h3>
            <p>{{ negotiation.success_rate }}</p>

            <h4>Signals a Landlord May Negotiate</h4>
            <ul class="signal-list">
                {% for signal in negotiation.signals %}
                <li>{{ signal }}</li>
                {% endfor %}
            </ul>

            <h4>Tips</h4>
            {% for tip in negotiation.tips %}
            <div class="tip-card">{{ tip }}</div>
            {% endfor %}
        </section>
        {% endif %}

        {# Self-Employed Application #}
        {% if self_employed %}
        <section class="mi-section">
            <h3>Self-Employed Application Guide</h3>
            <p>{{ self_employed.positioning }}</p>

            <h4>Required Documentation</h4>
            <ul>
                {% for doc in self_employed.documentation %}
                <li>{{ doc }}</li>
                {% endfor %}
            </ul>
        </section>
        {% endif %}

        {# Days on Market by Area #}
        {% if days_on_market %}
        <section class="mi-section">
            <h3>Days on Market by Area</h3>
            <p>Average time to let. Properties listed beyond these averages are more likely to be negotiable.</p>
            {% set max_days = days_on_market.values() | map(attribute='avg_days') | max %}
            <table class="dom-table">
                <thead><tr><th>Area</th><th>Avg Days</th><th></th><th>Note</th></tr></thead>
                <tbody>
                    {% for outcode, data in days_on_market.items() | sort(attribute='1.avg_days') %}
                    <tr>
                        <td><strong>{{ outcode }}</strong></td>
                        <td>{{ data.avg_days }}</td>
                        <td><span class="dom-bar" style="width: {{ (data.avg_days / max_days * 100) | round | int }}%"></span></td>
                        <td>{{ data.note }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </section>
        {% endif %}

        {# Platform Strategy #}
        {% if platform_strategy %}
        <section class="mi-section">
            <h3>Platform Strategy</h3>
            <table class="dom-table">
                <tbody>
                    <tr><td><strong>Fastest listings</strong></td><td>{{ platform_strategy.fastest_listings }}</td></tr>
                    <tr><td><strong>Most listings</strong></td><td>{{ platform_strategy.most_listings }}</td></tr>
                    <tr><td><strong>Best for value</strong></td><td>{{ platform_strategy.best_for_value }}</td></tr>
                </tbody>
            </table>
            {% if platform_strategy.notes %}
            <p class="detail-meta" style="margin-top: 0.5rem;">{{ platform_strategy.notes }}</p>
            {% endif %}
        </section>
        {% endif %}
    </main>
</body>
</html>
```

### Add nav link from dashboard

In `src/home_finder/web/templates/dashboard.html`, find the existing `<nav>` element and add a link:

```html
<a href="/market-intelligence">Market Intelligence</a>
```

Look for how existing nav links are structured and follow the same pattern.

---

## 3C. Optionally enhance value assessment prompt

This is a **stretch goal** — implement only if straightforward.

### File: `src/home_finder/filters/quality_prompts.py`

In `EVALUATION_SYSTEM_PROMPT`, Step 2 (Value Assessment) already says "Factor area context, true monthly cost..." — optionally add:

```
Properties listed for 14+ days may indicate negotiation opportunity — note this in your value assessment if listing age is available.
```

This would require knowing the listing age at evaluation time. Currently `first_seen` is set when the property is saved to the database, which is *after* quality analysis runs. So listing age is not available during analysis unless the scraper provides a "listed on" date.

**Recommendation:** Skip this for now. The market intelligence page serves the strategic purpose. Per-property listing age analysis can be a follow-up.

---

## 3D. Tests

### Data loading test — `tests/test_data/test_market_intelligence.py` (new file)

```python
from home_finder.data.market_intelligence import (
    SEASONAL_PATTERNS,
    NEGOTIATION,
    SELF_EMPLOYED_TIPS,
    DAYS_ON_MARKET,
    PLATFORM_STRATEGY,
)


def test_seasonal_patterns_loaded():
    """Seasonal patterns data loads correctly."""
    assert "best_months" in SEASONAL_PATTERNS
    assert "worst_months" in SEASONAL_PATTERNS
    assert isinstance(SEASONAL_PATTERNS["best_months"], list)
    assert len(SEASONAL_PATTERNS["best_months"]) > 0


def test_negotiation_loaded():
    """Negotiation data loads with signals and tips."""
    assert "signals" in NEGOTIATION
    assert "tips" in NEGOTIATION
    assert isinstance(NEGOTIATION["signals"], list)
    assert len(NEGOTIATION["signals"]) > 0


def test_self_employed_tips_loaded():
    """Self-employed tips load with documentation list."""
    assert "documentation" in SELF_EMPLOYED_TIPS
    assert isinstance(SELF_EMPLOYED_TIPS["documentation"], list)


def test_days_on_market_loaded():
    """Days on market data loads for known outcodes."""
    assert isinstance(DAYS_ON_MARKET, dict)
    assert "E8" in DAYS_ON_MARKET
    assert "avg_days" in DAYS_ON_MARKET["E8"]
    assert isinstance(DAYS_ON_MARKET["E8"]["avg_days"], int)


def test_platform_strategy_loaded():
    """Platform strategy data loads."""
    assert "fastest_listings" in PLATFORM_STRATEGY
    assert "most_listings" in PLATFORM_STRATEGY
```

### Web route tests — `tests/test_web/test_routes.py`

```python
class TestMarketIntelligence:
    """Tests for the /market-intelligence route."""

    def test_market_intelligence_page_loads(self, client: TestClient) -> None:
        """Market intelligence page returns 200."""
        resp = client.get("/market-intelligence")
        assert resp.status_code == 200

    def test_market_intelligence_has_sections(self, client: TestClient) -> None:
        """Market intelligence page contains expected sections."""
        resp = client.get("/market-intelligence")
        assert "Seasonal Timing" in resp.text
        assert "Negotiation" in resp.text
        assert "Self-Employed" in resp.text
        assert "Days on Market" in resp.text
        assert "Platform Strategy" in resp.text

    def test_dashboard_has_market_intelligence_link(self, client: TestClient) -> None:
        """Dashboard nav includes link to market intelligence."""
        resp = client.get("/")
        assert "market-intelligence" in resp.text
```

---

## Verification

1. `uv run pytest` — all existing + new tests pass
2. `uv run ruff check src tests` — no lint issues
3. `uv run mypy src` — type checking passes
4. `uv run home-finder --serve` — visit `/market-intelligence`, verify all sections render
5. Dashboard nav link works

---

## Files Changed Summary

| File | Change |
|------|--------|
| `src/home_finder/data/market_intelligence.json` | **New** — market strategy data |
| `src/home_finder/data/market_intelligence.py` | **New** — data loader |
| `src/home_finder/web/routes.py` | Add `GET /market-intelligence` route |
| `src/home_finder/web/templates/market_intelligence.html` | **New** — market intelligence page |
| `src/home_finder/web/templates/dashboard.html` | Add nav link to market intelligence |
| `tests/test_data/test_market_intelligence.py` | **New** — data loading tests |
| `tests/test_web/test_routes.py` | Add market intelligence route tests |
