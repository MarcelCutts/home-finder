"""Import labels from the HTML report's JSON export into the labels directory.

Usage:
    uv run python import_labels.py ~/Downloads/dedup_labels_2024-01-15.json
    uv run python import_labels.py ~/Downloads/dedup_labels_2024-01-15.json --name labels_v2
    uv run python import_labels.py ~/Downloads/dedup_labels_*.json --merge  # Merge multiple exports
"""

import argparse
import json
from datetime import datetime
from pathlib import Path


LABELS_DIR = Path(__file__).parent / "labels"


def import_labels(
    input_paths: list[Path],
    name: str | None = None,
    merge: bool = False,
) -> Path:
    """Import label files into the labels directory.

    Args:
        input_paths: Paths to exported JSON label files.
        name: Output filename (without extension). Auto-generated if None.
        merge: If True, merge all input files (later files override).

    Returns:
        Path to the saved labels file.
    """
    if merge:
        merged: dict = {}
        for path in input_paths:
            data = json.loads(path.read_text())
            # Unwrap nested format: {"labels": {...}} vs flat {pair_id: {...}}
            if "labels" in data and isinstance(data["labels"], dict):
                data = data["labels"]
            merged.update(data)
        raw_labels = merged
    else:
        if len(input_paths) != 1:
            raise ValueError("Without --merge, provide exactly one input file")
        raw_labels = json.loads(input_paths[0].read_text())

    # Normalize and validate
    labels = {}
    stats = {"match": 0, "no_match": 0, "uncertain": 0, "invalid": 0}

    for pair_id, data in raw_labels.items():
        label = data.get("label")
        if label not in ("match", "no_match", "uncertain"):
            stats["invalid"] += 1
            continue

        stats[label] += 1
        labels[pair_id] = {
            "label": label,
            "notes": data.get("notes", ""),
        }

    # Build output
    if name is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"labels_{timestamp}"

    output = {
        "created": datetime.now().isoformat(),
        "source_files": [str(p) for p in input_paths],
        "stats": stats,
        "labels": labels,
    }

    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = LABELS_DIR / f"{name}.json"
    output_path.write_text(json.dumps(output, indent=2))

    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Import labels from HTML report export")
    parser.add_argument("inputs", nargs="+", type=Path, help="Exported label JSON file(s)")
    parser.add_argument("--name", type=str, default=None, help="Output filename (no extension)")
    parser.add_argument("--merge", action="store_true", help="Merge multiple exports")
    args = parser.parse_args()

    for p in args.inputs:
        if not p.exists():
            print(f"Error: {p} not found")
            raise SystemExit(1)

    path = import_labels(args.inputs, name=args.name, merge=args.merge)

    # Print summary
    data = json.loads(path.read_text())
    stats = data["stats"]
    total = sum(stats.values())

    print(f"Labels saved to {path}")
    print(f"  Total: {total}")
    print(f"  Match: {stats['match']}")
    print(f"  No match: {stats['no_match']}")
    print(f"  Uncertain: {stats['uncertain']}")
    if stats["invalid"]:
        print(f"  Skipped (invalid): {stats['invalid']}")


if __name__ == "__main__":
    main()
