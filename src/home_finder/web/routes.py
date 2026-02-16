"""Web dashboard routes."""

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Final, cast

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from home_finder.data.area_context import (
    ACOUSTIC_PROFILES,
    AREA_CONTEXT,
    COUNCIL_TAX_MONTHLY,
    CREATIVE_SCENE,
    CRIME_RATES,
    HOSTING_TOLERANCE,
    NOISE_ENFORCEMENT,
    OUTCODE_BOROUGH,
    RENT_TRENDS,
    RENTAL_BENCHMARKS,
    get_area_overview,
    get_micro_area_for_ward,
    get_micro_areas,
    match_micro_area,
)
from home_finder.db import PropertyStorage
from home_finder.filters.fit_score import compute_fit_breakdown, compute_fit_score
from home_finder.logging import get_logger
from home_finder.models import (
    SOURCE_BADGES,
    SOURCE_NAMES,
    PropertyHighlight,
    PropertyImage,
    PropertyLowlight,
)
from home_finder.utils.address import extract_outcode
from home_finder.utils.image_cache import get_cache_dir, safe_dir_name, url_to_filename

logger = get_logger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

VALID_SORT_OPTIONS: Final = {"newest", "price_asc", "price_desc", "rating_desc", "fit_desc"}
VALID_PROPERTY_TYPES: Final = {
    "victorian",
    "edwardian",
    "georgian",
    "new_build",
    "purpose_built",
    "warehouse",
    "ex_council",
    "period_conversion",
}
VALID_NATURAL_LIGHT: Final = {"excellent", "good", "fair", "poor"}
VALID_HOB_TYPES: Final = {"gas", "induction", "electric"}
VALID_VALUE_RATINGS: Final = {"excellent", "good", "fair", "poor"}
VALID_FLOOR_LEVELS: Final = {"basement", "ground", "lower", "upper", "top"}
VALID_BUILDING_CONSTRUCTION: Final = {"solid_brick", "concrete", "timber_frame", "mixed"}
VALID_OFFICE_SEPARATION: Final = {"dedicated_room", "separate_area", "shared_space", "none"}
VALID_HOSTING_LAYOUT: Final = {"excellent", "good", "awkward", "poor"}
VALID_HOSTING_NOISE_RISK: Final = {"low", "moderate", "high"}
VALID_BROADBAND_TYPE: Final = {"fttp", "fttc", "cable", "standard"}

TAG_CATEGORIES: Final[dict[str, list[str]]] = {
    "Workspace": [
        PropertyHighlight.ULTRAFAST_BROADBAND.value,
        PropertyHighlight.DEDICATED_OFFICE.value,
        PropertyHighlight.SEPARATE_WORK_AREA.value,
        PropertyLowlight.BASIC_BROADBAND.value,
        PropertyLowlight.NO_WORK_SEPARATION.value,
    ],
    "Hosting": [
        PropertyHighlight.GREAT_HOSTING_LAYOUT.value,
        PropertyHighlight.SPACIOUS_LIVING.value,
        PropertyHighlight.OPEN_PLAN.value,
        PropertyLowlight.POOR_HOSTING_LAYOUT.value,
        PropertyLowlight.COMPACT_LIVING.value,
        PropertyLowlight.SMALL_LIVING.value,
        PropertyLowlight.NEW_BUILD_ACOUSTICS.value,
        PropertyLowlight.TRAFFIC_NOISE.value,
    ],
    "Kitchen": [
        PropertyHighlight.GAS_HOB.value,
        PropertyHighlight.INDUCTION_HOB.value,
        PropertyHighlight.DISHWASHER.value,
        PropertyHighlight.WASHING_MACHINE.value,
        PropertyHighlight.MODERN_KITCHEN.value,
        PropertyLowlight.ELECTRIC_HOB.value,
        PropertyLowlight.NO_DISHWASHER.value,
        PropertyLowlight.NO_WASHING_MACHINE.value,
        PropertyLowlight.DATED_KITCHEN.value,
    ],
    "Space & Light": [
        PropertyHighlight.EXCELLENT_LIGHT.value,
        PropertyHighlight.GOOD_LIGHT.value,
        PropertyHighlight.FLOOR_TO_CEILING_WINDOWS.value,
        PropertyHighlight.HIGH_CEILINGS.value,
        PropertyHighlight.BUILT_IN_WARDROBES.value,
        PropertyHighlight.GOOD_STORAGE.value,
        PropertyLowlight.POOR_STORAGE.value,
        PropertyLowlight.NO_STORAGE.value,
        PropertyLowlight.SMALL_BEDROOM.value,
        PropertyLowlight.COMPACT_BEDROOM.value,
    ],
    "Property": [
        PropertyHighlight.EXCELLENT_CONDITION.value,
        PropertyHighlight.RECENTLY_REFURBISHED.value,
        PropertyHighlight.PERIOD_FEATURES.value,
        PropertyHighlight.DOUBLE_GLAZING.value,
        PropertyHighlight.MODERN_BATHROOM.value,
        PropertyHighlight.TWO_BATHROOMS.value,
        PropertyHighlight.ENSUITE.value,
        PropertyLowlight.DATED_BATHROOM.value,
        PropertyLowlight.NEEDS_UPDATING.value,
        PropertyLowlight.BALCONY_CRACKING.value,
        PropertyLowlight.NO_INTERIOR_PHOTOS.value,
        PropertyLowlight.NO_BATHROOM_PHOTOS.value,
        PropertyLowlight.MISSING_KEY_PHOTOS.value,
    ],
    "Practical": [
        PropertyHighlight.PETS_ALLOWED.value,
        PropertyHighlight.BILLS_INCLUDED.value,
        PropertyHighlight.BIKE_STORAGE.value,
        PropertyHighlight.PARKING.value,
        PropertyHighlight.ON_SITE_GYM.value,
        PropertyHighlight.CONCIERGE.value,
        PropertyLowlight.SERVICE_CHARGE_UNSTATED.value,
    ],
    "Outdoor": [
        PropertyHighlight.PRIVATE_BALCONY.value,
        PropertyHighlight.PRIVATE_GARDEN.value,
        PropertyHighlight.PRIVATE_TERRACE.value,
        PropertyHighlight.SHARED_GARDEN.value,
        PropertyHighlight.COMMUNAL_GARDENS.value,
        PropertyHighlight.ROOF_TERRACE.value,
        PropertyHighlight.CANAL_VIEWS.value,
        PropertyHighlight.PARK_VIEWS.value,
        PropertyLowlight.NO_OUTDOOR_SPACE.value,
    ],
}


def _parse_optional_int(value: str | None) -> int | None:
    """Parse a string to int, returning None for empty/whitespace/non-numeric values."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def listing_age_filter(iso_str: str | None) -> str:
    """Convert an ISO datetime string to a human-readable listing age."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        delta = datetime.now(UTC) - dt
        days = delta.days
    except (ValueError, TypeError):
        return ""
    if days <= 0:
        return "today"
    if days < 7:
        return f"{days}d"
    if days < 28:
        weeks = days // 7
        return f"{weeks}w"
    months = days // 30
    return f"{months}mo" if months >= 1 else "4w"


templates.env.filters["listing_age"] = listing_age_filter


def get_storage(request: Request) -> PropertyStorage:
    """Dependency: get the PropertyStorage from app state."""
    return cast(PropertyStorage, request.app.state.storage)


def get_search_areas(request: Request) -> list[str]:
    """Dependency: get uppercased search areas from app settings."""
    settings = request.app.state.settings
    return [a.upper() for a in settings.get_search_areas()]


def get_data_dir(request: Request) -> str:
    """Dependency: get the data directory from app settings."""
    return cast(str, request.app.state.settings.data_dir)


StorageDep = Annotated[PropertyStorage, Depends(get_storage)]
SearchAreasDep = Annotated[list[str], Depends(get_search_areas)]
DataDirDep = Annotated[str, Depends(get_data_dir)]


def _validate_filters(
    *,
    min_price: str | None,
    max_price: str | None,
    bedrooms: str | None,
    min_rating: str | None,
    area: str | None,
    property_type: str | None,
    outdoor_space: str | None,
    natural_light: str | None,
    pets: str | None,
    value_rating: str | None,
    hob_type: str | None,
    floor_level: str | None,
    building_construction: str | None,
    office_separation: str | None,
    hosting_layout: str | None,
    hosting_noise_risk: str | None,
    broadband_type: str | None,
    tag: list[str],
) -> dict[str, Any]:
    """Validate and parse all filter params. Returns dict of validated values."""
    min_price_val = _parse_optional_int(min_price)
    max_price_val = _parse_optional_int(max_price)
    bedrooms_val = _parse_optional_int(bedrooms)
    min_rating_val = _parse_optional_int(min_rating)
    area_val = area.strip() if area else None
    if area_val == "":
        area_val = None

    if min_rating_val is not None:
        min_rating_val = max(1, min(5, min_rating_val))
    if bedrooms_val is not None:
        bedrooms_val = max(0, min(10, bedrooms_val))

    property_type_val = property_type.strip() if property_type else None
    if property_type_val and property_type_val not in VALID_PROPERTY_TYPES:
        property_type_val = None
    outdoor_space_val = outdoor_space.strip().lower() if outdoor_space else None
    if outdoor_space_val and outdoor_space_val not in ("yes", "no"):
        outdoor_space_val = None
    natural_light_val = natural_light.strip().lower() if natural_light else None
    if natural_light_val and natural_light_val not in VALID_NATURAL_LIGHT:
        natural_light_val = None
    pets_val = pets.strip().lower() if pets else None
    if pets_val and pets_val != "yes":
        pets_val = None
    value_rating_val = value_rating.strip().lower() if value_rating else None
    if value_rating_val and value_rating_val not in VALID_VALUE_RATINGS:
        value_rating_val = None
    hob_type_val = hob_type.strip().lower() if hob_type else None
    if hob_type_val and hob_type_val not in VALID_HOB_TYPES:
        hob_type_val = None
    floor_level_val = floor_level.strip().lower() if floor_level else None
    if floor_level_val and floor_level_val not in VALID_FLOOR_LEVELS:
        floor_level_val = None
    building_construction_val = (
        building_construction.strip().lower() if building_construction else None
    )
    if building_construction_val and building_construction_val not in VALID_BUILDING_CONSTRUCTION:
        building_construction_val = None
    office_separation_val = office_separation.strip().lower() if office_separation else None
    if office_separation_val and office_separation_val not in VALID_OFFICE_SEPARATION:
        office_separation_val = None
    hosting_layout_val = hosting_layout.strip().lower() if hosting_layout else None
    if hosting_layout_val and hosting_layout_val not in VALID_HOSTING_LAYOUT:
        hosting_layout_val = None
    hosting_noise_risk_val = hosting_noise_risk.strip().lower() if hosting_noise_risk else None
    if hosting_noise_risk_val and hosting_noise_risk_val not in VALID_HOSTING_NOISE_RISK:
        hosting_noise_risk_val = None
    broadband_type_val = broadband_type.strip().lower() if broadband_type else None
    if broadband_type_val and broadband_type_val not in VALID_BROADBAND_TYPE:
        broadband_type_val = None

    valid_tags = {v.value for v in PropertyHighlight} | {v.value for v in PropertyLowlight}
    tags_val = [t for t in tag if t in valid_tags]

    return {
        "min_price": min_price_val,
        "max_price": max_price_val,
        "bedrooms": bedrooms_val,
        "min_rating": min_rating_val,
        "area": area_val,
        "property_type": property_type_val,
        "outdoor_space": outdoor_space_val,
        "natural_light": natural_light_val,
        "pets": pets_val,
        "value_rating": value_rating_val,
        "hob_type": hob_type_val,
        "floor_level": floor_level_val,
        "building_construction": building_construction_val,
        "office_separation": office_separation_val,
        "hosting_layout": hosting_layout_val,
        "hosting_noise_risk": hosting_noise_risk_val,
        "broadband_type": broadband_type_val,
        "tags": tags_val,
    }


@router.get("/count")
async def filter_count(
    storage: StorageDep,
    min_price: str | None = None,
    max_price: str | None = None,
    bedrooms: str | None = None,
    min_rating: str | None = None,
    area: str | None = None,
    property_type: str | None = None,
    outdoor_space: str | None = None,
    natural_light: str | None = None,
    pets: str | None = None,
    value_rating: str | None = None,
    hob_type: str | None = None,
    floor_level: str | None = None,
    building_construction: str | None = None,
    office_separation: str | None = None,
    hosting_layout: str | None = None,
    hosting_noise_risk: str | None = None,
    broadband_type: str | None = None,
    tag: list[str] = Query(default=[]),
) -> Response:
    """Lightweight count endpoint for live filter preview in modal."""
    f = _validate_filters(
        min_price=min_price,
        max_price=max_price,
        bedrooms=bedrooms,
        min_rating=min_rating,
        area=area,
        property_type=property_type,
        outdoor_space=outdoor_space,
        natural_light=natural_light,
        pets=pets,
        value_rating=value_rating,
        hob_type=hob_type,
        floor_level=floor_level,
        building_construction=building_construction,
        office_separation=office_separation,
        hosting_layout=hosting_layout,
        hosting_noise_risk=hosting_noise_risk,
        broadband_type=broadband_type,
        tag=tag,
    )
    total = await storage.get_filter_count(**f)
    return Response(str(total), media_type="text/plain")


@router.get("/health")
async def health_check(request: Request) -> JSONResponse:
    """Health check endpoint for Fly.io with pipeline status."""
    storage: PropertyStorage = request.app.state.storage
    last_run = await storage.get_last_pipeline_run()
    pipeline_lock = getattr(request.app.state, "pipeline_lock", None)
    return JSONResponse(
        {
            "status": "ok",
            "pipeline_running": pipeline_lock.locked() if pipeline_lock else False,
            "last_run_at": last_run["completed_at"] if last_run else None,
            "last_run_status": last_run["status"] if last_run else None,
            "last_run_notified": last_run["notified_count"] if last_run else None,
        }
    )


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    storage: StorageDep,
    search_areas: SearchAreasDep,
    sort: str = "newest",
    min_price: str | None = None,
    max_price: str | None = None,
    bedrooms: str | None = None,
    min_rating: str | None = None,
    area: str | None = None,
    page: str | None = None,
    property_type: str | None = None,
    outdoor_space: str | None = None,
    natural_light: str | None = None,
    pets: str | None = None,
    value_rating: str | None = None,
    hob_type: str | None = None,
    floor_level: str | None = None,
    building_construction: str | None = None,
    office_separation: str | None = None,
    hosting_layout: str | None = None,
    hosting_noise_risk: str | None = None,
    broadband_type: str | None = None,
    tag: list[str] = Query(default=[]),
) -> HTMLResponse:
    """Dashboard page with property card grid."""
    f = _validate_filters(
        min_price=min_price,
        max_price=max_price,
        bedrooms=bedrooms,
        min_rating=min_rating,
        area=area,
        property_type=property_type,
        outdoor_space=outdoor_space,
        natural_light=natural_light,
        pets=pets,
        value_rating=value_rating,
        hob_type=hob_type,
        floor_level=floor_level,
        building_construction=building_construction,
        office_separation=office_separation,
        hosting_layout=hosting_layout,
        hosting_noise_risk=hosting_noise_risk,
        broadband_type=broadband_type,
        tag=tag,
    )

    min_price_val = f["min_price"]
    max_price_val = f["max_price"]
    bedrooms_val = f["bedrooms"]
    min_rating_val = f["min_rating"]
    area_val = f["area"]
    property_type_val = f["property_type"]
    outdoor_space_val = f["outdoor_space"]
    natural_light_val = f["natural_light"]
    pets_val = f["pets"]
    value_rating_val = f["value_rating"]
    hob_type_val = f["hob_type"]
    floor_level_val = f["floor_level"]
    building_construction_val = f["building_construction"]
    office_separation_val = f["office_separation"]
    hosting_layout_val = f["hosting_layout"]
    hosting_noise_risk_val = f["hosting_noise_risk"]
    broadband_type_val = f["broadband_type"]
    tags_val = f["tags"]

    page_val = _parse_optional_int(page) or 1
    page_val = max(1, page_val)
    sort = sort if sort in VALID_SORT_OPTIONS else "newest"

    per_page = 24

    try:
        properties, total = await storage.get_properties_paginated(
            sort=sort,
            min_price=min_price_val,
            max_price=max_price_val,
            bedrooms=bedrooms_val,
            min_rating=min_rating_val,
            area=area_val,
            page=page_val,
            per_page=per_page,
            property_type=property_type_val,
            outdoor_space=outdoor_space_val,
            natural_light=natural_light_val,
            pets=pets_val,
            value_rating=value_rating_val,
            hob_type=hob_type_val,
            floor_level=floor_level_val,
            building_construction=building_construction_val,
            office_separation=office_separation_val,
            hosting_layout=hosting_layout_val,
            hosting_noise_risk=hosting_noise_risk_val,
            broadband_type=broadband_type_val,
            tags=tags_val,
        )
    except Exception:
        logger.error("dashboard_query_failed", exc_info=True)
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "message": "Failed to load properties. Please try again."},
            status_code=500,
        )

    total_pages = math.ceil(total / per_page) if total > 0 else 1
    page_val = min(page_val, total_pages)

    # Build properties JSON for map view — all matching properties with coords,
    # not just the current page.
    try:
        map_markers = await storage.get_map_markers(**f)
    except Exception:
        logger.error("map_markers_query_failed", exc_info=True)
        map_markers = []
    properties_json = json.dumps(map_markers)

    # Build active filter descriptors for chips
    active_filters: list[dict[str, str]] = []
    if bedrooms_val is not None:
        bed_label = "Studio" if bedrooms_val == 0 else f"{bedrooms_val} bed"
        active_filters.append({"key": "bedrooms", "label": bed_label})
    if min_price_val is not None:
        active_filters.append({"key": "min_price", "label": f"Min £{min_price_val:,}"})
    if max_price_val is not None:
        active_filters.append({"key": "max_price", "label": f"Max £{max_price_val:,}"})
    if min_rating_val is not None:
        active_filters.append({"key": "min_rating", "label": f"{min_rating_val}+ stars"})
    if area_val:
        active_filters.append({"key": "area", "label": area_val})
    if property_type_val:
        pt_label = property_type_val.replace("_", " ").title()
        active_filters.append({"key": "property_type", "label": pt_label})
    if outdoor_space_val:
        active_filters.append({"key": "outdoor_space", "label": f"Outdoor: {outdoor_space_val}"})
    if natural_light_val:
        active_filters.append(
            {"key": "natural_light", "label": f"{natural_light_val.title()} light"}
        )
    if pets_val:
        active_filters.append({"key": "pets", "label": "Pets allowed"})
    if value_rating_val:
        active_filters.append({"key": "value_rating", "label": f"{value_rating_val.title()} value"})
    if hob_type_val:
        active_filters.append({"key": "hob_type", "label": f"{hob_type_val.title()} hob"})
    if floor_level_val:
        active_filters.append({"key": "floor_level", "label": f"{floor_level_val.title()} floor"})
    if building_construction_val:
        bc_label = building_construction_val.replace("_", " ").title()
        active_filters.append({"key": "building_construction", "label": bc_label})
    if office_separation_val:
        os_label = office_separation_val.replace("_", " ").title()
        active_filters.append({"key": "office_separation", "label": os_label})
    if hosting_layout_val:
        active_filters.append(
            {"key": "hosting_layout", "label": f"{hosting_layout_val.title()} hosting"}
        )
    if hosting_noise_risk_val:
        active_filters.append(
            {"key": "hosting_noise_risk", "label": f"{hosting_noise_risk_val.title()} noise risk"}
        )
    if broadband_type_val:
        active_filters.append(
            {"key": "broadband_type", "label": f"{broadband_type_val.upper()} broadband"}
        )

    for t in tags_val:
        active_filters.append({"key": "tag", "label": t, "value": t})

    any_quality_filter_active = any(
        [
            property_type_val,
            outdoor_space_val,
            natural_light_val,
            pets_val,
            value_rating_val,
            hob_type_val,
            floor_level_val,
            building_construction_val,
            office_separation_val,
            hosting_layout_val,
            hosting_noise_risk_val,
            broadband_type_val,
            tags_val,
        ]
    )

    # Count of active secondary (modal) filters for badge display
    secondary_filter_count = sum(
        1
        for v in [
            property_type_val,
            outdoor_space_val,
            natural_light_val,
            pets_val,
            value_rating_val,
            hob_type_val,
            floor_level_val,
            building_construction_val,
            office_separation_val,
            hosting_layout_val,
            hosting_noise_risk_val,
            broadband_type_val,
        ]
        if v is not None
    ) + len(tags_val)

    highlight_values = {h.value for h in PropertyHighlight}

    context: dict[str, Any] = {
        "request": request,
        "properties": properties,
        "total": total,
        "page": page_val,
        "total_pages": total_pages,
        "sort": sort,
        "min_price": min_price_val,
        "max_price": max_price_val,
        "bedrooms": bedrooms_val,
        "min_rating": min_rating_val,
        "area": area_val,
        "source_names": SOURCE_NAMES,
        "source_badges": SOURCE_BADGES,
        "properties_json": properties_json,
        "search_areas": search_areas,
        "active_filters": active_filters,
        "property_type": property_type_val,
        "outdoor_space": outdoor_space_val,
        "natural_light": natural_light_val,
        "pets": pets_val,
        "value_rating": value_rating_val,
        "hob_type": hob_type_val,
        "floor_level": floor_level_val,
        "building_construction": building_construction_val,
        "office_separation": office_separation_val,
        "hosting_layout": hosting_layout_val,
        "hosting_noise_risk": hosting_noise_risk_val,
        "broadband_type": broadband_type_val,
        "any_quality_filter_active": any_quality_filter_active,
        "tags": tags_val,
        "secondary_filter_count": secondary_filter_count,
        "tag_categories": TAG_CATEGORIES,
        "highlight_values": highlight_values,
    }

    # HTMX partial rendering
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("_results.html", context)

    return templates.TemplateResponse("dashboard.html", context)


_IMAGE_MEDIA_TYPES: Final = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


@router.get("/images/{unique_id}/{filename}")
async def serve_cached_image(unique_id: str, filename: str, data_dir: DataDirDep) -> Response:
    """Serve a cached property image from disk.

    Returns the image with immutable cache headers (images never change).
    """
    # Validate parameters — no directory traversal
    if ".." in unique_id or "/" in unique_id or "\\" in unique_id:
        return JSONResponse({"error": "invalid unique_id"}, status_code=400)
    if ".." in filename or "/" in filename or "\\" in filename:
        return JSONResponse({"error": "invalid filename"}, status_code=400)

    image_path = get_cache_dir(data_dir, unique_id) / filename

    if not image_path.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)

    # Determine media type from extension
    ext = image_path.suffix.lower()
    media_type = _IMAGE_MEDIA_TYPES.get(ext, "image/jpeg")

    return FileResponse(
        image_path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/property/{unique_id}", response_class=HTMLResponse)
async def property_detail(
    request: Request,
    unique_id: str,
    storage: StorageDep,
    data_dir: DataDirDep,
) -> HTMLResponse:
    """Property detail page."""
    try:
        prop = await storage.get_property_detail(unique_id)
    except Exception:
        logger.error("detail_query_failed", unique_id=unique_id, exc_info=True)
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "message": "Failed to load property details. Please try again."},
            status_code=500,
        )

    if prop is None:
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "message": "Property not found."},
            status_code=404,
        )

    # Hide properties with no images — not worth viewing without photos
    has_images = prop.get("image_url") or prop.get("gallery_images") or prop.get("floorplan_images")
    if not has_images:
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "message": "Property not found."},
            status_code=404,
        )

    # Extract outcode for area context
    outcode = extract_outcode(prop.get("postcode"))
    area_context: dict[str, Any] = {}
    if outcode:
        area_context["description"] = get_area_overview(outcode)
        all_micro = get_micro_areas(outcode)
        if all_micro:
            # Try ward-based matching first (reliable), fall back to text matching
            ward = str(prop["ward"]) if prop.get("ward") else None
            matched_name = get_micro_area_for_ward(ward, outcode) if ward else None
            if not matched_name:
                matched_name = match_micro_area(prop.get("address", ""), outcode)
            if matched_name and matched_name in all_micro:
                area_context["matched_micro_area"] = {
                    "name": matched_name,
                    "data": all_micro[matched_name],
                }
            area_context["micro_area_count"] = len(all_micro)
        area_context["benchmarks"] = RENTAL_BENCHMARKS.get(outcode)
        borough = OUTCODE_BOROUGH.get(outcode)
        if borough:
            area_context["borough"] = borough
            area_context["council_tax"] = COUNCIL_TAX_MONTHLY.get(borough)
            area_context["rent_trend"] = RENT_TRENDS.get(borough)
        area_context["crime"] = CRIME_RATES.get(outcode)
        area_context["hosting_tolerance"] = HOSTING_TOLERANCE.get(outcode)
        area_context["creative_scene"] = CREATIVE_SCENE.get(outcode)

    # Look up acoustic profile from quality analysis property_type
    qa = prop.get("quality_analysis")
    if qa is not None:
        le = getattr(qa, "listing_extraction", None)
        if le:
            prop_type = getattr(le, "property_type", None)
            if prop_type:
                acoustic = ACOUSTIC_PROFILES.get(str(prop_type))
                if acoustic:
                    area_context["acoustic_profile"] = acoustic

        borough = area_context.get("borough")
        if borough:
            enforcement = NOISE_ENFORCEMENT.get(borough)
            if enforcement:
                area_context["noise_enforcement"] = enforcement

    # Build URL mapping: original CDN URL -> local /images/ URL for cached images
    image_url_map: dict[str, str] = {}
    safe_id = safe_dir_name(unique_id)
    all_images: list[PropertyImage] = [
        *prop.get("gallery_images", []),
        *prop.get("floorplan_images", []),
    ]
    for idx, img in enumerate(all_images):
        img_url = str(img.url)
        fname = url_to_filename(img_url, img.image_type, idx)
        cached = get_cache_dir(data_dir, unique_id) / fname
        if cached.is_file():
            image_url_map[img_url] = f"/images/{safe_id}/{fname}"

    # Get the longest description
    descriptions: dict[str, str] = prop.get("descriptions_dict", {})
    best_description = ""
    for desc in descriptions.values():
        if desc and len(desc) > len(best_description):
            best_description = desc
    # Fall back to canonical description
    if not best_description and prop.get("description"):
        best_description = prop.get("description") or ""

    # Compute Marcel Fit Score + breakdown for detail page
    fit_score = None
    fit_breakdown = None
    qa = prop.get("quality_analysis")
    if qa is not None:
        analysis_dict = qa.model_dump()
        if outcode:
            ht = HOSTING_TOLERANCE.get(outcode)
            if ht:
                analysis_dict["_area_hosting_tolerance"] = ht.get("rating")
        bedrooms = prop.get("bedrooms", 0) or 0
        fit_score = compute_fit_score(analysis_dict, bedrooms)
        fit_breakdown = compute_fit_breakdown(analysis_dict, bedrooms)

    # Compute True Monthly Cost breakdown
    cost_breakdown = None
    if qa is not None:
        le = getattr(qa, "listing_extraction", None)
        if le:
            from home_finder.utils.cost_calculator import estimate_true_monthly_cost

            epc_rating = getattr(le, "epc_rating", None)
            prop_type = getattr(le, "property_type", None)
            service_charge_pcm = getattr(le, "service_charge_pcm", None)
            bills_included_raw = getattr(le, "bills_included", "unknown")
            bills_included = bills_included_raw == "yes"
            broadband_type = getattr(le, "broadband_type", None)
            council_tax_band = getattr(le, "council_tax_band", None)
            borough = area_context.get("borough")

            cost_breakdown = estimate_true_monthly_cost(
                rent_pcm=prop.get("price_pcm", 0),
                borough=borough,
                council_tax_band=council_tax_band,
                epc_rating=epc_rating,
                bedrooms=prop.get("bedrooms", 1) or 1,
                broadband_type=broadband_type,
                property_type=prop_type,
                service_charge_pcm=service_charge_pcm,
                bills_included=bills_included,
            )

    return templates.TemplateResponse(
        "detail.html",
        {
            "request": request,
            "prop": prop,
            "outcode": outcode,
            "area_context": area_context,
            "best_description": best_description,
            "source_names": SOURCE_NAMES,
            "source_badges": SOURCE_BADGES,
            "image_url_map": image_url_map,
            "fit_score": fit_score,
            "fit_breakdown": fit_breakdown,
            "cost_breakdown": cost_breakdown,
        },
    )


@router.get("/area/{outcode}", response_class=HTMLResponse)
async def area_detail(
    request: Request,
    outcode: str,
    highlight: str | None = None,
) -> HTMLResponse:
    """Area exploration page showing all micro-areas and reference data."""
    outcode = outcode.upper()

    if outcode not in AREA_CONTEXT:
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "message": f"No area data for {outcode}."},
            status_code=404,
        )

    area_context: dict[str, Any] = {
        "description": get_area_overview(outcode),
        "micro_areas": get_micro_areas(outcode),
        "benchmarks": RENTAL_BENCHMARKS.get(outcode),
    }

    borough = OUTCODE_BOROUGH.get(outcode)
    if borough:
        area_context["borough"] = borough
        area_context["council_tax"] = COUNCIL_TAX_MONTHLY.get(borough)
        area_context["rent_trend"] = RENT_TRENDS.get(borough)
        enforcement = NOISE_ENFORCEMENT.get(borough)
        if enforcement:
            area_context["noise_enforcement"] = enforcement

    area_context["crime"] = CRIME_RATES.get(outcode)

    return templates.TemplateResponse(
        "area.html",
        {
            "request": request,
            "outcode": outcode,
            "area_context": area_context,
            "highlight": highlight,
        },
    )


@router.post("/property/{unique_id}/reanalyze")
async def request_reanalysis(unique_id: str, storage: StorageDep) -> JSONResponse:
    """Flag a property for quality re-analysis.

    The actual analysis runs on next `--reanalyze` CLI invocation.
    """
    count = await storage.request_reanalysis([unique_id])
    if count == 0:
        return JSONResponse(
            {"error": "not found or no existing analysis"},
            status_code=404,
        )
    return JSONResponse({"status": "queued"})
