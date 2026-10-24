"""Third grammar probe: how many free-text strings, numbers and booleans fit."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from home_finder.filters.quality import VISUAL_ANALYSIS_TOOL
from mc.env import make_clients
from probe_grammar2 import call, tool

ROOT = Path(__file__).resolve().parent


def mixed(strings: int = 0, enums: int = 0, numbers: int = 0, booleans: int = 0, str_arrays: int = 0,
          nullable_numbers: int = 0) -> dict[str, Any]:
    props: dict[str, Any] = {}
    props |= {f"s{i}": {"type": "string"} for i in range(strings)}
    props |= {f"e{i}": {"type": "string", "enum": ["yes", "no", "unknown"]} for i in range(enums)}
    props |= {f"n{i}": {"type": "number"} for i in range(numbers)}
    props |= {f"b{i}": {"type": "boolean"} for i in range(booleans)}
    props |= {f"a{i}": {"type": "array", "items": {"type": "string"}} for i in range(str_arrays)}
    props |= {f"z{i}": {"anyOf": [{"type": "number"}, {"type": "null"}]} for i in range(nullable_numbers)}
    return {"type": "object", "properties": props, "required": list(props)}


def count_types(schema: Any, acc: dict[str, int] | None = None) -> dict[str, int]:
    acc = acc if acc is not None else {"free_strings": 0, "string_arrays": 0, "enums": 0, "numbers": 0,
                                       "booleans": 0, "integers": 0}
    if isinstance(schema, dict):
        if schema.get("type") == "string":
            acc["enums" if "enum" in schema else "free_strings"] += 1
        elif schema.get("type") == "array" and schema.get("items", {}).get("type") == "string" \
                and "enum" not in schema["items"]:
            acc["string_arrays"] += 1
            return acc
        elif schema.get("type") == "number":
            acc["numbers"] += 1
        elif schema.get("type") == "boolean":
            acc["booleans"] += 1
        elif schema.get("type") == "integer":
            acc["integers"] += 1
        for k, v in schema.items():
            if k != "items" or schema.get("type") != "array" or isinstance(v, dict):
                count_types(v, acc)
    elif isinstance(schema, list):
        for v in schema:
            count_types(v, acc)
    return acc


async def main() -> None:
    print("visual schema field types:", count_types(VISUAL_ANALYSIS_TOOL["input_schema"]), flush=True)
    a, _ = make_clients()
    variants = [("strings", {"strings": n}) for n in (6, 10, 14, 20, 28)]
    variants += [("numbers", {"numbers": 40}), ("booleans", {"booleans": 60}),
                 ("string arrays", {"str_arrays": 10}), ("nullable numbers", {"nullable_numbers": 12}),
                 ("10 strings + 50 enums", {"strings": 10, "enums": 50}),
                 ("14 strings + 50 enums", {"strings": 14, "enums": 50}),
                 ("6 strings + 6 str arrays + 50 enums", {"strings": 6, "str_arrays": 6, "enums": 50})]
    rows = []
    for name, kw in variants:
        verdict = await call(a, [tool("m", mixed(**kw))])
        rows.append({"variant": name, **kw, "verdict": verdict.strip()})
        print(f"{verdict} {name:36s} {kw}", flush=True)
    (ROOT / "data" / "grammar-probe-3.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    asyncio.run(main())
