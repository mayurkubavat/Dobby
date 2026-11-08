"""Pure geometry helpers for the overlay (no AppKit, unit-testable anywhere).

Two coordinate systems meet here:

* Quartz / AX "global" space: points, origin at the top-left of the primary
  display, y grows downward. Dobby actions and CGWindowList bounds use this.
* Cocoa space: points, origin at the bottom-left of the primary display,
  y grows upward. NSScreen.frame() and NSWindow frames use this.

Each overlay panel covers one screen and draws in its own local, non-flipped
layer coordinates (origin bottom-left of that screen).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

Rect = tuple[float, float, float, float]  # x, y, w, h
Point = tuple[float, float]


def global_point_to_local(pt: Point, screen_frame: Rect, primary_height: float) -> Point:
    """Global top-left point -> local bottom-left point inside one screen's panel."""
    x, y = pt
    fx, fy, _, _ = screen_frame
    return (x - fx, (primary_height - y) - fy)


def global_rect_to_local(rect: Rect, screen_frame: Rect, primary_height: float) -> Rect:
    x, y, w, h = rect
    fx, fy, _, _ = screen_frame
    return (x - fx, primary_height - y - h - fy, w, h)


def cocoa_rect_to_global(rect: Rect, primary_height: float) -> Rect:
    x, y, w, h = rect
    return (x, primary_height - y - h, w, h)


def contains(rect: Rect, pt: Point) -> bool:
    x, y, w, h = rect
    return x <= pt[0] < x + w and y <= pt[1] < y + h


def intersects(a: Rect, b: Rect) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def screen_index_for_point(pt: Point, global_frames: Sequence[Rect]) -> int | None:
    for i, fr in enumerate(global_frames):
        if contains(fr, pt):
            return i
    return None


def pixels_to_points(pt: Point, scale: float) -> Point:
    """Screenshot pixels -> points (divide by the backing scale, 2.0 on Retina)."""
    if scale <= 0:
        raise ValueError("scale must be positive")
    return (pt[0] / scale, pt[1] / scale)


def glide_ms(
    start: Point,
    end: Point,
    min_ms: float = 250,
    max_ms: float = 450,
    full_distance: float = 1200,
    speed: float = 1.0,
) -> int:
    """Animation length for a pointer glide; 0 when speed is 0 (tests, replay)."""
    if speed <= 0:
        return 0
    dist = math.hypot(end[0] - start[0], end[1] - start[1])
    if dist < 1:
        return 0
    frac = min(dist / full_distance, 1.0)
    return int(round((min_ms + (max_ms - min_ms) * frac) / speed))


def occluded(target: Rect, above: Iterable[Rect], min_fraction: float = 0.25) -> bool:
    """True when windows stacked above cover at least ``min_fraction`` of target.

    Approximate: sums overlap areas, so overlapping occluders may double count.
    Good enough to decide whether to dim the glow.
    """
    tx, ty, tw, th = target
    area = tw * th
    if area <= 0:
        return False
    covered = 0.0
    for ox, oy, ow, oh in above:
        w = min(tx + tw, ox + ow) - max(tx, ox)
        h = min(ty + th, oy + oh) - max(ty, oy)
        if w > 0 and h > 0:
            covered += w * h
    return covered / area >= min_fraction


def chip_rect(
    tip: Point, width: float, arrow_w: float, arrow_h: float, chip_h: float, gap: float = 4.0
) -> Rect:
    """Global top-left rect of the name tag drawn below-right of the pointer tip.

    Matches the helper's layout: the tag's top-left sits ``arrow_w - gap``
    right of the tip and ``arrow_h + gap`` below it.
    """
    return (tip[0] + arrow_w - gap, tip[1] + arrow_h + gap, float(width), float(chip_h))
