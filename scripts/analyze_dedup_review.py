"""Evaluate retained dedup evidence against explicit agent development labels.

Requires development dependencies. Blocks sockets, opens the source DB read-only,
and only invokes pure scoring/grouping functions. Threshold changes are temporary
in this process; no application database, cache, or configuration is changed.
Unlabelled pairs remain unlabelled, not presumed true negatives.
"""

import argparse
import json
import os
from collections import Counter, defaultdict
from itertools import combinations, product
from pathlib import Path

from pytest_socket import disable_socket
from replay_batch_dedup import digest, load_inputs

from home_finder.filters.deduplication import _group_items_greedy
from home_finder.filters.scoring import calculate_match_score, haversine_distance
from home_finder.logging import configure_logging
from home_finder.utils import image_hash
from home_finder.utils.address import extract_outcode, normalize_street_name


def pair_key(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left < right else (right, left)


def labelled_pairs(labels: dict) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}

    def add(left: str, right: str, decision: str) -> None:
        key = pair_key(left, right)
        if left == right or left not in labels["listing_ids"] or right not in labels["listing_ids"]:
            raise ValueError(f"Invalid labelled pair: {key}")
        if key in result and result[key] != decision:
            raise ValueError(f"Conflicting decisions: {key}")
        result[key] = decision

    for group in labels["same_home_groups"]:
        for left, right in combinations(group["members"], 2):
            add(left, right, "same_home")
    for case in labels["different_home_cases"]:
        for left, right in product(case["left"], case["right"]):
            add(left, right, "different_home")
    for case in labels["uncertain_pairs"]:
        add(*case["members"], "uncertain")
    return result


def analyze(source: Path, labels_path: Path, output: Path) -> dict:
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    labels = json.loads(labels_path.read_text())
    for name, expected in labels["input_sha256"].items():
        if digest(source / name) != expected:
            raise ValueError(f"Input does not match the adjudicated capture: {name}")
    decisions = labelled_pairs(labels)
    inputs, files = load_inputs(source)
    ids = {uid: label for label, uid in labels["listing_ids"].items()}
    if len(ids) != len(inputs) or set(ids) != {x.unique_id for x in inputs}:
        raise ValueError("Labels must identify exactly the selected source listings")
    protected = {p for paths in files.values() for p in paths}
    protected.update(source / name for name in ("properties.db", "events.jsonl", "report.json"))
    before = {str(p): digest(p) for p in sorted(protected)}
    hashes: dict[str, list[str]] = {}
    for uid, paths in files.items():
        gallery = [image_hash.hash_from_disk(p) for p in paths if p.name.startswith("gallery_")]
        if not gallery or any(h is None for h in gallery):
            raise ValueError(f"Incomplete decodable gallery: {uid}")
        hashes[uid] = [h for h in gallery if h is not None]

    blocks: dict[tuple[str, int], list] = defaultdict(list)
    for item in inputs:
        outcode = extract_outcode(item.canonical.postcode)
        if not outcode:
            raise ValueError("This captured-cohort experiment requires known outcodes")
        blocks[(outcode, item.canonical.bedrooms)].append(item)

    profiles = []
    for left, right in combinations(inputs, 2):
        a, b = left.canonical, right.canonical
        key = pair_key(ids[a.unique_id], ids[b.unique_id])
        distance = None
        if all(v is not None for v in (a.latitude, a.longitude, b.latitude, b.longitude)):
            distance = haversine_distance(a.latitude, a.longitude, b.latitude, b.longitude)
        minimum = min(
            (int(x, 16) ^ int(y, 16)).bit_count()
            for x in hashes[a.unique_id]
            for y in hashes[b.unique_id]
        )
        street_a, street_b = normalize_street_name(a.address), normalize_street_name(b.address)
        street = bool(street_a and street_a == street_b)
        screened = street or (distance is not None and distance <= 100) or minimum <= 12
        profiles.append(
            {
                "ids": key,
                "label": decisions.get(key, "unreviewed"),
                "minimum_phash_distance": minimum,
                "distance_m": distance,
                "same_normalized_street": street,
                "same_source": a.source == b.source,
                "same_block": (extract_outcode(a.postcode), a.bedrooms)
                == (extract_outcode(b.postcode), b.bedrooms),
                "non_image_score": calculate_match_score(a, b).to_dict(),
                "broad_review_screen": screened,
            }
        )

    positives = {key for key, label in decisions.items() if label == "same_home"}
    scenarios = []
    original_threshold = image_hash.HASH_DISTANCE_THRESHOLD
    try:
        for threshold in (8, 10, 12, 16, 20, 22, 24):
            image_hash.HASH_DISTANCE_THRESHOLD = threshold
            groups = [g for block in blocks.values() for g in _group_items_greedy(block, hashes)]
            members = sorted(sorted(ids[item.unique_id] for item in group) for group in groups)
            predicted = {pair_key(*pair) for group in members for pair in combinations(group, 2)}
            eligible = []
            for block in blocks.values():
                for a, b in combinations(block, 2):
                    if set(a.sources) & set(b.sources):
                        continue
                    score = calculate_match_score(a.canonical, b.canonical, hashes)
                    if score.is_match and score.image_hash > 0:
                        key = pair_key(ids[a.unique_id], ids[b.unique_id])
                        eligible.append({"ids": key, "label": decisions.get(key, "unreviewed")})
            scenarios.append(
                {
                    "threshold": threshold,
                    "homes": len(groups),
                    "groups": members,
                    "qualifying_pairs": eligible,
                    "qualifying_labels": dict(Counter(p["label"] for p in eligible)),
                    "accepted_labels": dict(
                        Counter(decisions.get(key, "unreviewed") for key in predicted)
                    ),
                    "missed_labelled_positives": sorted(positives - predicted),
                    "known_positive_pair_recall": len(predicted & positives) / len(positives),
                }
            )
    finally:
        image_hash.HASH_DISTANCE_THRESHOLD = original_threshold

    live: dict[str, list[str]] = defaultdict(list)
    for row in json.loads((source / "report.json").read_text())["source_membership"]:
        if row["unique_id"] in ids and row["merged_id"] is not None:
            live[row["merged_id"]].append(ids[row["unique_id"]])
    screened = [p for p in profiles if p["broad_review_screen"]]
    unchanged = all(digest(Path(name)) == value for name, value in before.items())
    report = {
        "schema_version": 1,
        "review_kind": "agent development adjudication, not independent human ground truth",
        "labels_sha256": digest(labels_path),
        "label_version": labels["version"],
        "input_count": len(inputs),
        "gallery_count": sum(len(h) for h in hashes.values()),
        "original_floorplan_count": sum(item.floorplan is not None for item in inputs),
        "all_pair_count": len(profiles),
        "label_counts": dict(Counter(p["label"] for p in profiles)),
        "broad_screen_count": len(screened),
        "broad_screen_labels": dict(Counter(p["label"] for p in screened)),
        "all_pairs": profiles,
        "scenarios": scenarios,
        "checks": {
            "baseline_matches_live_memberships": scenarios[0]["groups"]
            == sorted(sorted(group) for group in live.values()),
            "all_screened_pairs_adjudicated": all(p["label"] != "unreviewed" for p in screened),
            "input_artifacts_unchanged": unchanged,
            "threshold_restored": original_threshold == image_hash.HASH_DISTANCE_THRESHOLD,
        },
        "original_sha256": before,
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if not all(report["checks"].values()):
        raise ValueError("Review operating checks failed; inspect the generated report")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    disable_socket()
    configure_logging(json_output=True, level="WARNING")
    report = analyze(args.input.resolve(), args.labels.resolve(), args.output.resolve())
    print(
        json.dumps(
            {
                "listings": report["input_count"],
                "labels": report["label_counts"],
                "checks": report["checks"],
                "report": str(args.output / "report.json"),
            }
        )
    )


if __name__ == "__main__":
    main()
