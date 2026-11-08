"""Background clicks: press a button without moving the real mouse.

With the input lock on (``DOBBY_INPUT_LOCK=window``), Dobby tries this first
for a plain left click: it asks the target app's own accessibility tree what
is at the point and, when that is a button-like element that supports
``AXPress``, presses it. The app need not be in front and the person's
pointer stays where they left it; only Dobby's drawn pointer moves.

Anything else (text fields, canvases, lists, games, apps without
accessibility, right or double clicks) falls back to the real mouse.
``DOBBY_BACKGROUND_CLICKS=0`` turns this off.
"""

from __future__ import annotations

from typing import Any

# Roles where AXPress does exactly what a click does.
PRESSABLE = frozenset(
    {
        "AXButton",
        "AXCheckBox",
        "AXRadioButton",
        "AXMenuItem",
        "AXDisclosureTriangle",
        "AXLink",
    }
)
# Walking up from a label stops at these: a click there is not a press.
_STOP = frozenset(
    {
        "AXWindow",
        "AXSheet",
        "AXApplication",
        "AXWebArea",
        "AXScrollArea",
        "AXList",
        "AXTable",
        "AXOutline",
        "AXTextField",
        "AXTextArea",
        "AXComboBox",
    }
)
MAX_UP = 2


def _attr(ax: Any, el: Any, name: str) -> Any:
    try:
        err, value = ax.AXUIElementCopyAttributeValue(el, name, None)
        return value if err == 0 else None
    except Exception:  # noqa: BLE001
        return None


def _actions(ax: Any, el: Any) -> list:
    try:
        err, names = ax.AXUIElementCopyActionNames(el, None)
        return list(names or []) if err == 0 else []
    except Exception:  # noqa: BLE001
        return []


def ax_press_at(ax: Any, pid: int, x: float, y: float) -> str | None:
    """Press the element of app ``pid`` at screen point (x, y), top-left points.

    Returns the pressed role, or None when nothing pressable is there (the
    caller then clicks with the real mouse). The hit test is scoped to the
    app, so a window of another app covering the point does not matter.
    """
    try:
        app = ax.AXUIElementCreateApplication(int(pid))
        try:
            ax.AXUIElementSetMessagingTimeout(app, 0.5)
        except Exception:  # noqa: BLE001
            pass
        err, el = ax.AXUIElementCopyElementAtPosition(app, float(x), float(y), None)
    except Exception:  # noqa: BLE001
        return None
    if err != 0 or el is None:
        return None
    for _ in range(MAX_UP + 1):
        role = _attr(ax, el, "AXRole")
        if role in PRESSABLE:
            if _attr(ax, el, "AXEnabled") is False or "AXPress" not in _actions(ax, el):
                return None
            try:
                return role if ax.AXUIElementPerformAction(el, "AXPress") == 0 else None
            except Exception:  # noqa: BLE001
                return None
        if role is None or role in _STOP:
            return None
        el = _attr(ax, el, "AXParent")
        if el is None:
            return None
    return None
