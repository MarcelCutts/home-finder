"""Render the HTML labeling report from candidate pairs.

Usage:
    uv run python generate_report.py                           # Uses default data/candidates.json
    uv run python generate_report.py data/candidates.json      # Explicit input
    uv run python generate_report.py -o reports/report_v2.html  # Custom output
"""

import argparse
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader


TEMPLATES_DIR = Path(__file__).parent / "templates"
DEFAULT_INPUT = Path(__file__).parent / "data" / "candidates.json"
DEFAULT_OUTPUT = Path(__file__).parent / "reports" / "labeling_report.html"


def _slim_pairs(pairs: list[dict]) -> list[dict]:
    """Strip raw_a/raw_b from pairs — they're huge and not needed for labeling."""
    return [{k: v for k, v in p.items() if k not in ("raw_a", "raw_b")} for p in pairs]


def render_report(candidates_path: Path, output_path: Path) -> None:
    """Render the HTML labeling report.

    Embeds pair data as a JSON blob for client-side rendering instead of
    generating 15K+ HTML elements via Jinja2 loops. This reduces the output
    from ~142MB to ~5MB and keeps the DOM minimal (~50 nodes vs ~90,000).
    """
    data = json.loads(candidates_path.read_text())

    slim = _slim_pairs(data["pairs"])

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=False,  # Template handles escaping; we inject JSON via |tojson
    )
    template = env.get_template("report.html.j2")

    html = template.render(
        pairs_json=json.dumps(slim, separators=(",", ":")),
        total_pairs=data["total_pairs"],
        config=data.get("config", {}),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)

    slim_size_mb = len(html) / (1024 * 1024)
    print(f"Report size: {slim_size_mb:.1f} MB")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate HTML labeling report")
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=DEFAULT_INPUT,
        help="Candidates JSON file",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output HTML file",
    )
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Error: {args.input} not found. Run generate_pairs.py first.")
        raise SystemExit(1)

    render_report(args.input, args.output)
    print(f"Report saved to {args.output}")
    print(f"Open in browser: file://{args.output.resolve()}")


if __name__ == "__main__":
    main()
