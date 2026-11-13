"""Background clicks: act on an app through accessibility, without the real mouse.

With the input lock on (``PINLET_INPUT_LOCK=window``), Pinlet tries
:func:`ax_press_at` first for a plain left click: it asks the target app's
own accessibility tree what is at the point and, when that is a button-like
element that supports ``AXPress``, presses it. The app need not be in front
and the person's pointer stays where they left it; only Pinlet's drawn
pointer moves. Anything else falls back to the real mouse.
``PINLET_BACKGROUND_CLICKS=0`` turns this off.

Background mode (``PINLET_BACKGROUND=1``, ``pinlet.hal.macos_background``)
never uses the real mouse, so it also needs the rest of this module: giving a
text field the keyboard, selecting a row, opening a context menu and
scrolling, all through accessibility.
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


# A click in one of these gives it the keyboard.
EDITABLE = frozenset(
    {"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField", "AXSecureTextField"}
)
_ROW_ROLES = frozenset({"AXRow", "AXCell", "AXOutlineRow"})
_SCROLL_UP = 12  # how far up the tree a scroll area is looked for
_LINE_PT = 40.0  # points one wheel "line" moves the content


def element_at(ax: Any, pid: int, x: float, y: float) -> Any:
    """The deepest element of app ``pid`` at screen point (x, y), or None.

    Asked of the app itself, so it answers for a window that is covered or on
    another Desktop.
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
    return el if err == 0 else None


def _set(ax: Any, el: Any, name: str, value: Any) -> bool:
    try:
        return ax.AXUIElementSetAttributeValue(el, name, value) == 0
    except Exception:  # noqa: BLE001
        return False


def _perform(ax: Any, el: Any, action: str) -> bool:
    if action not in _actions(ax, el):
        return False
    try:
        return ax.AXUIElementPerformAction(el, action) == 0
    except Exception:  # noqa: BLE001
        return False


def ax_click_at(ax: Any, pid: int, x: float, y: float) -> str | None:
    """What a plain left click at (x, y) in app ``pid`` does, done without the mouse.

    Returns "pressed" (a button-like element), "focused" (a text field now has
    the keyboard), "selected" (a list or table row), or None when the point
    needs a real click (a canvas, a drag handle, an app without accessibility).
    """
    if ax_press_at(ax, pid, x, y) is not None:
        return "pressed"
    el = element_at(ax, pid, x, y)
    hops = 0
    while el is not None and hops <= MAX_UP + 2:
        role = _attr(ax, el, "AXRole")
        if role in EDITABLE:
            return "focused" if _set(ax, el, "AXFocused", True) else None
        if role in _ROW_ROLES:
            row = el if role != "AXCell" else (_attr(ax, el, "AXParent") or el)
            return "selected" if _set(ax, row, "AXSelected", True) else None
        if role is None or role in ("AXWindow", "AXSheet", "AXApplication"):
            return None
        if role == "AXWebArea":
            # Clicking empty page space only moves the keyboard to the page.
            return "focused" if _set(ax, el, "AXFocused", True) else None
        el = _attr(ax, el, "AXParent")
        hops += 1
    return None


def ax_action_at(ax: Any, pid: int, x: float, y: float, action: str) -> bool:
    """Performs ``action`` ("AXShowMenu", "AXOpen") on the element at (x, y) or a near parent."""
    el = element_at(ax, pid, x, y)
    for _ in range(MAX_UP + 1):
        if el is None:
            return False
        if _perform(ax, el, action):
            return True
        if _attr(ax, el, "AXRole") in _STOP:
            return False
        el = _attr(ax, el, "AXParent")
    return False


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _height(ax: Any, el: Any) -> float | None:
    size = _attr(ax, el, "AXSize")
    if size is None:
        return None
    try:
        ok, s = ax.AXValueGetValue(size, ax.kAXValueCGSizeType, None)
        return float(s.height) if ok else None
    except Exception:  # noqa: BLE001
        return None


def ax_scroll_at(ax: Any, pid: int, x: float, y: float, dx: int, dy: int) -> bool:
    """Scrolls the scroll area under (x, y) in app ``pid`` by moving its scroll bar.

    ``dy`` > 0 moves the content up (toward the start), as the mouse wheel
    does. False when there is no scroll area with a scroll bar to move.
    """
    el = element_at(ax, pid, x, y)
    for _ in range(_SCROLL_UP):
        if el is None:
            return False
        if _attr(ax, el, "AXRole") == "AXScrollArea":
            break
        el = _attr(ax, el, "AXParent")
    else:
        return False
    moved = False
    for lines, bar_attr in ((dy, "AXVerticalScrollBar"), (-dx, "AXHorizontalScrollBar")):
        if not lines:
            continue
        bar = _attr(ax, el, bar_attr)
        now = _number(_attr(ax, bar, "AXValue")) if bar is not None else None
        if now is None:
            continue
        # The bar's value runs 0..1 over what is hidden; turn lines into that fraction.
        step = 0.1 * abs(lines) / 3.0
        if bar_attr == "AXVerticalScrollBar":
            shown = _height(ax, el)
            contents = _attr(ax, el, "AXContents") or []
            total = _height(ax, contents[0]) if len(contents) else None
            if shown and total and total > shown:
                step = abs(lines) * _LINE_PT / (total - shown)
        target = min(1.0, max(0.0, now - step if lines > 0 else now + step))
        moved = _set(ax, bar, "AXValue", target) or moved
    return moved


_CMD_NAMES = frozenset({"cmd", "command", "super", "meta", "win"})
_SHIFT, _OPTION, _CONTROL, _NO_CMD = 1, 2, 4, 8  # AXMenuItemCmdModifiers bits
_MENU_DEPTH = 3
_MENU_ITEMS = 1500


def _shortcut(keys: list[str]) -> tuple[str, int] | None:
    """A Cmd chord as (character, AX modifier bits), or None when it is not one."""
    mods, chars = 0, []
    has_cmd = False
    for key in keys:
        k = str(key).lower().strip()
        if k in _CMD_NAMES:
            has_cmd = True
        elif k == "shift":
            mods |= _SHIFT
        elif k in ("alt", "option", "opt"):
            mods |= _OPTION
        elif k in ("ctrl", "control"):
            mods |= _CONTROL
        else:
            chars.append(k)
    if not has_cmd or len(chars) != 1 or len(chars[0]) != 1:
        return None
    return chars[0].upper(), mods


def ax_menu_shortcut(ax: Any, pid: int, keys: list[str]) -> bool:
    """Picks the menu item of app ``pid`` whose keyboard shortcut is ``keys``.

    An app that is not in front does not act on Cmd shortcuts sent as key
    events, but its menu items can still be pressed. False when the chord is
    not a Cmd shortcut, no enabled item has it, or the press failed (the
    caller then sends the keys anyway).
    """
    wanted = _shortcut(keys)
    if wanted is None:
        return False
    char, mods = wanted
    try:
        app = ax.AXUIElementCreateApplication(int(pid))
    except Exception:  # noqa: BLE001
        return False
    bar = _attr(ax, app, "AXMenuBar")
    if bar is None:
        return False
    budget = [_MENU_ITEMS]

    def search(el: Any, depth: int) -> Any:
        for child in _attr(ax, el, "AXChildren") or []:
            if budget[0] <= 0:
                return None
            budget[0] -= 1
            if str(_attr(ax, child, "AXMenuItemCmdChar") or "").upper() == char:
                item_mods = int(_number(_attr(ax, child, "AXMenuItemCmdModifiers")) or 0)
                if item_mods == mods:  # the "no Cmd" bit is not set, so Cmd is part of it
                    return child
            if depth < _MENU_DEPTH:
                for menu in _attr(ax, child, "AXChildren") or []:
                    hit = search(menu, depth + 1)
                    if hit is not None:
                        return hit
        return None

    item = search(bar, 0)
    if item is None or _attr(ax, item, "AXEnabled") is False:
        return False
    return _perform(ax, item, "AXPress")


def ax_select_all(ax: Any, pid: int) -> bool:
    """Selects all the text in the field of app ``pid`` that has the keyboard.

    What Cmd+A does in a text field, for an app that is not in front (its
    Select All menu item is disabled then). False when no text field has the
    keyboard or the selection could not be set.
    """
    try:
        app = ax.AXUIElementCreateApplication(int(pid))
    except Exception:  # noqa: BLE001
        return False
    el = _attr(ax, app, "AXFocusedUIElement")
    if el is None or _attr(ax, el, "AXRole") not in EDITABLE:
        return False
    count = _number(_attr(ax, el, "AXNumberOfCharacters"))
    if count is None:
        value = _attr(ax, el, "AXValue")
        if not isinstance(value, str):
            return False
        count = len(value.encode("utf-16-le")) // 2
    try:
        whole = ax.AXValueCreate(ax.kAXValueCFRangeType, (0, int(count)))
    except Exception:  # noqa: BLE001
        return False
    return whole is not None and _set(ax, el, "AXSelectedTextRange", whole)


def window_frame_at(ax: Any, pid: int, x: float, y: float) -> tuple[int, int, int, int] | None:
    """The frame of the app window that owns the element at (x, y), or None when unknown.

    An app answers a hit test for whichever of its windows is on top at that
    point, which may not be the one the agent works in.
    """
    el = element_at(ax, pid, x, y)
    if el is None:
        return None
    win = el if _attr(ax, el, "AXRole") == "AXWindow" else _attr(ax, el, "AXWindow")
    if win is None:
        return None
    try:
        ok1, p = ax.AXValueGetValue(_attr(ax, win, "AXPosition"), ax.kAXValueCGPointType, None)
        ok2, s = ax.AXValueGetValue(_attr(ax, win, "AXSize"), ax.kAXValueCGSizeType, None)
    except Exception:  # noqa: BLE001
        return None
    if not (ok1 and ok2):
        return None
    return int(p.x), int(p.y), int(s.width), int(s.height)


def enable_web_content(ax: Any, pid: int, browser: bool = False) -> bool:
    """Asks a Chromium or Electron app to build its page's accessibility tree.

    Electron apps build it once a client sets ``AXManualAccessibility``; native
    apps refuse the attribute, which is harmless. Chromium browsers do not know
    that attribute and want ``AXEnhancedUserInterface`` instead, the switch
    screen readers use. It can disturb window animations, so it is set only
    for a ``browser`` the agent picked as its own window.
    """
    try:
        app = ax.AXUIElementCreateApplication(int(pid))
    except Exception:  # noqa: BLE001
        return False
    if _set(ax, app, "AXManualAccessibility", True):
        return True
    return browser and _set(ax, app, "AXEnhancedUserInterface", True)
