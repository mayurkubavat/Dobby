"""What no AI may touch through Pinlet, at any asking level.

Three things are off limits whatever the mode (turbo included), whatever
``apps.json`` says and whatever the person ticked:

* **Pinlet itself.** Its Allow/Deny card, bar, menu, Settings and Activity
  windows. Text on the screen can steer an AI, so nothing an AI can click may
  count as the person's consent.
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
import re
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

# Buttons that spend money. Narrower than the "consequential" list: these are
# never clicked by Pinlet, not even after a question.
_PAYMENT = re.compile(
    r"^\W*("
    r"pay( now| with\b.*| \W?[\d$€£₹¥].*)?|buy( now| with\b.*| it now)?|purchase|"
    r"place (your |the )?order|complete (your |the )?(purchase|order|payment)|"
    r"confirm (and pay|purchase|payment|order|transfer)|submit (payment|order)|"
    r"check ?out( now)?|proceed to (checkout|payment)|subscribe( now| & pay)?|"
    r"send money|transfer( money| funds| now)?|donate( now)?|place (bid|trade)|"
    r"(buy|sell) (shares|stock|order)|confirm trade|top up|add funds"
    r")\W*$",
    re.I,
)
_PRESSABLE = ("button", "menu item", "link")

# A helper panel taller than this is one of the click-through drawing layers
# (glow, pointer); shorter ones are the bar and its hotspots.
_DRAWING_LAYER_MIN_H = 120.0


def payment_label(role: str | None, name: str | None) -> str | None:
    """The button's text when pressing it pays for something, else None."""
    name = " ".join(str(name or "").split())
    role = str(role or "").lower()
    if not name or len(name) > 48 or not any(r in role for r in _PRESSABLE):
        return None
    return name if _PAYMENT.match(name) else None


def app_pid(env: Mapping[str, str] | None = None) -> int | None:
    """Pinlet.app's process id when it owns this engine (it passes ``PINLET_APP_PID``)."""
    env = os.environ if env is None else env
    if env.get("PINLET_APP_OWNED") != "1":
        return None
    try:
        return int(env.get("PINLET_APP_PID", "")) or None
    except ValueError:
        return None


def _owner(w: Mapping[str, Any]) -> str:
    return " ".join(str(w.get("kCGWindowOwnerName", "") or "").split())


def _why(owner: str, title: str, pid: int, overlay: bool, own: set[int]) -> str | None:
    low, title = owner.lower(), title.lower()
    if overlay or pid in own:
        return (
            "That is Pinlet's own window. An AI cannot press Pinlet's buttons or answer its "
            "questions: only the person can. Carry on with the task, or wait for the person."
        )
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
        owner = _owner(w)
        why = _why(owner, title, pid, drawing, own)
        if why:
            return why, owner
        if layer == 0:
            return None  # an ordinary window takes the click
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


PAYMENT_REFUSAL = (
    "This is a payment step ({label}). Pinlet does not press payment buttons: everything is "
    "ready for the person, so ask them to press it, then carry on."
)
