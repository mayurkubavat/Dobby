"""What no AI may touch through Pinlet, at any asking level.

Three things are off limits whatever the mode (turbo included), whatever
``apps.json`` says and whatever the person ticked:

* **Pinlet itself.** Its Allow/Deny card, bar, menu and Activity window.
  Text on the screen can steer an AI, so nothing an AI can click may count as
  the person's consent. The Settings window is the exception: an AI may look
  at it and change the look, the companion and the like (what loosens a
  permission there takes Touch ID). Its Permissions tab stays the person's:
  the app gives the window another title while that tab is in front.
  ``PINLET_SETTINGS_BY_AI=0`` closes Settings to AIs again.
* **macOS security prompts.** The login/administrator sheet, privacy
  ("would like to control…") prompts and the Privacy & Security pane.
* **Payments.** Pinlet does everything up to the Pay button and then hands
  over: the person clicks it.

This module only decides; :class:`pinlet.overlay.hal.OverlayHAL` asks it before
each click, drag and key press. It imports nothing from the platform, so it
runs (and is tested) anywhere.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import Any

from .protocol import OVERLAY_WINDOW_TITLE

# Processes that draw macOS's own security questions. A click there is a
# person's decision by definition.
SECURITY_OWNERS = frozenset(
    {
        "securityagent",  # login / administrator password sheet
        "coreautha",  # Touch ID and password sheet (LocalAuthentication)
        "coreauthuiagent",
        "universalaccessauthwarn",  # "… would like to control this computer"
        "usernotificationcenter",  # privacy (TCC) prompts
        "tccd",
        "authorizationhost",
    }
)
# Windows of ordinary apps that are security surfaces all the same: (app, word in the title).
SECURITY_PANES = (
    ("system settings", "privacy & security"),
    ("system preferences", "security & privacy"),
    ("system settings", "touch id & password"),
    ("system settings", "login password"),
)

# Pinlet.app's Settings window, by its exact title. On the Permissions tab the
# app titles it "Pinlet Settings: Permissions", which is not this.
SETTINGS_TITLE = "Pinlet Settings"

# A helper panel taller than this is one of the click-through drawing layers
# (glow, pointer); shorter ones are the bar and its hotspots.
_DRAWING_LAYER_MIN_H = 120.0


def app_pid(env: Mapping[str, str] | None = None) -> int | None:
    """Pinlet.app's process id when it owns this engine (it passes ``PINLET_APP_PID``)."""
    env = os.environ if env is None else env
    if env.get("PINLET_APP_OWNED") != "1":
        return None
    try:
        return int(env.get("PINLET_APP_PID", "")) or None
    except ValueError:
        return None


def settings_open(env: Mapping[str, str] | None = None) -> bool:
    """Whether an AI may use Pinlet's Settings window (``PINLET_SETTINGS_BY_AI=0`` says no)."""
    env = os.environ if env is None else env
    return str(env.get("PINLET_SETTINGS_BY_AI", "1")).strip().lower() not in {
        "0",
        "off",
        "false",
        "no",
    }


def is_settings(title: str, pid: int, own: set[int]) -> bool:
    """True for Pinlet's Settings window, off its Permissions tab, while AIs may use it."""
    return pid in own and " ".join(title.split()) == SETTINGS_TITLE and settings_open()


def _owner(w: Mapping[str, Any]) -> str:
    return " ".join(str(w.get("kCGWindowOwnerName", "") or "").split())


OWN_REFUSAL = (
    "That is Pinlet's own window. An AI cannot press Pinlet's buttons or answer its "
    "questions: only the person can. Carry on with the task, or wait for the person."
)


def _why(owner: str, title: str, pid: int, overlay: bool, own: set[int]) -> str | None:
    if not overlay and is_settings(title, pid, own):
        return None
    low, title = owner.lower(), title.lower()
    if overlay or pid in own:
        return OWN_REFUSAL
    if low in SECURITY_OWNERS:
        return (
            "That is a macOS security prompt. Pinlet never answers those: ask the person to "
            "do it, then carry on."
        )
    for app, word in SECURITY_PANES:
        if low == app and word in title:
            return (
                "That is the macOS privacy and security settings. Pinlet does not change "
                "those: ask the person to do it."
            )
    return None


def refusal_at(
    records: Iterable[Mapping[str, Any]],
    x: float,
    y: float,
    own_pids: Iterable[int | None] = (),
    helper_pid: int | None = None,
) -> tuple[str, str] | None:
    """Why a click at global point (x, y) is refused, with the window's owner; None when it may go.

    ``records`` are Quartz window records, front to back, on every layer (so
    the card and the menu count). The walk stops at the first ordinary app
    window under the point: a Pinlet window hidden behind it is not in the
    way. System layers that cover the screen without taking the click (the
    menu bar agent, widgets) and the overlay's own click-through drawing
    layers are passed over.
    """
    own = {int(p) for p in own_pids if p}
    for owner, title, pid, layer, drawing in _under(records, x, y, helper_pid):
        why = _why(owner, title, pid, drawing, own)
        if why:
            return why, owner
        if layer == 0:
            return None  # an ordinary window takes the click
    return None


def _under(
    records: Iterable[Mapping[str, Any]], x: float, y: float, helper_pid: int | None
) -> Iterable[tuple[str, str, int, int, bool]]:
    """The windows under a point, front to back: (owner, title, pid, layer, is the overlay's)."""
    for w in records:
        b = w.get("kCGWindowBounds") or {}
        try:
            wx, wy = float(b.get("X", 0)), float(b.get("Y", 0))
            ww, wh = float(b.get("Width", 0)), float(b.get("Height", 0))
            alpha = float(w.get("kCGWindowAlpha", 1.0))
            pid = int(w.get("kCGWindowOwnerPID", -1))
            layer = int(w.get("kCGWindowLayer", 0))
        except (TypeError, ValueError):
            continue
        if alpha <= 0 or ww <= 0 or wh <= 0:
            continue
        if not (wx <= x < wx + ww and wy <= y < wy + wh):
            continue
        title = str(w.get("kCGWindowName", "") or "")
        drawing = title.startswith(OVERLAY_WINDOW_TITLE) or (
            helper_pid is not None and pid == helper_pid
        )
        if drawing and wh > _DRAWING_LAYER_MIN_H:
            continue
        yield _owner(w), title, pid, layer, drawing


def settings_at(
    records: Iterable[Mapping[str, Any]],
    x: float,
    y: float,
    own_pids: Iterable[int | None] = (),
    helper_pid: int | None = None,
) -> bool:
    """True when the window that takes a click at (x, y) is Pinlet's Settings window, open to AIs."""
    own = {int(p) for p in own_pids if p}
    for _owner_name, title, pid, layer, drawing in _under(records, x, y, helper_pid):
        if not drawing and is_settings(title, pid, own):
            return True
        if layer == 0 or drawing or pid in own:
            return False
    return False


def settings_in_front(
    records: Iterable[Mapping[str, Any]], pid: int | None, own_pids: Iterable[int | None] = ()
) -> bool:
    """True when the frontmost ordinary window of app ``pid`` is Pinlet's Settings window, open to AIs."""
    own = {int(p) for p in own_pids if p}
    if not pid or int(pid) not in own:
        return False
    for w in records:
        try:
            if (
                int(w.get("kCGWindowOwnerPID", -1)) != int(pid)
                or int(w.get("kCGWindowLayer", 0)) != 0
            ):
                continue
            if float(w.get("kCGWindowAlpha", 1.0)) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        return is_settings(str(w.get("kCGWindowName", "") or ""), int(pid), own)
    return False


def own_element(hit: Mapping[str, Any] | None, own_pids: Iterable[int | None] = ()) -> str | None:
    """Why the element under a click is off limits, or None.

    The window list misses Pinlet's menu bar icon, which macOS draws in a
    system window: the element itself still belongs to Pinlet.app, and when
    the owner is not known its name in the menu bar gives it away.
    """
    if not hit:
        return None
    own = {int(p) for p in own_pids if p}
    pid = hit.get("pid")
    if pid and int(pid) in own:
        return OWN_REFUSAL
    role = str(hit.get("role") or "").lower()
    name = str(hit.get("name") or "").strip().lower()
    if "menu" in role and (name == "pinlet" or name.startswith("pinlet:")):
        return OWN_REFUSAL
    return None


def refusal(window: Mapping[str, Any] | None, own_pids: Iterable[int | None] = ()) -> str | None:
    """Why Pinlet will not act on an app (``{"pid", "owner", "title"}``), or None when it may."""
    if not window:
        return None
    try:
        pid = int(window.get("pid") or -1)
    except (TypeError, ValueError):
        pid = -1
    return _why(
        " ".join(str(window.get("owner") or "").split()),
        str(window.get("title") or ""),
        pid,
        bool(window.get("overlay")),
        {int(p) for p in own_pids if p},
    )
