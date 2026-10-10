"""Withhold unsupported floorplan arithmetic before evaluating a new assessment."""

import re
from copy import deepcopy
from typing import Any


def reconcile_floorplan_areas(visual_data: dict[str, Any]) -> dict[str, Any]:
    """Keep labels/printed totals without treating bounding rectangles as usable area.

    This is a consistency check of validated model observations, not independent
    plan measurement. It deliberately runs after checkpoint retrieval so raw paid
    observations remain intact and receive the same checks on every evaluation.
    """
    data = deepcopy(visual_data)
    space = data["space"]
    rooms = space.get("room_areas", [])
    printed = space.get("floorplan_total_area_sqm")
    total = printed if printed is not None else space.get("total_area_sqm")
    living = space.get("living_room_sqm")
    # Allow only rounding slack, not a percentage of the whole flat. Even a
    # partial room sum cannot exceed the internal total.
    tolerance = max(0.5, len(rooms) * 0.05)
    room_sum = sum(room.get("area_sqm") or 0 for room in rooms)
    conflict = total is not None and (
        room_sum > total + tolerance or (living is not None and living > total + tolerance)
    )
    withheld = False
    for room in rooms:
        area = room.get("area_sqm")
        basis = room.get("area_basis", "unknown")
        length, width = room.get("length_m"), room.get("width_m")
        unsupported = basis == "unknown"
        if basis == "rectangular_dimensions":
            unsupported = (
                length is None
                or width is None
                or length <= 0
                or width <= 0
                or (area is not None and abs(area - length * width) > 0.5)
            )
        if conflict or unsupported:
            withheld = (
                withheld
                or area is not None
                or (
                    living is not None
                    and bool(re.search(r"living|reception|lounge", room["name"], re.I))
                )
            )
            room["area_sqm"] = None
            room["area_basis"] = "unknown"

    if printed is not None:
        space["total_area_sqm"] = printed
        space["area_estimation_method"] = "stated_on_floorplan"
    elif conflict or withheld:
        # Without a separate printed total, the computed total may incorporate
        # the very areas we have rejected.
        space["total_area_sqm"] = None
        space["area_estimation_method"] = None

    if conflict or withheld:
        note = (
            "Room areas conflict with the internal total; precise room areas are withheld. "
            if conflict
            else "Some room areas lack supported geometry; precise areas are withheld. "
        ) + "Readable dimensions are retained as labels, not usable areas; verify at viewing."
        space["living_room_sqm"] = None
        space["is_spacious_enough"] = None
        space["hosting_layout"] = "unknown"
        space["area_notes"] = note
        # The visual summary/space notes can repeat the rejected numbers (or
        # 'vast'/'spacious' conclusions). Do not pass these on as facts while
        # merely nulling the structured number. Other observations survive.
        data["summary"] = note
        for section in ("light_space", "bedroom"):
            data[section]["notes"] = note
        data["bedroom"]["can_fit_desk"] = "unknown"
    return data
