"""Summarize --test-profile JSON without confusing parallel work with elapsed time.

Run: python scripts/summarize_test_profile.py test-results/profile.json [other.json ...]
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def summarize(profile_path: Path) -> str:
    profile = json.loads(profile_path.read_text())
    if profile["schema_version"] != 1:
        raise ValueError(f"Unsupported test profile schema in {profile_path}")
    environment = profile["environment"]
    phases = profile["phases"]
    totals: dict[str, float] = defaultdict(float)
    modules: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for phase in phases:
        totals[phase["phase"]] += phase["seconds"]
        modules[phase["nodeid"].split("::", 1)[0]][phase["phase"]] += phase["seconds"]
    processes = profile["workers"] or {"main": profile["controller"]}
    fixture_totals: dict[str, float] = defaultdict(float)
    for process in processes.values():
        for name, fixture in process.get("fixtures", {}).items():
            fixture_totals[name] += fixture["seconds"]

    lines = [
        str(profile_path),
        f"Elapsed since pytest configuration: {profile['wall_seconds']:.2f}s; "
        f"exit: {profile['exit_code']}; "
        f"workers: {len(profile['workers'])}; Python: {environment['python']}; "
        f"Hypothesis: {environment['hypothesis_profile']}; coverage: {environment['coverage']}",
        f"Host load (1/5/15 minute): {environment['load_average_start']} -> "
        f"{environment['load_average_end']}",
        f"Phase outcomes: {profile['phase_outcomes']}",
        "Summed test phase seconds (overlap across workers): "
        + ", ".join(f"{name}={seconds:.2f}" for name, seconds in sorted(totals.items())),
        "Process wall/CPU counters start at pytest configuration and exclude test subprocesses.",
        "Workers: wall / CPU / collection seconds / selected cases / "
        "lifetime peak RSS MiB / crashed",
    ]
    if window := profile.get("test_window"):
        lines[4:4] = [
            f"Startup: {window['startup_seconds']:.2f}s; test window: {window['seconds']:.2f}s; "
            f"finish (worker shutdown/session hooks): {window['finish_seconds']:.2f}s",
            f"Workers inside test phases for {window['occupied_fraction']:.0%} of the window "
            "(wall occupancy, not CPU utilisation).",
            "Worker phase seconds: "
            + ", ".join(
                f"{worker}={seconds:.2f}"
                for worker, seconds in sorted(window["worker_phase_seconds"].items())
            ),
        ]
    for name, process in sorted(processes.items()):
        if not process.get("wall_seconds"):
            lines.append(f"  {name}: timing unavailable; crashed={process.get('crashed')}")
            continue
        lines.append(
            f"  {name}: {process['wall_seconds']:.2f} / {process['cpu_seconds']:.2f} / "
            f"{process['collection_seconds']:.2f} / {process['selected']} / "
            f"{process.get('peak_rss_mib', 'unavailable')} / "
            f"{process.get('crashed', False)}"
        )
    lines.append("Slowest modules by summed setup / call / teardown seconds:")
    for name, values in sorted(modules.items(), key=lambda row: -sum(row[1].values()))[:15]:
        lines.append(
            f"  {sum(values.values()):8.2f}  {values['setup']:7.2f} / "
            f"{values['call']:7.2f} / {values['teardown']:7.2f}  {name}"
        )
    lines.append("Slowest fixture setups (inclusive; not additive to phase times):")
    for name, seconds in sorted(fixture_totals.items(), key=lambda row: -row[1])[:10]:
        lines.append(f"  {seconds:8.2f}  {name}")
    lines.append("Slowest individual phases:")
    for phase in sorted(phases, key=lambda row: -row["seconds"])[:10]:
        lines.append(f"  {phase['seconds']:8.2f}  {phase['phase']:8}  {phase['nodeid']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profiles", nargs="+", type=Path)
    parser.add_argument(
        "--markdown", action="store_true", help="Wrap summaries for a CI job report"
    )
    args = parser.parse_args()
    for profile_path in args.profiles:
        summary = summarize(profile_path)
        print(f"```text\n{summary}\n```" if args.markdown else summary)


if __name__ == "__main__":
    main()
