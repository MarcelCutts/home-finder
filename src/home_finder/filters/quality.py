"""Property quality analysis filter using Claude vision."""

import asyncio
import base64
import json as _json
import re as _re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from home_finder.data.area_context import (
    ACOUSTIC_PROFILES,
    DEFAULT_BENCHMARK,
    RENTAL_BENCHMARKS,
    build_property_context,
)
from home_finder.logging import get_logger
from home_finder.models import (
    BathroomAnalysis,
    BedroomAnalysis,
    ConditionAnalysis,
    FlooringNoiseAnalysis,
    KitchenAnalysis,
    LightSpaceAnalysis,
    ListingExtraction,
    ListingRedFlags,
    MergedProperty,
    OutdoorSpaceAnalysis,
    PropertyHighlight,
    PropertyLowlight,
    PropertyQualityAnalysis,
    SpaceAnalysis,
    StorageAnalysis,
    ValueAnalysis,
    ViewingNotes,
)
from home_finder.utils.image_cache import is_valid_image_url, read_image_bytes
from home_finder.utils.image_processing import (
    ImageMediaType,
)
from home_finder.utils.image_processing import (
    is_valid_media_type as _is_valid_media_type,
)
from home_finder.utils.image_processing import (
    resize_image_bytes as _resize_image_bytes,
)

if TYPE_CHECKING:
    import anthropic
    from anthropic.types import ImageBlockParam

logger = get_logger(__name__)

# Rate limit settings for Tier 2 (1,000 RPM)
# SDK handles retry automatically, we just need a small delay to avoid bursts
DELAY_BETWEEN_CALLS: Final = 0.2  # seconds (1000 RPM = 0.06s minimum, add buffer)

# SDK retry configuration
MAX_RETRIES: Final = 3
REQUEST_TIMEOUT: Final = 180.0  # 3 minutes for vision requests

# Circuit breaker: stop hammering the API after consecutive outage-indicating errors
_CIRCUIT_BREAKER_THRESHOLD: Final = 3
_CIRCUIT_BREAKER_COOLDOWN: Final = 300  # seconds (5 min)


class APIUnavailableError(Exception):
    """Raised when the Anthropic API circuit breaker is open."""


def assess_value(price_pcm: int, postcode: str | None, bedrooms: int) -> ValueAnalysis:
    """Assess value-for-money based on local rental benchmarks.

    Args:
        price_pcm: Monthly rent in GBP.
        postcode: Property postcode (e.g., "E8 2LX").
        bedrooms: Number of bedrooms.

    Returns:
        ValueAnalysis with comparison to local average.
    """
    if not postcode:
        return ValueAnalysis(note="No postcode - cannot assess value")

    # Extract outcode (e.g., "E8" from "E8 2LX")
    outcode = postcode.split()[0].upper() if " " in postcode else postcode.upper()

    # Get benchmark for this area
    benchmarks = RENTAL_BENCHMARKS.get(outcode, DEFAULT_BENCHMARK)

    # Cap bedrooms at 3 for benchmark lookup
    bed_key = min(bedrooms, 3) if bedrooms >= 1 else 1
    average = benchmarks.get(bed_key)

    if average is None:
        return ValueAnalysis(note=f"No benchmark for {bed_key}-bed in {outcode}")

    difference = price_pcm - average
    pct_diff = (difference / average) * 100

    # Determine rating
    if pct_diff <= -10:
        rating: Literal["excellent", "good", "fair", "poor"] = "excellent"
    elif pct_diff <= 0:
        rating = "good"
    elif pct_diff <= 10:
        rating = "fair"
    else:
        rating = "poor"

    # Generate note
    if difference < 0:
        note = f"£{abs(difference):,} below {outcode} average"
    elif difference > 0:
        note = f"£{difference:,} above {outcode} average"
    else:
        note = f"At {outcode} average"

    return ValueAnalysis(
        area_average=average,
        difference=difference,
        rating=rating,
        note=note,
    )


from home_finder.filters.quality_prompts import (  # noqa: E402
    EVALUATION_SYSTEM_PROMPT,
    VISUAL_ANALYSIS_SYSTEM_PROMPT,
    build_evaluation_prompt,
    build_user_prompt,
)

# ── Response models for tool schema generation ──────────────────────────
# These define the strict schemas sent to the Anthropic API. They differ from
# the storage models (models.py) which allow None/defaults for backward compat.
_Forbid = ConfigDict(extra="forbid")


class _VisualAnalysisResponse(BaseModel):
    """Phase 1 tool response: visual observations from property images."""

    model_config = _Forbid

    class Kitchen(BaseModel):
        model_config = _Forbid
        overall_quality: Literal["modern", "decent", "dated", "unknown"] = Field(
            description="Overall kitchen quality/age assessment"
        )
        hob_type: Literal["gas", "electric", "induction", "unknown"] = Field(
            description="Type of hob if visible or mentioned"
        )
        has_dishwasher: Literal["yes", "no", "unknown"]
        has_washing_machine: Literal["yes", "no", "unknown"]
        notes: str = Field(description="Notable kitchen features or concerns")

    class Condition(BaseModel):
        model_config = _Forbid
        overall_condition: Literal["excellent", "good", "fair", "poor", "unknown"]
        has_visible_damp: Literal["yes", "no", "unknown"]
        has_visible_mold: Literal["yes", "no", "unknown"]
        has_worn_fixtures: Literal["yes", "no", "unknown"]
        maintenance_concerns: list[str] = Field(description="List of specific maintenance concerns")
        confidence: Literal["high", "medium", "low"]

    class LightSpace(BaseModel):
        model_config = _Forbid
        natural_light: Literal["excellent", "good", "fair", "poor", "unknown"]
        window_sizes: Literal["large", "medium", "small", "unknown"]
        feels_spacious: bool = Field(description="Whether the property feels spacious")
        ceiling_height: Literal["high", "standard", "low", "unknown"]
        floor_level: Literal["basement", "ground", "lower", "upper", "top", "unknown"] = Field(
            description="Estimated floor level from photos/description/floorplan"
        )
        notes: str

    class Space(BaseModel):
        model_config = _Forbid
        living_room_sqm: float | None = Field(
            description="Estimated living room size in sqm from floorplan"
        )
        is_spacious_enough: bool = Field(description="True if can fit office AND host 8+ people")
        confidence: Literal["high", "medium", "low"]
        hosting_layout: Literal["excellent", "good", "awkward", "poor", "unknown"] = Field(
            description=(
                "Layout flow for hosting: excellent = open-plan kitchen/living + accessible "
                "bathroom + practical entrance; good = mostly good flow; awkward = hosting "
                "friction (disconnected kitchen, narrow entrance, guests pass bedrooms); "
                "poor = fundamentally unsuitable (through-rooms, isolated kitchen)"
            ),
        )

    class Bathroom(BaseModel):
        model_config = _Forbid
        overall_condition: Literal["modern", "decent", "dated", "unknown"]
        has_bathtub: Literal["yes", "no", "unknown"]
        shower_type: Literal["overhead", "separate_cubicle", "electric", "none", "unknown"]
        is_ensuite: Literal["yes", "no", "unknown"]
        notes: str

    class Bedroom(BaseModel):
        model_config = _Forbid
        primary_is_double: Literal["yes", "no", "unknown"]
        has_built_in_wardrobe: Literal["yes", "no", "unknown"]
        can_fit_desk: Literal["yes", "no", "unknown"]
        office_separation: Literal[
            "dedicated_room", "separate_area", "shared_space", "none", "unknown"
        ] = Field(
            description=(
                "Quality of work-life separation: dedicated_room = closable room for "
                "office (2-bed with non-through second room); separate_area = alcove, "
                "mezzanine, or partitioned nook; shared_space = desk in living room, "
                "no separation; none = studio or nowhere viable"
            ),
        )
        notes: str

    class OutdoorSpace(BaseModel):
        model_config = _Forbid
        has_balcony: bool
        has_garden: bool
        has_terrace: bool
        has_shared_garden: bool
        notes: str

    class Storage(BaseModel):
        model_config = _Forbid
        has_built_in_wardrobes: Literal["yes", "no", "unknown"]
        has_hallway_cupboard: Literal["yes", "no", "unknown"]
        storage_rating: Literal["good", "adequate", "poor", "unknown"]

    class FlooringNoise(BaseModel):
        model_config = _Forbid
        primary_flooring: Literal["hardwood", "laminate", "carpet", "tile", "mixed", "unknown"]
        has_double_glazing: Literal["yes", "no", "unknown"]
        building_construction: Literal[
            "solid_brick", "concrete", "timber_frame", "mixed", "unknown"
        ] = Field(description="Building construction type estimated from visual cues")
        noise_indicators: list[str]
        hosting_noise_risk: Literal["low", "moderate", "high", "unknown"] = Field(
            description=(
                "Risk of disturbing neighbours when hosting: low = solid construction + "
                "carpet + top floor/detached; moderate = mixed signals; high = timber "
                "frame + hard floors + lower floor + shared walls"
            ),
        )
        notes: str

    class RedFlags(BaseModel):
        model_config = _Forbid
        missing_room_photos: list[str] = Field(
            description="Rooms not shown in photos (e.g. 'bathroom', 'kitchen')"
        )
        too_few_photos: bool
        selective_angles: bool
        description_concerns: list[str]
        red_flag_count: int = Field(description="Total number of red flags identified")

    kitchen: Kitchen
    condition: Condition
    light_space: LightSpace
    space: Space
    bathroom: Bathroom
    bedroom: Bedroom
    outdoor_space: OutdoorSpace
    storage: Storage
    flooring_noise: FlooringNoise
    listing_red_flags: RedFlags
    floorplan_detected_in_gallery: list[int] = Field(
        default_factory=list,
        description=(
            "1-based gallery image indices that appear to be floorplans or floor plan diagrams. "
            "Empty list if no gallery images look like floorplans."
        ),
    )
    overall_rating: int = Field(
        description="Overall 1-5 star rating for rental desirability (1=worst, 5=best)"
    )
    condition_concerns: bool = Field(description="True if any significant condition issues found")
    concern_severity: Literal["minor", "moderate", "serious", "none"]
    summary: str = Field(
        description=(
            "1-2 sentence property overview for notification. Focus on what "
            "it's like to live here: character, standout features, layout feel. "
            "Do NOT restate condition concerns (already listed separately)."
        ),
    )


class _EvaluationResponse(BaseModel):
    """Phase 2 tool response: evaluation and synthesis."""

    model_config = _Forbid

    class ListingExtraction(BaseModel):
        model_config = _Forbid
        epc_rating: Literal["A", "B", "C", "D", "E", "F", "G", "unknown"]
        service_charge_pcm: int | None
        deposit_weeks: int | None
        bills_included: Literal["yes", "no", "unknown"]
        pets_allowed: Literal["yes", "no", "unknown"]
        parking: Literal["dedicated", "street", "none", "unknown"]
        council_tax_band: Literal["A", "B", "C", "D", "E", "F", "G", "H", "unknown"]
        property_type: Literal[
            "victorian",
            "edwardian",
            "georgian",
            "new_build",
            "purpose_built",
            "warehouse",
            "ex_council",
            "period_conversion",
            "unknown",
        ]
        furnished_status: Literal["furnished", "unfurnished", "part_furnished", "unknown"]
        broadband_type: Literal["fttp", "fttc", "cable", "standard", "unknown"] = Field(
            description=(
                "Broadband type from listing: fttp = fibre/FTTP/FTTH/Hyperoptic/"
                "Community Fibre/full fibre/1Gbps; fttc = superfast/FTTC/up to 80Mbps; "
                "cable = Virgin Media/cable; standard = broadband alone/ADSL"
            ),
        )

    class ViewingNotes(BaseModel):
        model_config = _Forbid
        check_items: list[str] = Field(
            description="Property-specific things to inspect during viewing"
        )
        questions_for_agent: list[str] = Field(description="Questions to ask the letting agent")
        deal_breaker_tests: list[str] = Field(description="Quick tests to determine deal-breakers")

    class ValueForQuality(BaseModel):
        model_config = _Forbid
        rating: Literal["excellent", "good", "fair", "poor"] = Field(
            description="Value rating considering quality vs price"
        )
        reasoning: str = Field(
            description=(
                "Value justification: why this price is or isn't fair for "
                "what you get. Focus on price factors: stock type "
                "premium/discount, true monthly cost, area rent trajectory, "
                "service charges, incentives. Reference condition only as "
                "'condition justifies/doesn't justify price' — don't "
                "restate specific issues."
            ),
        )

    listing_extraction: ListingExtraction
    viewing_notes: ViewingNotes
    highlights: list[Literal[tuple(PropertyHighlight)]] = Field(  # type: ignore[valid-type]
        description="Top 3-5 positive features from the allowed highlight tags"
    )
    lowlights: list[Literal[tuple(PropertyLowlight)]] = Field(  # type: ignore[valid-type]
        description="Top 1-3 concerns from the allowed lowlight tags"
    )
    one_line: str = Field(description="6-12 word tagline capturing the property's character")
    value_for_quality: ValueForQuality


# ── Cross-reference: API response models ↔ storage models ────────────
# Each pair maps an API sub-model (used for JSON schema generation) to
# its storage counterpart (used for DB persistence with defaults/coercion).
#
# Intentionally unpaired:
#   - _EvaluationResponse.ValueForQuality (2 fields) vs ValueAnalysis (6 fields):
#     different structures by design — ValueAnalysis merges benchmark data with
#     LLM-assessed quality rating.
#   - _VisualAnalysisResponse.floorplan_detected_in_gallery: API-only field,
#     used during analysis but not stored as a separate model.
_MODEL_PAIRS: Final[list[tuple[type[BaseModel], type[BaseModel]]]] = [
    (_VisualAnalysisResponse.Kitchen, KitchenAnalysis),
    (_VisualAnalysisResponse.Condition, ConditionAnalysis),
    (_VisualAnalysisResponse.LightSpace, LightSpaceAnalysis),
    (_VisualAnalysisResponse.Space, SpaceAnalysis),
    (_VisualAnalysisResponse.Bathroom, BathroomAnalysis),
    (_VisualAnalysisResponse.Bedroom, BedroomAnalysis),
    (_VisualAnalysisResponse.OutdoorSpace, OutdoorSpaceAnalysis),
    (_VisualAnalysisResponse.Storage, StorageAnalysis),
    (_VisualAnalysisResponse.FlooringNoise, FlooringNoiseAnalysis),
    (_VisualAnalysisResponse.RedFlags, ListingRedFlags),
    (_EvaluationResponse.ListingExtraction, ListingExtraction),
    (_EvaluationResponse.ViewingNotes, ViewingNotes),
]


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve $ref/$defs in a Pydantic JSON schema and strip title fields."""
    defs = schema.pop("$defs", {})

    def _resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref_name = node["$ref"].rsplit("/", 1)[-1]
                return _resolve(dict(defs[ref_name]))
            return {k: _resolve(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [_resolve(item) for item in node]
        return node

    result: dict[str, Any] = _resolve(schema)
    result.pop("title", None)
    return result


def _build_tool_schema(
    name: str,
    description: str,
    model: type[BaseModel],
    *,
    strict: bool = False,
) -> dict[str, Any]:
    """Build an Anthropic tool schema from a Pydantic model class.

    Schemas are generated from the API response models (``extra="forbid"``,
    all-required, plain types) rather than the storage models (defaults,
    coercion validators).  The ``_MODEL_PAIRS`` constant documents the
    correspondence between each pair, and tests in
    ``TestModelPairConsistency`` mechanically verify they stay in sync.
    """
    schema = _inline_refs(model.model_json_schema())
    schema.pop("description", None)  # Strip Pydantic docstring
    tool: dict[str, Any] = {
        "name": name,
        "description": description,
        "input_schema": schema,
    }
    if strict:
        tool["strict"] = True
    return tool


VISUAL_ANALYSIS_TOOL: Final[dict[str, Any]] = _build_tool_schema(
    "property_visual_analysis",
    "Return visual property quality analysis results from images",
    _VisualAnalysisResponse,
    # No strict=True — schema exceeds Anthropic grammar compilation limits
    # (55 properties across 10 nested objects, ~7.8KB)
)

EVALUATION_TOOL: Final[dict[str, Any]] = _build_tool_schema(
    "property_evaluation",
    "Return property evaluation based on visual analysis observations",
    _EvaluationResponse,
    strict=True,
)


def _clean_value(val: Any) -> Any:
    """Clean a single response value — parse JSON strings that should be dicts/lists."""
    if not isinstance(val, str):
        return val
    s = val.strip()
    if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
        try:
            parsed = _json.loads(s)
            if isinstance(parsed, (dict, list)):
                return parsed
        except (ValueError, _json.JSONDecodeError):
            # Retry after stripping trailing commas (common LLM quirk)
            sanitized = _re.sub(r",\s*([}\]])", r"\1", s)
            try:
                parsed = _json.loads(sanitized)
                if isinstance(parsed, (dict, list)):
                    return parsed
            except (ValueError, _json.JSONDecodeError):
                pass
    return s


def _clean_list(lst: list[Any]) -> list[Any]:
    """Clean list values and remove junk entries (bare commas, empty strings)."""
    cleaned = []
    for item in lst:
        item = _clean_value(item)
        if isinstance(item, str) and item.strip() in ("", ","):
            continue
        if isinstance(item, dict):
            item = _clean_dict(item)
        cleaned.append(item)
    return cleaned


def _clean_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Clean a dict of response data — parse JSON strings, recurse nested dicts, remove junk."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, list):
            out[k] = _clean_list(v)
        elif isinstance(v, dict):
            out[k] = _clean_dict(v)
        else:
            out[k] = _clean_value(v)
    return out


# Construction type → acoustic profile key mapping
_CONSTRUCTION_TO_PROFILE: dict[str, str] = {
    "timber_frame": "victorian",
    "concrete": "ex_council",
    "solid_brick": "purpose_built",
}


class PropertyQualityFilter:
    """Analyze property quality using Claude vision API."""

    def __init__(
        self,
        api_key: str,
        max_images: int = 20,
        *,
        enable_extended_thinking: bool = True,
        thinking_budget_tokens: int = 10000,
    ) -> None:
        """Initialize the quality filter.

        Args:
            api_key: Anthropic API key.
            max_images: Maximum number of gallery images to analyze.
            enable_extended_thinking: Enable extended thinking for deeper analysis.
            thinking_budget_tokens: Token budget for extended thinking.
        """
        self._api_key = api_key
        self._max_images = max_images
        self._enable_extended_thinking = enable_extended_thinking
        self._thinking_budget_tokens = thinking_budget_tokens
        self._client: anthropic.AsyncAnthropic | None = None
        self._curl_session: Any | None = None  # curl_cffi.requests.AsyncSession
        # Circuit breaker state (asyncio is single-threaded, no lock needed)
        self._consecutive_api_failures = 0
        self._circuit_open = False
        self._circuit_opened_at: float | None = None

    def _get_client(self) -> "anthropic.AsyncAnthropic":
        """Get or create the Anthropic client with optimized settings."""
        if self._client is None:
            import anthropic as _anthropic
            import httpx

            self._client = _anthropic.AsyncAnthropic(
                api_key=self._api_key,
                max_retries=MAX_RETRIES,  # SDK handles retry with exponential backoff
                timeout=httpx.Timeout(REQUEST_TIMEOUT),  # 3 min for vision requests
            )
        return self._client

    async def _get_curl_session(self) -> Any:
        """Get or create the curl_cffi async session for image downloads."""
        if self._curl_session is None:
            from curl_cffi.requests import AsyncSession

            self._curl_session = AsyncSession()
        return self._curl_session

    def _record_api_failure(self) -> None:
        """Record a consecutive API failure and open circuit if threshold reached."""
        import time

        self._consecutive_api_failures += 1
        if self._consecutive_api_failures >= _CIRCUIT_BREAKER_THRESHOLD:
            self._circuit_open = True
            self._circuit_opened_at = time.monotonic()
            logger.warning(
                "api_circuit_breaker_open",
                consecutive_failures=self._consecutive_api_failures,
            )

    def _record_api_success(self) -> None:
        """Reset failure counter and close circuit if it was open (half-open recovery)."""
        if self._circuit_open:
            logger.info("api_circuit_breaker_closed")
        self._consecutive_api_failures = 0
        self._circuit_open = False
        self._circuit_opened_at = None

    def _is_circuit_open(self) -> bool:
        """Check if circuit breaker is open, with half-open recovery after cooldown."""
        if not self._circuit_open:
            return False
        import time

        elapsed = time.monotonic() - self._circuit_opened_at  # type: ignore[operator]
        if elapsed >= _CIRCUIT_BREAKER_COOLDOWN:
            logger.info(
                "circuit_breaker_half_open",
                cooldown_seconds=_CIRCUIT_BREAKER_COOLDOWN,
                elapsed_seconds=round(elapsed, 1),
            )
            return False  # Allow one retry attempt
        return True

    @staticmethod
    def _needs_base64_download(url: str) -> bool:
        """Check if URL requires local download due to anti-bot protection.

        Some image CDNs (Zoopla's zoocdn.com) use TLS fingerprinting to block
        non-browser requests. When we send URL-based images to Claude's API,
        Anthropic's servers fetch them directly and get blocked with 403.

        For these sites, we need to download the images locally using curl_cffi
        (which can impersonate Chrome's TLS fingerprint) and send as base64.
        """
        # Zoopla and OpenRent image CDNs block non-browser requests
        return "zoocdn.com" in url or "imagescdn.openrent.co.uk" in url

    @staticmethod
    def _get_media_type(url: str) -> ImageMediaType:
        """Determine media type from URL extension."""
        # Try to get from URL extension
        ext = url.lower().split("?")[0].rsplit(".", 1)[-1] if "." in url else ""
        type_map: dict[str, ImageMediaType] = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
            "webp": "image/webp",
        }
        return type_map.get(ext, "image/jpeg")  # Default to JPEG

    async def _download_image_as_base64(self, url: str) -> tuple[str, ImageMediaType] | None:
        """Download image using curl_cffi and return base64 data with media type.

        Uses Chrome TLS fingerprint impersonation to bypass anti-bot protection.

        Args:
            url: Image URL to download.

        Returns:
            Tuple of (base64_data, media_type) or None if download failed.
        """
        try:
            session = await self._get_curl_session()
            response = await session.get(
                url,
                impersonate="chrome",
                headers={
                    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                    "Accept-Language": "en-GB,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                },
                timeout=30,
            )
            if response.status_code != 200:
                logger.warning(
                    "image_download_failed",
                    url=url,
                    status=response.status_code,
                )
                return None

            # Get content type from response or guess from URL
            content_type = response.headers.get("content-type", "")
            media_type_raw = content_type.split(";")[0] if content_type else ""

            # Validate and use the media type if valid, otherwise guess from URL
            if _is_valid_media_type(media_type_raw):
                media_type: ImageMediaType = media_type_raw
            else:
                media_type = self._get_media_type(url)

            # Encode to base64
            image_data = base64.standard_b64encode(response.content).decode("utf-8")
            return image_data, media_type

        except Exception as e:
            logger.warning("image_download_error", url=url, error=str(e), exc_info=True)
            return None

    async def _build_image_block(
        self, url: str, cached_path: Path | None = None
    ) -> "ImageBlockParam | None":
        """Build an image block, using disk cache or downloading as needed.

        Args:
            url: Image URL.
            cached_path: Path to cached image on disk (if available).

        Returns:
            ImageBlockParam or None if image couldn't be loaded.
        """
        # Skip non-image URLs (e.g. YouTube embeds in OpenRent galleries)
        _VIDEO_MARKERS = ("youtube.com/", "youtu.be/", "vimeo.com/")
        if any(marker in url.lower() for marker in _VIDEO_MARKERS):
            logger.debug("skipping_video_url", url=url)
            return None

        from anthropic.types import (
            Base64ImageSourceParam,
            ImageBlockParam,
            URLImageSourceParam,
        )

        # Try disk cache first — avoids all HTTP requests
        if cached_path is not None:
            data = read_image_bytes(cached_path)
            if data is not None:
                media_type = self._get_media_type(url)
                logger.debug(
                    "image_block_from_cache",
                    url=url,
                    cached_path=str(cached_path),
                    data_len=len(data),
                    media_type=media_type,
                )
                # Validate image is decodable before sending to API
                try:
                    from io import BytesIO

                    from PIL import Image

                    img = Image.open(BytesIO(data))
                    img.load()  # Force full pixel decode
                    img.close()
                except Exception:
                    logger.warning(
                        "cached_image_corrupt",
                        url=url,
                        cached_path=str(cached_path),
                    )
                    data = None  # Fall through to download path
            if data is not None:
                data = await asyncio.to_thread(_resize_image_bytes, data)
                image_data = base64.standard_b64encode(data).decode("utf-8")
                return ImageBlockParam(
                    type="image",
                    source=Base64ImageSourceParam(
                        type="base64",
                        media_type=media_type,
                        data=image_data,
                    ),
                )

        if self._needs_base64_download(url):
            result = await self._download_image_as_base64(url)
            if result is None:
                return None
            image_data, media_type = result
            decoded = base64.standard_b64decode(image_data)
            resized = await asyncio.to_thread(_resize_image_bytes, decoded)
            image_data = base64.standard_b64encode(resized).decode("utf-8")
            return ImageBlockParam(
                type="image",
                source=Base64ImageSourceParam(
                    type="base64",
                    media_type=media_type,
                    data=image_data,
                ),
            )
        else:
            # Use URL-based image (Anthropic fetches directly)
            return ImageBlockParam(
                type="image",
                source=URLImageSourceParam(type="url", url=url),
            )

    async def analyze_single_merged(
        self,
        merged: MergedProperty,
        *,
        data_dir: str | None = None,
    ) -> tuple[MergedProperty, PropertyQualityAnalysis]:
        """Analyze quality for a single pre-enriched merged property.

        Does NOT sleep between calls — caller controls pacing.
        Handles errors internally (returns minimal analysis on failure).

        Args:
            merged: Enriched merged property to analyze.
            data_dir: Data directory for image cache.

        Returns:
            Tuple of (merged_property, analysis).
        """
        prop = merged.canonical
        value = assess_value(prop.price_pcm, prop.postcode, prop.bedrooms)

        ctx = build_property_context(prop.postcode, prop.bedrooms)

        # Build URL lists from pre-enriched images
        gallery_urls = [str(img.url) for img in merged.images if img.image_type == "gallery"]
        floorplan_url = str(merged.floorplan.url) if merged.floorplan else None

        # Resolve cached image paths (if data_dir is set)
        gallery_cached: list[Path | None] = []
        floorplan_cached: Path | None = None
        if data_dir:
            from home_finder.utils.image_cache import get_cached_image_path

            for idx, g_url in enumerate(gallery_urls):
                p = get_cached_image_path(data_dir, merged.unique_id, g_url, "gallery", idx)
                gallery_cached.append(p if p.is_file() else None)
            if floorplan_url:
                p = get_cached_image_path(data_dir, merged.unique_id, floorplan_url, "floorplan", 0)
                floorplan_cached = p if p.is_file() else None
        else:
            gallery_cached = [None] * len(gallery_urls)

        if not gallery_urls and not floorplan_url:
            logger.info(
                "no_images_for_analysis",
                property_id=merged.unique_id,
                sources=[s.value for s in merged.sources],
            )
            return merged, self._create_minimal_analysis(value=value)

        # Use best description from descriptions dict
        best_description: str | None = None
        for desc in merged.descriptions.values():
            if desc and (not best_description or len(desc) > len(best_description)):
                best_description = desc

        try:
            analysis = await self._analyze_property(
                merged.unique_id,
                gallery_urls=gallery_urls[: self._max_images],
                floorplan_url=floorplan_url,
                bedrooms=prop.bedrooms,
                price_pcm=prop.price_pcm,
                area_average=value.area_average,
                description=best_description,
                features=None,
                area_context=ctx.area_overview,
                outcode=ctx.outcode,
                council_tax_band_c=ctx.council_tax_band_c,
                energy_estimate=ctx.energy_estimate,
                crime_summary=ctx.crime_summary,
                rent_trend=ctx.rent_trend,
                hosting_tolerance=ctx.hosting_tolerance,
                gallery_cached_paths=gallery_cached[: self._max_images],
                floorplan_cached_path=floorplan_cached,
                data_dir=data_dir,
            )
        except APIUnavailableError:
            raise  # Propagate to caller for circuit breaker handling
        except Exception:
            logger.error(
                "analyze_single_merged_failed",
                property_id=merged.unique_id,
                exc_info=True,
            )
            return merged, self._create_minimal_analysis(value=value)

        if analysis:
            merged_value = ValueAnalysis(
                area_average=value.area_average,
                difference=value.difference,
                rating=value.rating,
                note=value.note,
                quality_adjusted_rating=analysis.value.quality_adjusted_rating
                if analysis.value
                else None,
                quality_adjusted_note=analysis.value.quality_adjusted_note
                if analysis.value
                else "",
            )
            analysis = PropertyQualityAnalysis(
                kitchen=analysis.kitchen,
                condition=analysis.condition,
                light_space=analysis.light_space,
                space=analysis.space,
                bathroom=analysis.bathroom,
                bedroom=analysis.bedroom,
                outdoor_space=analysis.outdoor_space,
                storage=analysis.storage,
                flooring_noise=analysis.flooring_noise,
                listing_extraction=analysis.listing_extraction,
                listing_red_flags=analysis.listing_red_flags,
                viewing_notes=analysis.viewing_notes,
                highlights=analysis.highlights,
                lowlights=analysis.lowlights,
                one_line=analysis.one_line,
                condition_concerns=analysis.condition_concerns,
                concern_severity=analysis.concern_severity,
                value=merged_value,
                overall_rating=analysis.overall_rating,
                summary=analysis.summary,
            )
            return merged, analysis
        else:
            return merged, self._create_minimal_analysis(value=value)

    async def analyze_merged_properties(
        self,
        properties: list[MergedProperty],
        *,
        data_dir: str | None = None,
    ) -> list[tuple[MergedProperty, PropertyQualityAnalysis]]:
        """Analyze quality for pre-enriched merged properties.

        Properties should already have images and floorplan populated
        by the detail enrichment step.

        Args:
            properties: Enriched merged properties to analyze.
            data_dir: Data directory for image cache. When set, reads
                cached images from disk instead of downloading.

        Returns:
            List of (merged_property, analysis) tuples.
        """
        results: list[tuple[MergedProperty, PropertyQualityAnalysis]] = []

        for merged in properties:
            results.append(await self.analyze_single_merged(merged, data_dir=data_dir))
            await asyncio.sleep(DELAY_BETWEEN_CALLS)

        return results

    def _create_minimal_analysis(
        self, value: ValueAnalysis | None = None
    ) -> PropertyQualityAnalysis:
        """Create a minimal analysis for properties without images."""
        return PropertyQualityAnalysis(
            kitchen=KitchenAnalysis(notes="No images available for analysis"),
            condition=ConditionAnalysis(
                overall_condition="unknown",
                confidence="low",
            ),
            light_space=LightSpaceAnalysis(
                natural_light="unknown",
                feels_spacious=None,
                notes="No images available for analysis",
            ),
            space=SpaceAnalysis(
                is_spacious_enough=None,
                confidence="low",
            ),
            condition_concerns=False,
            value=value,
            summary="No images available for quality analysis",
        )

    async def _run_visual_analysis(
        self,
        content: list[Any],
        property_id: str,
        *,
        data_dir: str | None = None,
    ) -> dict[str, Any] | None:
        """Run Phase 1 visual analysis API call.

        Args:
            content: Image and text content blocks for the API.
            property_id: Property ID for logging.
            data_dir: Data directory for image cache (used to clear cache on bad request).

        Returns:
            Raw visual analysis dict, or None if the call failed.

        Raises:
            APIUnavailableError: If circuit breaker trips (outage-indicating errors).
        """
        from anthropic import (
            APIConnectionError,
            APIStatusError,
            BadRequestError,
            InternalServerError,
            RateLimitError,
        )
        from anthropic.types import ToolParam, ToolUseBlock

        client = self._get_client()

        try:
            visual_tool: ToolParam = VISUAL_ANALYSIS_TOOL  # type: ignore[assignment]

            create_kwargs: dict[str, Any] = {
                "model": "claude-sonnet-4-5-20250929",
                "max_tokens": 16384,
                "system": [
                    {
                        "type": "text",
                        "text": VISUAL_ANALYSIS_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                "messages": [{"role": "user", "content": content}],
                "tools": [visual_tool],
            }

            if self._enable_extended_thinking:
                create_kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": self._thinking_budget_tokens,
                }
                create_kwargs["tool_choice"] = {"type": "auto"}
            else:
                create_kwargs["tool_choice"] = {
                    "type": "tool",
                    "name": "property_visual_analysis",
                }

            response = await client.messages.create(**create_kwargs)

            if hasattr(response, "usage"):
                usage = response.usage
                cache_read = getattr(usage, "cache_read_input_tokens", 0)
                cache_creation = getattr(usage, "cache_creation_input_tokens", 0)
                if cache_read or cache_creation:
                    logger.debug(
                        "prompt_cache_stats",
                        property_id=property_id,
                        phase="visual",
                        cache_read_tokens=cache_read,
                        cache_creation_tokens=cache_creation,
                    )

            tool_use_block = next(
                (block for block in response.content if isinstance(block, ToolUseBlock)),
                None,
            )
            if not tool_use_block:
                logger.warning(
                    "no_tool_use_in_response",
                    property_id=property_id,
                    phase="visual",
                    stop_reason=response.stop_reason,
                )
                return None

            self._record_api_success()
            return tool_use_block.input

        except BadRequestError as e:
            logger.warning(
                "visual_analysis_bad_request",
                property_id=property_id,
                error=str(e),
                request_id=getattr(e, "_request_id", None),
            )
            err_msg = str(e)
            if (
                "Could not process image" in err_msg
                or "file format is invalid" in err_msg
            ) and data_dir:
                from home_finder.utils.image_cache import clear_image_cache

                clear_image_cache(data_dir, property_id)
                logger.warning(
                    "image_cache_cleared_on_bad_request",
                    property_id=property_id,
                )
            return None

        except (RateLimitError, InternalServerError, APIConnectionError) as e:
            self._record_api_failure()
            log_event = {
                RateLimitError: "rate_limit_exhausted",
                InternalServerError: "server_error",
                APIConnectionError: "connection_error",
            }[type(e)]
            logger.error(
                log_event,
                property_id=property_id,
                phase="visual",
                error=str(e),
                request_id=getattr(e, "_request_id", None)
                if not isinstance(e, APIConnectionError)
                else None,
            )
            raise APIUnavailableError(str(e)) from e

        except APIStatusError as e:
            logger.warning(
                "api_status_error",
                property_id=property_id,
                phase="visual",
                status_code=e.status_code,
                error=str(e),
                request_id=getattr(e, "_request_id", None),
            )
            return None

        except Exception as e:
            logger.warning(
                "visual_analysis_failed",
                property_id=property_id,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True,
            )
            return None

    async def _run_evaluation(
        self,
        visual_data: dict[str, Any],
        property_id: str,
        *,
        description: str | None,
        price_pcm: int,
        bedrooms: int,
        effective_average: int,
        area_context: str | None,
        outcode: str | None,
        council_tax_band_c: float | None,
        crime_summary: str | None,
        rent_trend: str | None,
        energy_estimate: float | None,
        hosting_tolerance: str | None,
    ) -> dict[str, Any]:
        """Run Phase 2 evaluation API call.

        Args:
            visual_data: Phase 1 visual analysis output.
            property_id: Property ID for logging.
            description: Listing description text.
            price_pcm: Monthly rent price.
            bedrooms: Number of bedrooms.
            effective_average: Area average rent for comparison.
            area_context: Area context string.
            outcode: Property outcode.
            council_tax_band_c: Monthly council tax estimate.
            crime_summary: Crime rate summary.
            rent_trend: Rent trend string.
            energy_estimate: Energy cost estimate.
            hosting_tolerance: Hosting tolerance string.

        Returns:
            Evaluation data dict (empty if Phase 2 failed).
        """
        from anthropic.types import ToolParam, ToolUseBlock

        client = self._get_client()

        # Map building_construction from Phase 1 to acoustic profile for Phase 2
        acoustic_context: str | None = None
        flooring_raw = visual_data.get("flooring_noise")
        construction: str | None = (
            flooring_raw.get("building_construction") if isinstance(flooring_raw, dict) else None
        )
        if construction:
            profile_key = _CONSTRUCTION_TO_PROFILE.get(construction)
            if profile_key:
                profile = ACOUSTIC_PROFILES.get(profile_key)
                if profile:
                    db_range = profile["airborne_insulation_db"]
                    acoustic_context = (
                        f"Building construction: {construction}\n"
                        f"Typical sound insulation: {db_range} dB airborne\n"
                        f"Hosting safety: {profile['hosting_safety']}\n"
                        f"{profile['summary']}"
                    )

        eval_data: dict[str, Any] = {}
        try:
            eval_tool: ToolParam = EVALUATION_TOOL  # type: ignore[assignment]

            eval_prompt = build_evaluation_prompt(
                visual_data=visual_data,
                description=description,
                price_pcm=price_pcm,
                bedrooms=bedrooms,
                area_average=effective_average,
                area_context=area_context,
                outcode=outcode,
                council_tax_band_c=council_tax_band_c,
                crime_summary=crime_summary,
                rent_trend=rent_trend,
                energy_estimate=energy_estimate,
                hosting_tolerance=hosting_tolerance,
                acoustic_context=acoustic_context,
            )

            eval_response = await client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=4096,
                system=[
                    {
                        "type": "text",
                        "text": EVALUATION_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": eval_prompt}],
                tools=[eval_tool],
                tool_choice={"type": "tool", "name": "property_evaluation"},
            )

            eval_block = next(
                (block for block in eval_response.content if isinstance(block, ToolUseBlock)),
                None,
            )
            if eval_block:
                eval_data = eval_block.input
            else:
                logger.warning(
                    "no_tool_use_in_response",
                    property_id=property_id,
                    phase="evaluation",
                    stop_reason=eval_response.stop_reason,
                )

        except Exception as e:
            logger.warning(
                "evaluation_phase_failed",
                property_id=property_id,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True,
            )

        logger.info(
            "evaluation_complete",
            property_id=property_id,
            has_eval=bool(eval_data),
        )

        return eval_data

    @staticmethod
    def _merge_analysis_results(
        visual_data: dict[str, Any],
        eval_data: dict[str, Any],
        bedrooms: int,
        property_id: str,
    ) -> PropertyQualityAnalysis | None:
        """Merge Phase 1 + Phase 2 results into a PropertyQualityAnalysis.

        Cleans response data, validates with Pydantic, and applies post-processing
        (e.g. 2+ bed space override).

        Args:
            visual_data: Phase 1 visual analysis output.
            eval_data: Phase 2 evaluation output.
            bedrooms: Number of bedrooms (for space assessment override).
            property_id: Property ID for logging.

        Returns:
            Validated analysis, or None if validation failed.
        """
        visual_data = _clean_dict(visual_data)
        eval_data = _clean_dict(eval_data)

        # Extract value_for_quality from Phase 2 response
        value_for_quality = eval_data.pop("value_for_quality", {})
        quality_adjusted_rating = value_for_quality.get("rating")
        quality_adjusted_note = value_for_quality.get("reasoning", "")

        value = ValueAnalysis(
            quality_adjusted_rating=quality_adjusted_rating,
            quality_adjusted_note=quality_adjusted_note,
        )

        try:
            merged_data = {**visual_data, **eval_data, "value": value}
            merged_data.setdefault("summary", "Analysis completed")
            analysis = PropertyQualityAnalysis.model_validate(merged_data)
        except Exception as e:
            logger.warning(
                "analysis_validation_failed",
                property_id=property_id,
                error=str(e),
                exc_info=True,
            )
            return None

        # For 2+ bed properties, override space assessment
        # (office can go in spare room)
        if bedrooms >= 2 and not analysis.space.is_spacious_enough:
            analysis = analysis.model_copy(
                update={
                    "space": SpaceAnalysis(
                        living_room_sqm=analysis.space.living_room_sqm,
                        is_spacious_enough=True,
                        confidence="high",
                        hosting_layout=analysis.space.hosting_layout,
                    ),
                },
            )

        return analysis

    async def _analyze_property(
        self,
        property_id: str,
        *,
        gallery_urls: list[str],
        floorplan_url: str | None,
        bedrooms: int,
        price_pcm: int,
        area_average: int | None,
        description: str | None = None,
        features: list[str] | None = None,
        area_context: str | None = None,
        outcode: str | None = None,
        council_tax_band_c: float | None = None,
        energy_estimate: float | None = None,
        crime_summary: str | None = None,
        rent_trend: str | None = None,
        hosting_tolerance: str | None = None,
        gallery_cached_paths: list[Path | None] | None = None,
        floorplan_cached_path: Path | None = None,
        data_dir: str | None = None,
    ) -> PropertyQualityAnalysis | None:
        """Analyze a single property using two-phase chained Claude analysis.

        Phase 1 (Visual): Images + text → structured observations (extended thinking)
        Phase 2 (Evaluation): Phase 1 output + text → value/viewing/curation (text-only)

        Args:
            property_id: Property ID for logging.
            gallery_urls: List of gallery image URLs.
            floorplan_url: Floorplan URL if available.
            bedrooms: Number of bedrooms (for space assessment).
            price_pcm: Monthly rent price.
            area_average: Average rent for this area and bedroom count.
            description: Listing description text for cross-reference.
            features: Listed features for cross-reference.
            area_context: Area context string for the outcode.
            outcode: Property outcode (e.g., "E8").
            council_tax_band_c: Estimated monthly council tax (Band C).
            crime_summary: Crime rate summary string.
            rent_trend: YoY rent trend string.
            hosting_tolerance: Area hosting tolerance summary string.
            gallery_cached_paths: Paths to cached gallery images on disk.
            floorplan_cached_path: Path to cached floorplan image on disk.
            data_dir: Data directory for image cache (passed to error handlers).

        Returns:
            Analysis result or None if Phase 1 failed.
        """
        from anthropic.types import ImageBlockParam, TextBlockParam

        # Fast-fail if circuit breaker is open (with half-open recovery after cooldown)
        if self._is_circuit_open():
            raise APIUnavailableError("Circuit breaker is open — API unavailable")

        # Build image content blocks
        content: list[ImageBlockParam | TextBlockParam] = []

        has_floorplan = floorplan_url is not None and is_valid_image_url(floorplan_url)
        effective_max = self._max_images - (1 if has_floorplan else 0)

        gallery_num = 0
        cached_paths = gallery_cached_paths or [None] * len(gallery_urls)
        for idx, url in enumerate(gallery_urls[:effective_max]):
            cached = cached_paths[idx] if idx < len(cached_paths) else None
            image_block = await self._build_image_block(url, cached_path=cached)
            if image_block:
                gallery_num += 1
                content.append(TextBlockParam(type="text", text=f"Gallery image {gallery_num}:"))
                content.append(image_block)

        if floorplan_url and is_valid_image_url(floorplan_url):
            floorplan_block = await self._build_image_block(
                floorplan_url, cached_path=floorplan_cached_path
            )
            if floorplan_block:
                content.append(TextBlockParam(type="text", text="Floorplan:"))
                content.append(floorplan_block)
        elif floorplan_url:
            logger.debug("skipping_pdf_floorplan", url=floorplan_url)

        effective_average = area_average or DEFAULT_BENCHMARK.get(min(bedrooms, 3), 1800)
        user_prompt = build_user_prompt(
            price_pcm,
            bedrooms,
            effective_average,
            description,
            features,
            area_context=area_context,
            outcode=outcode,
            council_tax_band_c=council_tax_band_c,
            crime_summary=crime_summary,
            rent_trend=rent_trend,
            energy_estimate=energy_estimate,
            hosting_tolerance=hosting_tolerance,
            has_labeled_floorplan=has_floorplan,
        )
        content.append(TextBlockParam(type="text", text=user_prompt))

        has_usable_floorplan = floorplan_url is not None and is_valid_image_url(floorplan_url)
        image_blocks = [c for c in content if hasattr(c, "get") and c.get("type") == "image"]
        logger.info(
            "analyzing_property",
            property_id=property_id,
            gallery_count=len(gallery_urls),
            has_floorplan=has_usable_floorplan,
            image_blocks_sent=len(image_blocks),
            total_content_blocks=len(content),
        )

        # Phase 1: Visual Analysis
        visual_data = await self._run_visual_analysis(
            content, property_id, data_dir=data_dir
        )
        if visual_data is None:
            return None

        logger.info("visual_analysis_complete", property_id=property_id)

        detected_indices = visual_data.get("floorplan_detected_in_gallery", [])
        if detected_indices:
            logger.info(
                "floorplan_detected_by_claude",
                property_id=property_id,
                indices=detected_indices,
            )

        # Phase 2: Evaluation
        eval_data = await self._run_evaluation(
            visual_data,
            property_id,
            description=description,
            price_pcm=price_pcm,
            bedrooms=bedrooms,
            effective_average=effective_average,
            area_context=area_context,
            outcode=outcode,
            council_tax_band_c=council_tax_band_c,
            crime_summary=crime_summary,
            rent_trend=rent_trend,
            energy_estimate=energy_estimate,
            hosting_tolerance=hosting_tolerance,
        )

        # Phase 3: Merge and validate
        return self._merge_analysis_results(visual_data, eval_data, bedrooms, property_id)

    async def close(self) -> None:
        """Close clients."""
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self._curl_session is not None:
            await self._curl_session.close()
            self._curl_session = None
