"""Render data/summary.json into the static HTML report (inline SVG, no scripts).

uv run python report.py --summary data/summary.json --narrative narrative.html --out report.html
"""

from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent

LABELS = {
    "sonnet55": "Sonnet 5.5",
    "haiku55": "Haiku 5.5",
    "opus55": "Opus 5.5",
    "fable51": "Fable 5.1",
    "sol61": "GPT-6.1 Sol",
    "sol6": "GPT-6 Sol",
    "sol56": "GPT-5.6 Sol",
    "terra56": "GPT-5.6 Terra",
    "luna6": "GPT-6 Luna",
    "luna56": "GPT-5.6 Luna",
    "astra6": "GPT-6 Astra",
}


# Arms labelled on the scatter plots; the rest are drawn as unlabelled dots.
FOCUS = [
    "sonnet55-low",
    "sol61-low",
    "sol6-low",
    "haiku55-low",
    "luna6-low",
    "opus55-low",
    "sonnet55-low+haiku-eval",
    "sol61-low-flex",
    "sol56-low",
    "terra56-low",
]


# (text-anchor, dx, dy) per labelled arm, spreading the $0.04–0.06 cluster apart.
LABEL_OFFSETS = {
    "sol61-low-flex": ("end", -9, -8),
    "sonnet55-low+haiku-eval": ("end", -9, -9),
    "sol6-low": ("end", -9, 16),
    "sonnet55-low": ("start", 9, 16),
    "sol56-low": ("start", 9, -9),
    "opus55-low": ("start", 9, 16),
    "terra56-low": ("start", 9, 16),
    "haiku55-low": ("start", 9, 4),
}


def label(arm: str) -> str:
    if arm == "sonnet55-low+haiku-eval":
        return "Sonnet 5.5 low + Haiku eval"
    base, _, rest = arm.partition("-")
    name = LABELS.get(base, base)
    rest = (
        rest.replace("low-fast", "low · fast tier")
        .replace("low-flex", "low · flex tier")
        .replace("low-imglow", "low · 512px images")
        .replace("nothink", "no thinking")
    )
    return f"{name} {rest}".strip()


def esc(text: Any) -> str:
    return html.escape(str(text))


def fmt(v: Any, kind: str) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    if kind == "usd":
        return f"${v:.3f}" if v < 1 else f"${v:.2f}"
    if kind == "pct":
        return f"{100 * v:.0f}%"
    if kind == "s":
        return f"{v:.0f}s"
    if kind == "f1":
        return f"{v:.1f}"
    if kind == "f2":
        return f"{v:.2f}"
    return esc(v)


def scatter(arms: dict[str, Any], y_key: str, y_label: str, highlight: set[str]) -> str:
    """Cost (log x) vs quality metric, one dot per arm."""
    pts = [
        (a, m["cost_per_valid_home"], m[y_key], m["provider"])
        for a, m in arms.items()
        if m.get(y_key) is not None and m.get("cost_per_valid_home")
    ]
    if not pts:
        return ""
    w, h, left, right, top, bottom = 760, 420, 64, 24, 20, 52
    xs = [math.log10(p[1]) for p in pts]
    x0, x1 = math.floor(min(xs) * 2) / 2, math.ceil(max(xs) * 2) / 2
    ys = [p[2] for p in pts]
    y0 = max(0.0, math.floor(min(ys) * 20) / 20 - 0.05)
    y1 = min(1.0, math.ceil(max(ys) * 20) / 20 + 0.02)

    def sx(v: float) -> float:
        return left + (math.log10(v) - x0) / (x1 - x0) * (w - left - right)

    def sy(v: float) -> float:
        return top + (1 - (v - y0) / (y1 - y0)) * (h - top - bottom)

    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{esc(y_label)} against cost">']
    tick = y0
    while tick <= y1 + 1e-9:
        y = sy(tick)
        out.append(f'<line class="grid" x1="{left}" x2="{w - right}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(
            f'<text class="axis" x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">{100 * tick:.0f}%</text>'
        )
        tick += 0.05
    for dec in [0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1.0]:
        if x0 <= math.log10(dec) <= x1:
            x = sx(dec)
            out.append(
                f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{h - bottom}"/>'
            )
            out.append(
                f'<text class="axis" x="{x:.1f}" y="{h - bottom + 18}" text-anchor="middle">${dec:g}</text>'
            )
    out.append(
        f'<text class="axis-title" x="{(left + w - right) / 2}" y="{h - 10}" text-anchor="middle">'
        "Cost per valid home (log scale)</text>"
    )
    out.append(
        f'<text class="axis-title" transform="translate(16 {(top + h - bottom) / 2}) rotate(-90)" '
        f'text-anchor="middle">{esc(y_label)}</text>'
    )
    for arm, cost, y, provider in sorted(pts, key=lambda p: p[0] in highlight):
        cls = "oa" if provider == "openai" else "an" if provider == "anthropic" else "mix"
        strong = arm in highlight
        cx, cy = sx(cost), sy(y)
        out.append(
            f'<circle class="dot {cls}{" strong" if strong else ""}" cx="{cx:.1f}" cy="{cy:.1f}" '
            f'r="{6 if strong else 4.5}"><title>{esc(label(arm))}: {fmt(cost, "usd")}, '
            f"{100 * y:.0f}%</title></circle>"
        )
        if strong:
            anchor, dx, dy = LABEL_OFFSETS.get(arm, ("start", 9, -7))
            if anchor == "start" and cx > w - 160:
                anchor, dx = "end", -abs(dx)
            out.append(
                f'<text class="pt" x="{cx + dx:.1f}" y="{cy + dy:.1f}" text-anchor="{anchor}">'
                f"{esc(label(arm))}</text>"
            )
    out.append("</svg>")
    return "".join(out)


def speed_bars(arms: dict[str, Any]) -> str:
    rows = sorted(
        ((a, m) for a, m in arms.items() if m.get("wall_p50")), key=lambda kv: kv[1]["wall_p50"]
    )
    w, row_h, left, right = 760, 22, 230, 60
    h = row_h * len(rows) + 40
    top_val = max(m["wall_p90"] or m["wall_p50"] for _, m in rows)
    scale = (w - left - right) / top_val
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Seconds per home, p50 and p90">']
    for t in range(0, int(top_val) + 1, 30):
        x = left + t * scale
        out.append(f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="0" y2="{h - 28}"/>')
        out.append(f'<text class="axis" x="{x:.1f}" y="{h - 10}" text-anchor="middle">{t}s</text>')
    for i, (arm, m) in enumerate(rows):
        y = i * row_h + 4
        cls = "oa" if m["provider"] == "openai" else "an" if m["provider"] == "anthropic" else "mix"
        p50, p90 = m["wall_p50"], m["wall_p90"] or m["wall_p50"]
        out.append(
            f'<text class="rowlabel" x="{left - 10}" y="{y + 13}" text-anchor="end">{esc(label(arm))}</text>'
        )
        out.append(
            f'<rect class="bar {cls}" x="{left}" y="{y + 3}" width="{p50 * scale:.1f}" height="{row_h - 8}" rx="2"/>'
        )
        out.append(
            f'<line class="whisker" x1="{left + p50 * scale:.1f}" x2="{left + p90 * scale:.1f}" '
            f'y1="{y + row_h / 2 - 1:.1f}" y2="{y + row_h / 2 - 1:.1f}"/>'
        )
        out.append(
            f'<text class="val" x="{left + p90 * scale + 6:.1f}" y="{y + 13}">{p50:.0f}s</text>'
        )
    out.append("</svg>")
    return "".join(out)


def arm_table(arms: dict[str, Any]) -> str:
    cols = [
        ("Arm", None, None),
        ("Homes", "homes", "raw"),
        ("Valid", "reliability", "pct"),
        ("$ / valid result", "cost_per_valid_home", "usd"),
        ("$ cold / valid result", "cold_cost_per_valid_home", "usd"),
        ("p50", "wall_p50", "s"),
        ("p90", "wall_p90", "s"),
        ("Visual tok/s", "visual_tok_s_p50", "raw0"),
        ("Text facts", "text_fact_accuracy", "pct"),
        ("Flagship consensus", "consensus_agree_rate", "pct"),
        ("Field majority", "loo_agree_rate", "pct"),
        ("Bad badges / home", "badge_violations_per_home", "f2"),
    ]
    head = "".join(f"<th>{esc(c[0])}</th>" for c in cols)
    body = []
    for arm, m in sorted(
        arms.items(), key=lambda kv: (kv[1]["provider"], kv[1]["cost_per_valid_home"] or 9)
    ):
        cls = "oa" if m["provider"] == "openai" else "an" if m["provider"] == "anthropic" else "mix"
        cells = [f'<td class="armcell"><span class="sw {cls}"></span>{esc(label(arm))}</td>']
        for _, key, kind in cols[1:]:
            v = m.get(key)
            text = f"{v:.0f}" if kind == "raw0" and v is not None else fmt(v, kind)
            cells.append(f"<td>{text}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")
    return f'<div class="tablewrap"><table class="data"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def judge_table(judge: dict[str, Any]) -> str:
    if not judge:
        return "<p>No judged pairs.</p>"
    rows = []
    for _, j in judge.items():
        left, right = j["left"], j["right"]
        both = j["tallies"].get("both", {})

        def cell(crit: str) -> str:
            t = both.get(crit, {})
            lw, rw, ties = t.get("left", 0), t.get("right", 0), t.get("tie", 0)
            return f"<td>{lw}–{ties}–{rw}</td>"

        per_judge = []
        for jid, name in (("opus55", "Claude judge"), ("sol56", "GPT judge")):
            t = j["tallies"].get(jid, {}).get("overall", {})
            per_judge.append(f"<td>{t.get('left', 0)}–{t.get('tie', 0)}–{t.get('right', 0)}</td>")
        lo, hi = j["left_overall_ci"] or (None, None)
        score = j["left_overall_score"]
        err = j["mean_errors"]

        def errs(cfg: str) -> str:
            vals = [by.get(cfg) for jid, by in err.items() if by.get(cfg) is not None]
            return f"{sum(vals) / len(vals):.1f}" if vals else "–"

        rows.append(
            f"<tr><td class='armcell'>{esc(label(left))}</td><td class='armcell'>{esc(label(right))}</td>"
            f"<td>{j['cases']}</td>"
            f"<td><strong>{fmt(score, 'pct')}</strong> <span class='ci'>[{fmt(lo, 'pct')}, {fmt(hi, 'pct')}]</span></td>"
            f"{''.join(per_judge)}{cell('factual_accuracy')}{cell('calibration')}{cell('usefulness')}{cell('viewing_notes')}"
            f"<td>{errs(left)} / {errs(right)}</td></tr>"
        )
    head = (
        "<th>Left arm</th><th>Right arm</th><th>Homes</th><th>Left preferred overall</th>"
        "<th>Claude judge</th><th>GPT judge</th><th>Accuracy</th><th>Calibration</th>"
        "<th>Usefulness</th><th>Viewing notes</th><th>Errors / home (L / R)</th>"
    )
    return f'<div class="tablewrap"><table class="data"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


STYLE = """
/* Layout: one reading column, wide figures and tables break out to the content width. */
:root {
  --bg: #f5f6f4; --surface: #ffffff; --fg: #1b1d1c; --muted: #5d6460; --rule: #d9ddd8;
  --anthropic: #b85a1b; --openai: #12756a; --mix: #6b5aa6; --accent: #12756a;
  --display: "IBM Plex Sans Condensed", "Arial Narrow", system-ui, sans-serif;
  --body: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #121513; --surface: #1a1e1c; --fg: #e6e9e6; --muted: #9aa39e; --rule: #2e3532;
  --anthropic: #e38a4c; --openai: #3fb3a3; --mix: #a597dd; --accent: #3fb3a3; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #121513; --surface: #1a1e1c; --fg: #e6e9e6; --muted: #9aa39e; --rule: #2e3532;
  --anthropic: #e38a4c; --openai: #3fb3a3; --mix: #a597dd; --accent: #3fb3a3; color-scheme: dark }
body { background: var(--bg); color: var(--fg); font: 15px/1.6 var(--body); }
.wrap { max-width: 1040px; margin: 0 auto; padding-block: 40px 72px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  display: grid; gap: 28px; }
.prose { max-width: 68ch; }
h1, h2, h3 { font-family: var(--display); text-wrap: balance; line-height: 1.15; margin: 0; }
h1 { font-size: clamp(30px, 5vw, 44px); font-weight: 600; letter-spacing: -0.01em; }
h2 { font-size: 24px; font-weight: 600; margin-top: 12px; }
h3 { font-size: 18px; font-weight: 600; }
p { margin: 0; }
.prose > * + * { margin-top: 12px; }
.eyebrow { font: 600 12px/1 var(--mono); letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); }
.lede { font-size: 18px; color: var(--muted); max-width: 68ch; }
.verdict { background: var(--surface); border: 1px solid var(--rule); border-radius: 6px; padding: 20px 24px; display: grid; gap: 10px; }
.verdict ul { margin: 0; padding-left: 20px; display: grid; gap: 8px; }
.facts { display: flex; flex-wrap: wrap; gap: 8px 24px; font: 13px var(--mono); color: var(--muted); }
figure { margin: 0; display: grid; gap: 8px; min-width: 0; }
figure svg { width: 100%; height: auto; display: block; background: var(--surface); border: 1px solid var(--rule); border-radius: 6px; }
figcaption { color: var(--muted); font-size: 13px; max-width: 80ch; }
.legend { display: flex; gap: 16px; font-size: 13px; color: var(--muted); flex-wrap: wrap; }
.sw { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; vertical-align: 0; }
.sw.an { background: var(--anthropic); } .sw.oa { background: var(--openai); } .sw.mix { background: var(--mix); }
svg .grid { stroke: var(--rule); stroke-width: 1; }
svg .axis, svg .val { fill: var(--muted); font: 11px var(--mono); }
svg .axis-title { fill: var(--muted); font: 12px var(--body); }
svg .rowlabel, svg .pt { fill: var(--fg); font: 12px var(--body); }
svg .dot { stroke: var(--surface); stroke-width: 1.5; opacity: 0.75; }
svg .dot.strong { opacity: 1; }
svg .dot.an, svg .bar.an { fill: var(--anthropic); } svg .dot.oa, svg .bar.oa { fill: var(--openai); }
svg .dot.mix, svg .bar.mix { fill: var(--mix); }
svg .whisker { stroke: var(--muted); stroke-width: 2; }
.tablewrap { overflow-x: auto; border: 1px solid var(--rule); border-radius: 6px; background: var(--surface); }
table.data { border-collapse: collapse; width: 100%; font-size: 13px; font-variant-numeric: tabular-nums; }
table.data th { text-align: right; font: 600 11px/1.3 var(--mono); letter-spacing: 0.04em; text-transform: uppercase; color: var(--muted); padding: 10px 10px; border-bottom: 1px solid var(--rule); vertical-align: bottom; }
table.data td { text-align: right; padding: 7px 10px; border-bottom: 1px solid var(--rule); white-space: nowrap; }
table.data th:first-child, table.data td.armcell { text-align: left; }
table.data tr:last-child td { border-bottom: 0; }
.ci { color: var(--muted); font-size: 12px; }
.two { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }
.two > * { min-width: 0; }
code { font: 13px var(--mono); }
a { color: var(--accent); }
"""


def build(summary: dict[str, Any], narrative: str) -> str:
    arms = summary["arms"]
    focus = set(summary.get("focus") or FOCUS)
    parts = [
        "<title>Home Finder Model Bake-off</title>",
        '<meta name="claude:viewport-fit-sides" content="cover">',
        '<link rel="preconnect" href="https://fonts.googleapis.com">',
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans+Condensed:wght@600&family=IBM+Plex+Sans:wght@400;600&display=swap">',
        f"<style>{STYLE}</style>",
        '<main class="wrap">',
        narrative.replace(
            "{{SCATTER_CONSENSUS}}",
            scatter(arms, "consensus_agree_rate", "Agreement with flagship consensus", focus),
        )
        .replace(
            "{{SCATTER_LOO}}",
            scatter(arms, "loo_agree_rate", "Agreement with field majority", focus),
        )
        .replace("{{SPEED}}", speed_bars(arms))
        .replace("{{ARM_TABLE}}", arm_table(arms))
        .replace("{{JUDGE_TABLE}}", judge_table(summary.get("judge", {})))
        .replace(
            "{{LEGEND}}",
            '<div class="legend"><span><span class="sw an"></span>Anthropic</span>'
            '<span><span class="sw oa"></span>OpenAI</span><span><span class="sw mix"></span>Mixed</span></div>',
        ),
        "</main>",
    ]
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", default=str(ROOT / "data" / "summary.json"))
    parser.add_argument("--narrative", default=str(ROOT / "narrative.html"))
    parser.add_argument("--out", default=str(ROOT / "data" / "report.html"))
    args = parser.parse_args()
    summary = json.loads(Path(args.summary).read_text())
    Path(args.out).write_text(build(summary, Path(args.narrative).read_text()))
    print(args.out)


if __name__ == "__main__":
    main()
