"""Small image fixtures for cache, request-encoding, and transparency scenarios."""

import base64
from io import BytesIO

from PIL import Image

from home_finder.models import (
    MergedProperty,
)


def image_block(colour):
    stream = BytesIO()
    Image.new("RGB", (32, 32), colour).save(stream, format="JPEG")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.b64encode(stream.getvalue()).decode(),
        },
    }


def populate_image_cache(data_dir: str, merged: MergedProperty) -> None:
    """Create valid JPEG files in the image cache for all images in a MergedProperty."""
    from io import BytesIO

    from PIL import Image

    from home_finder.utils.image_cache import get_cached_image_path, save_image_bytes

    buf = BytesIO()
    Image.new("RGB", (10, 10), color="red").save(buf, format="JPEG")
    jpeg_bytes = buf.getvalue()

    for idx, img in enumerate(merged.images):
        path = get_cached_image_path(data_dir, merged.unique_id, str(img.url), img.image_type, idx)
        save_image_bytes(path, jpeg_bytes)

    if merged.floorplan:
        path = get_cached_image_path(
            data_dir, merged.unique_id, str(merged.floorplan.url), "floorplan", 0
        )
        save_image_bytes(path, jpeg_bytes)


def transparent_plan(mode: str, size: tuple[int, int] = (100, 100)) -> bytes:
    """Reproduce cached plans: black ink with black hidden behind transparent pixels."""
    buf = BytesIO()
    if mode == "P":
        img = Image.new("P", size, 0)
        img.putpalette([0, 0, 0] * 256)
        img.paste(1, (0, 0, size[0] // 2, size[1]))
        img.save(buf, format="PNG", transparency=0)
    elif mode == "RGB":
        img = Image.new("RGB", size, "white")
        img.paste((0, 0, 0), (0, 0, size[0] // 2, size[1]))
        img.save(buf, format="PNG", transparency=(255, 255, 255))
    else:
        clear = (0, 0) if mode == "LA" else (0, 0, 0, 0)
        ink = (0, 255) if mode == "LA" else (0, 0, 0, 255)
        img = Image.new(mode, size, clear)
        img.paste(ink, (0, 0, size[0] // 2, size[1]))
        img.save(buf, format="PNG")
    return buf.getvalue()
