#!/usr/bin/env python3
"""Render Sebastian's icon set (PNG + ICO) from the same geometry as static/icons/favicon.svg.

    uv run python scripts/make_icons.py

Pillow-only (no cairo/inkscape needed). Output goes to src/sebastian/ui/static/icons/.
Geometry is in the SVG's 64x64 space; we draw at 1024px and downsample for crisp edges.
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "src/sebastian/ui/static/icons"
S = 1024  # working canvas
K = S / 64
TOP, BOTTOM = (0x63, 0x66, 0xF1), (0x7C, 0x3A, 0xED)  # indigo-500 -> violet-600


def bezier(p0, p1, p2, p3, n=60):
    pts = []
    for i in range(n + 1):
        t = i / n
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t**2, t**3
        pts.append(
            (
                a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1],
            )
        )
    return pts


def gradient() -> Image.Image:
    img = Image.new("RGB", (S, S))
    px = []
    for y in range(S):
        for x in range(S):
            t = (x + y) / (2 * (S - 1))  # diagonal, top-left to bottom-right
            px.append(tuple(round(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)))
    img.putdata(px)
    return img


def artwork(scale: float = 1.0) -> Image.Image:
    """White cloche on transparent, centred and scaled about the middle of the 64-grid."""
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    def P(x, y):
        return ((32 + (x - 32) * scale) * K, (32 + (y - 32) * scale) * K)

    dome = (
        bezier((13, 44), (13, 30), (21, 21), (32, 21))
        + bezier((32, 21), (43, 21), (51, 30), (51, 44))[1:]
    )
    d.polygon([P(*p) for p in dome], fill=(255, 255, 255, 255))
    x0, y0 = P(9, 43)
    x1, y1 = P(55, 48)
    d.rounded_rectangle([x0, y0, x1, y1], radius=2.5 * K * scale, fill=(255, 255, 255, 255))
    cx, cy = P(32, 16.5)
    r = 3.6 * K * scale
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 255))
    # soft highlight on the dome: stamp round dabs along the curve into a mask (no ribbing),
    # then tint the mask with a translucent indigo
    mask = Image.new("L", (S, S), 0)
    md = ImageDraw.Draw(mask)
    rr = 1.2 * K * scale
    for px, py in (P(*p) for p in bezier((19.5, 38), (20.5, 32), (24.5, 28), (29, 26.8), n=400)):
        md.ellipse([px - rr, py - rr, px + rr, py + rr], fill=255)
    tint = Image.new("RGBA", (S, S), (99, 102, 241, 0))
    tint.putalpha(mask.point(lambda v: round(v * 72 / 255)))
    hl = tint
    return Image.alpha_composite(layer, hl)


def icon(rounded: bool, scale: float = 1.0) -> Image.Image:
    base = gradient().convert("RGBA")
    base = Image.alpha_composite(base, artwork(scale))
    if rounded:
        mask = Image.new("L", (S, S), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=15 * K, fill=255)
        base.putalpha(mask)
    return base


def save(img: Image.Image, name: str, size: int) -> None:
    img.resize((size, size), Image.LANCZOS).save(OUT / name, optimize=True)
    print(f"  {name} ({size}x{size})")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    rounded = icon(True)
    for size in (16, 32, 48, 96, 192, 512):
        save(rounded, f"icon-{size}.png", size)
    # iOS rounds the corners itself, so it wants a full-bleed square
    save(icon(False), "apple-touch-icon.png", 180)
    # Android "maskable": artwork inside the central safe zone of a full-bleed square
    save(icon(False, scale=0.78), "maskable-512.png", 512)
    # Pillow only embeds sizes <= the source image, so save from the full-size artwork
    rounded.save(OUT / "favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    print("  favicon.ico (16/32/48)")
