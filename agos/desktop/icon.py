"""Generate OpenSculpt tray icon programmatically (no external .ico file needed)."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont


def create_agos_icon(size: int = 64) -> Image.Image:
    """Create an OpenSculpt icon — dark circle with 'S' letter."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Dark background circle
    margin = 2
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(20, 20, 30, 255),
        outline=(0, 255, 170, 255),
        width=2,
    )

    # Letter "A" in the center
    try:
        font = ImageFont.truetype("arial", size // 2)
    except OSError:
        font = ImageFont.load_default()

    text = "A"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) // 2
    y = (size - th) // 2 - bbox[1]
    draw.text((x, y), text, fill=(0, 255, 170, 255), font=font)

    return img
