"""macOS input lock for the overlay helper (see ``inputlock`` for the rules).

An active Quartz event tap on the session drops the person's clicks, drags
and scrolls inside the window Dobby works in (and keys while that window is
in front) while Dobby works. Dobby's own events carry ``DOBBY_EVENT_TAG``
(``dobby.hal.macos``) and always pass, so Dobby keeps working with the real
mouse. Pointer moves always pass.

A background app cannot set the system cursor reliably, so while the
pointer is over the locked window a small "no entry" badge rides next to it,
and a swallowed click makes the badge pop and the bar flash.

Needs the Accessibility permission (the same one Dobby needs to click). If
the tap cannot be created the lock stays off and Dobby behaves as before.
If the helper stalls, macOS disables the tap by itself; if the helper exits,
the tap goes with it, so the person can never be locked out.
"""

from __future__ import annotations

import sys
import time
from typing import Any

from AppKit import (
    NSBackingStoreBuffered,
    NSImageView,
    NSPanel,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Quartz import (
    CABasicAnimation,
    CFMachPortCreateRunLoopSource,
    CFRunLoopAddSource,
    CFRunLoopGetMain,
    CGEventGetFlags,
    CGEventGetIntegerValueField,
    CGEventGetLocation,
    CGEventTapCreate,
    CGEventTapEnable,
    kCFRunLoopCommonModes,
    kCGEventSourceUnixProcessID,
)

from . import inputlock

_SESSION_TAP = 1  # kCGSessionEventTap
_HEAD_INSERT = 0  # kCGHeadInsertEventTap
_TAP_DEFAULT = 0  # kCGEventTapOptionDefault (may drop events)
_USER_DATA = 42  # kCGEventSourceUserData
_KEYCODE = 9  # kCGKeyboardEventKeycode
_DISABLED_TIMEOUT = 0xFFFFFFFE
_DISABLED_USER = 0xFFFFFFFF
_CTRL, _ALT = 0x40000, 0x80000
_HOTKEYS = {53, 35, 4}  # Esc (stop), P (pause), H (hide bar) with Ctrl+Option

# Quartz event type -> (kind, button, phase) for the rules in inputlock.
_TYPES: dict[int, tuple[str, str, str]] = {
    1: ("click", "l", "down"),
    2: ("click", "l", "up"),
    3: ("click", "r", "down"),
    4: ("click", "r", "up"),
    25: ("click", "o", "down"),
    26: ("click", "o", "up"),
    6: ("drag", "l", "drag"),
    7: ("drag", "r", "drag"),
    27: ("drag", "o", "drag"),
    5: ("move", "", "move"),
    22: ("scroll", "", "scroll"),
    10: ("key", "", "down"),
    11: ("key", "", "up"),
}
_MASK = 0
for _t in _TYPES:
    _MASK |= 1 << _t

_BADGE = 22.0


class _Badge:
    """A small "no entry" sign that follows the real pointer over the locked window."""

    def __init__(self, configure, symbol, rgb) -> None:
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        win = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (_BADGE, _BADGE)), style, NSBackingStoreBuffered, False
        )
        configure(win, {"capture_visible": False}, click_through=True)
        view = NSImageView.alloc().initWithFrame_(((0, 0), (_BADGE, _BADGE)))
        img = symbol("nosign", 15.0, rgb)
        if img is not None:
            view.setImage_(img)
        view.setWantsLayer_(True)
        win.setContentView_(view)
        self.window, self.view = win, view
        self.shown = False

    def place(self, pt: tuple[float, float] | None, primary_h: float) -> None:
        """pt: the pointer in global top-left points, or None to hide."""
        if pt is None:
            if self.shown:
                self.window.orderOut_(None)
                self.shown = False
            return
        x, y = pt[0] + 12.0, pt[1] + 14.0  # below-right of the arrow, like a cursor badge
        self.window.setFrameOrigin_((x, primary_h - y - _BADGE))
        if not self.shown:
            self.window.orderFrontRegardless()
            self.shown = True

    def pop(self) -> None:
        layer = self.view.layer()
        if layer is None:
            return
        anim = CABasicAnimation.animationWithKeyPath_("transform.scale")
        anim.setFromValue_(1.6)
        anim.setToValue_(1.0)
        anim.setDuration_(0.3)
        layer.addAnimation_forKey_(anim, "pop")

    def close(self) -> None:
        self.window.orderOut_(None)
        self.window.close()


class MacInputLock:
    """Owns the event tap. ``owner`` is the helper's ``OverlayController``."""

    def __init__(self, owner: Any, configure, symbol) -> None:
        self.owner = owner
        self.insist = inputlock.Insist()
        self.dropped_buttons: set[str] = set()
        self.dropped_keys: set[int] = set()
        self.timeouts = 0
        self.tap = None
        self.badge: _Badge | None = None
        try:
            self.badge = _Badge(configure, symbol, owner.colors["danger"])
        except Exception as exc:  # noqa: BLE001 - the badge is a nicety
            sys.stderr.write(f"dobby.overlay: no lock badge: {exc}\n")
        self._callback = self._on_event  # keep a reference for the tap's lifetime
        try:
            tap = CGEventTapCreate(
                _SESSION_TAP, _HEAD_INSERT, _TAP_DEFAULT, _MASK, self._callback, None
            )
        except Exception as exc:  # noqa: BLE001
            tap = None
            sys.stderr.write(f"dobby.overlay: input lock: {exc}\n")
        if tap is None:
            sys.stderr.write(
                "dobby.overlay: input lock off: no event tap "
                "(grant Accessibility to the app that runs Dobby)\n"
            )
            return
        src = CFMachPortCreateRunLoopSource(None, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetMain(), src, kCFRunLoopCommonModes)
        CGEventTapEnable(tap, True)
        self.tap = tap
        sys.stderr.write("dobby.overlay: input lock ready (window)\n")

    @property
    def ok(self) -> bool:
        return self.tap is not None

    # ------------------------------------------------------------- the tap
    def _ours(self, event) -> bool:
        try:
            if int(CGEventGetIntegerValueField(event, _USER_DATA)) == self.owner.event_tag:
                return True
            pid = int(CGEventGetIntegerValueField(event, kCGEventSourceUnixProcessID))
        except Exception:  # noqa: BLE001
            return False
        return bool(pid) and self.owner.is_dobby_pid(pid)

    def _on_event(self, _proxy, etype, event, _refcon):
        try:
            return self._filter(int(etype), event)
        except Exception as exc:  # noqa: BLE001 - never wedge the person's input
            sys.stderr.write(f"dobby.overlay: input lock: {exc}\n")
            return event

    def _filter(self, etype: int, event):
        if etype in (_DISABLED_TIMEOUT, _DISABLED_USER):
            # macOS turned the tap off (we were too slow). Turn it back on a
            # few times; after that leave it off rather than risk lag.
            self.timeouts += 1
            if etype == _DISABLED_TIMEOUT and self.timeouts <= 3 and self.tap is not None:
                CGEventTapEnable(self.tap, True)
            return event
        spec = _TYPES.get(etype)
        if spec is None or self._ours(event):
            return event
        kind, button, phase = spec
        owner = self.owner
        armed = owner.lock_armed()
        loc = CGEventGetLocation(event)
        pt = (float(loc.x), float(loc.y))
        if kind == "move" or kind == "drag":
            self._show_badge(pt if armed and owner.lock_blocks("click", pt) else None)
        if kind == "key":
            code = int(CGEventGetIntegerValueField(event, _KEYCODE))
            flags = int(CGEventGetFlags(event))
            if code in _HOTKEYS and flags & _CTRL and flags & _ALT:
                return event  # Stop / Pause / Hide always work
            if phase == "up":
                if code in self.dropped_keys:
                    self.dropped_keys.discard(code)
                    return None
                return event
            if armed and owner.lock_blocks("key", None):
                self.dropped_keys.add(code)
                owner.on_lock_swallowed("key", None)
                return None
            return event
        if phase in ("up", "drag"):
            # Only the rest of a press that was swallowed; never strand a
            # press that started before the lock came on.
            if button in self.dropped_buttons:
                if phase == "up":
                    self.dropped_buttons.discard(button)
                    if self.insist.release(time.monotonic()):
                        owner.on_lock_insist()
                return None
            return event
        if not armed or not owner.lock_blocks(kind, pt):
            return event
        if phase == "down" and self.insist.press(time.monotonic()):
            owner.on_lock_insist()  # they mean it: this click goes through
            return event
        if phase == "down":
            self.dropped_buttons.add(button)
        owner.on_lock_swallowed(kind, pt)
        if self.badge is not None:
            self._show_badge(pt)
            self.badge.pop()
        return None

    # ----------------------------------------------------------- the badge
    def _show_badge(self, pt: tuple[float, float] | None) -> None:
        if self.badge is not None:
            self.badge.place(pt, self.owner.primary_h)

    def tick(self) -> None:
        """From the helper's timer: hide the badge when unlocked; long presses."""
        now = time.monotonic()
        if self.dropped_buttons and self.insist.held(now):
            self.owner.on_lock_insist()
        if not self.owner.lock_armed():
            self._show_badge(None)

    def close(self) -> None:
        if self.tap is not None:
            try:
                CGEventTapEnable(self.tap, False)
            except Exception:  # noqa: BLE001
                pass
            self.tap = None
        if self.badge is not None:
            self.badge.close()
            self.badge = None
