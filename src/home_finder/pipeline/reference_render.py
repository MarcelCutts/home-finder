"""Render local, candidate-blind first-review forms without a web service."""

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from home_finder.models.reference import CaseAnnotation, ReferenceCase
from home_finder.pipeline.capture import _write_private

_FIELDS = [
    {
        "name": "printed_total_area",
        "title": "Printed total floor area",
        "kind": "area",
        "help": "Transcribe a printed total and its unit. Do not estimate or add room areas.",
    },
    {
        "name": "labelled_bedrooms_on_plan",
        "title": "Bedrooms labelled on the floorplan",
        "kind": "integer",
        "help": "Count rooms explicitly labelled bedroom, including a dual-use bedroom label.",
    },
    {
        "name": "advertised_epc",
        "title": "Advertised current EPC band",
        "kind": "band",
        "values": list("ABCDEFG"),
        "help": "Use the current rating, not the potential rating or an area estimate.",
    },
    {
        "name": "advertised_council_tax",
        "title": "Advertised council-tax band",
        "kind": "band",
        "values": list("ABCDEFGH"),
        "help": "Use an explicit listing statement. Budgeting assumptions are not evidence.",
    },
    {
        "name": "advertised_pets_allowed",
        "title": "Explicit pets permission",
        "kind": "boolean",
        "help": "True for permission; false for a ban. Subject to approval means unknown.",
    },
    {
        "name": "advertised_bills_included",
        "title": "All utility bills explicitly included",
        "kind": "boolean",
        "help": "True only for all utility bills. A partial list or unspecified bills is unknown.",
    },
    {
        "name": "bathtub_visible",
        "title": "Bathtub visibly present",
        "kind": "boolean",
        "help": "A clearly visible bathtub supports true. Missing bathroom views means unknown.",
    },
]


def render_pack(destination: Path, manifest: dict[str, Any]) -> None:
    templates = files("home_finder.web").joinpath("templates")
    environment = Environment(
        loader=FileSystemLoader(str(templates)), autoescape=select_autoescape(("html",))
    )
    index_html = environment.get_template("reference_index.html").render(manifest=manifest)
    _write_private(destination / "index.html", index_html.encode())
    template = environment.get_template("reference_case.html")
    for index, entry in enumerate(manifest["cases"]):
        case = ReferenceCase.model_validate_json((destination / entry["file"]).read_bytes())
        blank = CaseAnnotation(case_id=case.case_id, input_sha256=entry["sha256"])
        materials = [
            {"id": f"image:{item.sha256}", "label": f"Image {number} ({', '.join(item.roles)})"}
            for number, item in enumerate(case.images, 1)
        ] + [
            {"id": f"text:{item.sha256}", "label": f"Text {number} ({item.source})"}
            for number, item in enumerate(case.texts, 1)
        ]
        bodies = {item.sha256: (destination / item.file).read_text() for item in case.texts}
        html = template.render(
            case=case,
            number=index + 1,
            total=len(manifest["cases"]),
            next_case=(
                manifest["cases"][index + 1]["case_id"]
                if index + 1 < len(manifest["cases"])
                else None
            ),
            fields=_FIELDS,
            materials=materials,
            texts=bodies,
            blank=json.loads(blank.model_dump_json()),
        )
        _write_private(destination / "cases" / f"{case.case_id}.html", html.encode())
