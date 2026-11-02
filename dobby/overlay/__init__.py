"""Agent cursor and window glow overlay for Dobby (macOS and Linux X11).

Usage from the server or HAL factory::

    from dobby.overlay import wrap_hal
    hal = wrap_hal(hal)          # no-op unless the overlay is enabled

Enable/disable with ``DOBBY_OVERLAY=1|0``. Default: on for macOS and for
Linux with an X11 DISPLAY (helper: ``x11_helper.py``, GTK 3); off under
pytest, in cassette replay (``DOBBY_REPLAY=1``) and on other platforms.
Look settings come from ``DOBBY_OVERLAY_COLOR`` (hex), ``DOBBY_OVERLAY_SPEED``
(1.0 normal, 0 = no animation) and ``DOBBY_OVERLAY_LABEL``.
``DOBBY_OVERLAY_CAPTURE_VISIBLE=0`` hides the overlay from screen recordings
too (default: recordings and screen shares show it).

Controls: ``DOBBY_OVERLAY_RESUME=auto|manual`` (after user take-over, resume
by itself after ``DOBBY_OVERLAY_IDLE_SECS`` of quiet, default 2, or wait for
Resume), ``DOBBY_OVERLAY_TRAIL=0`` hides the numbered step trail,
``DOBBY_OVERLAY_TRACKS=0`` stops writing replay tracks to
``~/.dobby/overlay/tracks``.

``DOBBY_OVERLAY_TURBO=1`` starts in Turbo mode (also in Dobby's menu): no
Allow/Deny prompts for risky steps and no pause when you touch the mouse or
keyboard; only Stop / Pause halt Dobby.

Look: ``DOBBY_OVERLAY_STYLE=modern`` (default: compact sleeping bar that
grows on hover, icon buttons, frosted glass, live step and timer while
working, one colour per running Dobby, a glow that follows Dobby's state and
the window's corners, a short fading trail, a tag that flips away from screen
edges) or ``classic`` (the earlier look). ``DOBBY_OVERLAY_AGENT_COLORS=0``
keeps every Dobby violet; ``DOBBY_OVERLAY_COLOR`` fixes one colour.
The modern look narrates by default: the bar says "Dobby is working · Click
‘Send’ · step 7 · 0:42" (or "Dobby is thinking…", "Dobby is sleeping"), the
pointer tag keeps the current action until the next one or until Dobby goes
idle, clicks leave numbered marks that fade, and the Dobby dots (with a
"2 Dobbys" caption) appear only while two or more Dobbys are busy.
``DOBBY_OVERLAY_NARRATE=0`` turns that off for a quieter modern look.

Click the sleeping control bar to wake Dobby. If the AI waits in the
``wait_for_wake`` tool, that call returns and the bar says it hands back to
the AI (until its next action, or ``DOBBY_OVERLAY_AWAKE_SECS``, default 120).
If no AI waits, the bar says so for a few seconds and Dobby sleeps again: an
MCP server cannot start a new AI turn by itself.
The bar's ▾ button opens Dobby's menu. ``DOBBY_OVERLAY_MENU_BAR=1`` also puts
a Dobby icon in the macOS menu bar (off by default).

Hotkeys while Dobby runs: Ctrl+Option+Esc stops, Ctrl+Option+P pauses or
resumes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import contextlib

from .client import NullOverlay, OverlayClient, OverlayInterrupted, OverlayStopped
from .hal import OverlayHAL, unwrap
from .protocol import OVERLAY_WINDOW_TITLE, PROTOCOL_VERSION

__all__ = [
    "NullOverlay", "OverlayClient", "OverlayHAL", "OverlayStopped", "OverlayInterrupted",
    "unwrap", "make_overlay", "find_overlay_hal", "step",
    "wrap_hal", "overlay_enabled", "OVERLAY_WINDOW_TITLE", "PROTOCOL_VERSION",
]

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def overlay_enabled(env: Optional[Dict[str, str]] = None, platform: str = sys.platform) -> bool:
    env = os.environ if env is None else env
    flag = env.get("DOBBY_OVERLAY", "").strip().lower()
    if flag in _FALSE:
        return False
    if platform != "darwin" and not (platform.startswith("linux") and env.get("DISPLAY")):
        return False
    if flag in _TRUE:
        return True
    if "PYTEST_CURRENT_TEST" in env or env.get("DOBBY_REPLAY", "").lower() in _TRUE:
        return False
    return True


def overlay_config(env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    env = os.environ if env is None else env
    cfg: Dict[str, Any] = {}
    if env.get("DOBBY_OVERLAY_COLOR"):
        cfg["color"] = env["DOBBY_OVERLAY_COLOR"]
        cfg["color_fixed"] = True  # no per-Dobby colour from the registry
    look = env.get("DOBBY_OVERLAY_STYLE", "").strip().lower()
    if look in ("modern", "classic"):
        cfg["style"] = look
    if env.get("DOBBY_OVERLAY_AGENT_COLORS", "").strip().lower() in _FALSE:
        cfg["agent_colors"] = False
    if env.get("DOBBY_OVERLAY_NARRATE", "").strip().lower() in _FALSE:
        cfg["narrate"] = False
    if env.get("DOBBY_OVERLAY_LABEL"):
        cfg["label"] = env["DOBBY_OVERLAY_LABEL"]
    elif env.get("DOBBY_AGENT_NAME", "").strip():
        cfg["label"] = env["DOBBY_AGENT_NAME"].strip()  # which agent this cursor belongs to
    if env.get("DOBBY_OVERLAY_CAPTURE_VISIBLE", "").strip().lower() in _FALSE:
        cfg["capture_visible"] = False
    resume = env.get("DOBBY_OVERLAY_RESUME", "").strip().lower()
    if resume in ("auto", "manual"):
        cfg["takeover_resume"] = resume
    if env.get("DOBBY_OVERLAY_IDLE_SECS"):
        try:
            cfg["idle_secs"] = max(0.5, float(env["DOBBY_OVERLAY_IDLE_SECS"]))
        except ValueError:
            pass
    if env.get("DOBBY_OVERLAY_AWAKE_SECS"):
        try:
            cfg["awake_secs"] = max(5.0, float(env["DOBBY_OVERLAY_AWAKE_SECS"]))
        except ValueError:
            pass
    if env.get("DOBBY_OVERLAY_TURBO", "").strip().lower() in _TRUE:
        cfg["turbo"] = True
    if env.get("DOBBY_OVERLAY_TRAIL", "").strip().lower() in _FALSE:
        cfg["trail"] = False
    pill = env.get("DOBBY_OVERLAY_PILL", "").strip().lower()
    if pill in ("always", "auto", "off"):
        cfg["pill"] = pill
    elif pill in _FALSE:
        cfg["pill"] = "off"
    if env.get("DOBBY_OVERLAY_MENU_BAR", "").strip().lower() in _TRUE:
        cfg["menu_bar"] = True
    if env.get("DOBBY_OVERLAY_PILL_RECORD", "").strip().lower() in _TRUE:
        cfg["pill_in_recordings"] = True
    if env.get("DOBBY_OVERLAY_SPEED"):
        try:
            cfg["speed"] = float(env["DOBBY_OVERLAY_SPEED"])
        except ValueError:
            pass
    return cfg


def make_overlay(enabled: Optional[bool] = None) -> NullOverlay:
    if enabled is None:
        enabled = overlay_enabled()
    if not enabled:
        return NullOverlay()
    log_path = Path(os.path.expanduser("~/.dobby/logs/overlay.log"))
    tracks = None
    if os.environ.get("DOBBY_OVERLAY_TRACKS", "").strip().lower() not in _FALSE:
        tracks = Path(os.path.expanduser("~/.dobby/overlay/tracks"))
    return OverlayClient(config=overlay_config(), log_path=log_path, track_dir=tracks)


def wrap_hal(hal: Any, overlay: Optional[NullOverlay] = None, **kwargs: Any) -> Any:
    """Wrap ``hal`` in :class:`OverlayHAL` when the overlay is enabled."""
    if isinstance(hal, OverlayHAL):
        return hal
    overlay = overlay if overlay is not None else make_overlay()
    if isinstance(overlay, OverlayClient) or overlay.__class__ is not NullOverlay:
        if isinstance(overlay, OverlayClient) and overlay.config.get("pill", "always") == "always":
            overlay.start_async()  # show the control bar as soon as Dobby runs
        return OverlayHAL(hal, overlay, **kwargs)
    return hal


def find_overlay_hal(hal: Any) -> Optional[OverlayHAL]:
    """Find the OverlayHAL inside a HAL or DesktopSession, if any."""
    seen = 0
    while hal is not None and seen < 5:
        if isinstance(hal, OverlayHAL):
            return hal
        attrs = getattr(hal, "__dict__", {})
        # DesktopSession keeps .backend; wrappers such as ControlHAL keep ._inner.
        hal = attrs.get("backend") or attrs.get("_inner")
        seen += 1
    return None


def step(hal: Any, text: str, risk: Optional[str] = None, confirm: bool = False):
    """``with step(hal, "Run synthesis", risk="caution"): ...`` for tool code.

    Works with any HAL; a no-op context when the overlay is not in use.
    """
    ov = find_overlay_hal(hal)
    if ov is None:
        return contextlib.nullcontext()
    return ov.step(text, risk=risk, confirm=confirm)
