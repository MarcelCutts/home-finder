"""Observe up to 50 live input listings in a fresh, private experiment directory.

Uses the normal pipeline with notifications and other paid providers disabled.
The model budget defaults to zero; repeat invocations require new directories.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from home_finder.config import Settings
from home_finder.pipeline.smoke import run_batch_smoke


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--properties", type=int, default=50)
    parser.add_argument(
        "--sources",
        nargs="+",
        default=["rightmove", "onthemarket"],
        choices=["rightmove", "onthemarket", "openrent", "zoopla"],
    )
    parser.add_argument(
        "--areas", help="Comma-separated outcodes; default: configured search areas"
    )
    parser.add_argument("--budget-usd", default="0", help="Explicit total cap, at most US$10")
    args = parser.parse_args()
    os.umask(0o077)
    areas = args.areas.split(",") if args.areas else Settings().get_search_areas()
    report = asyncio.run(
        run_batch_smoke(
            args.output,
            sources=set(args.sources),
            areas=[area.strip() for area in areas],
            max_properties=args.properties,
            budget_usd=args.budget_usd,
        )
    )
    print(json.dumps({"status": report["status"], "report": str(args.output / "report.json")}))
    sys.exit(0 if report["status"] == "analyzed" else 1)


if __name__ == "__main__":
    main()
