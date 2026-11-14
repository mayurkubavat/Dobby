"""Rime's art for cairo: a faithful port of the Mac app's ``CompanionArt.swift``.

Rime looks the same on every platform. This module copies the Mac drawing
(shapes, colours, poses) one for one and adds nothing of its own, so change the
Swift art first and port the change here, never the other way round.

Everything is drawn in a 200 x 220 box, y down, from a pose (one value per
dial) and the opacity of each prop. Needs only pycairo.
"""

from __future__ import annotations

import math

BOX = (200.0, 220.0)

BODY1 = 0xFFFFFF
BODY2 = 0xC9ECF6
EDGE = 0x58B4CC
INK = 0x13273A
ICE = 0x6FE0F4
ICE2 = 0xB8F3FC
BLUSH = 0xB3A4F2
AMBER = 0xFFD48A

BASE = {
    "y": 0.0, "x": 0.0, "squash": 1.0, "tilt": 0.0,
    "eyeOpen": 1.0, "eyeCurve": 0.0, "lookX": 0.0, "lookY": 0.0,
    "browTilt": 0.0, "browY": 0.0, "brow": 0.0,
    "mouthWidth": 7.0, "mouthCurve": 4.0, "mouthOpen": 0.0,
    "armLeft": 0.0, "armRight": 0.0,
    "tuft": 0.0, "glow": 0.2, "blush": 0.55, "dim": 1.0,
}  # fmt: skip

_MOODS = {
    "sleep": {"eyeOpen": 0, "eyeCurve": -1, "mouthWidth": 3, "mouthCurve": 0, "mouthOpen": 0.5,
              "tuft": 26, "squash": 0.93, "tilt": -4, "glow": 0},
    "awake": {"mouthCurve": 5},
    "think": {"lookX": -3, "lookY": -5, "browTilt": -6, "browY": -2, "brow": 1, "mouthWidth": 4,
              "mouthCurve": -1, "armRight": -60, "tilt": -5, "glow": 0.6},
    "act": {"eyeOpen": 0.86, "browTilt": 9, "brow": 1, "mouthWidth": 5, "mouthCurve": 3, "glow": 1,
            "armLeft": 18, "armRight": 18},
    "ask": {"tilt": 7, "armRight": 140, "mouthWidth": 5, "mouthCurve": 2, "mouthOpen": 0.5, "brow": 1,
            "browTilt": -9, "browY": -3, "glow": 0.5},
    "pause": {"eyeOpen": 0.48, "mouthWidth": 6, "mouthCurve": 0, "tuft": 10, "glow": 0.1},
    "done": {"eyeOpen": 0, "eyeCurve": 1, "mouthWidth": 9, "mouthCurve": 8, "mouthOpen": 0.7,
             "armLeft": 140, "armRight": 140, "blush": 1, "glow": 0.8},
    "error": {"browTilt": -13, "brow": 1, "mouthWidth": 6, "mouthCurve": -5, "tuft": 18, "glow": 0,
              "lookY": 1},
    "lock": {"eyeOpen": 0, "eyeCurve": 0, "mouthWidth": 5, "mouthCurve": 0, "squash": 0.95, "tuft": 20,
             "glow": 0, "dim": 0.55},
}  # fmt: skip

PROP_OF = {
    "sleep": "zzz",
    "think": "think",
    "act": "spark",
    "done": "spark",
    "ask": "ask",
    "pause": "pause",
    "error": "sweat",
    "lock": "lock",
}


def pose(mood: str) -> dict[str, float]:
    """The resting pose for ``mood`` (the Mac's ``CompanionPose.target``)."""
    p = dict(BASE)
    p.update({k: float(v) for k, v in _MOODS.get(mood, {}).items()})
    return p


def _rgb(cr, hexv: int, alpha: float = 1.0) -> None:
    cr.set_source_rgba(
        ((hexv >> 16) & 255) / 255, ((hexv >> 8) & 255) / 255, (hexv & 255) / 255, alpha
    )


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return min(hi, max(lo, v))


def _oval(cr, cx: float, cy: float, rx: float, ry: float) -> None:
    cr.save()
    cr.translate(cx, cy)
    cr.scale(max(rx, 0.01), max(ry, 0.01))
    cr.new_sub_path()
    cr.arc(0, 0, 1, 0, 2 * math.pi)
    cr.restore()


def _rotate(cr, degrees: float, px: float, py: float) -> None:
    cr.translate(px, py)
    cr.rotate(math.radians(degrees))
    cr.translate(-px, -py)


def _radial(cr, inner: tuple, outer: tuple, cx: float, cy: float, r: float) -> None:
    import cairo

    g = cairo.RadialGradient(cx, cy, 0, cx, cy, r)
    g.add_color_stop_rgba(0, *inner)
    g.add_color_stop_rgba(1, *outer)
    cr.set_source(g)
    cr.paint()


def _c(hexv: int, alpha: float = 1.0) -> tuple[float, float, float, float]:
    return (((hexv >> 16) & 255) / 255, ((hexv >> 8) & 255) / 255, (hexv & 255) / 255, alpha)


def _quad(cr, x0: float, y0: float, cx: float, cy: float, x1: float, y1: float) -> None:
    """A quadratic curve from the current point (x0, y0) as a cubic."""
    cr.curve_to(
        x0 + 2 / 3 * (cx - x0), y0 + 2 / 3 * (cy - y0),
        x1 + 2 / 3 * (cx - x1), y1 + 2 / 3 * (cy - y1),
        x1, y1,
    )  # fmt: skip


def _polygon(cr, pts, fill: int) -> None:
    cr.move_to(*pts[0])
    for p in pts[1:]:
        cr.line_to(*p)
    cr.close_path()
    _rgb(cr, fill)
    cr.fill_preserve()
    _rgb(cr, EDGE)
    cr.set_line_width(1.5)
    cr.set_line_join(1)  # round
    cr.stroke()


def _limb(cr, cx: float, cy: float, rx: float, ry: float) -> None:
    _oval(cr, cx, cy, rx, ry)
    _rgb(cr, BODY2)
    cr.fill_preserve()
    _rgb(cr, EDGE)
    cr.set_line_width(2)
    cr.stroke()


def draw(cr, p: dict[str, float], props: dict[str, float], time: float = 0.0) -> None:
    """Draws Rime; ``cr`` must already map the 200 x 220 box, y down, onto the target."""
    cr.save()
    cr.translate(0, 20)
    lift = max(0.0, -p["y"])
    _rgb(cr, 0x000000, 0.22 * p["dim"])
    _oval(cr, 100, 180, 46 - lift * 0.35, 7)
    cr.fill()

    cr.save()
    cr.translate(p["x"], p["y"])
    cr.push_group()
    _body(cr, p)
    cr.pop_group_to_source()
    cr.paint_with_alpha(_clamp(p["dim"]))
    cr.restore()

    for name, alpha in props.items():
        alpha = _clamp(alpha)
        if alpha <= 0.02:
            continue
        cr.push_group()
        _prop(cr, name, time)
        cr.pop_group_to_source()
        cr.paint_with_alpha(alpha)
    cr.restore()


def _body(cr, p: dict[str, float]) -> None:
    fx, fy = 100.0, 176.0
    sy = p["squash"]
    sx = 1 + (1 - p["squash"]) * 0.8
    _rotate(cr, p["tilt"], fx, fy)
    cr.translate(fx, fy)
    cr.scale(sx, sy)
    cr.translate(-fx, -fy)

    glow = _clamp(p["glow"])
    if glow > 0.01:
        cr.save()
        _oval(cr, 100, 46, 34, 34)
        cr.clip()
        _radial(cr, _c(ICE, 0.9 * glow), _c(ICE, 0), 100, 46, 34)
        cr.restore()

    cr.save()
    _rotate(cr, p["tuft"], 100, 66)
    _polygon(cr, [(84, 42), (97, 64), (85, 66)], ICE2)
    _polygon(cr, [(117, 40), (115, 66), (103, 64)], ICE2)
    _polygon(cr, [(100, 26), (108, 66), (92, 66)], ICE)
    cr.restore()

    _limb(cr, 80, 174, 14, 7)
    _limb(cr, 120, 174, 14, 7)

    def torso() -> None:
        cr.move_to(100, 60)
        cr.curve_to(142, 60, 162, 96, 162, 126)
        cr.curve_to(162, 160, 136, 176, 100, 176)
        cr.curve_to(64, 176, 38, 160, 38, 126)
        cr.curve_to(38, 96, 58, 60, 100, 60)
        cr.close_path()

    cr.save()
    torso()
    cr.clip()
    _radial(cr, _c(BODY1), _c(BODY2), 85, 92, 105)
    cr.restore()
    torso()
    _rgb(cr, EDGE)
    cr.set_line_width(2)
    cr.stroke()

    _rgb(cr, BODY1, 0.7)
    _oval(cr, 100, 150, 30, 18)
    cr.fill()

    _rgb(cr, BLUSH, _clamp(p["blush"] * 0.6))
    _oval(cr, 62, 134, 9, 5)
    _oval(cr, 138, 134, 9, 5)
    cr.fill()

    _face(cr, p)

    cr.save()
    _rotate(cr, p["armLeft"], 50, 122)
    _limb(cr, 44, 136, 9, 15)
    cr.restore()
    cr.save()
    _rotate(cr, -p["armRight"], 150, 122)
    _limb(cr, 156, 136, 9, 15)
    cr.restore()


def _face(cr, p: dict[str, float]) -> None:
    px, py = p["lookX"], p["lookY"]
    opened = min(1.08, max(0.0, p["eyeOpen"]))
    cr.set_line_cap(1)  # round
    cr.set_line_join(1)
    for cx0 in (78.0, 122.0):
        cx, cy = cx0 + px, 118 + py
        if opened < 0.2:
            curve = p["eyeCurve"]
            cr.move_to(cx - 9, cy + curve * 3)
            _quad(cr, cx - 9, cy + curve * 3, cx, cy - curve * 9, cx + 9, cy + curve * 3)
            _rgb(cr, INK)
            cr.set_line_width(3)
            cr.stroke()
        else:
            _rgb(cr, INK)
            _oval(cr, cx, cy, 8.5, 12.5 * opened)
            cr.fill()
            if opened > 0.6:
                _rgb(cr, BODY1)
                _oval(cr, cx + 2.6, cy - 5 * opened, 3.2, 3.2)
                _oval(cr, cx - 3, cy + 4.5 * opened, 1.6, 1.6)
                cr.fill()

    brow = _clamp(p["brow"])
    if brow > 0.02:
        by, t = 97 + p["browY"] + py * 0.5, p["browTilt"] * 0.32
        _rgb(cr, INK, brow)
        cr.set_line_width(3)
        cr.move_to(70 + px, by - t)
        cr.line_to(86 + px, by + t)
        cr.move_to(130 + px, by - t)
        cr.line_to(114 + px, by + t)
        cr.stroke()

    mx, my = 100 + px * 0.6, 142 + py * 0.5
    w = max(2.0, p["mouthWidth"])
    gap = max(0.0, p["mouthOpen"]) * 9
    curve = p["mouthCurve"]
    cr.move_to(mx - w, my)
    _quad(cr, mx - w, my, mx, my + curve, mx + w, my)
    _quad(cr, mx + w, my, mx, my + curve + gap, mx - w, my)
    cr.close_path()
    _rgb(cr, INK)
    cr.fill_preserve()
    cr.set_line_width(3)
    cr.stroke()


def _chip(
    cr, x: float, y: float, w: float, h: float, radius: float | None = None, fill: int = ICE
) -> None:
    r = radius if radius is not None else min(w, h) / 2
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()
    _rgb(cr, fill)
    cr.fill_preserve()
    _rgb(cr, BODY1)
    cr.set_line_width(2)
    cr.stroke()


def _chip_oval(cr, cx: float, cy: float, r: float, fill: int = ICE) -> None:
    _chip(cr, cx - r, cy - r, 2 * r, 2 * r, fill=fill)


def _star(cr, cx: float, cy: float, r: float, phase: float, time: float) -> None:
    wave = 0.5 + 0.5 * math.sin(time * 5.7 + phase)
    s = r * (0.4 + 0.6 * wave)
    k = s * 0.25
    pts = [(0, -s), (k, -k), (s, 0), (k, k), (0, s), (-k, k), (-s, 0), (-k, -k)]
    cr.move_to(cx + pts[0][0], cy + pts[0][1])
    for x, y in pts[1:]:
        cr.line_to(cx + x, cy + y)
    cr.close_path()
    _rgb(cr, ICE2, 0.3 + 0.7 * wave)
    cr.fill()


def _prop(cr, prop: str, time: float) -> None:
    cr.set_line_cap(1)
    cr.set_line_join(1)
    if prop == "zzz":
        for i, (sx, sy, h) in enumerate(((153, 56, 3.5), (166, 39, 4.5), (179, 20, 5.5))):
            ph = (time / 2.8 + i / 3) % 1
            alpha = ph / 0.3 if ph < 0.3 else (1 - ph) / 0.7
            x, y = sx + 8 * ph, sy + 6 - 20 * ph
            cr.move_to(x - h, y - h)
            cr.line_to(x + h, y - h)
            cr.line_to(x - h, y + h)
            cr.line_to(x + h, y + h)
            _rgb(cr, ICE2, alpha)
            cr.set_line_width(2.2)
            cr.stroke()
    elif prop == "think":
        _chip_oval(cr, 150, 62, 4)
        _chip_oval(cr, 159, 48, 6)
        _chip(cr, 150, 8, 46, 28, radius=14)
        for i, x in enumerate((162.0, 173.0, 184.0)):
            wave = 0.5 + 0.5 * math.sin(time * 5.2 - i * 1.05)
            _rgb(cr, INK, 0.35 + 0.65 * wave)
            _oval(cr, x, 22, 3, 3)
            cr.fill()
    elif prop == "ask":
        _chip_oval(cr, 170, 34, 17)
        cr.move_to(164, 30)
        cr.curve_to(164, 22, 176, 22, 176, 30)
        cr.curve_to(176, 34.5, 170, 34, 170, 38.5)
        _rgb(cr, INK)
        cr.set_line_width(3.2)
        cr.stroke()
        _oval(cr, 170, 44.5, 2, 2)
        cr.fill()
    elif prop == "pause":
        _chip_oval(cr, 170, 34, 17)
        _rgb(cr, INK)
        for x in (162.0, 172.5):
            _chip_path(cr, x, 25, 5.5, 18, 2)
        cr.fill()
    elif prop == "spark":
        _star(cr, 30, 52, 12, 0, time)
        _star(cr, 172, 45, 15, 2, time)
        _star(cr, 180, 105.5, 9.5, 4, time)
    elif prop == "sweat":
        cr.move_to(158, 62)
        cr.curve_to(163, 70, 166, 74, 166, 78)
        cr.curve_to(166, 83, 162, 86, 158, 86)
        cr.curve_to(154, 86, 150, 83, 150, 78)
        cr.curve_to(150, 74, 153, 70, 158, 62)
        cr.close_path()
        _rgb(cr, ICE)
        cr.fill_preserve()
        _rgb(cr, BODY1)
        cr.set_line_width(1.5)
        cr.stroke()
    elif prop == "lock":
        _chip_oval(cr, 170, 34, 17, fill=AMBER)
        _rgb(cr, INK)
        _chip_path(cr, 161, 32, 18, 13, 3)
        cr.fill()
        cr.move_to(164.5, 32)
        cr.line_to(164.5, 28)
        cr.arc(170, 28, 5.5, math.pi, 2 * math.pi)
        cr.line_to(175.5, 32)
        cr.set_line_width(3)
        cr.stroke()


def _chip_path(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def draw_mood(cr, mood: str, time: float = 0.0) -> None:
    """Rime at rest in ``mood``, with that mood's prop."""
    prop = PROP_OF.get(mood)
    draw(cr, pose(mood), {prop: 1.0} if prop else {}, time)


__all__ = ("BOX", "PROP_OF", "draw", "draw_mood", "pose")
