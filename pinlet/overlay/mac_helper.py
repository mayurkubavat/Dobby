"""macOS overlay helper: agent pointer, window glow, controls.

Runs as its own process (``python -m pinlet.overlay``), started by
:class:`pinlet.overlay.client.OverlayClient`. Draws into one transparent,
click-through, non-activating panel per screen, above normal windows and on
every Space. Reads JSON-line commands on stdin (see ``protocol.py``) and exits
when stdin closes, so it never outlives the Pinlet server.

What it shows:

* the cyan pointer with a capsule name tag ("Pinlet", widening to
  "Pinlet › Click 'Run Synthesis'") that says what Pinlet is about to do,
  a box around the target element (found by an Accessibility hit test),
  click ripples and a numbered trail of recent clicks;
* a breathing glow around the window being worked on, dimmed when covered;
* a "looking" shimmer while Pinlet reads the screen;
* risk colours: cyan (ok), amber (caution), red (danger);
* Turbo mode (config, or Pinlet's menu): risky steps are allowed without a
  prompt and user input does not pause Pinlet; only Stop / Pause do;
* a control bar at the top of the screen that stays while Pinlet runs
  ("Pinlet is sleeping" / "working" / "paused", with Pause / Resume / Stop,
  or Allow / Deny for risky steps) and can be hidden with its "–" button,
  Ctrl+Option+H or Pinlet's menu (it comes back by itself when Pinlet wakes up
  and starts working again); it never appears in screenshots, so it does not
  blink during Pinlet's captures. When several Pinlet servers run, one helper
  (holding ~/.pinlet/overlay/bar.lock) shows the idle bar; the others show
  their bar only while they work;
* Pinlet's menu (status, Wake / Sleep, recent steps, Pause / Stop, Turbo,
  show/hide toggles), opened from the bar's ▾ button. Optional extras open
  the same menu: a clickable ▾ on the name tag (``chip_menu``, off by default);
* the menu bar item (``menu_bar``, on by default; see ``topbar``): a Pinlet
  icon with the number of running Pinlets and a Focus-style dropdown with one
  row per Pinlet (what it does, Pause / Resume / Stop) and Pinlet's settings.

It also watches for real user input while Pinlet works (take-over) and the
hotkeys Ctrl+Option+Esc (stop), Ctrl+Option+P (pause / resume) and
Ctrl+Option+H (hide / show the control bar), and sends events back on stdout.

All drawing is Core Animation; animations run in the window server, so Python
speed does not affect smoothness.
"""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from typing import Any

from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSApplicationDidChangeScreenParametersNotification,
    NSAttributedString,
    NSColor,
    NSEvent,
    NSEventMaskKeyDown,
    NSEventMaskLeftMouseDown,
    NSEventMaskLeftMouseDragged,
    NSEventMaskMouseMoved,
    NSEventMaskRightMouseDown,
    NSEventMaskScrollWheel,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSImage,
    NSMenu,
    NSMenuItem,
    NSRunningApplication,
    NSScreen,
    NSStatusBar,
    NSWorkspace,
)
from Foundation import NSNotificationCenter, NSTimer
from PyObjCTools import AppHelper
from Quartz import (
    CABasicAnimation,
    CACurrentMediaTime,
    CAGradientLayer,
    CALayer,
    CAMediaTimingFunction,
    CAShapeLayer,
    CATextLayer,
    CATransaction,
    CGEventGetIntegerValueField,
    CGPathCreateWithEllipseInRect,
    CGPathCreateWithRoundedRect,
    CGRectMake,
    CGWindowListCopyWindowInfo,
    kCAMediaTimingFunctionEaseInEaseOut,
    kCAMediaTimingFunctionLinear,
    kCGEventSourceUnixProcessID,
    kCGNullWindowID,
    kCGWindowListExcludeDesktopElements,
    kCGWindowListOptionIncludingWindow,
    kCGWindowListOptionOnScreenOnly,
)

from ..personality import Voice
from . import geometry, inputlock, protocol, style, topbar
from .barlock import (
    BAR_LOCK,
    mark_waiting,
    others_showing,
    others_waiting,
    showing_dir,
    waiting_dir,
)
from .barlock import BarOwner as _BarOwner
from .mac_views import (  # noqa: F401 - _liquid_glass and _BAR_RADIUS are used by tests
    _ARROW_H,
    _ARROW_W,
    _BAR_RADIUS,
    _CHIP_H,
    _HOTKEY_MODS,
    _INPUT_KIND,
    _KEY_ESC,
    _KEY_H,
    _KEY_P,
    _agent_pid_cache,
    _AXProbe,
    _cg,
    _ChipHotspot,
    _configure_overlay_window,
    _hex_rgb,
    _liquid_glass,
    _os_major,
    _Panel,
    _Pill,
    _reduce_motion,
    _short,
    _Target,
    _white_symbol,
)
from .menu import (
    bar_click,
    bar_tip,
    bar_view,
    idle_cleanup,
    label_click,
    menu_entries,
    picker_groups,
    status_of,
    takes_over,
)
from .protocol import ACTING_STATES

_kCGEventSourceUserData = 42
PINLET_EVENT_TAG = 0x444F4242  # same value as pinlet.hal.macos.PINLET_EVENT_TAG

DEFAULT_CONFIG: dict[str, Any] = {
    "color": "#0EA5C6",  # Pinlet cyan; deliberately not Claude's orange
    "caution_color": "#F5A524",
    "danger_color": "#F04438",
    "label": "Pinlet",
    "speed": 1.0,
    "glow_width": 3.0,
    # "auto": the window's own corner radius (0 for full-screen windows);
    # a number forces one radius.
    "glow_radius": "auto",
    "glow_blur": 18.0,
    "track_hz": 10.0,
    "capture_visible": True,  # False = hidden from every capture (macOS 27+)
    "trail": True,
    "trail_len": 5,
    "intent_ms": 1600,
    "takeover_resume": "auto",  # or "manual"
    "idle_secs": 2.0,
    "active_secs": 8.0,  # "working" lasts this long after an action
    # After active_secs + this of quiet the task is over: the "thinking"
    # sweep stops and the agent pointer and its tag are put away.
    "linger_secs": 20.0,
    "awake_secs": 120.0,  # woken from the bar: stays awake this long without a task
    "wake_note_secs": 4.0,  # woken while no AI waits: says so this long, then sleeps
    # Control bar at the top: "auto" (default: only while Pinlet works, asks or
    # is paused; out of sight while it rests), "always" (also the resting bar;
    # the user can hide it) or "off".
    "pill": "auto",
    # False: the bar never appears in any capture (so Pinlet's own screenshots
    # need not hide it and it never blinks). True: visible in recordings, but
    # it hides for a moment during each of Pinlet's captures.
    "pill_in_recordings": False,
    # The menu bar item (``topbar``): a Pinlet icon in the menu bar with a
    # Focus-style dropdown listing every running Pinlet. Only the bar owner
    # shows it. ``chip_menu``: a ▾ on the name tag opens Pinlet's menu.
    "menu_bar": True,
    "chip_menu": False,
    # Turbo: no Allow/Deny prompts and no take-over pause; only Stop/Pause
    # (bar, menu, hotkeys) halt Pinlet. Risky steps still flash red.
    "turbo": False,
    # "modern" (compact sleeping bar, icon buttons, glass, live step and
    # timer, per-Pinlet colours, state glow, light trail, tag flips at screen
    # edges) or "classic" (the look before those).
    "style": "modern",
    # Modern bar on macOS 26+: Liquid Glass (NSGlassEffectView) instead of the
    # frosted HUD material. "auto" (default: clear while sleeping, regular with
    # text), "clear" or "regular" where the OS has it; "off" never.
    "liquid_glass": "auto",
    # "dark" (default), "light", or "auto" to follow macOS (style.overlay_dark).
    "theme": "dark",
    # One colour per running Pinlet (from the agent registry). Off when
    # PINLET_OVERLAY_COLOR fixes the colour ("color_fixed").
    "agent_colors": True,
    "color_fixed": False,
    "trail_secs": 10.0,  # modern trail: dots fade out after this long
    # Modern look only: say what Pinlet does ("Pinlet is working · Click ‘Send’"
    # on the bar, the action on the pointer tag until the next one, numbered
    # trail marks, Pinlet dots only while two or more are busy).
    "narrate": True,
    # Input lock (pinlet.overlay.inputlock): "window" keeps the person's
    # clicks, scrolls and keys out of the window Pinlet works in while it
    # works; "off" keeps take-over as before.
    "input_lock": "off",
    "background": False,  # background mode: no drawn pointer or glow, no take-over
}


# -------------------------------------------------------------- controller
# The pointer label keeps a step's words at least this long, so a quick key
# press can still be read before the label goes back to "Thinking…".
_STEP_HOLD_S = 2.0


class OverlayController:
    """The macOS overlay helper: draws the pointer, glow and bar, and answers the server.

    Runs in its own process on the AppKit main thread and reads JSON-lines commands
    from the server on stdin.
    """

    def __init__(self, cfg: dict[str, Any], out=sys.stdout, interactive: bool = True) -> None:
        self.cfg = dict(DEFAULT_CONFIG)
        self.cfg.update(cfg)
        self.colors = {
            "ok": _hex_rgb(self.cfg["color"]),
            "caution": _hex_rgb(self.cfg["caution_color"]),
            "danger": _hex_rgb(self.cfg["danger_color"]),
        }
        self.risk_level = "ok"
        self.rgb = self.colors["ok"]
        self.style = style.normalize_style(self.cfg.get("style"))
        self.narrate = self.style == "modern" and bool(self.cfg.get("narrate", True))
        self.voice = Voice.from_config(self.cfg)  # Pinlet's words (PINLET_PERSONALITY)
        self.ok_hex = str(self.cfg["color"])
        self.os_major = _os_major()
        self.reduce_motion = _reduce_motion()
        # Other Pinlets (agent registry): colours, this one's name on the tag,
        # the bar's Pinlet dots.
        self.agents: list[dict[str, Any]] = []
        self.agents_next = 0.0
        self.agent_label = str(self.cfg["label"])
        self.agent_dots: list[tuple[int, str, bool, bool]] = []
        # The task in progress, for the bar's live step and timer.
        self.task_started = 0.0
        self.goal = ""  # what the AI says it is doing, for the bar
        self.intent_at = 0.0  # when the pointer label last got a step to show
        self.intent_held = False
        self.task_steps = 0
        self.current_step = ""
        self.bar_second = -1
        self.pill_hovered = False
        self._glow_sig: tuple | None = None
        self._glow_r = 0.0
        self.out = out
        self.out_lock = threading.Lock()
        self.server_pid = int(os.environ.get("PINLET_SERVER_PID", "0") or 0)
        self.agent_pids = _agent_pid_cache()  # every running Pinlet, not just ours
        self.panels: list[_Panel] = []
        self.primary_h = 0.0
        self.cursor: tuple[float, float] | None = None
        self.cursor_state = "idle"
        self.intent_text = ""
        self.intent_until = 0.0
        self.track_wid: int | None = None
        self.track_pid: int | None = None
        self.glow_rect: geometry.Rect | None = None
        self.glow_dim = False
        self.trail_points: list[tuple[int, tuple[float, float], float]] = []  # (step, point, time)
        self.step_no = 0
        self.steps: list[str] = []
        self.user_visible = True
        self.capture_hidden = False
        self.trail_on = bool(self.cfg["trail"])
        self.turbo = bool(self.cfg["turbo"])
        # control state
        self.last_action = 0.0
        self.paused = False
        self.takeover = False
        self.takeover_reported = False
        self.last_user_input = 0.0
        self.pending_confirm: tuple[Any, float] | None = None  # (msg id, deadline)
        self.stopped_until = 0.0
        self.awake_until = 0.0  # woken from the bar until then, or 0
        self.wake_pointer = False  # the pointer is showing only because of a wake
        # An AI waits in wait_for_wake (count of sleep.wait without sleep.done).
        self.ai_waiting = 0
        self.wait_reason = ""
        self.handoff = False  # the current wake went to a waiting AI
        self.others_waiting = False  # another helper's AI waits (bar owner steps aside)
        self.others_checked = 0.0
        self.probe = _AXProbe()
        self.target = _Target.alloc().initWithOwner_(self)
        mode = self.cfg.get("pill", "auto")
        if mode is True:
            mode = "always"
        self.pill_mode = mode if mode in ("always", "auto") else "off"
        self.pill = (
            _Pill(self.cfg, self.target, animate=interactive and not self.reduce_motion)
            if self.pill_mode != "off"
            else None
        )
        self.pill_user_hidden = False
        self.pointer_visible = False
        self.chip_menu_open = False
        self.hotspot = _ChipHotspot(self.target) if self.cfg.get("chip_menu") else None
        self.bar_owner = _BarOwner(self.cfg.get("bar_lock", BAR_LOCK))
        self.bar_owner_retry = 0.0
        self.wait_dir = waiting_dir(self.cfg.get("bar_lock", BAR_LOCK))
        self.pill_show_dir = showing_dir(self.cfg.get("bar_lock", BAR_LOCK))
        self.interactive = interactive
        self.status_item = None
        self.topbar_menu = None
        self.topbar_sig: tuple | None = None
        self.topbar_next = 0.0
        self.publish_next = 0.0
        self.app_names: dict[int, str] = {}
        self.rebuild_panels()
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self.target, "screensChanged:", NSApplicationDidChangeScreenParametersNotification, None
        )
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / float(self.cfg["track_hz"]), self.target, "tick:", None, True
        )
        self.monitors: list[Any] = []
        # Input lock: the tracked window's frame, windows above it, and
        # whether it is in front (from _track_tick); the tap itself.
        self.event_tag = PINLET_EVENT_TAG
        self.lock_mode = str(self.cfg.get("input_lock") or "off")
        self.lock_target: geometry.Rect | None = None
        self.lock_above: list[geometry.Rect] = []
        self.lock_front = False
        self.lock_note_next = 0.0
        self.lock_stopped = False  # Stop frees the window until Pinlet acts again
        self.voiceover = False
        self.voiceover_checked = 0.0
        self.input_lock = None
        if interactive and self.lock_mode != "off":
            try:
                from .mac_lock import MacInputLock

                lock = MacInputLock(self, _configure_overlay_window, _white_symbol)
                self.input_lock = lock if lock.ok else None
            except Exception as exc:  # noqa: BLE001 - Pinlet works without the lock
                sys.stderr.write(f"pinlet.overlay: input lock unavailable: {exc}\n")
        if interactive:
            self._install_monitors()
            self._refresh_agents(force=True)
            self._claim_bar()

    # --------------------------------------------------------------- output
    def _write(self, text: str) -> None:
        with self.out_lock:
            try:
                self.out.write(text)
                self.out.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass

    def emit(self, obj: dict[str, Any]) -> None:
        """Writes one JSON message to the server."""
        self._write(json.dumps(obj, separators=(",", ":")) + "\n")

    def send_event(self, name: str) -> None:
        """Sends a named event (pause, resume, stop, ...) to the server."""
        self._write(protocol.event(name))

    def ack(self, msg_id: Any, ok: bool = True, error: str | None = None) -> None:
        """Acknowledges the command with ``msg_id`` (nothing to do when it has none)."""
        if msg_id is None:
            return
        self._write(protocol.ack(msg_id, ok, error))

    # --------------------------------------------------------------- panels
    def rebuild_panels(self) -> None:
        """Recreates one overlay panel per screen, e.g. after a display change."""
        for p in self.panels:
            p.close()
        screens = list(NSScreen.screens() or [])
        self.primary_h = float(screens[0].frame().size.height) if screens else 0.0
        self.panels = [_Panel(s, self.cfg, self.rgb) for s in screens]
        label, detail = self._tag_parts()
        for p in self.panels:
            p.set_tag(label, detail, self.cursor_state, animate=False)
        if self.cursor is not None:
            self._place_pointer(self.cursor, 0)
            self._set_pointer_visible(True)
        if self.glow_rect is not None:
            self._place_glow(self.glow_rect, 0)
        self._redraw_trail()
        self._apply_alpha()
        self._glow_sig = None
        self._refresh_glow()

    def _apply_alpha(self) -> None:
        alpha = 1.0 if (self.user_visible and not self.capture_hidden) else 0.0
        for p in self.panels:
            p.window.setAlphaValue_(alpha)
        self._update_hotspot()
        if self.pill is not None and self.cfg.get("pill_in_recordings"):
            self.pill.window.setAlphaValue_(0.0 if self.capture_hidden else 1.0)

    def _duration(self, ms: float | None) -> float:
        if self.reduce_motion or not ms:
            return 0.0
        return max(0.0, float(ms) / 1000.0)

    def _tag_parts(self) -> tuple[str, str]:
        detail = (
            self.intent_text if (self.intent_text and time.monotonic() < self.intent_until) else ""
        )
        return self.agent_label, style.tag_detail(
            detail,
            self.cursor_state,
            self.narrate,
            self.goal,
            # A question is up, or the step is fresh: keep it readable before "Thinking…".
            self.risk_level == "danger" or time.monotonic() - self.intent_at < _STEP_HOLD_S,
        )

    def _refresh_tag(self) -> None:
        label, detail = self._tag_parts()
        for p in self.panels:
            p.set_tag(label, detail, self.cursor_state)
        for p in self._update_flips():  # the tag got wider or narrower
            p.set_tag(label, detail, self.cursor_state)
        self._update_hotspot()

    def _update_flips(self) -> list[_Panel]:
        """Flip the tag left / up near a screen edge (modern). Returns panels that changed."""
        changed = []
        for p in self.panels:
            flip = (False, False)
            if self.style == "modern" and self.cursor is not None:
                frame = p.global_frame
                if geometry.contains(frame, self.cursor):
                    flip = style.tag_flip(
                        self.cursor, p.tag.width, _CHIP_H, _ARROW_W, _ARROW_H, frame
                    )
            if flip != p.flip:
                p.flip = flip
                changed.append(p)
        return changed

    def _set_risk(self, level: str) -> None:
        self.risk_level = level
        self.rgb = self.colors.get(level, self.colors["ok"])
        for p in self.panels:
            p.apply_color(self.rgb)
        self._glow_sig = None  # apply_color reset the glow colour
        self._refresh_pill()

    # ------------------------------------------------------------ agents
    def _refresh_agents(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now < self.agents_next:
            return
        self.agents_next = now + 2.0
        if self.style != "modern" or not self.server_pid:
            return
        self._apply_agents(self._agents())

    def _apply_agents(self, agents: list[dict[str, Any]]) -> None:
        """Take this Pinlet's colour and name from the registry; update the bar's dots."""
        self.agents = list(agents)
        colors = style.agent_colors(self.agents)
        own = colors.get(self.server_pid)
        if (
            own
            and self.cfg.get("agent_colors", True)
            and not self.cfg.get("color_fixed")
            and own.upper() != self.ok_hex.upper()
        ):
            self.ok_hex = own
            self.colors["ok"] = _hex_rgb(own)
            if self.risk_level == "ok":
                self._set_risk("ok")  # recolours pointer, tag, glow and bar
            self._glow_sig = None
            self._redraw_trail()
        own_name = next(
            (str(a.get("name")) for a in self.agents if a.get("pid") == self.server_pid), None
        )
        label = style.tag_label(str(self.cfg["label"]), own_name, len(self.agents))
        if label != self.agent_label:
            self.agent_label = label
            self._refresh_tag()
        dots = style.agent_dots(self.agents, colors, self.server_pid, busy_only=self.narrate)
        if dots != self.agent_dots:
            self.agent_dots = dots
            self._refresh_pill()

    def _place_pointer(self, pt: tuple[float, float], ms: float | None) -> None:
        dur = self._duration(ms)
        CATransaction.begin()
        if dur <= 0:
            CATransaction.setDisableActions_(True)
        else:
            CATransaction.setAnimationDuration_(dur)
            CATransaction.setAnimationTimingFunction_(
                CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionEaseInEaseOut)
            )
        for p in self.panels:
            p.pointer.setPosition_(geometry.global_point_to_local(pt, p.frame, self.primary_h))
        CATransaction.commit()
        cursor = self.cursor
        self.cursor = pt  # flips are decided where the pointer is going
        changed = self._update_flips()
        self.cursor = cursor
        if changed:
            label, detail = self._tag_parts()
            for p in changed:
                p.set_tag(label, detail, self.cursor_state)
        self._update_hotspot()
        if self.pill is not None:
            self.pill.duck(self.pill.near((pt[0], self.primary_h - pt[1])))

    def _update_hotspot(self) -> None:
        if self.hotspot is None:
            return
        show = (
            self.pointer_visible
            and self.cursor is not None
            and self.user_visible
            and not self.capture_hidden
            and not self.chip_menu_open
        )
        width = max((p.tag.width for p in self.panels), default=0.0)
        rect = None
        if show:
            flip = next(
                (p.flip for p in self.panels if geometry.contains(p.global_frame, self.cursor)),
                (False, False),
            )
            x, y = style.tag_origin(self.cursor, width, _CHIP_H, _ARROW_W, _ARROW_H, flip)
            rect = (x, y, width, _CHIP_H)
        self.hotspot.place(rect, self.primary_h)

    def on_chip_click(self, view, _event) -> None:
        """The user clicked the name tag: open Pinlet's menu under it."""
        menu = self._build_menu(self._status()[0])
        self.chip_menu_open = True
        try:
            self._pop_up(menu, view, (0, 0))
        finally:
            self.chip_menu_open = False
            self._update_hotspot()

    def on_pill_menu(self) -> None:
        """The user clicked the bar's ▾: open Pinlet's menu under it."""
        if self.pill is None:
            return
        self._pop_up(self._build_menu(self._status()[0]), self.pill.view, self.pill.menu_anchor())

    @staticmethod
    def _pop_up(menu, view, location) -> None:
        # Blocks in modal menu tracking until the menu closes.
        menu.popUpMenuPositioningItem_atLocation_inView_(None, location, view)

    def _set_pointer_visible(self, visible: bool) -> None:
        self.pointer_visible = visible
        self._update_hotspot()
        CATransaction.begin()
        CATransaction.setAnimationDuration_(0.15)
        for p in self.panels:
            p.pointer.setOpacity_(1.0 if visible else 0.0)
        CATransaction.commit()

    def _place_glow(self, rect: geometry.Rect, ms: float) -> None:
        conf = self.cfg["glow_radius"]
        if self.style == "classic" and conf == "auto":
            conf = 11.0
        r = style.corner_radius(rect, [p.global_frame for p in self.panels], self.os_major, conf)
        self._glow_r = r
        inset = float(self.cfg["glow_width"]) / 2.0
        if self.style == "modern" and r > 0:
            r += inset  # the stroke sits outside the window: keep the curves concentric
        CATransaction.begin()
        if ms <= 0:
            CATransaction.setDisableActions_(True)
        else:
            CATransaction.setAnimationDuration_(ms / 1000.0)
            CATransaction.setAnimationTimingFunction_(
                CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionLinear)
            )
        for p in self.panels:
            x, y, w, h = geometry.global_rect_to_local(rect, p.frame, self.primary_h)
            box = CGRectMake(x - inset, y - inset, w + 2 * inset, h + 2 * inset)
            path = CGPathCreateWithRoundedRect(box, r, r, None)
            p.glow.setPath_(path)
            p.sweep.setPath_(path)
            length = 2 * (w + h + 4 * inset) + (2 * math.pi - 8) * r
            seg = min(180.0, length * 0.16)
            p.sweep.setLineDashPattern_([seg, max(1.0, length - seg)])
            if abs(getattr(p, "sweep_len", 0.0) - length) > 1.0:
                p.sweep_len = length
                if p.sweep.animationForKey_("run") is not None:
                    self._run_sweep(p)
        CATransaction.commit()

    def _run_sweep(self, p) -> None:
        run = CABasicAnimation.animationWithKeyPath_("lineDashPhase")
        run.setFromValue_(0.0)
        run.setToValue_(-float(getattr(p, "sweep_len", 1000.0)))
        run.setDuration_(2.4)
        run.setRepeatCount_(1e9)
        p.sweep.addAnimation_forKey_(run, "run")

    def _glow_state(self) -> str:
        working = time.monotonic() - self.last_action <= float(self.cfg["active_secs"])
        return style.glow_state(
            paused=self.paused,
            takeover=self.takeover,
            working=working,
            thinking=self.cursor_state == "thinking",
            risk=self.risk_level,
        )

    def _refresh_glow(self) -> None:
        """Modern glow: colour, opacity and motion follow Pinlet's state.

        Thinking: a bright segment runs around the edge. Acting: steady
        breathing. Paused / your turn: amber. Risky step: red pulse. Idle:
        fades away. Classic keeps the old always-on glow (see _track_tick).
        """
        if self.style == "classic":
            return
        state = self._glow_state()
        rgb = self.colors[style.glow_color_key(state, self.risk_level)]
        shown = self.glow_rect is not None
        sig = (state, rgb, self.glow_dim, shown)
        if sig == self._glow_sig:
            return
        self._glow_sig = sig
        opacity = style.glow_opacity(state, self.glow_dim) if shown else 0.0
        # Paused / your turn turns pointer and tag amber with the glow.
        pointer_rgb = self.colors["caution"] if state == "halted" else self.rgb
        CATransaction.begin()
        CATransaction.setAnimationDuration_(0.25)
        for p in self.panels:
            p.set_glow_color(rgb)
            p.arrow.setFillColor_(_cg(pointer_rgb))
            p.tag.apply_color(pointer_rgb)
        CATransaction.commit()
        self._set_glow_opacity(opacity)
        sweep = state == "thinking" and opacity > 0 and not self.reduce_motion
        pulse = state == "risk" and opacity > 0 and not self.reduce_motion
        CATransaction.begin()
        CATransaction.setAnimationDuration_(0.3)
        for p in self.panels:
            p.sweep.setOpacity_(opacity if sweep else 0.0)
            if sweep and p.sweep.animationForKey_("run") is None:
                self._run_sweep(p)
            elif not sweep:
                p.sweep.removeAnimationForKey_("run")
            if pulse and p.glow.animationForKey_("pulse") is None:
                beat = CABasicAnimation.animationWithKeyPath_("lineWidth")
                beat.setFromValue_(float(self.cfg["glow_width"]))
                beat.setToValue_(float(self.cfg["glow_width"]) * 2.2)
                beat.setDuration_(0.45)
                beat.setAutoreverses_(True)
                beat.setRepeatCount_(1e9)
                p.glow.addAnimation_forKey_(beat, "pulse")
            elif not pulse:
                p.glow.removeAnimationForKey_("pulse")
        CATransaction.commit()

    def _set_glow_opacity(self, opacity: float) -> None:
        CATransaction.begin()
        CATransaction.setAnimationDuration_(0.2)
        for p in self.panels:
            p.glow.setOpacity_(opacity)
            if (
                opacity > 0
                and not self.reduce_motion
                and p.glow.animationForKey_("breathe") is None
            ):
                anim = CABasicAnimation.animationWithKeyPath_("shadowOpacity")
                anim.setFromValue_(0.35)
                anim.setToValue_(0.75)
                anim.setDuration_(1.25)
                anim.setAutoreverses_(True)
                anim.setRepeatCount_(1e9)
                p.glow.addAnimation_forKey_(anim, "breathe")
        CATransaction.commit()

    def _show_box(self, rect: geometry.Rect) -> None:
        for p in self.panels:
            x, y, w, h = geometry.global_rect_to_local(rect, p.frame, self.primary_h)
            CATransaction.begin()
            CATransaction.setDisableActions_(True)
            p.box.setPath_(
                CGPathCreateWithRoundedRect(CGRectMake(x - 3, y - 3, w + 6, h + 6), 6, 6, None)
            )
            CATransaction.commit()
            CATransaction.begin()
            CATransaction.setAnimationDuration_(0.12)
            p.box.setOpacity_(1.0)
            CATransaction.commit()

    def _clear_box(self) -> None:
        CATransaction.begin()
        CATransaction.setAnimationDuration_(0.25)
        for p in self.panels:
            p.box.setOpacity_(0.0)
        CATransaction.commit()

    def _ripple(self, count: int) -> None:
        if self.cursor is None:
            return
        for p in self.panels:
            cx, cy = geometry.global_point_to_local(self.cursor, p.frame, self.primary_h)
            for i in range(max(1, min(int(count), 3))):
                ring = CAShapeLayer.layer()
                ring.setContentsScale_(p.scale)
                ring.setFrame_(CGRectMake(cx - 14, cy - 14, 28, 28))
                ring.setPath_(CGPathCreateWithEllipseInRect(CGRectMake(0, 0, 28, 28), None))
                ring.setFillColor_(None)
                ring.setStrokeColor_(_cg(self.rgb))
                ring.setLineWidth_(2.0)
                ring.setOpacity_(0.0)
                p.effects.addSublayer_(ring)
                CATransaction.begin()
                CATransaction.setCompletionBlock_(ring.removeFromSuperlayer)
                scale = CABasicAnimation.animationWithKeyPath_("transform.scale")
                scale.setFromValue_(0.3)
                scale.setToValue_(1.0)
                fade = CABasicAnimation.animationWithKeyPath_("opacity")
                fade.setFromValue_(0.9)
                fade.setToValue_(0.0)
                for a in (scale, fade):
                    a.setDuration_(0.3)
                    if i:
                        a.setBeginTime_(CACurrentMediaTime() + 0.12 * i)
                    ring.addAnimation_forKey_(a, a.keyPath())
                CATransaction.commit()

    def _scan(self, rect: geometry.Rect | None) -> None:
        rect = rect or self.glow_rect
        if rect is None or rect[2] < 8 or rect[3] < 8:
            return
        for p in self.panels:
            x, y, w, h = geometry.global_rect_to_local(rect, p.frame, self.primary_h)
            holder = CALayer.layer()
            holder.setContentsScale_(p.scale)
            holder.setFrame_(CGRectMake(x, y, w, h))
            holder.setMasksToBounds_(True)
            holder.setCornerRadius_(self._glow_r)
            p.effects.addSublayer_(holder)
            CATransaction.begin()
            CATransaction.setCompletionBlock_(holder.removeFromSuperlayer)
            if self.reduce_motion:
                holder.setBackgroundColor_(_cg(self.rgb, 0.10))
                fade = CABasicAnimation.animationWithKeyPath_("opacity")
                fade.setFromValue_(1.0)
                fade.setToValue_(0.0)
                fade.setDuration_(0.5)
                holder.setOpacity_(0.0)
                holder.addAnimation_forKey_(fade, "fade")
            else:
                band = max(80.0, w * 0.3)
                grad = CAGradientLayer.layer()
                grad.setContentsScale_(p.scale)
                grad.setFrame_(CGRectMake(-band, 0, band, h))
                tint = tuple(min(1.0, c * 0.4 + 0.6) for c in self.rgb)
                grad.setColors_([_cg(tint, 0.0), _cg(tint, 0.22), _cg(tint, 0.0)])
                grad.setStartPoint_((0.0, 0.5))
                grad.setEndPoint_((1.0, 0.5))
                holder.addSublayer_(grad)
                sweep = CABasicAnimation.animationWithKeyPath_("position.x")
                sweep.setFromValue_(-band / 2)
                sweep.setToValue_(w + band / 2)
                sweep.setDuration_(0.9)
                sweep.setTimingFunction_(
                    CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionEaseInEaseOut)
                )
                grad.setPosition_((w + band / 2, h / 2))
                grad.addAnimation_forKey_(sweep, "sweep")
            CATransaction.commit()

    def _redraw_trail(self) -> None:
        keep = 3 if self.style == "modern" else int(self.cfg["trail_len"])
        marks = style.trail_view(
            self.trail_points,
            time.monotonic(),
            self.style,
            keep=keep,
            fade_secs=float(self.cfg["trail_secs"]),
            numbered=self.narrate,
        )
        for p in self.panels:
            for sub in list(p.trail.sublayers() or []):
                sub.removeFromSuperlayer()
            if not self.trail_on:
                continue
            for num, pt, opacity, dia, left in marks:
                cx, cy = geometry.global_point_to_local(pt, p.frame, self.primary_h)
                if num is not None:  # numbered badge (classic, or modern with narration)
                    dot = CATextLayer.layer()
                    dot.setForegroundColor_(_cg((1, 1, 1)))
                    dot.setFontSize_(10.0)
                    dot.setAlignmentMode_("center")
                    dot.setString_(str(num))
                    dot.setBackgroundColor_(_cg(self.colors["ok"], 0.85))
                else:  # modern: a small dot that fades out
                    dot = CALayer.layer()
                    dot.setBackgroundColor_(_cg(self.colors["ok"]))
                    dot.setBorderColor_(_cg((1, 1, 1), 0.85))
                    dot.setBorderWidth_(1.0)
                dot.setContentsScale_(p.scale)
                dot.setFrame_(CGRectMake(cx - dia / 2, cy - dia / 2, dia, dia))
                dot.setCornerRadius_(dia / 2)
                dot.setOpacity_(opacity)
                if left > 0 and not self.reduce_motion:  # modern marks fade out
                    fade = CABasicAnimation.animationWithKeyPath_("opacity")
                    fade.setFromValue_(opacity)
                    fade.setToValue_(0.0)
                    fade.setDuration_(left)
                    dot.setOpacity_(0.0)
                    dot.addAnimation_forKey_(fade, "fade")
                p.trail.addSublayer_(dot)

    def _add_step(self, text: str) -> None:
        if not text:
            return
        self.step_no += 1
        self.task_steps += 1
        self.current_step = text
        self.steps.append(f"{self.step_no}. {text}")
        self.steps = self.steps[-10:]
        self._refresh_pill()  # the bar shows the step and count; this also refreshes the menu

    # ------------------------------------------------------------- tracking
    def _window_list(self, option: int, wid: int = kCGNullWindowID) -> list[dict[str, Any]]:
        return list(CGWindowListCopyWindowInfo(option, wid) or [])

    @staticmethod
    def _bounds(info: dict[str, Any]) -> geometry.Rect:
        b = info.get("kCGWindowBounds") or {}
        return (
            float(b.get("X", 0)),
            float(b.get("Y", 0)),
            float(b.get("Width", 0)),
            float(b.get("Height", 0)),
        )

    def _claim_bar(self) -> None:
        """Become the helper that shows the idle bar (and menu bar icon, if on)."""
        if not self.bar_owner.try_acquire():
            return
        if self.cfg["menu_bar"] and self.status_item is None:
            self._install_status_item()
        self._refresh_pill()  # "Pinlet is sleeping" from the start

    def on_tick(self) -> None:
        """Timer callback: animations, heartbeat, agent list and idle checks."""
        now = time.monotonic()
        if self.interactive:
            self._refresh_agents()
            if now >= self.publish_next:
                self._publish_status()  # heartbeat, and the timer for other helpers' menus
            self._refresh_topbar()
        if self.style == "modern" and self.trail_points:
            fresh = [t for t in self.trail_points if now - t[2] < float(self.cfg["trail_secs"])]
            if len(fresh) != len(self.trail_points):
                self.trail_points = fresh
                self._redraw_trail()
        if (
            self.style == "modern"
            and self.pill is not None
            and self.pill.mode == "working"
            and self.pending_confirm is None
        ):
            second = int(now - self.task_started)
            if second != self.bar_second:  # the bar's timer
                self.bar_second = second
                self._refresh_pill()
        if self.voice.refresh_due():
            self._refresh_pill()  # the "Pinlet did it!" cheer is over
        if self.interactive and not self.bar_owner.owned and now >= self.bar_owner_retry:
            self.bar_owner_retry = now + 2.0
            self._claim_bar()
        if self.intent_held and time.monotonic() - self.intent_at >= _STEP_HOLD_S:
            self.intent_held = False
            self._refresh_tag()  # the step has been readable long enough: "Thinking…" may show
        if self.intent_text and (
            now >= self.intent_until
            or (self.narrate and now - self.last_action > float(self.cfg["active_secs"]))
        ):
            self.intent_text = ""  # narration: the action stays until Pinlet goes idle
            self._refresh_tag()
        if self.pending_confirm is not None and now >= self.pending_confirm[1]:
            self._answer_confirm(False)
        if (
            self.takeover
            and self.cfg["takeover_resume"] == "auto"
            and now - self.last_user_input >= float(self.cfg["idle_secs"])
        ):
            self.takeover = False
            self.send_event("user_idle")
            self._refresh_pill()
        if self.awake_until and now >= self.awake_until:
            self.sleep()  # woken, but no task came
        self._put_away_when_done(now)
        if self.input_lock is not None:
            if now >= self.voiceover_checked:
                self.voiceover_checked = now + 2.0
                try:
                    self.voiceover = bool(NSWorkspace.sharedWorkspace().isVoiceOverEnabled())
                except Exception:  # noqa: BLE001 - macOS < 10.13
                    self.voiceover = False
            self.input_lock.tick()
        if self.bar_owner.owned and now >= self.others_checked:
            self.others_checked = now + 1.0
            # Step aside while another helper's AI waits or another Pinlet's bar shows.
            others = not self.ai_waiting and (
                others_waiting(self.wait_dir, os.getpid())
                or others_showing(self.pill_show_dir, os.getpid())
            )
            if others != self.others_waiting:
                self.others_waiting = others
                self._refresh_pill()
        if self.pill is not None:
            if self.pill.mode == "working" and now - self.last_action > float(
                self.cfg["active_secs"]
            ):
                self._refresh_pill()
            elif self.pill.mode == "stopped" and now >= self.stopped_until:
                self.pill.mode = "ready"
                self._refresh_pill()
        self._track_tick()

    def _put_away_when_done(self, now: float) -> None:
        """The task is over: stop the thinking sweep, hide the agent pointer."""
        reset_thinking, hide_pointer = idle_cleanup(
            since_action=now - self.last_action,
            active_secs=float(self.cfg["active_secs"]),
            linger_secs=float(self.cfg["linger_secs"]),
            cursor_state=self.cursor_state,
            pointer_visible=self.pointer_visible,
            awake=self._awake(),
            halted=self.paused or self.takeover,
            confirming=self.pending_confirm is not None,
        )
        if reset_thinking:
            self.cursor_state = "idle"
            self._refresh_tag()
            self._refresh_pill()  # also refreshes the glow
        if hide_pointer:
            self._set_pointer_visible(False)
            self.cursor = None  # the next task glides in from the real mouse
            self._clear_box()

    def _track_tick(self) -> None:
        if self.track_wid is None and self.track_pid is None:
            return
        onscreen = self._window_list(
            kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
        )
        me = os.getpid()
        wid = self.track_wid
        if wid is None:
            for info in onscreen:
                if (
                    info.get("kCGWindowOwnerPID") == self.track_pid
                    and info.get("kCGWindowLayer", 0) == 0
                ):
                    wid = int(info["kCGWindowNumber"])
                    break
        index = next(
            (i for i, info in enumerate(onscreen) if info.get("kCGWindowNumber") == wid), None
        )
        if wid is None or index is None:
            self.lock_target = None
            if self.glow_rect is not None:
                self.glow_rect = None
                self._set_glow_opacity(0.0)
                self._glow_sig = None
            if (
                wid is not None
                and self.track_pid is None
                and not self._window_list(kCGWindowListOptionIncludingWindow, wid)
            ):
                self.track_wid = None
            return
        rect = self._bounds(onscreen[index])
        above = [
            self._bounds(info)
            for info in onscreen[:index]
            if info.get("kCGWindowLayer", 0) == 0
            and info.get("kCGWindowOwnerPID") != me
            and float(info.get("kCGWindowAlpha", 1)) > 0
        ]
        dim = geometry.occluded(rect, above)
        self.lock_target = rect
        self.lock_above = above
        self.lock_front = inputlock.frontmost_owner(onscreen, me) == onscreen[index].get(
            "kCGWindowOwnerPID"
        )
        first = self.glow_rect is None
        if rect != self.glow_rect:
            self._place_glow(rect, 0 if first else 1000.0 / float(self.cfg["track_hz"]))
            self.glow_rect = rect
        if first or dim != self.glow_dim:
            self.glow_dim = dim
            if self.style == "classic":
                self._set_glow_opacity(0.3 if dim else 1.0)
            else:
                self._refresh_glow()

    # ------------------------------------------------------ controls: pill
    def _mark_active(self) -> None:
        now = time.monotonic()
        was_sleeping = now - self.last_action > float(self.cfg["active_secs"])
        if was_sleeping:  # a new task: the bar's step count and timer start over
            self.task_started = now
            self.task_steps = 0
            self.current_step = ""
        self.last_action = now
        self.lock_stopped = False
        self.wake_pointer = False  # Pinlet drives the pointer now
        if self.awake_until:
            self._end_awake()  # working takes over; afterwards Pinlet sleeps
        if was_sleeping and self.pill_user_hidden:
            # Hidden while Pinlet slept: bring the bar back now that it works.
            self.set_pill_hidden(False)
        elif self.pill is not None and self.pill.mode in ("hidden", "ready", "awake"):
            self._refresh_pill()

    def _awake(self) -> bool:
        return bool(self.awake_until) and time.monotonic() < self.awake_until

    def _end_awake(self) -> None:
        self.awake_until = 0.0
        self.handoff = False
        self.send_event("sleep")

    def on_pill_label(self) -> None:
        """The user clicked the bar away from its buttons."""
        if self.pill is None:
            return
        action = label_click(self.pill.mode)
        if action == "wake":
            self.wake()
        elif action == "sleep":
            self.sleep()

    def on_pill_hover(self, inside: bool) -> None:
        """The pointer entered or left the bar: the compact sleeping bar grows."""
        self.pill_hovered = inside
        if self.pill is None or self.pill.mode != "ready" or self.style != "modern":
            return
        if inside:
            self._refresh_pill()
        else:  # a short delay so brushing past the edge does not flicker
            AppHelper.callLater(0.4, self._hover_settle)

    def _hover_settle(self) -> None:
        if not self.pill_hovered and self.pill is not None and self.pill.mode == "ready":
            self._refresh_pill()

    def wake(self) -> None:
        """The user woke Pinlet.

        With an AI waiting in ``wait_for_wake`` the "wake" event returns that
        call and the bar says it hands back to the AI until the next action.
        With none, the bar says so for ``wake_note_secs`` and sleeps again:
        nothing would act on the wake. Waking also ends a pause or take-over,
        so the AI it hands back to is not blocked at its first step.
        """
        now = time.monotonic()
        if self.takeover:
            self.takeover = False
            self.send_event("user_idle")
        if self.paused:
            self.set_paused(False)
        self.handoff = self.ai_waiting > 0
        secs = float(self.cfg["awake_secs"]) if self.handoff else float(self.cfg["wake_note_secs"])
        self.awake_until = now + max(0.5, secs)
        self.send_event("wake")
        if self.pill_user_hidden:
            self.set_pill_hidden(False)
        self._refresh_pill()
        if self.pill is not None and not self.reduce_motion:
            self.pill.flash(self.colors["ok"])
        if self.cursor is None:
            self.cursor = self._below_bar()
            self._place_pointer(self.cursor, 0)
            self.wake_pointer = True
        self.cursor_state = "idle"
        self._refresh_tag()
        self._set_pointer_visible(True)

    def sleep(self) -> None:
        """Puts a woken Pinlet back to sleep."""
        if not self.awake_until:
            return
        self._end_awake()
        if self.wake_pointer:
            self.wake_pointer = False
            self._set_pointer_visible(False)
            self.cursor = None
        self._refresh_pill()

    def _ai_wait(self, start: bool, reason: str = "") -> None:
        """An AI starts or stops waiting in ``wait_for_wake``."""
        if start:
            self.ai_waiting += 1
            self.wait_reason = reason
            if self.awake_until:
                self.sleep()  # back to "sleeping · click to wake"
        else:
            self.ai_waiting = max(0, self.ai_waiting - 1)
        if not self.ai_waiting:
            self.wait_reason = ""
        mark_waiting(self.wait_dir, os.getpid(), self.ai_waiting > 0)
        if self.ai_waiting and self.pill_user_hidden:
            self.set_pill_hidden(False)  # the click target must be on screen
        self._refresh_pill()

    def _below_bar(self) -> tuple[float, float]:
        """Global top-left point under the bar, clear of where the bar ducks."""
        if self.pill is not None:
            f = self.pill.window.frame()
            x = f.origin.x + f.size.width / 2
            y = self.primary_h - f.origin.y + 90
        else:
            screens = NSScreen.screens() or []
            w = float(screens[0].frame().size.width) if screens else 800.0
            x, y = w / 2, 130.0
        return (x, y)

    def _refresh_pill(self) -> None:
        self._refresh_glow()  # every state change that moves the bar also moves the glow
        if self.pill is None or self.pending_confirm is not None:
            self._refresh_menu()
            return
        view = bar_view(
            paused=self.paused,
            takeover=self.takeover,
            takeover_resume=self.cfg["takeover_resume"],
            working=time.monotonic() - self.last_action <= float(self.cfg["active_secs"]),
            turbo=self.turbo,
            current_mode=self.pill.mode,
            bar_mode=self.pill_mode,
            owner=self.bar_owner.owned,
            awake=self._awake(),
            ai_waiting=self.ai_waiting > 0,
            handoff=self.handoff,
            other_waiting=self.others_waiting,
            locked=self.input_lock is not None,
        )
        if view is None:
            self.pill.hide()
        else:
            mode, _text, _primary, _secondary, color, _dot_alpha = view
            rgb = self.rgb if color == "risk" else self.colors[color]
            now = time.monotonic()
            spec = style.bar_spec(
                view,
                style=self.style,
                hovered=self.pill_hovered,
                label=str(self.cfg["label"]),
                step=self.current_step,
                steps=self.task_steps,
                goal=self.goal,
                elapsed=(now - self.task_started) if self.task_started else 0.0,
                turbo=self.turbo,
                thinking=self.cursor_state == "thinking",
                narrate=self.narrate,
                voice=self.voice,
            )
            self.pill.show_spec(
                spec,
                rgb,
                dots=self.agent_dots,
                tip=bar_tip(mode, self.ai_waiting > 0, self.wait_reason),
            )
        self._refresh_menu()

    def set_pill_hidden(self, hidden: bool) -> None:
        """Hides or shows the bar at the person's request."""
        self.pill_user_hidden = hidden
        if self.pill is not None:
            self.pill.set_user_hidden(hidden)
            if not hidden:
                self._refresh_pill()
        self._refresh_menu()

    def on_pill(self, which: str) -> None:
        """Handles a click on one of the bar's buttons."""
        if self.pill is None:
            return
        action = bar_click(self.pill.mode, which)
        if action in ("allow", "deny"):
            self._answer_confirm(action == "allow")
        elif action in ("hide", "dismiss"):
            if action == "hide":
                self.set_pill_hidden(True)
            self.pill.mode = "ready"
            self._refresh_pill()
        elif action == "stop":
            self.request_stop()
        elif action == "pause":
            self.set_paused(True)
        elif action == "resume":
            self.resume()

    def set_paused(self, paused: bool) -> None:
        """Pauses or resumes Pinlet and tells the server."""
        if paused == self.paused:
            return
        self.paused = paused
        self.takeover_reported = False
        self.send_event("pause" if paused else "resume")
        self._refresh_pill()

    def toggle_pause(self) -> None:
        """Resumes when paused or taken over, else pauses."""
        if self.paused or self.takeover:
            self.resume()
        else:
            self.set_paused(True)

    def resume(self) -> None:
        """Ends a pause or take-over and tells the server."""
        if self.takeover:
            self.takeover = False
            self.send_event("user_idle")
        if self.paused:
            self.set_paused(False)
        self._mark_active()
        self._refresh_pill()

    def request_stop(self) -> None:
        """Stops the current task: denies any pending prompt and tells the server."""
        if self.pending_confirm is not None:
            self._answer_confirm(False)
        self.paused = False
        self.takeover = False
        self.lock_stopped = True
        if self.awake_until:
            self.sleep()
        self.send_event("stop")
        self.send_event("user_idle")
        self.intent_text = "Stopped"
        self.intent_until = time.monotonic() + 1.5
        self._refresh_tag()
        if self.pill is not None:
            self.pill.show("stopped", "Pinlet stopped", "OK", "Hide", self.colors["danger"])
            self.stopped_until = time.monotonic() + 2.0
        self._refresh_menu()

    def _start_confirm(
        self,
        msg_id: Any,
        text: str,
        secs: float,
        detail: str = "",
        allow: str = "Allow",
        deny: str = "Deny",
        kind: str = "danger",
    ) -> None:
        if self.pending_confirm is not None:
            self._answer_confirm(False)
        if self.turbo:
            self._add_step("Turbo allowed: " + _short(text, 50))
            self.ack(msg_id, True)
            return
        self.pending_confirm = (msg_id, time.monotonic() + max(1.0, secs))
        if self.pill is None:
            self._answer_confirm(False)  # no way to ask: deny
            return
        spec = style.confirm_spec(
            _short(text, 70), allow=allow, deny=deny, kind=kind, detail=detail
        )
        rgb = self.colors["ok"] if kind == "access" else self.colors["danger"]
        self.pill.show_spec(spec, rgb)

    def _answer_confirm(self, ok: bool) -> None:
        if self.pending_confirm is None:
            return
        msg_id, _ = self.pending_confirm
        self.pending_confirm = None
        self.ack(msg_id, ok)
        # The user answering is not Pinlet waking up: keep a bar they hid hidden.
        self.last_action = time.monotonic()
        self._refresh_pill()

    # ------------------------------------------- controls: input + hotkeys
    def _install_monitors(self) -> None:
        mask = (
            NSEventMaskKeyDown
            | NSEventMaskLeftMouseDown
            | NSEventMaskRightMouseDown
            | NSEventMaskMouseMoved
            | NSEventMaskLeftMouseDragged
            | NSEventMaskScrollWheel
        )
        try:
            mon = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                mask, self._on_global_event
            )
            if mon is not None:
                self.monitors.append(mon)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"pinlet.overlay: no input monitor: {exc}\n")

    def _event_pid(self, event) -> int:
        try:
            cg = event.CGEvent()
            return (
                int(CGEventGetIntegerValueField(cg, kCGEventSourceUnixProcessID))
                if cg is not None
                else 0
            )
        except Exception:  # noqa: BLE001
            return 0

    def _event_tag(self, event) -> int:
        try:
            cg = event.CGEvent()
            return (
                int(CGEventGetIntegerValueField(cg, _kCGEventSourceUserData))
                if cg is not None
                else 0
            )
        except Exception:  # noqa: BLE001
            return 0

    def _on_global_event(self, event) -> None:
        try:
            if self._event_tag(event) == PINLET_EVENT_TAG:
                return  # synthetic input from any Pinlet process, not the person
            pid = self._event_pid(event)
            if pid and (
                pid in (self.server_pid, os.getpid())
                or (self.agent_pids is not None and pid in self.agent_pids)
            ):
                return  # synthetic input from this or another Pinlet agent, not the person
            etype = int(event.type())
            if etype == 10:  # NSEventTypeKeyDown
                flags = int(event.modifierFlags()) & _HOTKEY_MODS
                if flags == _HOTKEY_MODS and int(event.keyCode()) == _KEY_ESC:
                    self.request_stop()
                    return
                if flags == _HOTKEY_MODS and int(event.keyCode()) == _KEY_P:
                    self.toggle_pause()
                    return
                if flags == _HOTKEY_MODS and int(event.keyCode()) == _KEY_H:
                    self.set_pill_hidden(not self.pill_user_hidden)
                    return
            if self.lock_armed():
                # The lock already swallowed what landed in Pinlet's window;
                # what got here is the person using another app: no take-over.
                return
            self._on_user_input(_INPUT_KIND.get(etype, "key"))
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"pinlet.overlay: input monitor: {exc}\n")

    def _on_user_input(self, kind: str = "click") -> None:
        if self.cfg.get("background"):
            return  # the agent works in its own window; the person's input is theirs
        now = time.monotonic()
        working = now - self.last_action <= float(self.cfg["active_secs"])
        if self.takeover and kind == "move":
            self.last_user_input = now  # still busy: keep the take-over going
            return
        if not takes_over(
            kind,
            working=working,
            paused=self.paused,
            takeover=self.takeover,
            turbo=self.turbo,
            confirming=self.pending_confirm is not None,
        ):
            return  # idle, Turbo, reaching for the bar / Allow-Deny, or just moving the mouse
        self.last_user_input = now
        if self.paused:
            if not self.takeover_reported:
                self.takeover_reported = True
                self.send_event("user_input")
                self.send_event("user_idle")  # the pause already holds Pinlet
            return
        if not self.takeover:
            self.send_event("user_input")
            if self.cfg["takeover_resume"] == "manual":
                self.paused = True
                self.takeover_reported = True
                self.send_event("pause")
                self.send_event("user_idle")
            else:
                self.takeover = True
            self._refresh_pill()

    # ------------------------------------------- controls: input lock
    def is_pinlet_pid(self, pid: int) -> bool:
        """True when ``pid`` belongs to this Pinlet or another running one."""
        if pid in (self.server_pid, os.getpid()):
            return True
        return self.agent_pids is not None and pid in self.agent_pids

    def lock_armed(self) -> bool:
        """True when the input lock should be blocking the person's input right now."""
        return inputlock.armed(
            mode=self.lock_mode if self.input_lock is not None else "off",
            working=time.monotonic() - self.last_action <= float(self.cfg["active_secs"]),
            paused=self.paused or self.lock_stopped,
            takeover=self.takeover,
            confirming=self.pending_confirm is not None,
            has_target=self.lock_target is not None,
            screen_reader=self.voiceover,
        )

    def _own_rects(self) -> list[geometry.Rect]:
        """The overlay's clickable windows (bar, tag hotspot), global top-left."""
        rects: list[geometry.Rect] = []
        if self.pill is not None and self.pill.visible and not self.pill.ducked:
            f = self.pill.window.frame()
            rects.append(
                (
                    float(f.origin.x),
                    self.primary_h - float(f.origin.y) - float(f.size.height),
                    float(f.size.width),
                    float(f.size.height),
                )
            )
        if self.hotspot is not None and self.hotspot.rect is not None:
            rects.append(self.hotspot.rect)
        return rects

    def lock_blocks(self, kind: str, pt: tuple[float, float] | None) -> bool:
        """True when the input lock should swallow this event at ``pt``."""
        return inputlock.blocks(
            kind,
            pt,
            target=self.lock_target,
            above=self.lock_above,
            own=self._own_rects(),
            target_front=self.lock_front,
        )

    def on_lock_swallowed(self, kind: str, pt: tuple[float, float] | None) -> None:
        """The person tried to use Pinlet's window: point them at Take over."""
        now = time.monotonic()
        if now < self.lock_note_next:
            return
        self.lock_note_next = now + 0.8
        if self.pill_user_hidden:
            self.set_pill_hidden(False)  # the way out must be visible
        if self.pill is not None and self.pill.visible:
            self.pill.flash(self.colors["caution"])

    def on_lock_insist(self) -> None:
        """Clicked again and again (or held): that is a take-over."""
        if self.input_lock is not None:
            self.input_lock.insist.reset()
        if not self.turbo:
            self._on_user_input("click")
        if not (self.paused or self.takeover):
            self.set_paused(True)  # Turbo ignores take-over; a pause still frees the window
        self._refresh_pill()

    # ------------------------------------- controls: menu (bar ▾, menu bar)
    def _install_status_item(self) -> None:
        try:
            self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(-1.0)
            self.topbar_menu = NSMenu.alloc().init()
            self.topbar_menu.setAutoenablesItems_(False)
            self.topbar_menu.setDelegate_(self.target)  # filled in menuNeedsUpdate_
            self.status_item.setMenu_(self.topbar_menu)
            self.topbar_sig = None
            self._refresh_topbar(force=True)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"pinlet.overlay: no menu bar item: {exc}\n")
            self.status_item = None

    def _menu_item(self, menu, title: str, action: str | None = None, state: bool | None = None):
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
        if action:
            item.setTarget_(self.target)
        item.setEnabled_(action is not None)
        if state is not None:
            item.setState_(1 if state else 0)
        menu.addItem_(item)
        return item

    def _status(self) -> tuple[str, str]:
        """(status text, one-glyph menu bar title)."""
        working = time.monotonic() - self.last_action <= float(self.cfg["active_secs"])
        return status_of(self.paused, self.takeover, working, self.turbo)

    def _build_menu(self, status: str):
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        working = time.monotonic() - self.last_action <= float(self.cfg["active_secs"])
        halted = self.paused or self.takeover
        for title, action, state in menu_entries(
            status,
            self.steps,
            halted,
            self.turbo,
            self.user_visible,
            self.trail_on,
            None if self.pill is None else not self.pill_user_hidden,
            awake=None if (working or (halted and not self.ai_waiting)) else self._awake(),
        ):
            if title == "-":
                menu.addItem_(NSMenuItem.separatorItem())
            else:
                self._menu_item(menu, title, action, state)
        self._add_picker(menu)
        return menu

    # ------------------------------------------------------- Pinlet picker
    def _agents(self) -> list:
        try:
            from pinlet.control import commands, control_root

            return commands.overview(control_root())
        except Exception as exc:  # noqa: BLE001 - the menu works without it
            sys.stderr.write(f"pinlet.overlay: picker: {exc}\n")
            return []

    def _add_picker(self, menu) -> None:
        """Insert one submenu per running Pinlet after the status line."""
        groups = picker_groups(self._agents(), self.server_pid, self.paused or self.takeover)
        if not groups:
            return
        at = 2  # after "Pinlet: <status>" and its separator
        header = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Pinlets", None, "")
        header.setEnabled_(False)
        menu.insertItem_atIndex_(header, at)
        for title, pid, items in groups:
            at += 1
            sub = NSMenu.alloc().initWithTitle_(title)
            sub.setAutoenablesItems_(False)
            for item_title, action in items:
                item = self._menu_item(sub, item_title, action)
                item.setRepresentedObject_(pid)
            parent = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
            parent.setSubmenu_(sub)
            menu.insertItem_atIndex_(parent, at)
        menu.insertItem_atIndex_(NSMenuItem.separatorItem(), at + 1)

    def _commands(self):
        from pinlet.control import commands, control_root

        return commands, control_root()

    def agent_pause_toggle(self, pid: int) -> None:
        """Pauses or resumes the agent with ``pid`` from the menu."""
        if pid == self.server_pid:
            self.toggle_pause()
            return
        commands, root = self._commands()
        commands.set_paused(root, pid, not commands.read(root, pid)["paused"])

    def agent_wake(self, pid: int) -> None:
        """Wakes the Pinlet with ``pid`` (its AI waits in ``wait_for_wake``) from the menu."""
        if pid == self.server_pid:
            self.wake()
            return
        from pinlet.control import control_root, inbox

        inbox.request_wake(control_root(), pid)

    def agent_stop(self, pid: int) -> None:
        """Stops the agent with ``pid`` from the menu."""
        if pid == self.server_pid:
            self.request_stop()
            return
        commands, root = self._commands()
        commands.request_stop(root, pid)

    def all_paused(self, paused: bool) -> None:
        """Pauses or resumes every agent."""
        commands, root = self._commands()
        for agent in commands.overview(root):
            if agent["pid"] == self.server_pid:
                if paused and not (self.paused or self.takeover):
                    self.set_paused(True)
                elif not paused and (self.paused or self.takeover):
                    self.resume()
            else:
                commands.set_paused(root, agent["pid"], paused)

    def _refresh_menu(self) -> None:
        """Our state changed: tell the top-bar item (ours, or another helper's)."""
        self._publish_status()
        self._refresh_topbar(force=True)

    # ------------------------------------------------- menu bar item (topbar)
    def _app_name(self) -> str:
        pid = self.track_pid
        if not pid:
            return ""
        if pid not in self.app_names:
            try:
                app = NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid))
                self.app_names[pid] = str(app.localizedName() or "") if app is not None else ""
            except Exception:  # noqa: BLE001
                self.app_names[pid] = ""
        return self.app_names[pid]

    def _publish_status(self) -> None:
        """Write what this Pinlet does for whichever helper owns the menu bar item."""
        if not self.interactive or not self.server_pid:
            return
        now = time.monotonic()
        working = now - self.last_action <= float(self.cfg["active_secs"])
        mode = topbar.pinlet_mode(
            paused=self.paused,
            takeover=self.takeover,
            confirming=self.pending_confirm is not None,
            stopped=now < self.stopped_until,
            working=working,
            awake=self._awake(),
        )
        topbar.publish(
            self.server_pid,
            mode=mode,
            step=self.current_step if working else "",
            steps=self.task_steps if working else 0,
            elapsed=(now - self.task_started) if (working and self.task_started) else 0.0,
            turbo=self.turbo,
            app=self._app_name() if working else "",
        )
        self.publish_next = now + 5.0

    def _topbar_view(self) -> topbar.View:
        return topbar.topbar_view(self._agents(), topbar.read_states(), self.server_pid)

    def _dark_menu_bar(self) -> bool:
        try:
            name = self.status_item.button().effectiveAppearance().name()
            return "Dark" in str(name)
        except Exception:  # noqa: BLE001
            return False

    def _refresh_topbar(self, force: bool = False) -> None:
        """Update the menu bar icon, its count and tooltip (about once a second)."""
        if self.status_item is None:
            return
        now = time.monotonic()
        if not force and now < self.topbar_next:
            return
        self.topbar_next = now + 1.0
        try:
            view = self._topbar_view()
            dark = self._dark_menu_bar()
            sig = (topbar.icon_state(view), topbar.icon_title(view), topbar.tooltip(view), dark)
            if sig == self.topbar_sig:
                return
            self.topbar_sig = sig
            button = self.status_item.button()
            path = topbar.bar_icon_path(sig[0], 36, dark=dark)
            image = NSImage.alloc().initWithContentsOfFile_(path) if path else None
            if image is not None:
                image.setSize_((18, 18))
                button.setImage_(image)
                button.setImagePosition_(2)  # NSImageLeft: the count sits to its right
                button.setTitle_(sig[1])
            else:
                button.setImage_(None)
                button.setTitle_("◆ " + sig[1] if sig[1] else "◆")
            button.setToolTip_(sig[2])
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"pinlet.overlay: menu bar item: {exc}\n")

    def _row_image(self, color: str, shape: str):
        path = topbar.row_icon_path(color, shape, 52)
        image = NSImage.alloc().initWithContentsOfFile_(path) if path else None
        if image is not None:
            image.setSize_((26, 26))
        return image

    @staticmethod
    def _two_lines(first: str, second: str, bold: bool = False):
        """Attributed menu title: a name over a smaller grey line (Focus style)."""
        from AppKit import NSMutableAttributedString

        text = NSMutableAttributedString.alloc().initWithString_attributes_(
            first,
            {
                NSFontAttributeName: NSFont.boldSystemFontOfSize_(13.0)
                if bold
                else NSFont.menuFontOfSize_(13.0)
            },
        )
        if second:
            text.appendAttributedString_(
                NSAttributedString.alloc().initWithString_attributes_(
                    "\n" + second,
                    {
                        NSFontAttributeName: NSFont.systemFontOfSize_(11.0),
                        NSForegroundColorAttributeName: NSColor.secondaryLabelColor(),
                    },
                )
            )
        return text

    def fill_topbar_menu(self, menu) -> None:
        """Build the dropdown as it opens: header, one row per Pinlet, settings."""
        try:
            self._publish_status()
            view = self._topbar_view()
            menu.removeAllItems()
            header = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(view.header, None, "")
            header.setAttributedTitle_(self._two_lines(view.header, view.subtitle, bold=True))
            header.setEnabled_(False)
            menu.addItem_(header)
            menu.addItem_(NSMenuItem.separatorItem())
            for row in view.rows:
                item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(row.title, None, "")
                item.setAttributedTitle_(
                    self._two_lines(
                        row.title + ("  (this bar)" if row.own and len(view.rows) > 1 else ""),
                        row.detail,
                    )
                )
                image = self._row_image(row.color, row.shape)
                if image is not None:
                    item.setImage_(image)
                sub = NSMenu.alloc().initWithTitle_(row.title)
                sub.setAutoenablesItems_(False)
                for title, action in row.actions:
                    selector = {
                        "pause_toggle": "menuAgentPauseToggle:",
                        "stop": "menuAgentStop:",
                        "wake": "menuAgentWake:",
                    }[action]
                    self._menu_item(sub, title, selector).setRepresentedObject_(row.pid)
                item.setSubmenu_(sub)
                item.setEnabled_(True)
                menu.addItem_(item)
            if view.all_actions:
                menu.addItem_(NSMenuItem.separatorItem())
                for title, action in view.all_actions:
                    self._menu_item(
                        menu,
                        title,
                        {
                            "pause_all": "menuAllPause:",
                            "resume_all": "menuAllResume:",
                            "stop_all": "menuAllStop:",
                        }[action],
                    )
            menu.addItem_(NSMenuItem.separatorItem())
            settings = NSMenu.alloc().initWithTitle_("Pinlet Settings")
            settings.setAutoenablesItems_(False)
            self._menu_item(
                settings,
                "Turbo mode (no prompts, ignores take-over)",
                "menuToggleTurbo:",
                self.turbo,
            )
            self._menu_item(settings, "Show overlay", "menuToggleOverlay:", self.user_visible)
            if self.pill is not None:
                self._menu_item(
                    settings, "Show control bar (⌃⌥H)", "menuTogglePill:", not self.pill_user_hidden
                )
            self._menu_item(settings, "Show step trail", "menuToggleTrail:", self.trail_on)
            self._menu_item(settings, "Clear step trail", "menuClearTrail:")
            parent = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Pinlet Settings", None, ""
            )
            parent.setSubmenu_(settings)
            menu.addItem_(parent)
        except Exception as exc:  # noqa: BLE001 - never break the menu bar
            sys.stderr.write(f"pinlet.overlay: menu bar menu: {exc}\n")

    def all_stop(self) -> None:
        """Stops every agent."""
        commands, root = self._commands()
        for agent in commands.overview(root):
            if agent["pid"] == self.server_pid:
                self.request_stop()
            else:
                commands.request_stop(root, agent["pid"])

    def toggle_overlay(self) -> None:
        """Shows or hides the overlay."""
        self.user_visible = not self.user_visible
        self._apply_alpha()
        self._refresh_menu()

    def toggle_turbo(self) -> None:
        """Turns turbo mode (no prompts) on or off."""
        self.turbo = not self.turbo
        if self.turbo and self.takeover:
            self.takeover = False
            self.send_event("user_idle")
        self._refresh_pill()

    def toggle_trail(self) -> None:
        """Turns the pointer trail on or off."""
        self.trail_on = not self.trail_on
        self._redraw_trail()
        self._refresh_menu()

    def clear_trail(self) -> None:
        """Clears the pointer trail."""
        self.trail_points = []
        self._redraw_trail()

    # ------------------------------------------------------------- commands
    def handle_line(self, line: str) -> None:
        """Parses and handles one line from the server; bad lines are acknowledged with an error."""
        line = line.strip()
        if not line:
            return
        msg_id = None
        try:
            raw = json.loads(line)
            msg_id = raw.get("id") if isinstance(raw, dict) else None
            msg = protocol.validate(raw)
            self.handle(msg)
        except Exception as exc:  # noqa: BLE001 - never die on one bad message
            sys.stderr.write(f"pinlet.overlay: {exc}\n")
            self.ack(msg_id, False, str(exc))

    def _set_intent(self, text: str, ms: float | None = None) -> None:
        self.intent_text = _short(text) if text else ""
        if text:
            self.intent_at = time.monotonic()
            self.intent_held = True
        secs = style.intent_secs(ms, float(self.cfg["intent_ms"]), self.narrate)
        if not text:
            self.intent_until = 0.0
        else:  # None: until the next action, or until Pinlet is idle (see on_tick)
            self.intent_until = time.monotonic() + secs if secs is not None else float("inf")
        self._refresh_tag()

    def handle(self, msg: dict[str, Any]) -> None:
        """Handles one decoded command from the server."""
        op, msg_id = msg["op"], msg.get("id")
        if op == "cursor.move":
            self._mark_active()
            target = (float(msg["x"]), float(msg["y"]))
            verb = msg.get("verb")
            if msg.get("probe"):
                name, frame = self.probe.at(*target)
                if (
                    frame is not None
                    and 3 <= frame[2] <= 1600
                    and 3 <= frame[3] <= 900
                    and geometry.contains(frame, target)
                ):
                    self._show_box(frame)
                else:
                    self._clear_box()
                if verb:
                    text = style.action_text(verb, name, msg.get("app"))
                    self._set_intent(text)
                    self._add_step(text)
            elif verb and verb != "Move":
                self._set_intent(verb)
                self._add_step(verb)
            if self.cursor is None:
                start = self._real_mouse()
                self._place_pointer(start, 0)
                self._set_pointer_visible(True)
                self.cursor = start
            ms = msg.get("ms")
            if ms is None:
                ms = geometry.glide_ms(self.cursor, target, speed=float(self.cfg["speed"]))
            dur = self._duration(ms)
            if self.cursor_state == "thinking":
                self.cursor_state = "idle"
                self._refresh_tag()
            self._place_pointer(target, ms)
            self.cursor = target
            if msg_id is not None:
                AppHelper.callLater(dur, self.ack, msg_id)
        elif op == "cursor.click":
            self._mark_active()
            self._ripple(int(msg.get("count", 1)))
            if self.cursor is not None:
                self.trail_points.append(
                    (self.step_no or len(self.trail_points) + 1, self.cursor, time.monotonic())
                )
                self.trail_points = self.trail_points[-int(self.cfg["trail_len"]) :]
                self._redraw_trail()
            AppHelper.callLater(0.5, self._clear_box)
            self.ack(msg_id)
        elif op == "cursor.state":
            self.cursor_state = msg["state"]
            if self.cursor_state in ("thinking", *ACTING_STATES):
                self._mark_active()
            if (
                self.cursor_state in ACTING_STATES
                and self.cursor is None
                and not self.cfg.get("background")
            ):
                # Keys and scrolling land where the real pointer is: show Pinlet's there.
                start = self._real_mouse()
                self._place_pointer(start, 0)
                self._set_pointer_visible(True)
                self.cursor = start
            self._refresh_tag()
            self._refresh_pill()  # thinking: bar shimmer and glow sweep
            self.ack(msg_id)
        elif op == "cursor.hide":
            self._set_pointer_visible(False)
            self.cursor = None
            self.ack(msg_id)
        elif op == "intent":
            text = msg["text"]
            if text:  # clearing the tag after a step is not a new step
                self._mark_active()
            self._set_intent(text, msg.get("ms"))
            if text:
                self._add_step(_short(text, 60))
            self.ack(msg_id)
        elif op == "target.box":
            self._show_box((float(msg["x"]), float(msg["y"]), float(msg["w"]), float(msg["h"])))
            self.ack(msg_id)
        elif op == "target.clear":
            self._clear_box()
            self.ack(msg_id)
        elif op == "goal":
            self.goal = msg["text"]
            self._refresh_tag()
            self._refresh_pill()
            self.ack(msg_id)
        elif op == "risk":
            self._set_risk(msg["level"])
            self.ack(msg_id)
        elif op == "scan":
            rect = None
            if "x" in msg:
                rect = (float(msg["x"]), float(msg["y"]), float(msg["w"]), float(msg["h"]))
            self._scan(rect)
            self.ack(msg_id)
        elif op == "trail.clear":
            self.clear_trail()
            self.ack(msg_id)
        elif op == "confirm":
            self._mark_active()
            self._start_confirm(
                msg_id,
                msg["text"],
                float(msg.get("secs", 30.0)),
                detail=str(msg.get("detail") or ""),
                allow=str(msg.get("allow") or "Allow"),
                deny=str(msg.get("deny") or "Deny"),
                kind=str(msg.get("kind") or "danger"),
            )
        elif op == "confirm.cancel":
            if self.pending_confirm is not None:
                self._answer_confirm(False)
            self.ack(msg_id)
        elif op == "overlay.visible":
            self.user_visible = bool(msg["on"])
            self._apply_alpha()
            self._refresh_menu()
            self.ack(msg_id)
        elif op == "glow.track":
            self.track_wid = protocol.window_id_int(msg.get("window_id"))
            self.track_pid = msg.get("pid")
            self.glow_rect = None
            self._track_tick()
            self.ack(msg_id)
        elif op == "glow.clear":
            self.track_wid = self.track_pid = None
            self.glow_rect = None
            self.lock_target = None
            self._set_glow_opacity(0.0)
            self._glow_sig = None
            self.ack(msg_id)
        elif op == "hide":
            self.capture_hidden = True
            self._apply_alpha()
            CATransaction.flush()
            # Give the window server a couple of frames to composite.
            AppHelper.callLater(0.05, self.ack, msg_id)
        elif op == "show":
            self.capture_hidden = False
            self._apply_alpha()
            self.ack(msg_id)
        elif op == "sleep.wait":
            self.goal = ""  # that task is over
            self._ai_wait(True, str(msg.get("reason") or ""))
            self.ack(msg_id)
        elif op == "sleep.done":
            self._ai_wait(False)
            self.ack(msg_id)
        elif op == "input.pass":
            self.ack(msg_id)  # the tap lets Pinlet's tagged events through already
        elif op == "turbo":
            self.turbo = bool(msg["on"])
            self.cfg["turbo"] = self.turbo
            self._refresh_pill()
            self.ack(msg_id)
        elif op == "ping":
            self.ack(msg_id)
        elif op == "bye":
            self.ack(msg_id)
            self.quit()

    def _real_mouse(self) -> tuple[float, float]:
        loc = NSEvent.mouseLocation()  # Cocoa global, bottom-left origin
        return (float(loc.x), self.primary_h - float(loc.y))

    def quit(self) -> None:
        """Stops the timer, input monitors and lock, and exits the app."""
        try:
            self.timer.invalidate()
        except Exception:  # noqa: BLE001
            pass
        for mon in self.monitors:
            try:
                NSEvent.removeMonitor_(mon)
            except Exception:  # noqa: BLE001
                pass
        if self.pending_confirm is not None:
            self._answer_confirm(False)
        if self.input_lock is not None:
            self.input_lock.close()
            self.input_lock = None
        for p in self.panels:
            p.close()
        if self.hotspot is not None:
            self.hotspot.close()
        if self.pill is not None:
            self.pill.close()
        if self.status_item is not None:
            try:
                NSStatusBar.systemStatusBar().removeStatusItem_(self.status_item)
            except Exception:  # noqa: BLE001
                pass
        if self.interactive and self.server_pid:
            topbar.clear(self.server_pid)
        self.bar_owner.release()
        mark_waiting(self.wait_dir, os.getpid(), False)
        AppHelper.stopEventLoop()


# ------------------------------------------------------------ entry points
def _stdin_loop(controller: OverlayController) -> None:
    for line in sys.stdin:
        AppHelper.callAfter(controller.handle_line, line)
    AppHelper.callAfter(controller.quit)


def _schedule(
    controller: OverlayController, steps: list[tuple[float, dict[str, Any]]], tail: float
) -> None:
    for delay, msg in steps:
        AppHelper.callLater(delay, controller.handle, msg)
    end = (steps[-1][0] if steps else 0.0) + tail
    AppHelper.callLater(end, controller.quit)


def _demo(controller: OverlayController) -> None:
    screen = NSScreen.mainScreen().frame()
    w, h = float(screen.size.width), float(screen.size.height)
    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    pid = int(front.processIdentifier()) if front else None
    steps: list[tuple[float, dict[str, Any]]] = [
        (0.2, {"op": "cursor.move", "x": w * 0.3, "y": h * 0.3, "verb": "Click", "probe": True}),
        (0.9, {"op": "cursor.click"}),
        (1.4, {"op": "scan"}),
        (1.4, {"op": "cursor.state", "state": "thinking"}),
        (
            2.4,
            {
                "op": "cursor.move",
                "x": w * 0.7,
                "y": h * 0.4,
                "verb": "Double-click",
                "probe": True,
            },
        ),
        (3.1, {"op": "cursor.click", "count": 2}),
        (3.5, {"op": "intent", "text": "Type “hello”"}),
        (3.5, {"op": "cursor.state", "state": "typing"}),
        (4.6, {"op": "cursor.state", "state": "idle"}),
        (4.8, {"op": "risk", "level": "caution"}),
        (4.8, {"op": "intent", "text": "Check before saving"}),
        (5.0, {"op": "cursor.move", "x": w * 0.5, "y": h * 0.6, "verb": "Click", "probe": True}),
        (5.7, {"op": "cursor.click"}),
        (6.2, {"op": "risk", "level": "danger"}),
        (6.2, {"op": "intent", "text": "Press ⌘Q (quit the app)", "ms": 2500}),
        (8.4, {"op": "risk", "level": "ok"}),
        (8.6, {"op": "cursor.move", "x": w * 0.45, "y": h * 0.35, "verb": "Click", "probe": True}),
        (9.3, {"op": "cursor.click"}),
    ]
    if pid is not None:
        steps.insert(0, (0.1, {"op": "glow.track", "pid": pid}))
    _schedule(controller, steps, tail=3.0)


def load_track(path: str) -> list[dict[str, Any]]:
    """Reads a recorded track (``.jsonl``) for replay."""
    events = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


_REPLAY_SKIP = ("confirm", "confirm.cancel", "hide", "show", "bye", "ping")


def _replay(controller: OverlayController, path: str, speed: float) -> None:
    steps = []
    t0 = None
    for e in load_track(path):
        if "ts" not in e or e.get("op") in _REPLAY_SKIP:
            continue
        msg = {k: v for k, v in e.items() if k != "ts"}
        try:
            protocol.validate(msg)
        except protocol.ProtocolError:
            continue
        t0 = float(e["ts"]) if t0 is None else t0
        steps.append(((float(e["ts"]) - t0) / max(speed, 0.05), msg))
    if not steps:
        sys.stderr.write(f"pinlet.overlay: nothing to replay in {path}\n")
        AppHelper.callLater(0.1, controller.quit)
        return
    _schedule(controller, steps, tail=2.0)


def _arg(argv: list[str], name: str) -> str | None:
    if name in argv and argv.index(name) + 1 < len(argv):
        return argv[argv.index(name) + 1]
    return None


def main(argv: list[str] | None = None) -> int:
    """Runs the helper: reads the config, builds the controller and starts the app loop."""
    argv = list(argv or [])
    cfg: dict[str, Any] = {}
    raw = os.environ.get("PINLET_OVERLAY_CONFIG")
    if raw:
        try:
            cfg = json.loads(raw)
        except json.JSONDecodeError:
            cfg = {}
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    demo = "--demo" in argv
    replay = _arg(argv, "--replay")
    try:
        speed = float(_arg(argv, "--speed") or 1.0)
    except ValueError:
        speed = 1.0
    controller = OverlayController(cfg, interactive=not (demo or replay))
    if demo:
        _demo(controller)
    elif replay:
        _replay(controller, replay, speed)
    else:
        threading.Thread(target=_stdin_loop, args=(controller,), name="stdin", daemon=True).start()
        from pinlet.parentwatch import watch_parent

        watch_parent(
            lambda: AppHelper.callAfter(controller.quit)
        )  # server died, pipe held elsewhere
    controller.emit({"ready": True, "version": protocol.PROTOCOL_VERSION, "pid": os.getpid()})
    AppHelper.runEventLoop(installInterrupt=True)
    return 0
