"""Is something covering the target before we click it?

Two cheap checks, both before the click and both from data we already have:

1. Window order: ``hal.list_windows()`` is front to back. If a window in front
   of the target's window contains the click point, the click would land on
   that window instead.
2. Accessibility hit test (macOS, optional): the element at the click point.
   If it is a sheet, popover, menu or dialog that is not the target itself, or
   its frame does not overlap the target at all, the target is covered.

A check that cannot run (no window list, no hit test) says "unknown" and never
blocks a click by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

COVERING_ROLES = {"dialog", "sheet", "popover", "menu", "alert", "AXSheet", "AXPopover", "AXMenu"}


@dataclass(frozen=True)
class Occlusion:
    covered: bool | None  # None when no check could run
    reason: str
    by: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"covered": self.covered, "reason": self.reason, "by": self.by}


def _contains(box: tuple[int, int, int, int], x: int, y: int) -> bool:
    bx, by, bw, bh = box
    return bx <= x < bx + bw and by <= y < by + bh


def _overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def _win_box(w: Any) -> tuple[int, int, int, int] | None:
    try:
        b = w.bbox
        return (int(b.x), int(b.y), int(b.width), int(b.height))
    except Exception:
        return None


def check_occlusion(
    hal: Any,
    target_box: tuple[int, int, int, int],
    point: tuple[int, int],
    target_window_id: str | None = None,
    target_role: str = "",
    hit_test: Any = None,
    in_menu_bar: bool = False,
) -> Occlusion:
    """Checks whether ``point`` (inside ``target_box``) is covered.

    ``target_window_id`` is the window the target belongs to (the active window
    for screen state). ``hit_test(x, y)`` returns ``{"role", "name", "bbox"}``
    or None. Menu bar targets skip the window check (the menu bar sits above
    every window).
    """
    x, y = point
    ran = False
    if not in_menu_bar and target_window_id is not None:
        try:
            windows = list(hal.list_windows())
        except Exception:
            windows = []
        if windows:
            ran = True
            for w in windows:  # front to back
                if str(w.window_id) == str(target_window_id):
                    break
                box = _win_box(w)
                if box and _contains(box, x, y):
                    return Occlusion(
                        True,
                        f"covered by window '{w.title}'",
                        {"window_id": str(w.window_id), "title": w.title, "app": w.app_name},
                    )

    if hit_test is not None:
        try:
            hit = hit_test(x, y)
        except Exception:
            hit = None
        if hit:
            ran = True
            role = str(hit.get("role", "") or "")
            hb = hit.get("bbox")
            hbox = (
                (int(hb["x"]), int(hb["y"]), int(hb["width"]), int(hb["height"]))
                if isinstance(hb, dict)
                else None
            )
            if role in COVERING_ROLES and role != target_role:
                return Occlusion(True, f"covered by {role} '{hit.get('name', '')}'", dict(hit))
            if hbox is not None and not _overlap(hbox, target_box):
                return Occlusion(
                    True, f"point hits {role} '{hit.get('name', '')}' outside the target", dict(hit)
                )

    if not ran:
        return Occlusion(None, "no occlusion check available")
    return Occlusion(False, "clear")
