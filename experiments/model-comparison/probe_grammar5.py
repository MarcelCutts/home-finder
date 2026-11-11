"""Fifth grammar probe: strict structured facts + non-strict free-text notes in one request."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path
from typing import Any

from home_finder.filters.quality import VISUAL_ANALYSIS_TOOL
from mc.env import make_clients
from probe_grammar import stats, strict_shape
from probe_grammar2 import call

ROOT = Path(__file__).resolve().parent


def is_free_text(node: dict[str, Any]) -> bool:
    if node.get("type") == "string" and "enum" not in node:
        return True
    items = node.get("items", {})
    return node.get("type") == "array" and items.get("type") == "string" and "enum" not in items


def split(schema: dict[str, Any], keep_room_names: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    facts = copy.deepcopy(schema)
    notes: dict[str, Any] = {}

    def walk(node: dict[str, Any], prefix: str) -> None:
        props = node.get("properties", {})
        for name in list(props):
            child = props[name]
            if is_free_text(child) and not (keep_room_names and prefix.endswith("room_areas")):
                notes[f"{prefix}{name}".replace(".", "__")] = props.pop(name)
                if name in node.get("required", []):
                    node["required"].remove(name)
            elif child.get("type") == "object":
                walk(child, f"{prefix}{name}.")
            elif child.get("type") == "array" and child.get("items", {}).get("type") == "object":
                walk(child["items"], f"{prefix}{name}.")

    walk(facts, "")
    notes_schema = {"type": "object", "properties": notes, "required": list(notes)}
    return facts, notes_schema


def tool(name: str, schema: dict[str, Any], strict: bool) -> dict[str, Any]:
    t = {"name": name, "description": name, "input_schema": strict_shape(schema) if strict else schema}
    if strict:
        t["strict"] = True
    return t


async def main() -> None:
    a, _ = make_clients()
    base = VISUAL_ANALYSIS_TOOL["input_schema"]
    rows = []
    for keep in (True, False):
        facts, notes = split(base, keep_room_names=keep)
        label = "room names kept" if keep else "room names moved to notes"
        for name, tools in [
            (f"facts strict alone ({label})", [tool("visual_facts", facts, True)]),
            (f"facts strict + notes non-strict ({label})",
             [tool("visual_facts", facts, True), tool("visual_notes", notes, False)]),
        ]:
            verdict = await call(a, tools)
            st = stats(facts)
            rows.append({"variant": name, "verdict": verdict.strip(), "fact_props": st["props"],
                         "note_fields": len(notes["properties"])})
            print(f"{verdict} {name:62s} facts props={st['props']} notes={len(notes['properties'])}",
                  flush=True)
    (ROOT / "data" / "grammar-probe-5.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    asyncio.run(main())
