"""Detail page fetcher for extracting gallery and floorplan URLs."""

import asyncio
import io
import json
import re
from dataclasses import dataclass
from typing import Any, NamedTuple, assert_never

import httpx
from curl_cffi import CurlError
from curl_cffi.requests import AsyncSession
from PIL import Image
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)

from home_finder.logging import get_logger
from home_finder.models import SQM_PER_SQFT, Property, PropertySource
from home_finder.scrapers.constants import BROWSER_HEADERS
from home_finder.scrapers.retry import RetryableHttpError
from home_finder.utils.circuit_breaker import ConsecutiveFailureBreaker
from home_finder.utils.image_cache import is_valid_image_bytes

_MAX_RETRIES = 2  # cross-run retry handles persistent failures
_FLOOR_AREA_MIN_SQFT = 100
_FLOOR_AREA_MAX_SQFT = 5000
_RETRY_BASE_DELAY = 2.0  # seconds, doubled each retry (2, 4)
_ZOOPLA_MIN_INTERVAL = 3.0  # seconds between Zoopla detail page requests
_OTM_MIN_INTERVAL = 0.3  # seconds between OnTheMarket requests (less aggressive)
_OPENRENT_MIN_INTERVAL = 0.3  # seconds between OpenRent detail requests

# Per-CDN image throttle intervals (seconds).
# zoocdn is the most aggressive at IP-blocking; OTM and OpenRent less so.
_ZOOCDN_IMAGE_MIN_INTERVAL = 0.5
_OTM_IMAGE_MIN_INTERVAL = 0.3
_OPENRENT_IMAGE_MIN_INTERVAL = 0.3

# Image download timeout (seconds).  CDN images respond in <1s when healthy;
# a 15s cap avoids grinding through 30s timeouts when the CDN is blocking.
_IMAGE_TIMEOUT = 15

# Circuit-breaker: trip after this many consecutive timeout/connection failures.
_IMAGE_CB_THRESHOLD = 3

# Map CDN domain fragments → (throttle_name, interval) for image downloads.
_CDN_THROTTLE_CONFIG: dict[str, tuple[str, float]] = {
    "zoocdn.com": ("img_zoocdn", _ZOOCDN_IMAGE_MIN_INTERVAL),
    "onthemarket.com": ("img_otm", _OTM_IMAGE_MIN_INTERVAL),
    "imagescdn.openrent.co.uk": ("img_openrent", _OPENRENT_IMAGE_MIN_INTERVAL),
}

# curl error codes that indicate CDN-level blocking (timeouts / connection drops).
_BREAKER_CURL_CODES = frozenset({7, 28, 55, 56})

logger = get_logger(__name__)

# Substrings in image URLs that indicate EPC (Energy Performance Certificate) charts
_EPC_URL_MARKERS = ("epc", "energy-performance", "energy_performance")


def _is_epc_url(url: str) -> bool:
    """Check if a URL likely points to an EPC chart image."""
    url_lower = url.lower()
    return any(marker in url_lower for marker in _EPC_URL_MARKERS)


_VIDEO_URL_MARKERS = ("youtube.com/", "youtu.be/", "vimeo.com/", "dailymotion.com/")


def _is_video_url(url: str) -> bool:
    """Check if a URL points to a video embed rather than an image."""
    url_lower = url.lower()
    return any(marker in url_lower for marker in _VIDEO_URL_MARKERS)


def _find_dict_with_key(data: Any, key: str, depth: int = 0) -> dict[str, Any] | None:
    """Recursively find a dict containing the given key."""
    if depth > 10:
        return None
    if isinstance(data, dict):
        if key in data:
            return data
        for v in data.values():
            r = _find_dict_with_key(v, key, depth + 1)
            if r:
                return r
    elif isinstance(data, list):
        for item in data:
            r = _find_dict_with_key(item, key, depth + 1)
            if r:
                return r
    return None


# ---------------------------------------------------------------------------
# Zoopla detail page extraction helpers (pure functions, no self dependency)
# ---------------------------------------------------------------------------


class _NextDataResult(NamedTuple):
    """Structured result from __NEXT_DATA__ extraction."""

    gallery_urls: list[str]
    floorplan_url: str | None
    description: str | None
    features: list[str]


def _zoopla_from_next_data(html: str, max_gallery_images: int) -> _NextDataResult | None:
    """Extract gallery, floorplan, description, features from __NEXT_DATA__."""
    match = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        return None

    data = json.loads(match.group(1))
    listing = data.get("props", {}).get("pageProps", {}).get("listing", {})
    media = listing.get("propertyMedia", [])

    gallery_urls: list[str] = []
    floorplan_url: str | None = None
    for item in media:
        item_type = item.get("type")
        if item_type == "floorplan" and floorplan_url is None:
            floorplan_url = item.get("original")
        elif item_type == "image" and len(gallery_urls) < max_gallery_images:
            url = item.get("original")
            if url:
                gallery_urls.append(url)

    description = listing.get("detailedDescription")

    features: list[str] = []
    key_features = listing.get("keyFeatures", [])
    if key_features:
        features.extend(key_features)
    bullets = listing.get("bullets", [])
    if bullets:
        features.extend(bullets)
    tags = listing.get("tags", [])
    for tag in tags:
        if isinstance(tag, dict) and tag.get("label"):
            features.append(tag["label"])
        elif isinstance(tag, str):
            features.append(tag)

    return _NextDataResult(gallery_urls, floorplan_url, description, features)


def _zoopla_desc_from_rsc(html: str) -> tuple[str | None, list[str]]:
    """Extract description and features from RSC taxonomy payload.

    Returns (description, features).
    """
    rsc_pattern = r"self\.__next_f\.push\(\s*\[(.*?)\]\s*\)"
    for m in re.finditer(rsc_pattern, html, re.DOTALL):
        match_text = m.group(1)
        if "epcRating" not in match_text or "numBaths" not in match_text:
            continue
        try:
            arr = json.loads(f"[{match_text}]")
            if len(arr) >= 2 and isinstance(arr[1], str):
                payload = arr[1]
                colon_idx = payload.find(":")
                if colon_idx >= 0:
                    parsed = json.loads(payload[colon_idx + 1 :])
                    taxonomy = _find_dict_with_key(parsed, "epcRating")
                    if taxonomy:
                        desc = taxonomy.get("detailedDescription", "")
                        description: str | None = None
                        if desc and not desc.startswith("$"):
                            desc = re.sub(r"<[^>]+>", " ", desc)
                            description = re.sub(r"\s+", " ", desc).strip()
                        kf = taxonomy.get("keyFeatures", [])
                        features: list[str] = []
                        if isinstance(kf, list) and kf:
                            features = [f for f in kf if isinstance(f, str)]
                        return description, features
        except (json.JSONDecodeError, TypeError):
            continue
    return None, []


def _zoopla_desc_from_html(html: str) -> str | None:
    """Extract description from HTML <p id="detailed-desc"> tag."""
    desc_match = re.search(
        r'<p[^>]*id="detailed-desc"[^>]*>(.*?)</p>',
        html,
        re.DOTALL,
    )
    if desc_match:
        desc = re.sub(r"<[^>]+>", " ", desc_match.group(1))
        desc = re.sub(r"\s+", " ", desc).strip()
        if len(desc) > 20:
            return desc
    return None


def _zoopla_images_from_rsc_captions(
    html: str, max_gallery_images: int
) -> tuple[list[str], set[str]]:
    """Extract gallery URLs from RSC caption/filename pairs.

    Returns (gallery_urls, seen_hashes) where seen_hashes includes ALL
    encountered hashes, including EPC images that were filtered from the URL list.
    """
    seen_hashes: set[str] = set()
    gallery_urls: list[str] = []

    # Pre-populate seen_hashes with floorplan image hashes (lc.zoocdn.com).
    # This prevents floorplan images from appearing as gallery photos when they
    # also show up in the RSC caption/filename data with null caption.
    fp_hash_matches = re.findall(
        r"lc\.zoocdn\.com/([a-f0-9]+\.(?:jpg|jpeg|png|webp))",
        html,
        re.IGNORECASE,
    )
    for fp_filename in fp_hash_matches:
        seen_hashes.add(fp_filename.split(".")[0])

    # Match both quoted captions and null captions:
    #   \"caption\":\"Some text\",\"filename\":\"hash.jpg\"
    #   \"caption\":null,\"filename\":\"hash.jpg\"
    rsc_matches = re.findall(
        r'\\"caption\\":(?:\\"([^\\]*)\\"|null),\\"filename\\":\\"([a-f0-9]+\.(?:jpg|jpeg|png|webp))\\"',
        html,
        re.IGNORECASE,
    )
    for caption, filename in rsc_matches:
        hash_part = filename.split(".")[0]
        if hash_part in seen_hashes:
            continue
        seen_hashes.add(hash_part)
        # null caption (group is empty string from non-matching group) = gallery photo
        if caption is not None:
            caption_lower = caption.lower()
            # Skip EPC rating graphs and floorplans — not gallery images
            if (
                "epc" in caption_lower
                or "floorplan" in caption_lower
                or "ee rating" in caption_lower
            ):
                continue
        url = f"https://lid.zoocdn.com/u/1024/768/{filename}"
        gallery_urls.append(url)
        if len(gallery_urls) >= max_gallery_images:
            break
    return gallery_urls, seen_hashes


def _zoopla_images_from_full_urls(
    html: str,
    existing_gallery_urls: list[str],
    seen_hashes: set[str],
    max_gallery_images: int,
) -> list[str]:
    """Extract additional gallery URLs from full lid.zoocdn.com URLs in HTML.

    Returns additional URLs to append (not already in existing_gallery_urls or seen_hashes).
    """
    existing_hashes = {u.rsplit("/", 1)[-1].split(".")[0] for u in existing_gallery_urls}
    existing_hashes |= seen_hashes
    img_matches = re.findall(
        r"https://lid\.zoocdn\.com/u/(\d+)/(\d+)/([a-f0-9]+\.(?:jpg|jpeg|png|webp))",
        html,
        re.IGNORECASE,
    )
    seen_url_hashes: dict[str, tuple[int, str]] = {}
    for width, height, filename in img_matches:
        hash_part = filename.split(".")[0]
        if hash_part in existing_hashes:
            continue
        size = int(width) * int(height)
        if hash_part not in seen_url_hashes or size > seen_url_hashes[hash_part][0]:
            seen_url_hashes[hash_part] = (
                size,
                f"https://lid.zoocdn.com/u/{width}/{height}/{filename}",
            )

    remaining = max_gallery_images - len(existing_gallery_urls)
    sorted_imgs = sorted(seen_url_hashes.values(), key=lambda x: -x[0])
    return [url for _, url in sorted_imgs[:remaining]]


def _zoopla_size_from_rsc(html: str) -> int | None:
    """Extract sizeSqft from RSC taxonomy payload.

    Returns floor area in sqft, or None if not found/invalid.
    """
    rsc_pattern = r"self\.__next_f\.push\(\s*\[(.*?)\]\s*\)"
    for m in re.finditer(rsc_pattern, html, re.DOTALL):
        match_text = m.group(1)
        if "sizeSqft" not in match_text:
            continue
        try:
            arr = json.loads(f"[{match_text}]")
            if len(arr) >= 2 and isinstance(arr[1], str):
                payload = arr[1]
                colon_idx = payload.find(":")
                if colon_idx >= 0:
                    parsed = json.loads(payload[colon_idx + 1 :])
                    taxonomy = _find_dict_with_key(parsed, "sizeSqft")
                    if taxonomy:
                        raw = taxonomy.get("sizeSqft")
                        if (
                            isinstance(raw, (int, float))
                            and _FLOOR_AREA_MIN_SQFT <= raw <= _FLOOR_AREA_MAX_SQFT
                        ):
                            return int(raw)
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def _zoopla_floorplan_from_html(html: str) -> str | None:
    """Extract floorplan URL from lc.zoocdn.com references in HTML."""
    # Try extension-based match first (more specific)
    match = re.search(
        r'(https://lc\.zoocdn\.com/[^\s"\']+\.(?:jpg|jpeg|png|gif|webp))',
        html,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)
    # Fallback: extension-less URL with "floor" in path
    match = re.search(
        r'(https://lc\.zoocdn\.com/[^\s"\']*floor[^\s"\']*)',
        html,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


@dataclass
class DetailPageData:
    """Data extracted from a property detail page."""

    floorplan_url: str | None = None
    gallery_urls: list[str] | None = None
    description: str | None = None
    features: list[str] | None = None  # Key features like "Gas central heating"
    latitude: float | None = None
    longitude: float | None = None
    postcode: str | None = None
    floor_area_sqm: float | None = None
    floor_area_source: str | None = None  # "rightmove" | "zoopla" | "onthemarket"


class DetailFetcher:
    """Fetches property detail pages and extracts floorplan/gallery URLs."""

    def __init__(self, max_gallery_images: int = 10, *, proxy_url: str = "") -> None:
        """Initialize the detail fetcher.

        Args:
            max_gallery_images: Maximum number of gallery images to extract.
            proxy_url: HTTP/SOCKS5 proxy URL for geo-restricted sites.
        """
        self._client: httpx.AsyncClient | None = None
        self._curl_session: AsyncSession | None = None  # type: ignore[type-arg]
        self._max_gallery_images = max_gallery_images
        self._proxy_url = proxy_url
        # Per-purpose throttles: Zoopla detail pages are heavily rate-limited,
        # OTM less so.  Image CDNs get their own per-CDN throttles.
        self._zoopla_lock = asyncio.Lock()
        self._zoopla_next_time: float = 0.0
        self._otm_lock = asyncio.Lock()
        self._otm_next_time: float = 0.0
        self._openrent_lock = asyncio.Lock()
        self._openrent_next_time: float = 0.0
        # Per-CDN image throttles
        self._img_zoocdn_lock = asyncio.Lock()
        self._img_zoocdn_next_time: float = 0.0
        self._img_otm_lock = asyncio.Lock()
        self._img_otm_next_time: float = 0.0
        self._img_openrent_lock = asyncio.Lock()
        self._img_openrent_next_time: float = 0.0
        # Per-CDN circuit breakers (created lazily via _get_image_breaker)
        self._image_breakers: dict[str, ConsecutiveFailureBreaker] = {}
        self._image_skip_counts: dict[str, int] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                },
            )
        return self._client

    async def _get_curl_session(self) -> AsyncSession:  # type: ignore[type-arg]
        """Get or create a reusable curl_cffi session for anti-bot sites."""
        if self._curl_session is None:
            self._curl_session = AsyncSession()
        return self._curl_session

    async def _httpx_get_with_retry(self, url: str) -> httpx.Response:
        """GET with retry on 429 Too Many Requests."""
        client = await self._get_client()

        @retry(
            stop=stop_after_attempt(_MAX_RETRIES),
            wait=wait_exponential(multiplier=2, min=2, max=8),
            retry=retry_if_exception_type(RetryableHttpError),
            reraise=True,
        )
        async def _do_get() -> httpx.Response:
            response = await client.get(url)
            if response.status_code == 429:
                raise RetryableHttpError(response.status_code, url)
            response.raise_for_status()
            return response

        try:
            return await _do_get()
        except RetryableHttpError:
            # Final 429 after exhaustion — raise as httpx error for caller
            response = await client.get(url)
            response.raise_for_status()
            return response  # unreachable but satisfies type checker

    async def _throttle(self, lock: asyncio.Lock, attr: str, interval: float) -> None:
        """Ensure minimum interval between requests for a specific throttle."""
        async with lock:
            now = asyncio.get_event_loop().time()
            next_time = getattr(self, attr)
            wait = next_time - now
            if wait > 0:
                await asyncio.sleep(wait)
            setattr(self, attr, asyncio.get_event_loop().time() + interval)

    async def _curl_get_with_retry(
        self,
        url: str,
        *,
        min_interval: float = _ZOOPLA_MIN_INTERVAL,
        throttle_name: str | None = None,
        timeout: int = 30,
    ) -> Any:
        """GET with retry on 429 for curl_cffi session.

        Args:
            url: URL to fetch.
            min_interval: Minimum seconds between requests for this throttle.
            throttle_name: Explicit throttle bucket name. When provided, selects
                the lock directly instead of inferring from min_interval.
            timeout: Request timeout in seconds (default 30).
        """
        if throttle_name == "img_zoocdn":
            lock, attr = self._img_zoocdn_lock, "_img_zoocdn_next_time"
        elif throttle_name == "img_otm":
            lock, attr = self._img_otm_lock, "_img_otm_next_time"
        elif throttle_name == "img_openrent":
            lock, attr = self._img_openrent_lock, "_img_openrent_next_time"
        elif throttle_name == "openrent":
            lock, attr = self._openrent_lock, "_openrent_next_time"
        elif min_interval >= _ZOOPLA_MIN_INTERVAL:
            lock, attr = self._zoopla_lock, "_zoopla_next_time"
        elif min_interval >= _OTM_MIN_INTERVAL:
            lock, attr = self._otm_lock, "_otm_next_time"
        else:
            raise ValueError(
                f"No throttle bucket for throttle_name={throttle_name!r}, "
                f"min_interval={min_interval}"
            )

        session = await self._get_curl_session()
        kwargs: dict[str, object] = {
            "impersonate": "chrome",
            "headers": BROWSER_HEADERS,
            "timeout": timeout,
        }
        if self._proxy_url:
            kwargs["proxy"] = self._proxy_url

        last_response: Any = None

        @retry(
            stop=stop_after_attempt(_MAX_RETRIES),
            wait=wait_exponential(multiplier=2, min=2, max=8) + wait_random(0, 1),
            retry=retry_if_exception_type(RetryableHttpError),
            reraise=True,
        )
        async def _do_get() -> Any:
            nonlocal last_response
            await self._throttle(lock, attr, min_interval)
            last_response = await session.get(url, **kwargs)  # type: ignore[arg-type]
            if last_response.status_code == 429:
                raise RetryableHttpError(last_response.status_code, url)
            return last_response

        try:
            return await _do_get()
        except RetryableHttpError:
            logger.warning("rate_limit_retries_exhausted", url=url, attempts=_MAX_RETRIES)
            return last_response

    async def fetch_floorplan_url(self, prop: Property) -> str | None:
        """Fetch detail page and extract floorplan URL.

        Args:
            prop: Property to fetch floorplan for.

        Returns:
            Floorplan URL or None if not found.
        """
        data = await self.fetch_detail_page(prop)
        return data.floorplan_url if data else None

    async def fetch_detail_page(self, prop: Property) -> DetailPageData | None:
        """Fetch detail page and extract floorplan and gallery URLs.

        Args:
            prop: Property to fetch details for.

        Returns:
            DetailPageData with floorplan and gallery URLs, or None on failure.
        """
        match prop.source:
            case PropertySource.RIGHTMOVE:
                return await self._fetch_rightmove(prop)
            case PropertySource.ZOOPLA:
                return await self._fetch_zoopla(prop)
            case PropertySource.OPENRENT:
                return await self._fetch_openrent(prop)
            case PropertySource.ONTHEMARKET:
                return await self._fetch_onthemarket(prop)
            case _ as unreachable:
                assert_never(unreachable)

    async def _fetch_rightmove(self, prop: Property) -> DetailPageData | None:
        """Extract floorplan and gallery URLs from Rightmove detail page."""
        try:
            response = await self._httpx_get_with_retry(str(prop.url))
            html = response.text

            # Find PAGE_MODEL JSON start
            start_match = re.search(r"window\.PAGE_MODEL\s*=\s*", html)
            if not start_match:
                logger.debug("no_page_model", property_id=prop.unique_id)
                return None

            # Extract JSON using brace counting (handles nested objects)
            start_idx = start_match.end()
            depth = 0
            end_idx = start_idx
            for i, char in enumerate(html[start_idx:]):
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        end_idx = start_idx + i + 1
                        break

            json_str = html[start_idx:end_idx]
            data = json.loads(json_str)
            property_data = data.get("propertyData", {})

            # Extract floorplan
            floorplan_url: str | None = None
            floorplans = property_data.get("floorplans", [])
            if floorplans and floorplans[0].get("url"):
                floorplan_url = floorplans[0]["url"]

            # Extract gallery images
            gallery_urls: list[str] = []
            images = property_data.get("images", [])
            for img in images[: self._max_gallery_images]:
                url = img.get("url", "")
                if url and not _is_epc_url(url):
                    gallery_urls.append(url)

            # Extract description
            description = property_data.get("text", {}).get("description")

            # Extract key features
            features: list[str] = []
            key_features = property_data.get("keyFeatures", [])
            if key_features:
                features.extend(key_features)

            # Extract location coordinates
            latitude: float | None = None
            longitude: float | None = None
            location = property_data.get("location", {})
            lat_raw = location.get("latitude")
            lng_raw = location.get("longitude")
            if lat_raw is not None and lng_raw is not None:
                latitude = float(lat_raw)
                longitude = float(lng_raw)

            # Extract full postcode from address data
            postcode: str | None = None
            address_data = property_data.get("address", {})
            outcode = address_data.get("outcode", "")
            incode = address_data.get("incode", "")
            if outcode and incode:
                postcode = f"{outcode} {incode}"

            # Extract floor area from sizings (validate in sqft, convert to sqm)
            floor_area_sqm: float | None = None
            sizings = property_data.get("sizings", [])
            for sizing in sizings:
                if sizing.get("unit") == "sqft":
                    raw = sizing.get("maximumSize") or sizing.get("minimumSize")
                    if (
                        isinstance(raw, (int, float))
                        and _FLOOR_AREA_MIN_SQFT <= raw <= _FLOOR_AREA_MAX_SQFT
                    ):
                        floor_area_sqm = round(raw * SQM_PER_SQFT, 1)
                        break

            return DetailPageData(
                floorplan_url=floorplan_url,
                gallery_urls=gallery_urls if gallery_urls else None,
                description=description,
                features=features if features else None,
                latitude=latitude,
                longitude=longitude,
                postcode=postcode,
                floor_area_sqm=floor_area_sqm,
                floor_area_source="rightmove" if floor_area_sqm else None,
            )

        except Exception as e:
            logger.warning(
                "rightmove_fetch_failed",
                property_id=prop.unique_id,
                error=str(e),
            )
            return None

    async def _fetch_zoopla(self, prop: Property) -> DetailPageData | None:
        """Extract floorplan and gallery URLs from Zoopla detail page.

        Uses curl_cffi with Chrome TLS fingerprint impersonation to bypass
        Zoopla's bot detection.
        """
        try:
            response = await self._curl_get_with_retry(str(prop.url))
            if response.status_code != 200:
                logger.warning(
                    "zoopla_http_error",
                    property_id=prop.unique_id,
                    status=response.status_code,
                )
                return None
            html: str = response.text
            max_imgs = self._max_gallery_images

            floorplan_url: str | None = None
            gallery_urls: list[str] = []
            description: str | None = None
            features: list[str] = []
            seen_hashes: set[str] = set()

            next_data = _zoopla_from_next_data(html, max_imgs)
            if next_data:
                gallery_urls, floorplan_url, description, features = next_data

            if not description:
                rsc_desc, rsc_feats = _zoopla_desc_from_rsc(html)
                description = rsc_desc
                if rsc_feats and not features:
                    features = rsc_feats

            if not description:
                description = _zoopla_desc_from_html(html)

            if not gallery_urls:
                gallery_urls, seen_hashes = _zoopla_images_from_rsc_captions(html, max_imgs)

            if len(gallery_urls) < 3:
                gallery_urls.extend(
                    _zoopla_images_from_full_urls(html, gallery_urls, seen_hashes, max_imgs)
                )

            if not floorplan_url:
                floorplan_url = _zoopla_floorplan_from_html(html)

            # Extract floor area from RSC payload (sqft), convert to sqm
            raw_sqft = _zoopla_size_from_rsc(html)
            floor_area_sqm = round(raw_sqft * SQM_PER_SQFT, 1) if raw_sqft else None

            return DetailPageData(
                floorplan_url=floorplan_url,
                gallery_urls=gallery_urls if gallery_urls else None,
                description=description,
                features=features if features else None,
                floor_area_sqm=floor_area_sqm,
                floor_area_source="zoopla" if floor_area_sqm else None,
            )

        except Exception as e:
            logger.warning("zoopla_fetch_failed", property_id=prop.unique_id, error=str(e))
            return None

    async def _fetch_openrent(self, prop: Property) -> DetailPageData | None:
        """Extract floorplan and gallery URLs from OpenRent detail page."""
        try:
            response = await self._curl_get_with_retry(
                str(prop.url),
                min_interval=_OPENRENT_MIN_INTERVAL,
                throttle_name="openrent",
            )
            if response.status_code != 200:
                logger.warning(
                    "openrent_http_error",
                    property_id=prop.unique_id,
                    status=response.status_code,
                )
                return None
            html: str = response.text

            # Check if we got redirected to homepage (property no longer available)
            if "/properties-to-rent" in str(response.url) or "homepage" in html.lower()[:1000]:
                logger.debug("openrent_property_unavailable", property_id=prop.unique_id)
                return None

            # Extract floorplan - look for floorplan images in the carousel
            floorplan_url: str | None = None
            floorplan_match = re.search(
                r'href="(//imagescdn\.openrent\.co\.uk/[^"]*floorplan[^"]*)"',
                html,
                re.IGNORECASE,
            )
            if floorplan_match:
                url = floorplan_match.group(1)
                floorplan_url = f"https:{url}" if url.startswith("//") else url

            # Extract gallery images from PhotoSwipe lightbox (new structure)
            # OpenRent now uses class="lightbox_item" with data-pswp-* attributes
            gallery_urls: list[str] = []

            # Pattern 1: PhotoSwipe lightbox items (current structure)
            gallery_matches = re.findall(
                r'<a[^>]*href="([^"]+)"[^>]*class="[^"]*lightbox_item[^"]*"',
                html,
                re.IGNORECASE,
            )
            for url in gallery_matches[: self._max_gallery_images]:
                if (
                    url
                    and "floorplan" not in url.lower()
                    and not _is_epc_url(url)
                    and not _is_video_url(url)
                ):
                    full_url = f"https:{url}" if url.startswith("//") else url
                    gallery_urls.append(full_url)

            # Pattern 2: Fallback - old data-lightbox="gallery" pattern
            if not gallery_urls:
                gallery_matches = re.findall(
                    r'<a[^>]*href="([^"]+)"[^>]*data-lightbox="gallery"',
                    html,
                    re.IGNORECASE,
                )
                for url in gallery_matches[: self._max_gallery_images]:
                    if (
                        url
                        and "floorplan" not in url.lower()
                        and not _is_epc_url(url)
                        and not _is_video_url(url)
                    ):
                        full_url = f"https:{url}" if url.startswith("//") else url
                        gallery_urls.append(full_url)

            # Pattern 3: Fallback - look for property images by URL pattern
            if not gallery_urls:
                img_matches = re.findall(
                    r'(//imagescdn\.openrent\.co\.uk/listings/\d+/[^"]+\.(?:jpg|jpeg|png|webp))',
                    html,
                    re.IGNORECASE,
                )
                for url in img_matches[: self._max_gallery_images]:
                    if url and "floorplan" not in url.lower() and not _is_epc_url(url):
                        full_url = f"https:{url}"
                        if full_url not in gallery_urls:
                            gallery_urls.append(full_url)

            # Extract description - OpenRent uses a description div
            description: str | None = None
            desc_match = re.search(
                r'<div[^>]*class="[^"]*description[^"]*"[^>]*>(.*?)</div>',
                html,
                re.DOTALL | re.IGNORECASE,
            )
            if desc_match:
                # Strip HTML tags
                desc_text = re.sub(r"<[^>]+>", " ", desc_match.group(1))
                desc_text = re.sub(r"\s+", " ", desc_text).strip()
                if desc_text:
                    description = desc_text

            # Extract features - OpenRent lists features in a ul
            features: list[str] = []
            features_match = re.search(
                r'<ul[^>]*class="[^"]*feature[^"]*"[^>]*>(.*?)</ul>',
                html,
                re.DOTALL | re.IGNORECASE,
            )
            if features_match:
                feature_items = re.findall(r"<li[^>]*>(.*?)</li>", features_match.group(1))
                for item in feature_items:
                    text = re.sub(r"<[^>]+>", "", item).strip()
                    if text:
                        features.append(text)

            return DetailPageData(
                floorplan_url=floorplan_url,
                gallery_urls=gallery_urls if gallery_urls else None,
                description=description,
                features=features if features else None,
            )

        except Exception as e:
            logger.warning("openrent_fetch_failed", property_id=prop.unique_id, error=str(e))
            return None

    async def _fetch_onthemarket(self, prop: Property) -> DetailPageData | None:
        """Extract floorplan and gallery URLs from OnTheMarket detail page.

        Uses curl_cffi with Chrome TLS fingerprint impersonation to bypass
        OnTheMarket's bot detection.
        """
        try:
            response = await self._curl_get_with_retry(
                str(prop.url), min_interval=_OTM_MIN_INTERVAL
            )
            if response.status_code != 200:
                logger.warning(
                    "onthemarket_http_error",
                    property_id=prop.unique_id,
                    status=response.status_code,
                )
                return None
            html: str = response.text

            # OnTheMarket uses Next.js with Redux state in __NEXT_DATA__
            match = re.search(
                r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                html,
                re.DOTALL,
            )
            if not match:
                return None

            data = json.loads(match.group(1))
            redux_state = data.get("props", {}).get("initialReduxState", {})
            property_data = redux_state.get("property", {})

            # Extract floorplan
            floorplan_url: str | None = None
            floorplans = property_data.get("floorplans", [])
            if floorplans:
                fp = floorplans[0]
                floorplan_url = fp.get("original") or fp.get("largeUrl") or fp.get("url")

            # Extract gallery images
            # OnTheMarket uses 'largeUrl' or 'prefix' + geometry suffix
            gallery_urls: list[str] = []
            images = property_data.get("images", [])
            for img in images[: self._max_gallery_images]:
                if isinstance(img, dict):
                    # Try various URL fields
                    url = img.get("original") or img.get("largeUrl") or img.get("url")
                    # Fallback: construct from prefix if available
                    if not url and img.get("prefix"):
                        url = f"{img['prefix']}-1024x1024.jpg"
                else:
                    url = img
                if url and not _is_epc_url(url):
                    gallery_urls.append(url)

            # Extract description
            description = property_data.get("description")

            # Extract features
            features: list[str] = []
            key_features = property_data.get("keyFeatures", [])
            if key_features:
                features.extend(key_features)
            # Also check for bullet points
            bullets = property_data.get("bullets", [])
            if bullets:
                features.extend(bullets)
            # Features as array of objects {id, feature}
            feature_objects = property_data.get("features", [])
            for feat in feature_objects:
                if isinstance(feat, dict) and feat.get("feature"):
                    features.append(feat["feature"])

            # Extract floor area (validate in sqft, convert to sqm)
            floor_area_sqm: float | None = None
            raw_sqft = property_data.get("minimumAreaSqFt")
            if (
                isinstance(raw_sqft, (int, float))
                and _FLOOR_AREA_MIN_SQFT <= raw_sqft <= _FLOOR_AREA_MAX_SQFT
            ):
                floor_area_sqm = round(raw_sqft * SQM_PER_SQFT, 1)

            return DetailPageData(
                floorplan_url=floorplan_url,
                gallery_urls=gallery_urls if gallery_urls else None,
                description=description,
                features=features if features else None,
                floor_area_sqm=floor_area_sqm,
                floor_area_source="onthemarket" if floor_area_sqm else None,
            )

        except Exception as e:
            logger.warning("onthemarket_fetch_failed", property_id=prop.unique_id, error=str(e))
            return None

    @staticmethod
    def _get_cdn_key(url: str) -> str | None:
        """Return the CDN throttle key for *url*, or None for non-CDN URLs."""
        for domain_fragment in _CDN_THROTTLE_CONFIG:
            if domain_fragment in url:
                return domain_fragment
        return None

    def _get_image_breaker(self, cdn_key: str) -> ConsecutiveFailureBreaker:
        """Return (or lazily create) a circuit breaker for *cdn_key*."""
        breaker = self._image_breakers.get(cdn_key)
        if breaker is None:
            breaker = ConsecutiveFailureBreaker(
                threshold=_IMAGE_CB_THRESHOLD,
                name=f"image_cdn_{cdn_key}",
            )
            self._image_breakers[cdn_key] = breaker
        return breaker

    async def download_image_bytes(self, url: str) -> bytes | None:
        """Download image bytes from a URL.

        Uses curl_cffi with per-CDN throttling and circuit breakers for
        anti-bot CDNs (zoocdn.com, onthemarket.com, imagescdn.openrent.co.uk).
        Uses httpx for everything else.

        Args:
            url: Image URL to download.

        Returns:
            Raw image bytes, or None if download failed.
        """
        cdn_key = self._get_cdn_key(url)

        if cdn_key is not None:
            # curl_cffi path with per-CDN throttle + circuit breaker
            breaker = self._get_image_breaker(cdn_key)
            if breaker.is_tripped:
                self._image_skip_counts[cdn_key] = self._image_skip_counts.get(cdn_key, 0) + 1
                logger.debug("image_download_circuit_open", url=url, cdn=cdn_key)
                return None

            throttle_name, interval = _CDN_THROTTLE_CONFIG[cdn_key]
            try:
                response = await self._curl_get_with_retry(
                    url,
                    min_interval=interval,
                    throttle_name=throttle_name,
                    timeout=_IMAGE_TIMEOUT,
                )
                if response.status_code != 200:
                    logger.debug(
                        "image_download_failed", url=url, status=response.status_code
                    )
                    return None
                breaker.record_success()
                data: bytes = response.content
            except CurlError as exc:
                if exc.code in _BREAKER_CURL_CODES:
                    breaker.record_failure()
                    if breaker.is_tripped:
                        logger.warning(
                            "image_cdn_circuit_opened",
                            cdn=cdn_key,
                            failures=breaker.failure_count,
                        )
                logger.debug("image_download_error", url=url, error=str(exc))
                return None
            except Exception as exc:
                logger.debug("image_download_error", url=url, error=str(exc))
                return None
        else:
            # httpx path — no throttle, no breaker
            try:
                response = await self._httpx_get_with_retry(url)
                data = response.content
            except Exception as exc:
                logger.debug("image_download_error", url=url, error=str(exc))
                return None

        if not is_valid_image_bytes(data):
            logger.warning("image_download_not_image", url=url, prefix=data[:16])
            return None
        # Validate PIL can fully decode it (catches truncated/corrupt downloads)
        try:
            Image.open(io.BytesIO(data)).load()
        except Exception:
            logger.warning("image_download_corrupt", url=url)
            return None
        return data

    async def __aenter__(self) -> "DetailFetcher":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the HTTP clients."""
        for cdn, count in self._image_skip_counts.items():
            logger.warning(
                "image_cdn_circuit_summary",
                cdn=cdn,
                skipped=count,
                threshold=_IMAGE_CB_THRESHOLD,
            )
        if self._client:
            await self._client.aclose()
            self._client = None
        if self._curl_session:
            await self._curl_session.close()
            self._curl_session = None
