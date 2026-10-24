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


def detect_media_type_from_bytes(data: bytes) -> ImageMediaType | None:
    """Detect image media type from magic bytes (first 12 bytes)."""
    if len(data) < 12:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"\x89PNG":
        return "image/png"
    if data[:4] == b"GIF8":
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def is_valid_media_type(value: str) -> TypeGuard[ImageMediaType]:
    """Check if a string is a valid image media type for Claude vision API."""
    return value in VALID_MEDIA_TYPES


def _has_transparency(img: Image.Image) -> bool:
    if "transparency" in img.info:
        return True
    if img.mode not in ("RGBA", "LA", "PA"):
        return False
    low, _ = img.getchannel("A").getextrema()
    return isinstance(low, (int, float)) and low < 255


def _on_white(img: Image.Image) -> Image.Image:
    """Keep black-ink transparent plans readable regardless of provider compositing."""
    rgba = img.convert("RGBA")
    flat = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    flat.alpha_composite(rgba)
    return flat.convert("RGB")


def resize_image_bytes(data: bytes, max_dim: int = MAX_IMAGE_DIMENSION) -> bytes:
    """Downscale static images and flatten transparency onto white.

    Small opaque images and animated images retain their original bytes.
    """
    try:
        with Image.open(BytesIO(data)) as img:
            # Keep all frames for admission to check; saving could drop all but the first.
            if getattr(img, "n_frames", 1) != 1:
                return data
            w, h = img.size
            oversized = w > max_dim or h > max_dim
            transparent = _has_transparency(img)
            if not oversized and not transparent:
                return data
            # Preserve format before converting or resizing clears it.
            fmt = img.format or "JPEG"
            out = _on_white(img) if transparent else img
            if oversized:
                scale = max_dim / max(w, h)
                new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
                out = out.resize(new_size, Image.Resampling.LANCZOS, reducing_gap=3.0)
            # JPEG cannot encode RGBA/P/CMYK — convert to RGB.
            if fmt == "JPEG" and out.mode not in ("RGB", "L"):
                out = out.convert("RGB")
            buf = BytesIO()
            # Do not introduce lossy compression into transparent WebP line art.
            out.save(buf, format=fmt, quality=85, lossless=transparent and fmt == "WEBP")
            return buf.getvalue()
    except Exception:
        logger.debug("image_resize_failed", exc_info=True)
        return data
