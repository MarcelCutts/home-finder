#!/usr/bin/env python3
"""Generate PNG icons and OG image for the Home Finder web dashboard.

Produces:
  - static/apple-touch-icon.png  (180x180)
  - static/icon-192.png          (192x192)
  - static/icon-512.png          (512x512)
  - static/og-image.png          (1200x630)

Uses the same brand language as the dashboard: dark background (#0a0a0a/#09090b)
with a purple gradient (#8b5cf6 -> #d946ef) house silhouette.

Run once:
    uv run python scripts/generate_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

STATIC_DIR = Path(__file__).resolve().parent.parent / "src" / "home_finder" / "web" / "static"

# Brand colours
BG_DARK = (9, 9, 11)  # #09090b
ICON_BG = (10, 10, 10)  # #0a0a0a
PURPLE_START = (139, 92, 246)  # #8b5cf6
PURPLE_END = (217, 70, 239)  # #d946ef


def _lerp_color(
    c1: tuple[int, int, int], c2: tuple[int, int, int], t: float
) -> tuple[int, int, int]:
    """Linearly interpolate between two RGB colours."""
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def _draw_house(draw: ImageDraw.ImageDraw, size: int, offset_x: int, offset_y: int) -> list[tuple[int, int]]:
    """Return house polygon points scaled to `size` with given offset.

    House shape: peaked roof, rectangular body, door cutout implied by fill.
    """
    # House proportions relative to a unit square of `size`
    s = size
    cx = offset_x + s // 2

    # Roof peak
    top_y = offset_y
    # Eaves
    eave_y = offset_y + int(s * 0.42)
    # Base
    base_y = offset_y + s

    # Widths
    roof_half = int(s * 0.52)
    body_half = int(s * 0.38)
    door_half = int(s * 0.10)
    door_top = offset_y + int(s * 0.68)

    # Outer polygon (house outline)
    return [
        (cx, top_y),  # roof peak
        (cx - roof_half, eave_y),  # left eave
        (cx - body_half, eave_y),  # left wall top
        (cx - body_half, base_y),  # left wall bottom
        (cx - door_half, base_y),  # door left bottom
        (cx - door_half, door_top),  # door left top
        (cx + door_half, door_top),  # door right top
        (cx + door_half, base_y),  # door right bottom
        (cx + body_half, base_y),  # right wall bottom
        (cx + body_half, eave_y),  # right wall top
        (cx + roof_half, eave_y),  # right eave
    ]


def _gradient_fill(img: Image.Image, polygon: list[tuple[int, int]]) -> None:
    """Fill a polygon with a diagonal purple gradient."""
    # Create a gradient image
    w, h = img.size
    gradient = Image.new("RGB", (w, h), BG_DARK)
    for y in range(h):
        for x in range(w):
            # Diagonal: top-left to bottom-right
            t = ((x / w) + (y / h)) / 2.0
            gradient.putpixel((x, y), _lerp_color(PURPLE_START, PURPLE_END, t))

    # Create mask from polygon
    mask = Image.new("L", (w, h), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.polygon(polygon, fill=255)

    # Composite
    img.paste(gradient, mask=mask)


def _draw_rounded_rect_bg(img: Image.Image, radius: int) -> None:
    """Fill the image with a rounded rectangle background."""
    draw = ImageDraw.Draw(img)
    w, h = img.size
    draw.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=radius, fill=ICON_BG)


def generate_icon(size: int, filename: str, corner_radius: int | None = None) -> Path:
    """Generate a single icon PNG at the given size.

    Renders at 2x resolution then downscales with LANCZOS for anti-aliased edges.
    """
    scale = 2
    big_size = size * scale
    img = Image.new("RGB", (big_size, big_size), BG_DARK)

    # Rounded rect background (at 2x)
    radius = (corner_radius or max(size // 5, 4)) * scale
    _draw_rounded_rect_bg(img, radius)

    # House — sized to ~60% of icon with padding (at 2x)
    padding = int(big_size * 0.2)
    house_size = big_size - 2 * padding
    polygon = _draw_house(ImageDraw.Draw(img), house_size, padding, padding)
    _gradient_fill(img, polygon)

    # Downscale to target size with LANCZOS for smooth anti-aliased edges
    img = img.resize((size, size), Image.LANCZOS)

    out = STATIC_DIR / filename
    img.save(out, "PNG")
    print(f"  {out.name} ({size}x{size})")
    return out


def generate_og_image() -> Path:
    """Generate the OG social preview image (1200x630).

    Renders at 2x resolution for anti-aliased edges. Content block is vertically
    centred on the canvas.
    """
    w, h = 1200, 630
    scale = 2
    big_w, big_h = w * scale, h * scale
    img = Image.new("RGB", (big_w, big_h), BG_DARK)

    draw = ImageDraw.Draw(img)

    # Font sizes at 2x
    title_size = 52 * scale
    subtitle_size = 24 * scale
    try:
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", title_size)
        subtitle_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", subtitle_size)
    except (OSError, IOError):
        try:
            # macOS system font paths
            title_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", title_size)
            subtitle_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", subtitle_size)
        except (OSError, IOError):
            title_font = ImageFont.load_default()
            subtitle_font = ImageFont.load_default()

    # Measure text heights for vertical centering
    icon_size = 180 * scale
    gap_icon_title = 40 * scale
    gap_title_subtitle = 16 * scale

    title = "Home Finder"
    title_bbox = draw.textbbox((0, 0), title, font=title_font)
    title_h = title_bbox[3] - title_bbox[1]

    subtitle = "London Rental Property Finder"
    subtitle_bbox = draw.textbbox((0, 0), subtitle, font=subtitle_font)
    subtitle_h = subtitle_bbox[3] - subtitle_bbox[1]

    total_height = icon_size + gap_icon_title + title_h + gap_title_subtitle + subtitle_h
    icon_y = (big_h - total_height) // 2

    # House icon (at 2x)
    icon_x = (big_w - icon_size) // 2
    polygon = _draw_house(ImageDraw.Draw(img), icon_size, icon_x, icon_y)
    _gradient_fill(img, polygon)

    draw = ImageDraw.Draw(img)

    # Title
    title_y = icon_y + icon_size + gap_icon_title
    title_w = title_bbox[2] - title_bbox[0]
    draw.text(((big_w - title_w) // 2, title_y), title, fill=(255, 255, 255), font=title_font)

    # Subtitle
    subtitle_y = title_y + title_h + gap_title_subtitle
    sub_w = subtitle_bbox[2] - subtitle_bbox[0]
    draw.text(((big_w - sub_w) // 2, subtitle_y), subtitle, fill=(160, 160, 170), font=subtitle_font)

    # Downscale to target size with LANCZOS
    img = img.resize((w, h), Image.LANCZOS)

    out = STATIC_DIR / "og-image.png"
    img.save(out, "PNG")
    print(f"  {out.name} ({w}x{h})")
    return out


def main() -> None:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    print("Generating icons...")
    generate_icon(180, "apple-touch-icon.png")
    generate_icon(192, "icon-192.png")
    generate_icon(512, "icon-512.png")
    generate_og_image()
    print("Done.")


if __name__ == "__main__":
    main()
