"""Web dashboard routes."""

import json
import math
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from home_finder.db import PropertyStorage
from home_finder.filters.quality import (
    AREA_CONTEXT,
    COUNCIL_TAX_MONTHLY,
    CRIME_RATES,
    OUTCODE_BOROUGH,
    RENT_TRENDS,
    RENTAL_BENCHMARKS,
)
from home_finder.logging import get_logger
from home_finder.models import SOURCE_NAMES

logger = get_logger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

VALID_SORT_OPTIONS = {"newest", "price_asc", "price_desc", "rating_desc"}


def _get_storage(request: Request) -> PropertyStorage:
    return request.app.state.storage  # type: ignore[no-any-return]


def _get_search_areas(request: Request) -> list[str]:
    settings = request.app.state.settings
    return [a.upper() for a in settings.get_search_areas()]


def _extract_outcode(postcode: str | None) -> str | None:
    if not postcode:
        return None
    parts = postcode.strip().upper().split()
    return parts[0] if parts else None


@router.get("/health")
async def health_check() -> JSONResponse:
    """Health check endpoint for Fly.io."""
    return JSONResponse({"status": "ok"})


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    sort: str = "newest",
    min_price: int | None = None,
    max_price: int | None = None,
    bedrooms: int | None = None,
    min_rating: int | None = None,
    area: str | None = None,
    page: int = 1,
) -> HTMLResponse:
    """Dashboard page with property card grid."""
    # Validate and clamp inputs
    page = max(1, page)
    sort = sort if sort in VALID_SORT_OPTIONS else "newest"
    if min_rating is not None:
        min_rating = max(1, min(5, min_rating))
    if bedrooms is not None:
        bedrooms = max(0, min(10, bedrooms))

    storage = _get_storage(request)
    per_page = 24

    try:
        properties, total = await storage.get_properties_paginated(
            sort=sort,
            min_price=min_price,
            max_price=max_price,
            bedrooms=bedrooms,
            min_rating=min_rating,
            area=area,
            page=page,
            per_page=per_page,
        )
    except Exception:
        logger.error("dashboard_query_failed", exc_info=True)
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "message": "Failed to load properties. Please try again."},
            status_code=500,
        )

    total_pages = math.ceil(total / per_page) if total > 0 else 1
    page = min(page, total_pages)

    # Build properties JSON for map view
    properties_json = json.dumps(
        [
            {
                "lat": p["latitude"],
                "lon": p["longitude"],
                "price": p["price_pcm"],
                "bedrooms": p["bedrooms"],
                "rating": p.get("quality_rating"),
                "title": p["title"],
                "url": f"/property/{p['unique_id']}",
            }
            for p in properties
            if p.get("latitude") and p.get("longitude")
        ]
    )

    search_areas = _get_search_areas(request)

    context: dict[str, Any] = {
        "request": request,
        "properties": properties,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "sort": sort,
        "min_price": min_price,
        "max_price": max_price,
        "bedrooms": bedrooms,
        "min_rating": min_rating,
        "area": area,
        "source_names": SOURCE_NAMES,
        "properties_json": properties_json,
        "search_areas": search_areas,
    }

    # HTMX partial rendering
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("_results.html", context)

    return templates.TemplateResponse("dashboard.html", context)


@router.get("/property/{unique_id}", response_class=HTMLResponse)
async def property_detail(request: Request, unique_id: str) -> HTMLResponse:
    """Property detail page."""
    storage = _get_storage(request)

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
    outcode = _extract_outcode(prop.get("postcode"))
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

    # Get the longest description
    descriptions = prop.get("descriptions_dict", {})
    best_description = ""
    for desc in descriptions.values():
        if desc and len(desc) > len(best_description):
            best_description = desc
    # Fall back to canonical description
    if not best_description and prop.get("description"):
        best_description = prop["description"]

    return templates.TemplateResponse(
        "detail.html",
        {
            "request": request,
            "prop": prop,
            "outcode": outcode,
            "area_context": area_context,
            "best_description": best_description,
            "source_names": SOURCE_NAMES,
        },
    )
