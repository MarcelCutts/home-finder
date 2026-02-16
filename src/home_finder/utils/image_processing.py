"""Image processing utilities for quality analysis."""

from io import BytesIO
from typing import Final, Literal, TypeAlias, TypeGuard, get_args

from PIL import Image

from home_finder.logging import get_logger

logger = get_logger(__name__)

# Limit decompression bomb threshold for untrusted image bytes.
# Default Pillow limit is ~178M pixels; 50M is generous for property photos.
Image.MAX_IMAGE_PIXELS = 50_000_000

# Valid media types for Claude vision API
ImageMediaType: TypeAlias = Literal["image/jpeg", "image/png", "image/gif", "image/webp"]
VALID_MEDIA_TYPES: Final[tuple[str, ...]] = get_args(ImageMediaType)

# Anthropic recommends ≤1568px on longest edge for optimal performance.
# Also well under the 2000px hard limit for requests with >20 images.
MAX_IMAGE_DIMENSION: Final = 1568


def is_valid_media_type(value: str) -> TypeGuard[ImageMediaType]:
    """Check if a string is a valid image media type for Claude vision API."""
    return value in VALID_MEDIA_TYPES


def resize_image_bytes(data: bytes, max_dim: int = MAX_IMAGE_DIMENSION) -> bytes:
    """Downscale image so longest edge <= max_dim. Returns original bytes if already small."""
    try:
        img = Image.open(BytesIO(data))
        w, h = img.size
        if w <= max_dim and h <= max_dim:
            return data
        scale = max_dim / max(w, h)
        new_size = (int(w * scale), int(h * scale))
        # Preserve format before resize (resize clears it)
        fmt = img.format or "JPEG"
        resized: Image.Image = img.resize(new_size, Image.Resampling.LANCZOS)
        buf = BytesIO()
        resized.save(buf, format=fmt, quality=85)
        return buf.getvalue()
    except Exception:
        logger.debug("image_resize_failed", exc_info=True)
        return data
