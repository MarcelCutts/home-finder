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
    AREA_CONTEXT,
    COUNCIL_TAX_MONTHLY,
    CRIME_RATES,
    OUTCODE_BOROUGH,
    RENT_TRENDS,
    RENTAL_BENCHMARKS,
)
from home_finder.db import PropertyStorage
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
    "victorian", "edwardian", "georgian", "new_build", "purpose_built",
    "warehouse", "ex_council", "period_conversion",
}
VALID_NATURAL_LIGHT: Final = {"excellent", "good", "fair", "poor"}
VALID_HOB_TYPES: Final = {"gas", "induction", "electric"}
VALID_VALUE_RATINGS: Final = {"excellent", "good", "fair", "poor"}
VALID_FLOOR_LEVELS: Final = {"basement", "ground", "lower", "upper", "top"}
VALID_BUILDING_CONSTRUCTION: Final = {"solid_brick", "concrete", "timber_frame", "mixed"}


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


@router.get("/health")
async def health_check() -> JSONResponse:
    """Health check endpoint for Fly.io."""
    return JSONResponse({"status": "ok"})


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
    tag: list[str] = Query(default=[]),
) -> HTMLResponse:
    """Dashboard page with property card grid."""
    # Parse optional int params (empty strings → None)
    min_price_val = _parse_optional_int(min_price)
    max_price_val = _parse_optional_int(max_price)
    bedrooms_val = _parse_optional_int(bedrooms)
    min_rating_val = _parse_optional_int(min_rating)
    page_val = _parse_optional_int(page) or 1
    area_val = area.strip() if area else None
    if area_val == "":
        area_val = None

    # Validate and clamp inputs
    page_val = max(1, page_val)
    sort = sort if sort in VALID_SORT_OPTIONS else "newest"
    if min_rating_val is not None:
        min_rating_val = max(1, min(5, min_rating_val))
    if bedrooms_val is not None:
        bedrooms_val = max(0, min(10, bedrooms_val))

    # Validate quality filter params against whitelists (invalid → None)
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

    # Validate tag params against known highlight/lowlight values
    valid_tags = {v.value for v in PropertyHighlight} | {v.value for v in PropertyLowlight}
    tags_val = [t for t in tag if t in valid_tags]

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

    # Build properties JSON for map view
    properties_json = json.dumps(
        [
            {
                "id": p["unique_id"],
                "lat": p["latitude"],
                "lon": p["longitude"],
                "price": p["price_pcm"],
                "bedrooms": p["bedrooms"],
                "rating": p.get("quality_rating"),
                "title": p["title"],
                "url": f"/property/{p['unique_id']}",
                "image_url": p.get("image_url"),
                "postcode": p.get("postcode"),
                "commute_minutes": p.get("commute_minutes"),
                "value_rating": p.get("value_rating"),
                "one_line": p.get("one_line"),
                "fit_score": p.get("fit_score"),
            }
            for p in properties
            if p.get("latitude") and p.get("longitude")
        ]
    )

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
        active_filters.append(
            {"key": "outdoor_space", "label": f"Outdoor: {outdoor_space_val}"}
        )
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

    for t in tags_val:
        active_filters.append({"key": "tag", "label": t, "value": t})

    any_quality_filter_active = any([
        property_type_val, outdoor_space_val, natural_light_val,
        pets_val, value_rating_val, hob_type_val, floor_level_val,
        building_construction_val, tags_val,
    ])

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
        "any_quality_filter_active": any_quality_filter_active,
        "tags": tags_val,
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
    # Validate filename — no directory traversal
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

    # Extract outcode for area context
    outcode = extract_outcode(prop.get("postcode"))
    area_context: dict[str, Any] = {}
    if outcode:
        area_context["description"] = AREA_CONTEXT.get(outcode)
        area_context["benchmarks"] = RENTAL_BENCHMARKS.get(outcode)
        borough = OUTCODE_BOROUGH.get(outcode)
        if borough:
            area_context["borough"] = borough
            area_context["council_tax"] = COUNCIL_TAX_MONTHLY.get(borough)
            area_context["rent_trend"] = RENT_TRENDS.get(borough)
        area_context["crime"] = CRIME_RATES.get(outcode)

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
        },
    )
