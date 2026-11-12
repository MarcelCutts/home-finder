"""Score saved analysis outputs against what the floorplans and adverts say; calls no model.

Run from the repository root:
  python docs/audits/2026-11-12-eval.py RUN_DIR [RUN_DIR ...]

Each RUN_DIR holds one JSON file per run in the bake-off harness layout
(``case_id``, ``analysis``, ``failure``, ``error``). The two label files sit
beside this script. Only aggregate counts, field names and case ids are printed.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
LABELS = json.loads((HERE / "2026-11-12-eval-floorplan-labels.json").read_text())
HOMES: dict[str, dict[str, Any]] = LABELS["homes"]
NO_FLOORPLAN: list[str] = LABELS["no_floorplan"]
ADVERTS = json.loads((HERE / "2026-11-12-eval-advert-labels.json").read_text())
ADVERT_FIELDS: list[str] = ADVERTS["fields"]
EMPTY = (None, "unknown", "")
AREA_TOLERANCE = 0.005  # relative, with a floor of 0.15 m²
DIMENSION_TOLERANCE_M = 0.021
Dimensions = tuple[float, float]


def load(directory: Path) -> dict[str, list[dict[str, Any]]]:
    """Valid analyses by home; a home run several times has several."""
    runs: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for path in sorted(directory.glob("*.json")):
        run = json.loads(path.read_text())
        analysis = run.get("analysis")
        if not analysis or run.get("failure") or run.get("error"):
            continue
        if not analysis.get("analysis_failure"):
            runs[run["case_id"]].append(analysis)
    return runs


def area_matches(value: float, home: dict[str, Any]) -> bool:
    targets = [home["total_sqm"], *home.get("also_accept_sqm", [])]
    return any(abs(value - target) <= max(0.15, AREA_TOLERANCE * target) for target in targets)


def dimensions(room: dict[str, Any]) -> Dimensions | None:
    length, width = room.get("length_m"), room.get("width_m")
    if length is None or width is None:
        return None
    low, high = sorted((float(length), float(width)))
    return low, high


def same(a: Dimensions, b: Dimensions) -> bool:
    return all(abs(x - y) <= DIMENSION_TOLERANCE_M for x, y in zip(a, b, strict=True))


def score_rooms(
    space: dict[str, Any], home: dict[str, Any], counts: collections.Counter[str]
) -> None:
    reported = [(dimensions(room), room) for room in space.get("room_areas") or []]
    unused = [(size, room) for size, room in reported if size is not None]
    for printed in home["rooms"]:
        low, high = sorted(printed["m"])
        hit = next((pair for pair in unused if same(pair[0], (low, high))), None)
        if hit is None:
            counts["printed room not transcribed"] += 1
            continue
        unused.remove(hit)
        counts["printed room transcribed"] += 1
        if printed.get("external"):
            marked = hit[1].get("area_scope") == "external"
            counts["balcony marked external" if marked else "balcony not marked external"] += 1
    counts["reported room matching nothing printed"] += len(unused)


def score_living_room(
    space: dict[str, Any], home: dict[str, Any], counts: collections.Counter[str]
) -> None:
    printed = next((room for room in home["rooms"] if room.get("living")), None)
    if printed is None:
        return
    reported = space.get("living_room_sqm")
    if printed.get("irregular"):
        counts["irregular: left empty" if reported is None else "irregular: area given"] += 1
    elif reported is None:
        counts["rectangular: left empty"] += 1
    elif abs(reported - printed["m"][0] * printed["m"][1]) <= 0.25:
        counts["rectangular: matches printed size"] += 1
    else:
        counts["rectangular: other figure"] += 1


def score(directory: Path) -> None:
    runs = load(directory)
    label = "/".join(directory.parts[-2:])
    if not runs:
        print(f"\n== {label}: no valid runs")
        return
    total: collections.Counter[str] = collections.Counter()
    method: collections.Counter[str] = collections.Counter()
    rooms: collections.Counter[str] = collections.Counter()
    living: collections.Counter[str] = collections.Counter()
    absent: collections.Counter[str] = collections.Counter()
    floor: collections.Counter[str] = collections.Counter()
    outdoor: collections.Counter[str] = collections.Counter()
    wrong: list[str] = []
    for case_id, home in HOMES.items():
        for analysis in runs.get(case_id, []):
            space = analysis["space"]
            value = space.get("total_area_sqm")
            if value is None:
                total["missing"] += 1
            elif area_matches(value, home):
                total["right"] += 1
            else:
                total["wrong"] += 1
                wrong.append(f"{case_id}: reported {value}, plan prints {home['total_sqm']}")
            method[space.get("area_estimation_method") or "none"] += 1
            score_rooms(space, home, rooms)
            score_living_room(space, home, living)
            if "floor_level" in home:
                answer = analysis["light_space"].get("floor_level")
                right = answer == home["floor_level"]
                unsure = answer in (None, "unknown")
                floor["right" if right else "unknown" if unsure else "wrong"] += 1
            for flag in home.get("outdoor", []):
                outdoor["shown" if analysis["outdoor_space"].get(flag) else "missed"] += 1
    for case_id in NO_FLOORPLAN:
        for analysis in runs.get(case_id, []):
            space = analysis["space"]
            given = space.get("total_area_sqm") is not None
            absent[f"area given ({space.get('area_estimation_method')})" if given else "none"] += 1
    runs_scored = sum(total.values())
    homes_scored = sum(case_id in runs for case_id in HOMES)
    print(f"\n== {label}: {runs_scored} runs over {homes_scored} of {len(HOMES)} floorplan homes")
    print(
        f"   total floor area: right {total['right']}/{runs_scored}, "
        f"wrong {total['wrong']}, missing {total['missing']}"
    )
    for line in wrong[:6]:
        print(f"      {line}")
    print(f"   how the total was labelled: {dict(method)}")
    found, missed = rooms["printed room transcribed"], rooms["printed room not transcribed"]
    balconies = rooms["balcony marked external"] + rooms["balcony not marked external"]
    print(
        f"   room dimensions: {found}/{found + missed} printed rooms transcribed; "
        f"{rooms['reported room matching nothing printed']} reported rooms match nothing printed; "
        f"balconies marked external {rooms['balcony marked external']}/{balconies}"
    )
    print(f"   living room area: {dict(living)}")
    print(f"   homes without a floorplan, total area: {dict(absent)}")
    print(
        f"   floor level printed on the plan: right {floor['right']}, wrong {floor['wrong']}, "
        f"unknown {floor['unknown']}"
    )
    print(
        f"   balcony, terrace or shared garden drawn on the plan: shown {outdoor['shown']}, "
        f"missed {outdoor['missed']}"
    )


def score_adverts(directory: Path) -> None:
    """Compare the stored listing extraction with what the advert text states."""
    runs = load(directory)
    stated: collections.Counter[str] = collections.Counter()
    unstated: collections.Counter[str] = collections.Counter()
    by_field: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    claims: collections.Counter[str] = collections.Counter()
    errors: collections.Counter[str] = collections.Counter()
    for case_id, home in ADVERTS["homes"].items():
        for analysis in runs.get(case_id, []):
            extraction = analysis.get("listing_extraction") or {}
            for field in ADVERT_FIELDS:
                answer = extraction.get(field)
                label = home.get(field)
                if label is None:
                    outcome = "left empty" if answer in EMPTY else "claimed"
                    unstated[outcome] += 1
                    if outcome == "claimed":
                        claims[f"{field}={answer} ({case_id})"] += 1
                    continue
                allowed = [label["value"], *label.get("accept", [])]
                if answer in allowed or (answer in EMPTY and label["value"] in EMPTY):
                    outcome = "right"
                elif answer in EMPTY:
                    outcome = "missed"
                else:
                    outcome = "wrong"
                    errors[f"{field}={answer}, advert says {label['value']} ({case_id})"] += 1
                stated[outcome] += 1
                by_field[field][outcome] += 1
    if not stated and not unstated:
        return
    total = sum(stated.values())
    print(
        f"   advert facts stated: right {stated['right']}/{total}, missed {stated['missed']}, "
        f"wrong {stated['wrong']}"
    )
    for field in ADVERT_FIELDS:
        if by_field[field]:
            counts = by_field[field]
            print(
                f"      {field}: right {counts['right']}, missed {counts['missed']}, "
                f"wrong {counts['wrong']}"
            )
    for line, count in errors.most_common(8):
        print(f"      wrong x{count}: {line}")
    slots = sum(unstated.values())
    print(f"   advert facts not stated: left empty {unstated['left empty']}/{slots}")
    for line, count in claims.most_common(12):
        print(f"      claimed x{count}: {line}")


def main() -> None:
    arguments = sys.argv[1:]
    if not arguments:
        raise SystemExit(__doc__)
    rooms = sum(len(home["rooms"]) for home in HOMES.values())
    facts = sum(field in home for home in ADVERTS["homes"].values() for field in ADVERT_FIELDS)
    print(
        f"labels: {len(HOMES)} floorplan homes, {rooms} rooms with printed dimensions, "
        f"{len(NO_FLOORPLAN)} homes without a floorplan; "
        f"{len(ADVERTS['homes'])} adverts with {facts} stated facts"
    )
    for argument in arguments:
        directory = Path(argument).resolve()
        score(directory)
        score_adverts(directory)


if __name__ == "__main__":
    main()
