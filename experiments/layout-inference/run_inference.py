"""Step 2: Re-analyze ground truth properties with floorplan stripped.

For each property in ground_truth.json:
- Reconstruct MergedProperty from DB with floorplan=None
- Call PropertyQualityFilter.analyze_single_merged()
- Save the photo-only analysis alongside ground truth

This naturally triggers has_labeled_floorplan=False, activating the
existing <floorplan_note> prompt path. No production code changes needed.

Usage:
    uv run python run_inference.py --limit 3                           # Smoke test
    uv run python run_inference.py                                     # Full run
    uv run python run_inference.py --max-gallery 8                     # Photo count sensitivity
    uv run python run_inference.py --prompt-variant reference_objects   # Prompt iteration
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import aiosqlite

from home_finder.db.row_mappers import row_to_merged_property
from home_finder.filters.quality import DELAY_BETWEEN_CALLS, PropertyQualityFilter
from home_finder.models import PropertyImage

DATA_DIR = Path(__file__).parent / "data"

# Prompt variants for iteration (T3/T4)
PROMPT_VARIANTS: dict[str, str] = {
    "reference_objects": (
        "\n\n<floorplan_note>\n"
        "No dedicated floorplan was provided for this listing. Some gallery images may be "
        "unlabeled floorplans (floor plan diagrams showing room layouts, dimensions, and "
        "labels on a white/light background). If you spot any, report their 1-based indices "
        "in floorplan_detected_in_gallery and use them for room size estimates and layout "
        "assessment as you would a labeled floorplan.\n\n"
        "When estimating room dimensions from photos (not floorplans), use these UK reference "
        "objects for scale:\n"
        "- Standard interior door: ~76cm wide × 198cm tall\n"
        "- Double bed: ~135 × 190cm\n"
        "- Single bed: ~90 × 190cm\n"
        "- Kitchen base units: ~60cm depth\n"
        "- Standard radiator: ~60cm or ~100cm wide\n"
        "- Ceiling height: ~240cm (modern), ~270-300cm (Victorian/period)\n"
        "</floorplan_note>"
    ),
}


async def load_merged_from_db(
    db_path: str,
    unique_id: str,
    *,
    strip_floorplan: bool = True,
    max_gallery: int | None = None,
) -> "MergedProperty | None":
    """Reconstruct a MergedProperty from the database.

    Args:
        db_path: Path to SQLite database.
        unique_id: Property unique ID.
        strip_floorplan: If True, set floorplan=None to trigger photo-only analysis.
        max_gallery: If set, cap gallery images to this many.
    """
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row

    try:
        cursor = await conn.execute(
            "SELECT * FROM properties WHERE unique_id = ?", (unique_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        # Load images
        img_cursor = await conn.execute(
            "SELECT source, url, image_type FROM property_images "
            "WHERE property_unique_id = ? ORDER BY image_type, id",
            (unique_id,),
        )
        img_rows = await img_cursor.fetchall()

        from home_finder.models import PropertySource

        images = [
            PropertyImage(
                source=PropertySource(r["source"]),
                url=r["url"],
                image_type=r["image_type"],
            )
            for r in img_rows
        ]

        # Separate gallery and floorplan
        gallery = tuple(img for img in images if img.image_type == "gallery")
        floorplan = next((img for img in images if img.image_type == "floorplan"), None)

        if max_gallery is not None:
            gallery = gallery[:max_gallery]

        if strip_floorplan:
            floorplan = None

        # Build MergedProperty using row_to_merged_property pattern
        from home_finder.db.row_mappers import row_to_property
        from home_finder.models import MergedProperty, PropertySource as PS
        from pydantic import HttpUrl

        prop = row_to_property(row)
        sources_list = []
        source_urls: dict = {}
        descriptions: dict = {}

        if row["sources"]:
            for s in json.loads(row["sources"]):
                sources_list.append(PS(s))
        else:
            sources_list.append(prop.source)

        if row["source_urls"]:
            for s, url in json.loads(row["source_urls"]).items():
                source_urls[PS(s)] = HttpUrl(url)
        else:
            source_urls[prop.source] = prop.url

        if row["descriptions_json"]:
            for s, desc in json.loads(row["descriptions_json"]).items():
                descriptions[PS(s)] = desc

        min_price = row["min_price"] if row["min_price"] is not None else prop.price_pcm
        max_price = row["max_price"] if row["max_price"] is not None else prop.price_pcm

        return MergedProperty(
            canonical=prop,
            sources=tuple(sources_list),
            source_urls=source_urls,
            images=gallery,
            floorplan=floorplan,
            min_price=min_price,
            max_price=max_price,
            descriptions=descriptions,
        )
    finally:
        await conn.close()


def _patch_floorplan_note(variant: str) -> None:
    """Monkey-patch the floorplan_note prompt for variant testing.

    This temporarily replaces the no-floorplan prompt text in quality_prompts
    so we can test alternate prompts without changing production code.
    """
    import home_finder.filters.quality_prompts as qp

    original_build = qp.build_user_prompt

    variant_text = PROMPT_VARIANTS[variant]

    def patched_build(*args: object, **kwargs: object) -> str:
        # Call original with has_labeled_floorplan=True to suppress the default note
        kwargs["has_labeled_floorplan"] = True
        result = original_build(*args, **kwargs)
        # Remove the trailing tool instruction, append our variant, then re-add it
        tool_suffix = "\n\nProvide your visual quality assessment using the property_visual_analysis tool."
        if result.endswith(tool_suffix):
            result = result[: -len(tool_suffix)]
        result += variant_text
        result += tool_suffix
        return result

    qp.build_user_prompt = patched_build  # type: ignore[assignment]


async def run(
    db_path: str,
    *,
    limit: int | None = None,
    max_gallery: int | None = None,
    prompt_variant: str | None = None,
) -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY environment variable not set", file=sys.stderr)
        raise SystemExit(1)

    gt_path = DATA_DIR / "ground_truth.json"
    if not gt_path.exists():
        print(f"Error: {gt_path} not found. Run collect_ground_truth.py first.", file=sys.stderr)
        raise SystemExit(1)

    ground_truth = json.loads(gt_path.read_text())
    if limit:
        ground_truth = ground_truth[:limit]

    print(f"Properties to analyze: {len(ground_truth)}")
    print(f"Estimated cost: ~${len(ground_truth) * 0.06:.2f}")
    if max_gallery:
        print(f"Max gallery images: {max_gallery}")
    if prompt_variant:
        print(f"Prompt variant: {prompt_variant}")

    # Apply prompt variant if requested
    if prompt_variant:
        if prompt_variant not in PROMPT_VARIANTS:
            print(f"Error: Unknown variant '{prompt_variant}'. Available: {list(PROMPT_VARIANTS.keys())}")
            raise SystemExit(1)
        _patch_floorplan_note(prompt_variant)

    data_dir = str(Path(db_path).parent)
    quality_filter = PropertyQualityFilter(api_key=api_key)

    results = []
    errors = []
    start_time = time.time()

    for i, entry in enumerate(ground_truth):
        unique_id = entry["unique_id"]
        print(f"\n[{i + 1}/{len(ground_truth)}] Analyzing {unique_id}...", end=" ", flush=True)

        merged = await load_merged_from_db(
            db_path,
            unique_id,
            strip_floorplan=True,
            max_gallery=max_gallery,
        )
        if merged is None:
            print("SKIP (not found in DB)")
            continue

        gallery_count = len(merged.images)
        print(f"({gallery_count} gallery images)", end=" ", flush=True)

        try:
            _, analysis = await quality_filter.analyze_single_merged(
                merged, data_dir=data_dir
            )

            space = analysis.space
            bedroom = analysis.bedroom

            result = {
                "unique_id": unique_id,
                "gallery_count_used": gallery_count,
                "max_gallery_cap": max_gallery,
                "prompt_variant": prompt_variant,
                "inference": {
                    "living_room_sqm": space.living_room_sqm if space else None,
                    "is_spacious_enough": space.is_spacious_enough if space else None,
                    "hosting_layout": space.hosting_layout if space else None,
                    "confidence": space.confidence if space else None,
                    "office_separation": bedroom.office_separation if bedroom else None,
                },
                "ground_truth": entry["ground_truth"],
            }
            results.append(result)
            inf = result["inference"]
            print(
                f"OK  sqm={inf['living_room_sqm']}  "
                f"spacious={inf['is_spacious_enough']}  "
                f"hosting={inf['hosting_layout']}  "
                f"office={inf['office_separation']}"
            )

        except Exception as e:
            print(f"ERROR: {e}")
            errors.append({"unique_id": unique_id, "error": str(e)})

        # Rate limit
        if i < len(ground_truth) - 1:
            await asyncio.sleep(DELAY_BETWEEN_CALLS)

    elapsed = time.time() - start_time
    print(f"\n{'=' * 60}")
    print(f"Completed: {len(results)}/{len(ground_truth)} ({len(errors)} errors)")
    print(f"Time: {elapsed:.1f}s ({elapsed / max(len(results), 1):.1f}s/property)")

    # Save results
    suffix = ""
    if prompt_variant:
        suffix += f"_{prompt_variant}"
    if max_gallery:
        suffix += f"_gallery{max_gallery}"

    out_path = DATA_DIR / f"inference_results{suffix}.json"
    output = {
        "config": {
            "max_gallery": max_gallery,
            "prompt_variant": prompt_variant,
            "total_properties": len(ground_truth),
            "successful": len(results),
            "errors": len(errors),
            "elapsed_seconds": elapsed,
        },
        "results": results,
        "errors": errors,
    }
    out_path.write_text(json.dumps(output, indent=2))
    print(f"Saved to {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run photo-only layout inference")
    parser.add_argument(
        "--db",
        default="../../data/properties.db",
        help="Path to production SQLite database",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process first N properties (for iteration)",
    )
    parser.add_argument(
        "--max-gallery",
        type=int,
        default=None,
        help="Cap gallery images to test photo count sensitivity",
    )
    parser.add_argument(
        "--prompt-variant",
        type=str,
        default=None,
        choices=list(PROMPT_VARIANTS.keys()),
        help="Test alternate prompt text",
    )
    args = parser.parse_args()

    db_path = str(Path(args.db).resolve()) if not Path(args.db).is_absolute() else args.db
    if not Path(db_path).exists():
        alt = Path(__file__).parent / args.db
        if alt.exists():
            db_path = str(alt.resolve())
        else:
            print(f"Error: Database not found at {db_path}", file=sys.stderr)
            raise SystemExit(1)

    asyncio.run(run(db_path, limit=args.limit, max_gallery=args.max_gallery, prompt_variant=args.prompt_variant))


if __name__ == "__main__":
    main()
