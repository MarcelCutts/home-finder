"""Generate candidate pairs from a snapshot for labeling.

Usage:
    uv run python generate_pairs.py data/snapshots/snapshot_20240101_120000.json
    uv run python generate_pairs.py data/snapshots/snapshot_*.json  # Merge multiple
    uv run python generate_pairs.py data/snapshots/snapshot_*.json --limit 200
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from home_finder.utils.address import extract_outcode

from scorer import ScorerConfig, score_pair, PRODUCTION_CONFIG
from signals import compute_all_signals


def load_snapshot(path: Path) -> list[dict]:
    """Load properties from a snapshot JSON file."""
    data = json.loads(path.read_text())
    return data["properties"]


def load_snapshots(paths: list[Path]) -> list[dict]:
    """Load and merge properties from multiple snapshots, deduping by unique_id."""
    seen = {}
    for path in paths:
        for prop in load_snapshot(path):
            uid = f"{prop['source']}:{prop['source_id']}"
            if uid not in seen:
                seen[uid] = prop
    return list(seen.values())


def block_properties(properties: list[dict]) -> dict[str, list[dict]]:
    """Group properties by outcode + bedrooms for blocking."""
    blocks: dict[str, list[dict]] = defaultdict(list)

    for prop in properties:
        outcode = extract_outcode(prop.get("postcode"))
        if outcode:
            key = f"{outcode}:{prop.get('bedrooms', 0)}"
            blocks[key].append(prop)

    return blocks


def generate_cross_platform_pairs(
    blocks: dict[str, list[dict]],
) -> list[tuple[dict, dict, str]]:
    """Generate all cross-platform pairs within each block.

    Returns list of (prop_a, prop_b, block_key) tuples.
    """
    pairs = []
    for block_key, props in blocks.items():
        if len(props) < 2:
            continue

        for i in range(len(props)):
            for j in range(i + 1, len(props)):
                a, b = props[i], props[j]
                # Only cross-platform pairs
                if a["source"] != b["source"]:
                    pairs.append((a, b, block_key))

    return pairs


def make_pair_id(a: dict, b: dict) -> str:
    """Create a stable pair ID (sorted to avoid A-B / B-A duplicates)."""
    uid_a = f"{a['source']}:{a['source_id']}"
    uid_b = f"{b['source']}:{b['source_id']}"
    return "||".join(sorted([uid_a, uid_b]))


def property_summary(prop: dict) -> dict:
    """Create a compact summary for a property (for the candidates file)."""
    detail = prop.get("detail", {}) or {}
    return {
        "unique_id": f"{prop['source']}:{prop['source_id']}",
        "source": prop["source"],
        "title": prop.get("title", ""),
        "price_pcm": prop.get("price_pcm", 0),
        "bedrooms": prop.get("bedrooms", 0),
        "address": prop.get("address", ""),
        "postcode": prop.get("postcode"),
        "latitude": prop.get("latitude"),
        "longitude": prop.get("longitude"),
        "url": prop.get("url", ""),
        "image_url": _get_image_url(prop),
        "description_preview": (_get_desc(prop) or "")[:200],
        "has_description": bool(_get_desc(prop)),
        "gallery_count": len(detail.get("gallery_urls") or []),
        "features": detail.get("features") or [],
    }


def _get_desc(prop: dict) -> str | None:
    if prop.get("description"):
        return prop["description"]
    detail = prop.get("detail", {}) or {}
    return detail.get("description")


def _get_image_url(prop: dict) -> str | None:
    """Get a real image URL, falling back to first gallery image.

    Filters out Rightmove placeholder SVGs (camera-white, floorplan-white, etc).
    """
    url = prop.get("image_url")
    if url and ".svg" not in url and "/assets/" not in url:
        return url
    # Fall back to first gallery image from detail page
    detail = prop.get("detail", {}) or {}
    gallery = detail.get("gallery_urls") or []
    return gallery[0] if gallery else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate candidate pairs for labeling")
    parser.add_argument("snapshots", nargs="+", type=Path, help="Snapshot JSON file(s)")
    parser.add_argument("--limit", type=int, default=None, help="Max pairs to output")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/candidates.json"),
        help="Output path for candidates",
    )
    parser.add_argument(
        "--sort",
        choices=["score_desc", "score_asc", "hardest"],
        default="hardest",
        help="Sort order: score_desc (highest first), score_asc (lowest first), "
        "hardest (closest to threshold — best for labeling)",
    )
    args = parser.parse_args()

    # Load data
    properties = load_snapshots(args.snapshots)
    print(f"Loaded {len(properties)} unique properties")

    by_source = defaultdict(int)
    for p in properties:
        by_source[p["source"]] += 1
    for source, count in sorted(by_source.items()):
        print(f"  {source}: {count}")

    # Block and generate pairs
    blocks = block_properties(properties)
    print(f"Created {len(blocks)} blocks")

    pairs = generate_cross_platform_pairs(blocks)
    print(f"Generated {len(pairs)} cross-platform pairs")

    if not pairs:
        print("No cross-platform pairs found. Need properties from multiple sources in same area.")
        sys.exit(0)

    # Pre-compute corpus-level TF-IDF vectorizer and sentence embeddings
    from sklearn.feature_extraction.text import TfidfVectorizer
    from signals import extract_property_text, _get_description, _prop_uid

    print("Fitting corpus-level TF-IDF vectorizer...")
    desc_texts = []
    desc_uids = []
    for prop in properties:
        desc = _get_description(prop)
        if desc:
            cleaned = extract_property_text(desc)
            if len(cleaned) >= 20:
                desc_texts.append(cleaned)
                desc_uids.append(_prop_uid(prop))

    vectorizer = None
    if len(desc_texts) >= 2:
        vectorizer = TfidfVectorizer(
            stop_words="english", max_features=5000, ngram_range=(1, 2),
        )
        vectorizer.fit(desc_texts)
        print(f"  Fitted on {len(desc_texts)} descriptions, {len(vectorizer.vocabulary_)} features")
    else:
        print("  Too few descriptions for corpus-level TF-IDF, using pair-level fallback")

    print("Pre-computing sentence embeddings...")
    embeddings_dict = {}
    if desc_texts:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer("all-MiniLM-L6-v2")
        # Truncate to 512 chars (model effective limit)
        truncated = [t[:512] for t in desc_texts]
        all_embeddings = model.encode(truncated, batch_size=32, show_progress_bar=False)
        embeddings_dict = dict(zip(desc_uids, all_embeddings))
        print(f"  Computed {len(embeddings_dict)} embeddings")

    # Score all pairs
    config = PRODUCTION_CONFIG
    scored_pairs = []

    for a, b, block_key in pairs:
        bundle = compute_all_signals(
            a, b, vectorizer=vectorizer, description_embeddings=embeddings_dict,
        )
        result = score_pair(a, b, config=config, bundle=bundle)

        scored_pairs.append({
            "pair_id": make_pair_id(a, b),
            "block": block_key,
            "property_a": property_summary(a),
            "property_b": property_summary(b),
            "raw_a": a,
            "raw_b": b,
            "scorer": {
                "score": result.score,
                "signal_count": result.signal_count,
                "is_match": result.is_match,
                "breakdown": result.breakdown,
            },
            "signals": bundle.to_dict(),
        })

    # Sort
    if args.sort == "score_desc":
        scored_pairs.sort(key=lambda p: p["scorer"]["score"], reverse=True)
    elif args.sort == "score_asc":
        scored_pairs.sort(key=lambda p: p["scorer"]["score"])
    elif args.sort == "hardest":
        # Sort by distance from threshold — hardest cases first
        threshold = config.match_threshold
        scored_pairs.sort(key=lambda p: abs(p["scorer"]["score"] - threshold))

    # Limit
    if args.limit:
        scored_pairs = scored_pairs[: args.limit]

    # Save
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "generated_from": [str(p) for p in args.snapshots],
        "config": config.to_dict(),
        "total_properties": len(properties),
        "total_pairs": len(scored_pairs),
        "pairs": scored_pairs,
    }

    args.output.write_text(json.dumps(output, indent=2, default=str))
    print(f"\nSaved {len(scored_pairs)} pairs to {args.output}")

    # Summary
    matches = sum(1 for p in scored_pairs if p["scorer"]["is_match"])
    non_matches = len(scored_pairs) - matches
    print(f"  Predicted matches: {matches}")
    print(f"  Predicted non-matches: {non_matches}")

    if scored_pairs:
        scores = [p["scorer"]["score"] for p in scored_pairs]
        print(f"  Score range: {min(scores):.1f} — {max(scores):.1f}")


if __name__ == "__main__":
    main()
