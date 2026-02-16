"""Web dashboard query service — read-only queries for the web UI."""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any, cast

import aiosqlite

from home_finder.db.row_mappers import (
    PropertyDetailItem,
    PropertyListItem,
    parse_json_fields,
)
from home_finder.logging import get_logger
from home_finder.models import PropertyImage, PropertyQualityAnalysis

if TYPE_CHECKING:
    from home_finder.web.filters import PropertyFilter

logger = get_logger(__name__)


def build_filter_clauses(
    filters: PropertyFilter,
) -> tuple[str, list[Any]]:
    """Build WHERE clause and params for property filtering.

    Args:
        filters: Validated filter parameters.

    Returns:
        Tuple of (where_sql, params).
    """
    where_clauses: list[str] = [
        "COALESCE(p.enrichment_status, 'enriched') != 'pending'",
        "p.notification_status != 'pending_analysis'",
        # Hide properties with fallback analysis (API failed, no real quality data).
        # A fallback has a quality_analyses row but NULL overall_rating.
        # Properties with no analysis row at all (q.property_unique_id IS NULL) are fine.
        "(q.overall_rating IS NOT NULL OR q.property_unique_id IS NULL)",
        # Hide properties with no images — not worth viewing without photos
        """(p.image_url IS NOT NULL OR EXISTS (
            SELECT 1 FROM property_images pi
            WHERE pi.property_unique_id = p.unique_id
            AND pi.image_type = 'gallery'))""",
    ]
    params: list[Any] = []

    if filters.min_price is not None:
        where_clauses.append("p.price_pcm >= ?")
        params.append(filters.min_price)
    if filters.max_price is not None:
        where_clauses.append("p.price_pcm <= ?")
        params.append(filters.max_price)
    if filters.bedrooms is not None:
        where_clauses.append("p.bedrooms = ?")
        params.append(filters.bedrooms)
    if filters.min_rating is not None:
        where_clauses.append("q.overall_rating >= ?")
        params.append(filters.min_rating)
    if filters.area:
        where_clauses.append("UPPER(p.postcode) LIKE ?")
        params.append(f"{filters.area.upper()}%")
    if filters.property_type:
        where_clauses.append(
            "json_extract(q.analysis_json, '$.listing_extraction.property_type') = ?"
        )
        params.append(filters.property_type)
    if filters.outdoor_space == "yes":
        where_clauses.append("q.has_outdoor_space = 1")
    elif filters.outdoor_space == "no":
        where_clauses.append("(q.has_outdoor_space = 0 OR q.has_outdoor_space IS NULL)")
    if filters.natural_light:
        where_clauses.append("json_extract(q.analysis_json, '$.light_space.natural_light') = ?")
        params.append(filters.natural_light)
    if filters.pets == "yes":
        where_clauses.append(
            "json_extract(q.analysis_json, '$.listing_extraction.pets_allowed') = 'yes'"
        )
    if filters.value_rating:
        where_clauses.append(
            "(json_extract(q.analysis_json, '$.value.quality_adjusted_rating') = ?"
            " OR json_extract(q.analysis_json, '$.value.rating') = ?)"
        )
        params.extend([filters.value_rating, filters.value_rating])
    if filters.hob_type:
        where_clauses.append("json_extract(q.analysis_json, '$.kitchen.hob_type') = ?")
        params.append(filters.hob_type)
    if filters.floor_level:
        where_clauses.append("json_extract(q.analysis_json, '$.light_space.floor_level') = ?")
        params.append(filters.floor_level)
    if filters.building_construction:
        where_clauses.append(
            "json_extract(q.analysis_json, '$.flooring_noise.building_construction') = ?"
        )
        params.append(filters.building_construction)
    if filters.office_separation:
        where_clauses.append("json_extract(q.analysis_json, '$.bedroom.office_separation') = ?")
        params.append(filters.office_separation)
    if filters.hosting_layout:
        where_clauses.append("json_extract(q.analysis_json, '$.space.hosting_layout') = ?")
        params.append(filters.hosting_layout)
    if filters.hosting_noise_risk:
        where_clauses.append(
            "json_extract(q.analysis_json, '$.flooring_noise.hosting_noise_risk') = ?"
        )
        params.append(filters.hosting_noise_risk)
    if filters.broadband_type:
        where_clauses.append(
            "json_extract(q.analysis_json, '$.listing_extraction.broadband_type') = ?"
        )
        params.append(filters.broadband_type)
    if filters.tags:
        for t in filters.tags:
            where_clauses.append(
                "(json_extract(q.analysis_json, '$.highlights') LIKE ?"
                " OR json_extract(q.analysis_json, '$.lowlights') LIKE ?)"
            )
            escaped = t.replace("%", "\\%").replace("_", "\\_")
            params.extend([f"%{escaped}%", f"%{escaped}%"])

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
    return where_sql, params


class WebQueryService:
    """Read-only query service for the web dashboard."""

    def __init__(
        self,
        get_connection: Callable[[], Coroutine[Any, Any, aiosqlite.Connection]],
        get_property_images: Callable[[str], Coroutine[Any, Any, list[PropertyImage]]],
    ) -> None:
        self._get_connection = get_connection
        self._get_property_images = get_property_images

    async def get_filter_count(
        self,
        filters: PropertyFilter,
    ) -> int:
        """Get count of properties matching filters (no data fetch).

        Args:
            filters: Validated filter parameters.

        Returns:
            Total count of matching properties.
        """
        conn = await self._get_connection()
        where_sql, params = build_filter_clauses(filters)
        cursor = await conn.execute(
            f"""
            SELECT COUNT(*) FROM properties p
            LEFT JOIN quality_analyses q ON p.unique_id = q.property_unique_id
            WHERE {where_sql}
            """,
            params,
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def get_map_markers(
        self,
        filters: PropertyFilter,
    ) -> list[dict[str, Any]]:
        """Get lightweight map marker data for all matching properties with coordinates.

        Same filters as get_properties_paginated but no pagination and only
        map-relevant columns. Returns only properties that have lat/lon.

        Args:
            filters: Validated filter parameters.

        Returns:
            List of dicts with map marker fields.
        """
        conn = await self._get_connection()
        where_sql, params = build_filter_clauses(filters)
        cursor = await conn.execute(
            f"""
            SELECT p.unique_id, p.latitude, p.longitude, p.price_pcm,
                   p.bedrooms, p.title, p.postcode,
                   p.commute_minutes, p.image_url,
                   q.overall_rating as quality_rating,
                   json_extract(q.analysis_json, '$.value.quality_adjusted_rating') as value_rating,
                   json_extract(q.analysis_json, '$.one_line') as one_line
            FROM properties p
            LEFT JOIN quality_analyses q ON p.unique_id = q.property_unique_id
            WHERE {where_sql}
              AND p.latitude IS NOT NULL AND p.longitude IS NOT NULL
            """,
            params,
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row["unique_id"],
                "lat": row["latitude"],
                "lon": row["longitude"],
                "price": row["price_pcm"],
                "bedrooms": row["bedrooms"],
                "rating": row["quality_rating"],
                "title": row["title"],
                "url": f"/property/{row['unique_id']}",
                "image_url": row["image_url"],
                "postcode": row["postcode"],
                "commute_minutes": row["commute_minutes"],
                "value_rating": row["value_rating"],
                "one_line": row["one_line"],
            }
            for row in rows
        ]

    async def get_properties_paginated(
        self,
        filters: PropertyFilter,
        *,
        sort: str = "newest",
        page: int = 1,
        per_page: int = 24,
    ) -> tuple[list[PropertyListItem], int]:
        """Get paginated properties with optional filters.

        Args:
            filters: Validated filter parameters.
            sort: Sort order key.
            page: Page number (1-indexed).
            per_page: Items per page.

        Returns:
            Tuple of (property dicts, total count).
        """
        conn = await self._get_connection()

        where_sql, params = build_filter_clauses(filters)

        order_map = {
            "newest": "p.first_seen DESC",
            "price_asc": "p.price_pcm ASC",
            "price_desc": "p.price_pcm DESC",
            "rating_desc": "COALESCE(q.overall_rating, 0) DESC, p.first_seen DESC",
            "fit_desc": "COALESCE(q.fit_score, -1) DESC, p.first_seen DESC",
        }
        order_sql = order_map.get(sort, "p.first_seen DESC")

        # Count total
        count_cursor = await conn.execute(
            f"""
            SELECT COUNT(*) FROM properties p
            LEFT JOIN quality_analyses q ON p.unique_id = q.property_unique_id
            WHERE {where_sql}
            """,
            params,
        )
        count_row = await count_cursor.fetchone()
        total = count_row[0] if count_row else 0

        # Subquery: first non-EPC gallery image as fallback thumbnail
        gallery_subquery = """
            (SELECT pi.url FROM property_images pi
             WHERE pi.property_unique_id = p.unique_id
             AND pi.image_type = 'gallery'
             AND LOWER(pi.url) NOT LIKE '%epc%'
             AND LOWER(pi.url) NOT LIKE '%energy-performance%'
             AND LOWER(pi.url) NOT LIKE '%energy_performance%'
             ORDER BY pi.id LIMIT 1) as first_gallery_url
        """

        offset = (page - 1) * per_page
        cursor = await conn.execute(
            f"""
            SELECT p.*, q.overall_rating as quality_rating,
                   q.condition_concerns as quality_concerns,
                   q.concern_severity as quality_severity,
                   q.analysis_json,
                   {gallery_subquery}
            FROM properties p
            LEFT JOIN quality_analyses q ON p.unique_id = q.property_unique_id
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """,
            [*params, per_page, offset],
        )
        rows = await cursor.fetchall()

        properties: list[PropertyListItem] = []
        for row in rows:
            prop_dict = dict(row)
            parse_json_fields(prop_dict)
            # Prefer enriched gallery image (EPC URLs already filtered by subquery)
            # over scraper thumbnail which may be low-res or expired
            if prop_dict.get("first_gallery_url"):
                prop_dict["image_url"] = prop_dict["first_gallery_url"]
            # Extract quality fields from analysis_json
            if prop_dict.get("analysis_json"):
                try:
                    analysis = json.loads(prop_dict["analysis_json"])
                    prop_dict["quality_summary"] = analysis.get("summary", "")
                    value = analysis.get("value") or {}
                    prop_dict["value_rating"] = value.get("quality_adjusted_rating") or value.get(
                        "rating"
                    )
                    # Extended quality fields for card display
                    # Defensive: filter out junk entries (commas, empties) from
                    # old analyses where Claude returned malformed lists
                    raw_hl = analysis.get("highlights")
                    raw_ll = analysis.get("lowlights")
                    prop_dict["highlights"] = (
                        [t for t in raw_hl if isinstance(t, str) and t.strip() not in ("", ",")]
                        if isinstance(raw_hl, list)
                        else None
                    )
                    prop_dict["lowlights"] = (
                        [t for t in raw_ll if isinstance(t, str) and t.strip() not in ("", ",")]
                        if isinstance(raw_ll, list)
                        else None
                    )
                    prop_dict["one_line"] = analysis.get("one_line")
                    listing_ext = analysis.get("listing_extraction") or {}
                    prop_dict["property_type"] = listing_ext.get("property_type")
                    prop_dict["epc_rating"] = listing_ext.get("epc_rating")
                except (json.JSONDecodeError, TypeError):
                    prop_dict["quality_summary"] = ""
                    prop_dict["value_rating"] = None
                    prop_dict["highlights"] = None
                    prop_dict["lowlights"] = None
                    prop_dict["one_line"] = None
                    prop_dict["property_type"] = None
                    prop_dict["epc_rating"] = None
            else:
                prop_dict["quality_summary"] = ""
                prop_dict["value_rating"] = None
                prop_dict["highlights"] = None
                prop_dict["lowlights"] = None
                prop_dict["one_line"] = None
                prop_dict["property_type"] = None
                prop_dict["epc_rating"] = None
            properties.append(cast(PropertyListItem, prop_dict))

        return properties, total

    async def get_property_detail(self, unique_id: str) -> PropertyDetailItem | None:
        """Get full property detail including quality analysis and images.

        Args:
            unique_id: Property unique ID.

        Returns:
            Dict with property data, quality analysis, and images, or None.
        """
        conn = await self._get_connection()

        cursor = await conn.execute(
            """
            SELECT p.*, q.analysis_json, q.overall_rating as quality_rating
            FROM properties p
            LEFT JOIN quality_analyses q ON p.unique_id = q.property_unique_id
            WHERE p.unique_id = ?
            """,
            (unique_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        prop_dict = dict(row)
        parse_json_fields(prop_dict)

        # Parse quality analysis
        if prop_dict.get("analysis_json"):
            prop_dict["quality_analysis"] = PropertyQualityAnalysis.model_validate_json(
                prop_dict["analysis_json"]
            )
        else:
            prop_dict["quality_analysis"] = None

        # Get images
        images = await self._get_property_images(unique_id)
        prop_dict["gallery_images"] = [img for img in images if img.image_type == "gallery"]
        prop_dict["floorplan_images"] = [img for img in images if img.image_type == "floorplan"]

        return cast(PropertyDetailItem, prop_dict)

    async def get_property_count(self) -> int:
        """Get total number of tracked properties.

        Returns:
            Count of properties in database.
        """
        conn = await self._get_connection()
        cursor = await conn.execute("SELECT COUNT(*) FROM properties")
        row = await cursor.fetchone()
        return row[0] if row else 0
