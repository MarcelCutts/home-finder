"""Web dashboard routes."""

import asyncio
import functools
import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from html import escape as html_escape
from pathlib import Path
from typing import Annotated, Any, Final, Literal, cast
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from home_finder.analysis.image_review import SUPPLIED_PHOTO_LOWLIGHT_LABELS
from home_finder.config import Settings
from home_finder.data.area_context import (
    ACOUSTIC_PROFILES,
    AREA_CONTEXT,
    CREATIVE_SCENE,
    HOSTING_TOLERANCE,
    NOISE_ENFORCEMENT,
    build_area_context,
    get_micro_area_for_ward,
    get_micro_areas,
    match_micro_area,
)
from home_finder.data.rental_guidance import GUIDANCE
from home_finder.db import PropertyStorage
from home_finder.filters.fit_score import (
    FIT_TIERS,
    compute_fit_score_and_breakdown,
    compute_lifestyle_icons,
    fit_color_class,
    fit_tier_class,
    fit_tier_label,
)
from home_finder.logging import get_logger
from home_finder.models import (
    SOURCE_BADGES,
    SOURCE_NAMES,
    USER_STATUS_META,
    PropertyHighlight,
    PropertyImage,
    PropertyLowlight,
    UserStatus,
)
from home_finder.models.delivery import UnfinishedDeliveryState
from home_finder.models.quality import lowlight_display_label
from home_finder.utils.address import extract_outcode
from home_finder.utils.cost_calculator import build_monthly_budget, move_in_cash
from home_finder.utils.image_cache import (
    find_cached_file,
    find_thumbnail,
    get_cache_dir,
    safe_dir_name,
)
from home_finder.utils.image_hash import hash_from_disk, hashes_match
from home_finder.utils.negotiation import generate_negotiation_brief
from home_finder.web.annotate import annotate_listing
from home_finder.web.app import _compute_static_version
from home_finder.web.brief import brief_lead, check_brief
from home_finder.web.deliveries import DELIVERY_STATES, delivery_view
from home_finder.web.filters import (
    ADDED_OPTIONS,
    DEFAULT_SORT,
    VALID_SORT_OPTIONS,
    FilterDep,
    _parse_optional_int,
)
from home_finder.web.verdict import build_verdict
from home_finder.web.working import fit_working

logger = get_logger(__name__)

router = APIRouter()


def _map_context(request: Request) -> dict[str, str]:
    settings: Settings = request.app.state.settings
    return {"map_api_key": settings.carto_basemap_api_key.get_secret_value()}


templates = Jinja2Templates(
    directory=Path(__file__).parent / "templates", context_processors=[_map_context]
)

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
        *SUPPLIED_PHOTO_LOWLIGHT_LABELS.values(),
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


def bed_label_filter(bedrooms: int | None) -> str:
    """Name a bedroom count; a studio is not "0 bed"."""
    return "Studio" if bedrooms == 0 else f"{bedrooms} bed"


def short_date_filter(iso_str: str | None) -> str:
    """Format an ISO timestamp as a date such as "13 Feb 2026"."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
    except ValueError:
        return ""
    return f"{dt.day} {dt:%b %Y}"


def gbp_filter(amount: int | float | Decimal) -> str:
    """Format pounds, showing pence only when there are some."""
    value = Decimal(str(amount))
    if value == value.to_integral_value():
        return f"£{int(value):,}"
    return f"£{value:,.2f}"


templates.env.filters["listing_age"] = listing_age_filter
templates.env.filters["bed_label"] = bed_label_filter
templates.env.filters["gbp"] = gbp_filter
templates.env.filters["short_date"] = short_date_filter
templates.env.filters["lowlight_label"] = lowlight_display_label
templates.env.filters["json_script_safe"] = lambda s: s.replace("</", r"<\/")

templates.env.globals["static_version"] = _compute_static_version()
templates.env.globals["fit_tier_label"] = fit_tier_label
templates.env.globals["fit_tier_class"] = fit_tier_class
templates.env.globals["fit_color_class"] = fit_color_class
templates.env.globals["FIT_TIERS"] = FIT_TIERS


def days_since_filter(iso_str: str | None) -> int:
    """Return integer days since ISO datetime string, or 0 if invalid."""
    if not iso_str:
        return 0
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0, (datetime.now(UTC) - dt).days)
    except (ValueError, TypeError):
        return 0


templates.env.filters["days_since"] = days_since_filter


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


def get_base_url(request: Request) -> str:
    """Dependency: get the web base URL from app settings (empty string if unset)."""
    return cast(str, request.app.state.settings.web_base_url).rstrip("/")


StorageDep = Annotated[PropertyStorage, Depends(get_storage)]
SearchAreasDep = Annotated[list[str], Depends(get_search_areas)]
DataDirDep = Annotated[str, Depends(get_data_dir)]
BaseUrlDep = Annotated[str, Depends(get_base_url)]


def _brief_limits(settings: Settings) -> dict[str, Any]:
    """The brief's hard limits and must-have dimensions, as check_brief wants them."""
    return {
        "min_bedrooms": settings.min_bedrooms,
        "max_bedrooms": settings.max_bedrooms,
        "max_price": settings.max_price,
        "max_commute_minutes": settings.max_commute_minutes,
        "must_haves": settings.brief_must_haves,
    }


def _enrich_fit_scores(properties: list[Any], settings: Settings | None = None) -> None:
    """Compute fit_score, fit_breakdown, lifestyle_icons and the brief lead for each property.

    Mutates dicts in place. Reads analysis_json from each property dict.
    Sets None defaults for properties without analysis data. The brief lead needs
    the settings; without them it is left out.
    """
    for prop in properties:
        prop["lead"] = None
        prop["brief_check"] = None
        analysis_json = prop.get("analysis_json")
        if not prop.get("is_assessed") or not analysis_json:
            prop["fit_score"] = None
            prop["fit_breakdown"] = None
            prop["lifestyle_icons"] = None
            continue
        try:
            analysis = json.loads(analysis_json)
        except (json.JSONDecodeError, TypeError):
            prop.setdefault("fit_score", None)
            prop.setdefault("fit_breakdown", None)
            prop.setdefault("lifestyle_icons", None)
            continue
        postcode = prop.get("postcode") or ""
        bedrooms = prop.get("bedrooms", 0) or 0
        fit_score, fit_breakdown = compute_fit_score_and_breakdown(
            analysis, bedrooms, postcode=postcode
        )
        prop["fit_score"] = fit_score
        prop["fit_breakdown"] = fit_breakdown
        prop["lifestyle_icons"] = compute_lifestyle_icons(analysis, bedrooms, postcode=postcode)
        if settings is not None:
            check = check_brief(prop, analysis, fit_breakdown, **_brief_limits(settings))
            prop["brief_check"] = check
            prop["lead"] = brief_lead(check, analysis)


@router.get("/count")
async def filter_count(storage: StorageDep, filters: FilterDep) -> Response:
    """Lightweight count endpoint for live filter preview in modal."""
    total = await storage.web.get_filter_count(filters)
    return Response(str(total), media_type="text/plain")


@router.get("/health")
async def health_check() -> JSONResponse:
    """Minimal public liveness endpoint for Fly.io."""
    return JSONResponse({"status": "ok"})


@router.get("/diagnostics")
async def diagnostics(request: Request) -> JSONResponse:
    """Owner-only operational detail, protected by the application access boundary."""
    storage: PropertyStorage = request.app.state.storage
    last_run = await storage.runs.get_last_pipeline_run()
    pipeline_lock = getattr(request.app.state, "pipeline_lock", None)
    return JSONResponse(
        {
            "status": "ok",
            "pipeline_running": pipeline_lock.locked() if pipeline_lock else False,
            "last_run_at": last_run["completed_at"] if last_run else None,
            "last_run_status": last_run["status"] if last_run else None,
            "last_run_notified": last_run["notified_count"] if last_run else None,
            "processing_counts": await storage.web.get_processing_counts(),
            "delivery": await storage.deliveries.report(),
        }
    )


@router.get("/deliveries", response_class=HTMLResponse)
async def delivery_status(
    request: Request,
    storage: StorageDep,
    base_url: BaseUrlDep,
    state: UnfinishedDeliveryState | Literal[""] = "",
) -> HTMLResponse:
    """Owner-only read view; reconciliation remains fenced by the local admin command."""
    report = await storage.deliveries.report(state=state or None)
    return templates.TemplateResponse(
        request,
        "deliveries.html",
        {
            "base_url": base_url,
            "delivery": delivery_view(report),
            "delivery_states": DELIVERY_STATES,
            "selected_state": state,
        },
    )


def _resolve_cached_thumbnail(unique_id: str, image_url: str | None, data_dir: str) -> str | None:
    """Resolve a CDN image URL to a locally cached path, or return None.

    Prefers ``thumb_`` prefixed thumbnails when available, falling back to
    the original gallery file for pre-existing caches without thumbnails.

    The DB stores the scraper thumbnail URL which often differs from the
    enrichment gallery URL (different CDN resolution path), so we fall back
    to the first ``gallery_*`` file in the cache directory.
    """
    safe_id = safe_dir_name(unique_id)

    # Fast path: find cached file by URL hash (index-independent)
    if image_url and image_url.startswith("http"):
        cached = find_cached_file(data_dir, unique_id, image_url, "gallery")
        if cached:
            thumb = find_thumbnail(cached)
            serve = thumb or cached
            return f"/images/{safe_id}/{serve.name}"

    # Fallback: first gallery file on disk, prefer its thumbnail
    cache_dir = get_cache_dir(data_dir, unique_id)
    if cache_dir.is_dir():
        for f in sorted(cache_dir.iterdir()):
            if f.name.startswith("gallery_") and f.is_file():
                thumb = find_thumbnail(f)
                serve = thumb or f
                return f"/images/{safe_id}/{serve.name}"

    return None


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    storage: StorageDep,
    search_areas: SearchAreasDep,
    data_dir: DataDirDep,
    base_url: BaseUrlDep,
    filters: FilterDep,
    sort: str = DEFAULT_SORT,
    page: str | None = None,
) -> HTMLResponse:
    """Dashboard page with property card grid."""
    page_val = _parse_optional_int(page) or 1
    page_val = max(1, page_val)
    sort = sort if sort in VALID_SORT_OPTIONS else DEFAULT_SORT

    per_page = 24

    try:
        properties, total = await storage.web.get_properties_paginated(
            filters, sort=sort, page=page_val, per_page=per_page
        )
        total_pages = max(1, math.ceil(total / per_page))
        if page_val > total_pages:
            page_val = total_pages
            properties, total = await storage.web.get_properties_paginated(
                filters, sort=sort, page=page_val, per_page=per_page
            )
        _enrich_fit_scores(properties, request.app.state.settings)

        # Rewrite external CDN thumbnail URLs to locally cached images.
        # The DB image_url is often a different CDN resolution than the
        # gallery URL used during caching, so we also try matching any
        # gallery_000_* file in the cache directory.
        for prop in properties:
            resolved = _resolve_cached_thumbnail(prop["unique_id"], prop.get("image_url"), data_dir)
            if resolved:
                prop["image_url"] = resolved
    except Exception:
        logger.error("dashboard_query_failed", exc_info=True)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"request": request, "message": "Failed to load properties. Please try again."},
            status_code=500,
        )

    # Build properties JSON for map view — all matching properties with coords,
    # not just the current page.
    try:
        map_markers = await storage.web.get_map_markers(filters)
    except Exception:
        logger.error("map_markers_query_failed", exc_info=True)
        map_markers = []

    # Rewrite external CDN URLs in map markers too
    for marker in map_markers:
        resolved = _resolve_cached_thumbnail(marker["id"], marker.get("image_url"), data_dir)
        if resolved:
            marker["image_url"] = resolved

    properties_json = json.dumps(map_markers)

    highlight_values = {h.value for h in PropertyHighlight}

    def page_url(target_page: int) -> str:
        return "?" + urlencode(
            [("page", str(target_page)), ("sort", sort), *filters.query_params()]
        )

    active_filters = filters.active_filter_chips()

    context: dict[str, Any] = {
        "request": request,
        "properties": properties,
        "total": total,
        "page": page_val,
        "total_pages": total_pages,
        "sort": sort,
        "page_url": page_url,
        "source_names": SOURCE_NAMES,
        "source_badges": SOURCE_BADGES,
        "properties_json": properties_json,
        "search_areas": search_areas,
        "active_filters": active_filters,
        "applied_filters": {
            "values": {**filters.model_dump(), "sort": sort},
            "active_count": len(active_filters),
            "secondary_count": filters.secondary_filter_count,
            "total": total,
        },
        "any_quality_filter_active": filters.quality_fields_active,
        "secondary_filter_count": filters.secondary_filter_count,
        "tag_categories": TAG_CATEGORIES,
        "highlight_values": highlight_values,
        "added_options": ADDED_OPTIONS,
        "user_statuses": list(UserStatus),
        "status_meta": USER_STATUS_META,
        "base_url": base_url,
        **filters.model_dump(),
    }

    # HTMX partial rendering
    if request.headers.get("HX-Request") and not request.headers.get("HX-History-Restore-Request"):
        response = templates.TemplateResponse(request, "_results.html", context)
        response.headers["Vary"] = "HX-Request, HX-History-Restore-Request"
        response.headers["HX-Push-Url"] = "/" + page_url(page_val)
        return response

    response = templates.TemplateResponse(request, "dashboard.html", context)
    response.headers["Vary"] = "HX-Request, HX-History-Restore-Request"
    return response


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
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.get("/property/{unique_id}/card", response_class=HTMLResponse)
async def property_card(
    request: Request,
    unique_id: str,
    storage: StorageDep,
    data_dir: DataDirDep,
) -> HTMLResponse:
    """Return a single property card partial for map marker click."""
    try:
        prop = await storage.web.get_property_card(unique_id)
    except Exception:
        logger.error("card_query_failed", unique_id=unique_id, exc_info=True)
        return HTMLResponse("", status_code=500)

    if not prop:
        return HTMLResponse("", status_code=404)

    _enrich_fit_scores([prop], request.app.state.settings)
    resolved = _resolve_cached_thumbnail(prop["unique_id"], prop.get("image_url"), data_dir)
    if resolved:
        prop["image_url"] = resolved

    return templates.TemplateResponse(
        request,
        "_property_card.html",
        {
            "request": request,
            "prop": prop,
            "source_badges": SOURCE_BADGES,
            "status_meta": USER_STATUS_META,
        },
    )


@functools.lru_cache(maxsize=1024)
def _cached_image_hash(path: str, mtime_ns: int) -> str | None:
    """Hash one version of a cached image file; the mtime keys out stale entries."""
    return hash_from_disk(Path(path))


def _split_floorplans(
    data_dir: str, unique_id: str, floorplans: list[PropertyImage]
) -> tuple[list[PropertyImage], list[PropertyImage]]:
    """Return (primary plans, additional versions), preserving every source image.

    Similarity controls presentation only: even identical perceptual hashes can
    conceal changed dimensions or labels. Keep similar or uncomparable copies
    available for inspection, with source-declared plans presented first.
    """
    hashed: list[tuple[PropertyImage, str | None]] = []
    for img in floorplans:
        cached = find_cached_file(data_dir, unique_id, str(img.url), img.image_type)
        try:
            digest = _cached_image_hash(str(cached), cached.stat().st_mtime_ns) if cached else None
        except OSError:
            # Cache cleanup can remove the file between lookup and stat.
            digest = None
        hashed.append((img, digest))
    # Source-declared and comparable plans go first, so they are the ones kept.
    hashed.sort(key=lambda pair: (not pair[0].is_source_floorplan, pair[1] is None))

    shown: list[PropertyImage] = []
    variants: list[PropertyImage] = []
    seen: list[str] = []
    for img, digest in hashed:
        if digest is not None and any(hashes_match(digest, other) for other in seen):
            variants.append(img)
            continue
        if digest is None and shown and img.source not in {s.source for s in shown}:
            variants.append(img)
            continue
        shown.append(img)
        if digest is not None:
            seen.append(digest)
    return shown, variants


@router.get("/property/{unique_id}", response_class=HTMLResponse)
async def property_detail(
    request: Request,
    unique_id: str,
    storage: StorageDep,
    data_dir: DataDirDep,
    base_url: BaseUrlDep,
) -> HTMLResponse:
    """Property detail page."""
    try:
        prop = await storage.web.get_property_detail(unique_id)
    except Exception:
        logger.error("detail_query_failed", unique_id=unique_id, exc_info=True)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"request": request, "message": "Failed to load property details. Please try again."},
            status_code=500,
        )

    if prop is None:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"request": request, "message": "Property not found."},
            status_code=404,
        )

    # Extract outcode for area context
    outcode = extract_outcode(prop.get("postcode"))
    area_context: dict[str, Any] = {}
    if outcode:
        area_context = build_area_context(outcode)
        all_micro = get_micro_areas(outcode)
        if all_micro:
            # Try ward-based matching first (reliable), fall back to text matching
            ward = str(prop["ward"]) if prop.get("ward") else None
            matched_name = get_micro_area_for_ward(ward, outcode) if ward else None
            if not matched_name and ward:
                logger.debug("unmapped_ward", ward=ward, outcode=outcode, unique_id=unique_id)
            if not matched_name:
                matched_name = match_micro_area(prop.get("address", ""), outcode)
            if matched_name and matched_name in all_micro:
                area_context["matched_micro_area"] = {
                    "name": matched_name,
                    "data": all_micro[matched_name],
                }
            area_context["micro_area_count"] = len(all_micro)
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

    prop["floorplan_images"], floorplan_repeats = await asyncio.to_thread(
        _split_floorplans, data_dir, unique_id, prop.get("floorplan_images", [])
    )

    # Build URL mapping: original CDN URL -> local /images/ URL for cached images
    image_url_map: dict[str, str] = {}
    safe_id = safe_dir_name(unique_id)
    all_images: list[PropertyImage] = [
        *prop.get("gallery_images", []),
        *prop.get("floorplan_images", []),
        *floorplan_repeats,
    ]
    for img in all_images:
        img_url = str(img.url)
        cached = find_cached_file(data_dir, unique_id, img_url, img.image_type)
        if cached is not None:
            image_url_map[img_url] = f"/images/{safe_id}/{cached.name}"

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
        bedrooms = prop.get("bedrooms", 0) or 0
        fit_score, fit_breakdown = compute_fit_score_and_breakdown(
            qa, bedrooms, postcode=prop.get("postcode")
        )

    verdict = build_verdict(qa, fit_breakdown) if qa is not None else None

    # The brief: what was asked for, measured against this listing; its first gap
    # opens the page. The agent's words get notes from the same analysis.
    settings = request.app.state.settings
    brief_check = check_brief(prop, qa, fit_breakdown, **_brief_limits(settings))
    lead = brief_lead(brief_check, qa) if qa is not None else None
    annotated = (
        annotate_listing(best_description, qa, outcode=outcode, price_pcm=prop.get("price_pcm"))
        if best_description and qa
        else None
    )
    working = fit_working(fit_breakdown) if fit_breakdown else None

    # Rebuild from current retained listing evidence and rent, without model work.
    extraction = qa.listing_extraction if qa else None
    cost_breakdown = build_monthly_budget(
        rent_pcm=prop.get("price_pcm", 0),
        evidence=prop.get("listing_evidence", {}),
        earlier_service_charge=extraction.service_charge_pcm if extraction else None,
    )
    move_in = move_in_cash(prop["price_pcm"]) if prop.get("price_pcm") else None

    # Resolve floor area: prefer scraped, fall back to Claude estimate from floorplan
    if not prop.get("floor_area_sqm") and qa is not None:
        space = getattr(qa, "space", None)
        if space:
            total_sqm = getattr(space, "total_area_sqm", None)
            if isinstance(total_sqm, (int, float)) and total_sqm > 0:
                prop["floor_area_sqm"] = total_sqm
                prop["floor_area_source"] = "estimated"

    # Status tracking (Ticket 7)
    user_status = prop.get("user_status") or "new"
    status_history = await storage.get_status_history(unique_id)

    # Price history (Ticket 10)
    price_history = await storage.get_price_history(unique_id)

    # Negotiation intelligence (Ticket 10)
    negotiation = generate_negotiation_brief(
        days_observed=days_since_filter(cast("str | None", prop.get("first_seen"))),
        price_history=price_history,
    )

    # OG image: use first gallery image as absolute URL for social previews
    og_image_url = ""
    if base_url and image_url_map:
        gallery_images = prop.get("gallery_images", [])
        for img in gallery_images:
            local_path = image_url_map.get(str(img.url))
            if local_path:
                og_image_url = f"{base_url}{local_path}"
                break

    return templates.TemplateResponse(
        request,
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
            "floorplan_repeats": floorplan_repeats,
            "base_url": base_url,
            "og_image_url": og_image_url,
            "fit_score": fit_score,
            "fit_breakdown": fit_breakdown,
            "cost_breakdown": cost_breakdown,
            "user_status": user_status,
            "user_statuses": list(UserStatus),
            "status_meta": USER_STATUS_META,
            "status_history": status_history,
            "price_history": price_history,
            "negotiation": negotiation,
            "rental_guidance": GUIDANCE,
            "verdict": verdict,
            "move_in": move_in,
            "brief_check": brief_check,
            "lead": lead,
            "annotated": annotated,
            "working": working,
        },
    )


@router.get("/area/{outcode}", response_class=HTMLResponse)
async def area_detail(
    request: Request,
    outcode: str,
    base_url: BaseUrlDep,
    highlight: str | None = None,
) -> HTMLResponse:
    """Area exploration page showing all micro-areas and reference data."""
    outcode = outcode.upper()

    if outcode not in AREA_CONTEXT:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"request": request, "message": f"No area data for {outcode}."},
            status_code=404,
        )

    area_context = build_area_context(outcode)
    area_context["micro_areas"] = get_micro_areas(outcode)
    borough = area_context.get("borough")
    if borough:
        enforcement = NOISE_ENFORCEMENT.get(borough)
        if enforcement:
            area_context["noise_enforcement"] = enforcement

    return templates.TemplateResponse(
        request,
        "area.html",
        {
            "request": request,
            "outcode": outcode,
            "area_context": area_context,
            "highlight": highlight,
            "base_url": base_url,
        },
    )


@router.patch("/property/{unique_id}/status")
async def update_property_status(
    request: Request,
    unique_id: str,
    storage: StorageDep,
    data_dir: DataDirDep,
) -> Response:
    """Update property user status.

    Returns a full card partial when HX-Target indicates a card-level swap
    (quick-action buttons on dashboard), or a status badge partial for
    detail-page swaps.
    """
    form = await request.form(max_fields=4, max_files=0, max_part_size=1024)
    status_value = str(form.get("status", "")).strip().lower()

    try:
        new_status = UserStatus(status_value)
    except ValueError:
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                f'<p class="toast-message">Invalid status: {html_escape(status_value)}</p>',
                status_code=400,
            )
        return JSONResponse({"error": f"Invalid status: {status_value}"}, status_code=400)

    prev = await storage.update_user_status(unique_id, new_status)
    if prev is None:
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                '<p class="toast-message">Property not found</p>',
                status_code=404,
            )
        return JSONResponse({"error": "Property not found"}, status_code=404)

    # Card-level swap: return full card partial (same as /property/{id}/card)
    if form.get("source") == "card":
        prop = await storage.web.get_property_card(unique_id)
        if prop:
            _enrich_fit_scores([prop], request.app.state.settings)
            resolved = _resolve_cached_thumbnail(prop["unique_id"], prop.get("image_url"), data_dir)
            if resolved:
                prop["image_url"] = resolved
            return templates.TemplateResponse(
                request,
                "_property_card.html",
                {
                    "request": request,
                    "prop": prop,
                    "source_badges": SOURCE_BADGES,
                    "status_meta": USER_STATUS_META,
                },
            )

    # Detail page: return full status selector so active state updates
    return templates.TemplateResponse(
        request,
        "_status_controls.html",
        {
            "request": request,
            "unique_id": unique_id,
            "current_status": new_status.value,
            "status_meta": USER_STATUS_META,
        },
    )


@router.post("/property/{unique_id}/reanalyze")
async def request_reanalysis(unique_id: str, storage: StorageDep) -> JSONResponse:
    """Flag a property for quality re-analysis.

    The actual analysis runs on next `--reanalyze` CLI invocation.
    """
    count = await storage.processing.request_reanalysis([unique_id])
    if count == 0:
        return JSONResponse(
            {"error": "not found or no existing analysis"},
            status_code=404,
        )
    return JSONResponse({"status": "queued"})
