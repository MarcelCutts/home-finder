"""Fourth grammar probe: realistic schema edits that cut free-text fields."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path
from typing import Any

from home_finder.filters.quality import VISUAL_ANALYSIS_TOOL
from mc.env import make_clients
from probe_grammar import stats
from probe_grammar2 import call, tool
from probe_grammar3 import count_types

ROOT = Path(__file__).resolve().parent
ARRAYS = [("condition", "maintenance_concerns"), ("flooring_noise", "noise_indicators"),
          ("listing_red_flags", "missing_room_photos"), ("listing_red_flags", "description_concerns")]
NOTES = [("kitchen", "notes"), ("light_space", "notes"), ("bathroom", "notes"), ("bedroom", "notes"),
         ("outdoor_space", "notes"), ("flooring_noise", "notes"), ("space", "area_notes")]


def edit(schema: dict[str, Any], *, arrays_to_text: bool = False, drop_notes: bool = False,
         merge_notes: bool = False) -> dict[str, Any]:
    s = copy.deepcopy(schema)
    props = s["properties"]
    if arrays_to_text:
        for section, field in ARRAYS:
            props[section]["properties"][field] = {"type": "string", "description": "Semicolon-separated."}
    if drop_notes or merge_notes:
        for section, field in NOTES:
            props[section]["properties"].pop(field, None)
            req = props[section].get("required")
            if req and field in req:
                req.remove(field)
    if merge_notes:
        props["section_notes"] = {"type": "string", "description": "Notes per section, one line each."}
        s.setdefault("required", []).append("section_notes")
    return s


async def main() -> None:
    a, _ = make_clients()
    base = VISUAL_ANALYSIS_TOOL["input_schema"]
    variants = {
        "string arrays -> one text field each": edit(base, arrays_to_text=True),
        "drop 7 per-section notes": edit(base, drop_notes=True),
        "7 notes merged into one field": edit(base, merge_notes=True),
        "notes merged + arrays -> text": edit(base, merge_notes=True, arrays_to_text=True),
    }
    rows = []
    for name, schema in variants.items():
        verdict = await call(a, [tool("property_visual_analysis", schema)])
        st = {**stats(schema), **count_types(schema)}
        rows.append({"variant": name, "verdict": verdict.strip(), **st})
        print(f"{verdict} {name:40s} props={st['props']} free={st['free_strings']} arrays={st['string_arrays']}",
              flush=True)
    (ROOT / "data" / "grammar-probe-4.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    asyncio.run(main())
