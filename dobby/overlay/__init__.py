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
grows on hover, icon buttons, Liquid Glass on macOS 26+ (clear while the bar
sleeps, regular once it narrates; frosted glass before macOS 26;
``DOBBY_OVERLAY_GLASS=clear|regular`` pins one, ``0`` for the frosted bar), live step and timer while
working, one colour per running Dobby, a glow that follows Dobby's state and
the window's corners, a short fading trail, a tag that flips away from screen
edges) or ``classic`` (the earlier look). ``DOBBY_OVERLAY_AGENT_COLORS=0``
keeps every Dobby violet; ``DOBBY_OVERLAY_COLOR`` fixes one colour.
``DOBBY_OVERLAY_THEME=dark`` (default) draws the dark bar on any desktop;
``light`` the light one; ``auto`` follows the desktop's light or dark mode.
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
The bar's ▾ button opens Dobby's menu. A Dobby icon in the top bar (the
macOS menu bar, or the Linux panel's tray) shows how many Dobbys run and, in
a Focus-style dropdown, what each one does, with Pause / Resume / Stop
(see ``dobby.overlay.topbar``). ``DOBBY_OVERLAY_MENU_BAR=0`` hides it.
On Linux the tray icon is a small violet tile (pointer and spark), which
reads on light and dark panels alike.

Hotkeys while Dobby runs: Ctrl+Option+Esc stops, Ctrl+Option+P pauses or
resumes.

Input lock: ``DOBBY_INPUT_LOCK=window`` keeps the person's clicks (and keys,
while it is in front) out of the window Dobby works in, like an agent browser
locking its tab; other windows stay usable and the bar offers "Take over".
Default off. See ``dobby.overlay.inputlock``. With the lock on, plain clicks
on buttons go through accessibility (``AXPress`` on macOS, the AT-SPI
"click" action on Linux) when the app supports it, so the person's pointer
does not move (``DOBBY_BACKGROUND_CLICKS=0`` turns that off; see
``dobby.hal.background`` and ``ATSPIInspector.press_at``).

Turbo is the default: no permission prompts. Start Dobby with ``--secure``
(or ``DOBBY_MODE=secure``) for the prompts below (see ``dobby.mode``).

App grants (secure): the first action in an app asks "Let Dobby use …?" once per
session; ``~/.dobby/apps.json`` sets standing tiers (see ``dobby.overlay.grants``).
Clicks on Send / Delete / Pay-like buttons ask Allow/Deny
(``DOBBY_CONFIRM_CLICKS=0`` turns that off).
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import Any

from ..mode import is_secure
from ..personality import normalize_level
from .client import (
    NullOverlay,
    OverlayBlocked,
    OverlayClient,
    OverlayDenied,
    OverlayInterrupted,
    OverlayPaused,
    OverlayStopped,
)
from .hal import OverlayHAL, unwrap
from .inputlock import lock_mode
from .protocol import OVERLAY_WINDOW_TITLE, PROTOCOL_VERSION

__all__ = [
    "OVERLAY_WINDOW_TITLE",
    "PROTOCOL_VERSION",
    "NullOverlay",
    "OverlayBlocked",
    "OverlayClient",
    "OverlayDenied",
    "OverlayHAL",
    "OverlayInterrupted",
    "OverlayPaused",
    "OverlayStopped",
    "find_overlay_hal",
    "make_overlay",
    "overlay_enabled",
    "step",
    "unwrap",
    "wrap_hal",
]

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _admin_secure() -> bool:
    from ..policy import current

    return current().forces_secure


def overlay_enabled(env: dict[str, str] | None = None, platform: str = sys.platform) -> bool:
    """True when the visible overlay should run (``DOBBY_OVERLAY``, platform support, admin policy)."""
    env = os.environ if env is None else env
    flag = env.get("DOBBY_OVERLAY", "").strip().lower()
    if flag in _FALSE and not _admin_secure():
        return False  # an admin's secure policy keeps the overlay (the Allow/Deny prompts) on
    if platform != "darwin" and not (platform.startswith("linux") and env.get("DISPLAY")):
        return False
    if flag in _TRUE:
        return True
    return not ("PYTEST_CURRENT_TEST" in env or env.get("DOBBY_REPLAY", "").lower() in _TRUE)


def overlay_config(env: dict[str, str] | None = None) -> dict[str, Any]:
    env = os.environ if env is None else env
    cfg: dict[str, Any] = {}
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
    if env.get("DOBBY_PERSONALITY", "").strip():
        cfg["personality"] = normalize_level(env["DOBBY_PERSONALITY"])
    if env.get("DOBBY_USER_NAME", "").strip():
        cfg["user_name"] = env["DOBBY_USER_NAME"].strip()
    if env.get("DOBBY_PERSONALITY_LINES", "").strip():
        cfg["personality_lines"] = env["DOBBY_PERSONALITY_LINES"].strip()
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
    turbo_flag = env.get("DOBBY_OVERLAY_TURBO", "").strip().lower()
    if not _admin_secure() and (
        turbo_flag in _TRUE or (turbo_flag not in _FALSE and not is_secure(env))
    ):
        cfg["turbo"] = True  # default: turbo; ``--secure`` / DOBBY_MODE=secure turns the prompts on
    if env.get("DOBBY_OVERLAY_TRAIL", "").strip().lower() in _FALSE:
        cfg["trail"] = False
    pill = env.get("DOBBY_OVERLAY_PILL", "").strip().lower()
    if pill in ("always", "auto", "off"):
        cfg["pill"] = pill
    elif pill in _FALSE:
        cfg["pill"] = "off"
    menu_bar = env.get("DOBBY_OVERLAY_MENU_BAR", "").strip().lower()
    if menu_bar in _TRUE:
        cfg["menu_bar"] = True
    elif menu_bar in _FALSE:
        cfg["menu_bar"] = False
    if env.get("DOBBY_OVERLAY_PILL_RECORD", "").strip().lower() in _TRUE:
        cfg["pill_in_recordings"] = True
    glass = env.get("DOBBY_OVERLAY_GLASS", "").strip().lower()
    if glass in _FALSE:
        cfg["liquid_glass"] = "off"
    elif glass in ("auto", "clear", "regular"):
        cfg["liquid_glass"] = glass
    theme = env.get("DOBBY_OVERLAY_THEME", "").strip().lower()
    if theme in ("dark", "light", "auto"):
        cfg["theme"] = theme
    if lock_mode(env) != "off":
        cfg["input_lock"] = lock_mode(env)
    if env.get("DOBBY_BACKGROUND_CLICKS", "").strip().lower() in _FALSE:
        cfg["background_clicks"] = False
    if env.get("DOBBY_OVERLAY_SPEED"):
        try:
            cfg["speed"] = float(env["DOBBY_OVERLAY_SPEED"])
        except ValueError:
            pass
    return cfg


def make_overlay(enabled: bool | None = None) -> NullOverlay:
    """Starts the overlay helper, or returns a :class:`NullOverlay` when it is off or fails to start."""
    if enabled is None:
        enabled = overlay_enabled()
    if not enabled:
        return NullOverlay()
    log_path = Path(os.path.expanduser("~/.dobby/logs/overlay.log"))
    tracks = None
    if os.environ.get("DOBBY_OVERLAY_TRACKS", "").strip().lower() not in _FALSE:
        tracks = Path(os.path.expanduser("~/.dobby/overlay/tracks"))
    return OverlayClient(config=overlay_config(), log_path=log_path, track_dir=tracks)


def wrap_hal(hal: Any, overlay: NullOverlay | None = None, **kwargs: Any) -> Any:
    """Wrap ``hal`` in :class:`OverlayHAL` when the overlay is enabled."""
    if isinstance(hal, OverlayHAL):
        return hal
    overlay = overlay if overlay is not None else make_overlay()
    if isinstance(overlay, OverlayClient) or overlay.__class__ is not NullOverlay:
        if isinstance(overlay, OverlayClient) and overlay.config.get("pill", "auto") == "always":
            overlay.start_async()  # show the control bar as soon as Dobby runs
        from .grants import AppGrants

        # The mode is read per call (dobby.mode): a shared Dobby serves turbo
        # and secure clients at once. Turbo: no prompts at all, not even for ⌘Q.
        kwargs.setdefault("policy", is_secure)
        kwargs.setdefault("grants", AppGrants())  # secure: "Let Dobby use …?" once per app
        kwargs.setdefault(
            "confirm_clicks",
            _admin_secure()
            or os.environ.get("DOBBY_CONFIRM_CLICKS", "").strip().lower() not in _FALSE,
        )
        return OverlayHAL(hal, overlay, **kwargs)
    from ..policy import current

    rules = current()
    if rules.forces_secure or rules.apps or rules.app_default:
        # No overlay to ask on, but the admin policy still applies: its app
        # tiers hold, and every question it would ask is answered Deny.
        from .grants import AppGrants

        kwargs.setdefault("policy", is_secure)
        kwargs.setdefault("grants", AppGrants())
        return OverlayHAL(hal, DenyingOverlay(), **kwargs)
    return hal


class DenyingOverlay(NullOverlay):
    """No overlay under an admin policy: nobody can say Allow, so the answer is Deny."""

    def confirm(
        self,
        text: str,
        secs: float = 30.0,
        detail: str = "",
        allow: str = "Allow",
        deny: str = "Deny",
        kind: str = "danger",
    ) -> bool:
        return False


def find_overlay_hal(hal: Any) -> OverlayHAL | None:
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


def step(hal: Any, text: str, risk: str | None = None, confirm: bool = False):
    """Shows a step on the overlay while the ``with`` block runs.

    Usage in tool code: ``with step(hal, "Run synthesis", risk="caution"): ...``

    Works with any HAL; a no-op context when the overlay is not in use.
    """
    ov = find_overlay_hal(hal)
    if ov is None:
        return contextlib.nullcontext()
    return ov.step(text, risk=risk, confirm=confirm)
