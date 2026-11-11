"""Second grammar probe: property count vs enum values, and two strict tools in one request."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path
from typing import Any

from home_finder.filters.quality import VISUAL_ANALYSIS_TOOL
from mc.env import make_clients
from probe_grammar import stats, strict_shape, subset

ROOT = Path(__file__).resolve().parent


def enums_to_strings(schema: Any) -> Any:
    if isinstance(schema, dict):
        out = {k: enums_to_strings(v) for k, v in schema.items() if k != "enum"}
        return out
    if isinstance(schema, list):
        return [enums_to_strings(x) for x in schema]
    return schema


def synthetic(n_props: int, enum_size: int = 0) -> dict[str, Any]:
    prop = {"type": "string", **({"enum": [f"v{i}" for i in range(enum_size)]} if enum_size else {})}
    props = {f"f{i}": dict(prop) for i in range(n_props)}
    return {"type": "object", "properties": props, "required": list(props)}


async def call(client: Any, tools: list[dict[str, Any]]) -> str:
    try:
        await client.messages.create(
            model="claude-haiku-5-5", max_tokens=16, thinking={"type": "disabled"},
            messages=[{"role": "user", "content": "Reply with the word ok."}],
            tools=tools, tool_choice={"type": "auto"},
        )
        return "OK "
    except Exception as exc:
        return "NO " if "too large" in str(exc) or "complex" in str(exc) else f"ERR {str(exc)[:120]}"


def tool(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "description": name, "input_schema": strict_shape(schema), "strict": True}


async def main() -> None:
    a, _ = make_clients()
    base = VISUAL_ANALYSIS_TOOL["input_schema"]
    sections = list(base["properties"])
    half = len(sections) // 2
    cases: list[tuple[str, list[dict[str, Any]]]] = [
        ("full schema, enums -> plain strings", [tool("v", enums_to_strings(base))]),
        ("first 8 sections, enums -> strings", [tool("v", enums_to_strings(subset(base, sections[:8])))]),
        ("two strict tools (half A + half B) in one request",
         [tool("visual_a", subset(base, sections[:half])), tool("visual_b", subset(base, sections[half:]))]),
    ]
    for n in (40, 56, 64, 80, 120):
        cases.append((f"synthetic {n} string props, flat", [tool("s", synthetic(n))]))
    for n, k in ((40, 5), (40, 10), (56, 3)):
        cases.append((f"synthetic {n} props x {k}-value enums", [tool("s", synthetic(n, k))]))
    rows = []
    for name, tools in cases:
        verdict = await call(a, tools)
        st = stats({"properties": {t["name"]: t["input_schema"] for t in tools}})
        rows.append({"variant": name, "verdict": verdict.strip(), **st})
        print(f"{verdict} {name:52s} props={st['props']:3d} enum={st['enum_values']:3d}", flush=True)
    (ROOT / "data" / "grammar-probe-2.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    asyncio.run(main())
