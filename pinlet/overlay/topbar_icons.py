"""PNG icons for Pinlet's top-bar item, drawn with Pillow.

The menu bar and tray icon, the tile icon and the round per-Pinlet row icons,
shared by both platforms (no fonts needed except the optional count). Each is
drawn once and kept as a file under ``~/.pinlet/overlay/icons``.

Pure (no AppKit, no GTK). :mod:`pinlet.overlay.topbar` exports everything
here, so callers keep using ``topbar.render_icon`` and friends.
"""

from __future__ import annotations

import os

from .palette import PALETTE

ICON_DIR = os.path.expanduser("~/.pinlet/overlay/icons")


def _rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha


# Pointer and spark (same geometry as apps/macos/Sources/Pinlet/Icons.swift),
# in an 18 x 18 box, y down.
_POINTER = [
    (3.6, 2.6),
    (3.6, 14.2),
    (6.5, 11.4),
    (8.6, 15.9),
    (10.7, 15.0),
    (8.7, 10.6),
    (12.8, 10.6),
]
_SPARK_C, _SPARK_R = (14.0, 3.8), 2.9
_DOT_C, _DOT_R, _DOT_RING = (14.6, 14.4), 3.0, 1.2


def _spark_points(cx: float, cy: float, r: float, steps: int = 10) -> list[tuple[float, float]]:
    """Draws a pinched four-point star.

    The tips are joined by quadratic curves whose control points sit at centre ± 0.16·r.
    """
    tips = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
    k = 0.16 * r
    ctrls = [(cx + k, cy - k), (cx + k, cy + k), (cx - k, cy + k), (cx - k, cy - k)]
    pts: list[tuple[float, float]] = []
    for i in range(4):
        (x0, y0), (x1, y1), (qx, qy) = tips[i], tips[(i + 1) % 4], ctrls[i]
        for j in range(steps):
            t = j / steps
            u = 1 - t
            pts.append(
                (u * u * x0 + 2 * u * t * qx + t * t * x1, u * u * y0 + 2 * u * t * qy + t * t * y1)
            )
    return pts


def _mark(d, s: float, pointer, spark, spark_shown: bool = True) -> None:
    """Pinlet's mark, scaled to an ``s`` px square: a pointer with a spark."""
    k = s / 18.0
    poly = [(x * k, y * k) for x, y in _POINTER]
    d.polygon(poly, fill=pointer)
    w = max(1, int(round(0.9 * k)))
    d.line(poly + [poly[0]], fill=pointer, width=w, joint="curve")  # the 0.9 round-joined stroke
    for x, y in poly:
        d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=pointer)
    if spark_shown:
        d.polygon(_spark_points(_SPARK_C[0] * k, _SPARK_C[1] * k, _SPARK_R * k), fill=spark)


def render_icon(
    state: str, size: int = 36, dark: bool = False, template: bool = False, count: int = 0
):
    """The top-bar icon as a Pillow RGBA image (``size`` px square).

    ``template``: plain black (macOS tints it to the menu bar). Otherwise the
    pointer is ink on a light bar or near-white on a dark one, and the spark
    is Pinlet cyan. A green dot means working, amber means a Pinlet needs
    you. ``count`` > 1 puts a number badge where the spark is (for trays that
    cannot show text beside the icon).
    """
    from PIL import Image, ImageDraw

    scale = 4  # draw big, shrink: smooth edges without antialiasing support
    s = size * scale
    k = s / 18.0
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if template:
        pointer = spark = (0, 0, 0, 255)
    else:
        pointer = _rgba(PALETTE["surface_light"] if dark else PALETTE["surface_dark"])
        spark = _rgba(PALETTE["accent_dark"] if dark else PALETTE["accent"])
    _mark(d, s, pointer, spark, spark_shown=count <= 1)
    if state in ("working", "attention"):
        dot = (
            (0, 0, 0, 255)
            if template
            else _rgba(PALETTE["caution"] if state == "attention" else PALETTE["ok"])
        )
        cx, cy, r, ring = _DOT_C[0] * k, _DOT_C[1] * k, _DOT_R * k, _DOT_RING * k
        clear = Image.new("L", (s, s), 0)
        ImageDraw.Draw(clear).ellipse(
            [cx - r - ring, cy - r - ring, cx + r + ring, cy + r + ring], fill=255
        )
        img.paste((0, 0, 0, 0), (0, 0), clear)  # clear the ring first
        d = ImageDraw.Draw(img)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=dot)
    if count > 1:
        _badge(d, s, str(count) if count < 10 else "9+")
    return img.resize((size, size), Image.LANCZOS)


def _gradient(size: int, top: str, bottom: str):
    from PIL import Image

    t, b = _rgba(top), _rgba(bottom)
    col = Image.new("RGBA", (1, size))
    for y in range(size):
        f = y / max(1, size - 1)
        col.putpixel((0, y), tuple(int(t[i] + (b[i] - t[i]) * f) for i in range(4)))
    return col.resize((size, size))


# The Pinlet.app icon's tile (1024 canvas, 824 tile at 100,100, radius 186), mapped to a
# tile that fills the whole tray icon: a pointer and spark as big as the icon allows.
_APP_K, _APP_OFF = 1024 / 18 * 0.72, (225, 156)
_TILE_RADIUS = 186 / 824


def _tile_xy(x: float, y: float, s: float) -> tuple[float, float]:
    return ((_APP_OFF[0] + x * _APP_K - 100) / 824 * s, (_APP_OFF[1] + y * _APP_K - 100) / 824 * s)


def render_tile_icon(state: str, size: int = 44, count: int = 0):
    """Draws the tray icon as a small Pinlet.app tile.

    "Frost": a white frosted square with a pearl pointer and a cyan star, like
    Pinlet.app's icon.

    Reads on light and dark panels alike. Status dot and count badge as in :func:`render_icon`.
    """
    from PIL import Image, ImageDraw

    scale = 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    mask = Image.new("L", (s, s), 0)
    inset = s * 0.03
    ImageDraw.Draw(mask).rounded_rectangle(
        [inset, inset, s - inset, s - inset], radius=_TILE_RADIUS * s, fill=255
    )
    img.paste(_gradient(s, "#FFFFFF", "#DCE5EE"), (0, 0), mask)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(  # the frosted tile's faint edge, so it holds on light panels
        [inset, inset, s - inset, s - inset],
        radius=_TILE_RADIUS * s,
        outline=(11, 18, 32, 40),
        width=max(1, int(s * 0.012)),
    )
    poly = [_tile_xy(x, y, s) for x, y in _POINTER]
    white = (255, 255, 255, 255)
    for color, width in (((142, 166, 186, 255), 50), ((244, 248, 251, 255), 34)):
        w = max(1, int(round(width / 824 * s)))  # a cool edge, then the pearl pointer
        d.polygon(poly, fill=color)
        d.line(poly + [poly[0]], fill=color, width=w, joint="curve")
        for x, y in poly:
            d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=color)
    sc, sr = ((722 - 100) / 824 * s, (318 - 100) / 824 * s), 122 / 824 * s * 1.15
    if count > 1:
        r = sr * 1.05
        d.ellipse([sc[0] - r, sc[1] - r, sc[0] + r, sc[1] + r], fill=_rgba(PALETTE["fill"]))
        _badge_text(d, sc, r, str(count) if count < 10 else "9+", white)
    else:
        spark = Image.new("L", (s, s), 0)
        ImageDraw.Draw(spark).polygon(_spark_points(sc[0], sc[1], sr), fill=255)
        img.paste(_gradient(s, "#8BEAFB", "#0EA5C6"), (0, 0), spark)
    if state in ("working", "attention"):
        d = ImageDraw.Draw(img)
        dot = _rgba(PALETTE["caution"] if state == "attention" else PALETTE["ok"])
        cx = cy = s * 0.83
        r = s * 0.15
        d.ellipse([cx - r * 1.3, cy - r * 1.3, cx + r * 1.3, cy + r * 1.3], fill=white)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=dot)
    return img.resize((size, size), Image.LANCZOS)


def _badge_text(d, centre: tuple[float, float], r: float, text: str, color) -> None:
    from PIL import ImageFont

    try:
        font = ImageFont.load_default(size=int(r * 1.5))  # Pillow 10.1+, needs FreeType
    except (TypeError, OSError, AttributeError):
        return
    d.text(centre, text, fill=color, font=font, anchor="mm")


def _badge(d, s: float, text: str) -> None:
    from PIL import ImageFont

    k = s / 18.0
    cx, cy, r = _SPARK_C[0] * k, _SPARK_C[1] * k, 3.4 * k  # where the spark sits
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_rgba(PALETTE["fill"]))
    try:
        font = ImageFont.load_default(size=int(r * 1.5))  # Pillow 10.1+, needs FreeType
    except (TypeError, OSError, AttributeError):
        return  # the disc alone still says "more than one"
    d.text((cx, cy), text, fill=(255, 255, 255, 255), font=font, anchor="mm")


def _glyph(d, shape: str, s: float, color) -> None:
    c = s / 2
    if shape == "spark":  # four-point star
        a, b = s * 0.26, s * 0.07
        d.polygon(
            [
                (c, c - a),
                (c + b, c - b),
                (c + a, c),
                (c + b, c + b),
                (c, c + a),
                (c - b, c + b),
                (c - a, c),
                (c - b, c - b),
            ],
            fill=color,
        )
    elif shape == "moon":
        r = s * 0.22
        d.ellipse([c - r, c - r, c + r, c + r], fill=color)
        off = s * 0.11
        d.ellipse(
            [c - r + off, c - r - off * 0.6, c + r + off, c + r - off * 0.6], fill=(0, 0, 0, 0)
        )
    elif shape == "pause":
        w, h = s * 0.08, s * 0.22
        for x in (c - s * 0.08, c + s * 0.08):
            d.rounded_rectangle([x - w / 2, c - h, x + w / 2, c + h], radius=w / 2, fill=color)
    elif shape == "stop":
        a = s * 0.16
        d.rounded_rectangle([c - a, c - a, c + a, c + a], radius=s * 0.04, fill=color)
    elif shape == "alert":
        w = s * 0.08
        d.rounded_rectangle(
            [c - w / 2, c - s * 0.24, c + w / 2, c + s * 0.06], radius=w / 2, fill=color
        )
        d.ellipse([c - w * 0.6, c + s * 0.13, c + w * 0.6, c + s * 0.13 + w * 1.2], fill=color)
    elif shape == "hand":  # an open palm: four fingers and a thumb
        d.rounded_rectangle(
            [c - s * 0.16, c - s * 0.02, c + s * 0.14, c + s * 0.24], radius=s * 0.08, fill=color
        )
        for i, x in enumerate((-0.13, -0.05, 0.03, 0.11)):
            top = c - s * (0.2 if i in (1, 2) else 0.15)
            d.rounded_rectangle(
                [c + s * x - s * 0.03, top, c + s * x + s * 0.03, c + s * 0.05],
                radius=s * 0.03,
                fill=color,
            )
        d.polygon(
            [
                (c - s * 0.16, c + s * 0.06),
                (c - s * 0.27, c - s * 0.04),
                (c - s * 0.22, c - s * 0.08),
                (c - s * 0.12, c),
            ],
            fill=color,
        )
    elif shape == "clock":
        r, w = s * 0.2, max(1.0, s * 0.05)
        d.ellipse([c - r, c - r, c + r, c + r], outline=color, width=int(w))
        d.line([(c, c), (c, c - r * 0.6)], fill=color, width=int(w))
        d.line([(c, c), (c + r * 0.5, c)], fill=color, width=int(w))


def render_row_icon(color: str, shape: str, size: int = 52, filled: bool = True):
    """A row icon: a rounded square in the row's colour with a white glyph."""
    from PIL import Image, ImageDraw

    scale = 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(
        [0, 0, s - 1, s - 1], radius=s * 0.28, fill=_rgba(color, 255 if filled else 90)
    )
    glyph = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    _glyph(ImageDraw.Draw(glyph), shape, s, (255, 255, 255, 255))
    img.alpha_composite(glyph)
    return img.resize((size, size), Image.LANCZOS)


def icon_file(kind: str, *parts: object, render=None, directory: str = ICON_DIR) -> str | None:
    """Render once to ``<directory>/<kind>-<parts>.png`` and return its path.

    The name changes with the content, which AppIndicator needs (it caches
    icons by name). Returns None if the file cannot be written.
    """
    name = "-".join([kind] + [str(p).lstrip("#").lower() for p in parts]) + ".png"
    path = os.path.join(directory, name)
    if os.path.isfile(path):
        return path
    try:
        os.makedirs(directory, exist_ok=True)
        img = render()
        tmp = path + f".{os.getpid()}.tmp"
        img.save(tmp, "PNG")
        os.replace(tmp, path)
        return path
    except Exception:  # noqa: BLE001 - an icon is cosmetic
        return None


def bar_icon_path(
    state: str,
    size: int = 36,
    dark: bool = False,
    template: bool = False,
    count: int = 0,
    directory: str = ICON_DIR,
) -> str | None:
    # "bar-ps": pointer and spark. A new look needs a new file name (icons are cached).
    """Path of the cached menu bar icon PNG for ``state``, drawing it on first use.

    Returns None when the icon cannot be drawn.
    """
    return icon_file(
        "bar-ps",
        state,
        size,
        "dark" if dark else "light",
        "t" if template else "c",
        min(count, 10) if count > 1 else 0,
        render=lambda: render_icon(state, size, dark, template, count),
        directory=directory,
    )


def tray_icon_path(
    state: str, size: int = 44, count: int = 0, directory: str = ICON_DIR
) -> str | None:
    """The Linux tray's tile icon (see :func:`render_tile_icon`)."""
    return icon_file(
        "tray-frost",
        state,
        size,
        min(count, 10) if count > 1 else 0,
        render=lambda: render_tile_icon(state, size, count),
        directory=directory,
    )


def row_icon_path(color: str, shape: str, size: int = 52, directory: str = ICON_DIR) -> str | None:
    """Path of the cached square row icon in ``color`` and ``shape``, drawing it on first use."""
    return icon_file(
        "row-sq",
        color,
        shape,
        size,
        render=lambda: render_row_icon(color, shape, size),
        directory=directory,
    )
