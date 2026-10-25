"""Find which parts of the visual schema push Anthropic's strict grammar over its size limit.

Sends tiny strict-tool requests (max_tokens 16) with schema variants; a rejection
costs nothing, an acceptance costs a fraction of a cent.

    uv run python probe_grammar.py
"""

from __future__ import annotations

import asyncio
import copy
import json
import sys
import time
from pathlib import Path
from typing import Any

from home_finder.filters.quality import VISUAL_ANALYSIS_TOOL, _eval_json_schema
from mc.env import make_clients

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "grammar-probe.json"


def strict_shape(schema: dict[str, Any]) -> dict[str, Any]:
    """Anthropic strict needs additionalProperties false on every object; keep required as-is."""

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: walk(v) for k, v in node.items()}
            if out.get("type") == "object":
                out["additionalProperties"] = False
            return out
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(copy.deepcopy(schema))


def strip(schema: Any, keys: set[str]) -> Any:
    if isinstance(schema, dict):
        return {k: strip(v, keys) for k, v in schema.items() if k not in keys}
    if isinstance(schema, list):
        return [strip(x, keys) for x in schema]
    return schema


def all_required(schema: Any) -> Any:
    if isinstance(schema, dict):
        out = {k: all_required(v) for k, v in schema.items() if k != "default"}
        if out.get("type") == "object" and "properties" in out:
            out["required"] = list(out["properties"])
        return out
    if isinstance(schema, list):
        return [all_required(x) for x in schema]
    return schema


def no_nullable(schema: Any) -> Any:
    """Replace anyOf[X, null] with X (a sentinel would carry 'unknown' instead)."""
    if isinstance(schema, dict):
        if "anyOf" in schema:
            options = [o for o in schema["anyOf"] if o.get("type") != "null"]
            if len(options) == 1:
                merged = {k: v for k, v in schema.items() if k != "anyOf"}
                merged.update(options[0])
                return no_nullable(merged)
        return {k: no_nullable(v) for k, v in schema.items()}
    if isinstance(schema, list):
        return [no_nullable(x) for x in schema]
    return schema


def subset(schema: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    out = copy.deepcopy(schema)
    out["properties"] = {k: v for k, v in schema["properties"].items() if k in keys}
    out["required"] = [k for k in schema.get("required", []) if k in keys]
    return out


def stats(schema: Any) -> dict[str, int]:
    counts = {"chars": len(json.dumps(schema, separators=(",", ":"))), "props": 0, "enum_values": 0,
              "anyOf": 0, "optional": 0}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "properties" in node:
                counts["props"] += len(node["properties"])
                counts["optional"] += len(set(node["properties"]) - set(node.get("required", [])))
            counts["enum_values"] += len(node.get("enum", []))
            counts["anyOf"] += "anyOf" in node
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    return counts


async def accepted(client: Any, schema: dict[str, Any]) -> tuple[bool, str, float]:
    t0 = time.perf_counter()
    try:
        await client.messages.create(
            model="claude-haiku-5-5",
            max_tokens=16,
            thinking={"type": "disabled"},
            messages=[{"role": "user", "content": "Reply with the word ok."}],
            tools=[{
                "name": "property_visual_analysis",
                "description": "Return visual property quality analysis results from images",
                "input_schema": strict_shape(schema),
                "strict": True,
            }],
            tool_choice={"type": "auto"},
        )
        return True, "", time.perf_counter() - t0
    except Exception as exc:  # 400 on rejection
        return False, str(exc)[:160], time.perf_counter() - t0


async def main() -> None:
    a, _ = make_clients()
    base = VISUAL_ANALYSIS_TOOL["input_schema"]
    sections = list(base["properties"])
    results: list[dict[str, Any]] = []

    async def probe(name: str, schema: dict[str, Any]) -> bool:
        ok, err, secs = await accepted(a, schema)
        row = {"variant": name, "accepted": ok, "seconds": round(secs, 1), **stats(schema), "error": err}
        results.append(row)
        print(f"{'OK ' if ok else 'NO '} {name:46s} chars={row['chars']:6d} props={row['props']:3d} "
              f"enum={row['enum_values']:3d} anyOf={row['anyOf']:2d} opt={row['optional']:2d} {secs:5.1f}s",
              flush=True)
        OUT.write_text(json.dumps(results, indent=1))
        return ok

    await probe("full schema as sent today", base)
    await probe("evaluation schema (control, strict today)", _eval_json_schema())
    for section in sections:
        await probe(f"only {section}", subset(base, [section]))
    # Grow top-level sections in schema order until the grammar no longer compiles.
    for i in range(2, len(sections) + 1):
        if not await probe(f"first {i} sections (..{sections[i - 1]})", subset(base, sections[:i])):
            break
    await probe("no descriptions", strip(base, {"description"}))
    await probe("all required, no defaults", all_required(base))
    await probe("no nullable unions", no_nullable(base))
    await probe("no nullable + all required", all_required(no_nullable(base)))
    no_rooms = copy.deepcopy(base)
    no_rooms["properties"]["space"]["properties"].pop("room_areas")
    await probe("without space.room_areas", no_rooms)
    no_space = subset(base, [s for s in sections if s != "space"])
    await probe("without space section", no_space)
    await probe("without space, no nullable", no_nullable(no_space))
    half = len(sections) // 2
    await probe("split A: first half of sections", subset(base, sections[:half]))
    await probe("split B: second half of sections", subset(base, sections[half:]))


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    asyncio.run(main())
