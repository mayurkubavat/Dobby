"""macOS overlay helper: agent pointer, window glow, controls.

Runs as its own process (``python -m dobby.overlay``), started by
:class:`dobby.overlay.client.OverlayClient`. Draws into one transparent,
click-through, non-activating panel per screen, above normal windows and on
every Space. Reads JSON-line commands on stdin (see ``protocol.py``) and exits
when stdin closes, so it never outlives the Dobby server.

What it shows:

* the violet pointer with a capsule name tag ("Dobby", widening to
  "Dobby › Click 'Run Synthesis'") that says what Dobby is about to do,
  a box around the target element (found by an Accessibility hit test),
  click ripples and a numbered trail of recent clicks;
* a breathing glow around the window being worked on, dimmed when covered;
* a "looking" shimmer while Dobby reads the screen;
* risk colours: violet (ok), amber (caution), red (danger);
* Turbo mode (config, or Dobby's menu): risky steps are allowed without a
  prompt and user input does not pause Dobby; only Stop / Pause do;
* a control bar at the top of the screen that stays while Dobby runs
  ("Dobby is sleeping" / "working" / "paused", with Pause / Resume / Stop,
  or Allow / Deny for risky steps) and can be hidden with its "–" button,
  Ctrl+Option+H or Dobby's menu (it comes back by itself when Dobby wakes up
  and starts working again); it never appears in screenshots, so it does not
  blink during Dobby's captures. When several Dobby servers run, one helper
  (holding ~/.dobby/overlay/bar.lock) shows the idle bar; the others show
  their bar only while they work;
* Dobby's menu (status, Wake / Sleep, recent steps, Pause / Stop, Turbo,
  show/hide toggles), opened from the bar's ▾ button. Optional extras open
  the same menu: a clickable ▾ on the name tag (``chip_menu``, off by default);
* the menu bar item (``menu_bar``, on by default; see ``topbar``): a Dobby
  icon with the number of running Dobbys and a Focus-style dropdown with one
  row per Dobby (what it does, Pause / Resume / Stop) and Dobby's settings.

It also watches for real user input while Dobby works (take-over) and the
hotkeys Ctrl+Option+Esc (stop), Ctrl+Option+P (pause / resume) and
Ctrl+Option+H (hide / show the control bar), and sends events back on stdout.

All drawing is Core Animation; animations run in the window server, so Python
speed does not affect smoothness.
"""

from __future__ import annotations

import json
import math
import os
import platform
import sys
import threading
import time
from typing import Any

import objc
from AppKit import (
    NSAnimationContext,
    NSAppearance,
    NSAppearanceNameDarkAqua,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSApplicationDidChangeScreenParametersNotification,
    NSAttributedString,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSButton,
    NSColor,
    NSEvent,
    NSEventMaskKeyDown,
    NSEventMaskLeftMouseDown,
    NSEventMaskLeftMouseDragged,
    NSEventMaskMouseMoved,
    NSEventMaskRightMouseDown,
    NSEventMaskScrollWheel,
    NSEventModifierFlagControl,
    NSEventModifierFlagOption,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSImage,
    NSImageOnly,
    NSMenu,
    NSMenuItem,
    NSPanel,
    NSRunningApplication,
    NSScreen,
    NSScreenSaverWindowLevel,
    NSStatusBar,
    NSTextField,
    NSTrackingArea,
    NSView,
    NSVisualEffectView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorIgnoresCycle,
    NSWindowCollectionBehaviorStationary,
    NSWindowSharingNone,
    NSWindowSharingReadOnly,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
    NSWorkspace,
)
from Foundation import NSNotificationCenter, NSObject, NSTimer, NSUserDefaults
from PyObjCTools import AppHelper
from Quartz import (
    CABasicAnimation,
    CACurrentMediaTime,
    CAGradientLayer,
    CAKeyframeAnimation,
    CALayer,
    CAMediaTimingFunction,
    CAShapeLayer,
    CATextLayer,
    CATransaction,
    CGColorCreateGenericRGB,
    CGEventGetIntegerValueField,
    CGPathAddLineToPoint,
    CGPathCloseSubpath,
    CGPathCreateMutable,
    CGPathCreateWithEllipseInRect,
    CGPathCreateWithRoundedRect,
    CGPathMoveToPoint,
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
    BarRow,
    mark_waiting,
    others_showing,
    others_waiting,
    showing_dir,
    waiting_dir,
)
from .barlock import BarOwner as _BarOwner
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

_kCGEventSourceUserData = 42
DOBBY_EVENT_TAG = 0x444F4242  # same value as dobby.hal.macos.DOBBY_EVENT_TAG

DEFAULT_CONFIG: dict[str, Any] = {
    "color": "#7C5CFF",  # Dobby violet; deliberately not Claude's orange
    "caution_color": "#F5A524",
    "danger_color": "#F04438",
    "label": "Dobby",
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
    # Control bar at the top: "always" (stays while Dobby runs; the user can
    # hide it), "auto" (only while working) or "off".
    "pill": "always",
    # False: the bar never appears in any capture (so Dobby's own screenshots
    # need not hide it and it never blinks). True: visible in recordings, but
    # it hides for a moment during each of Dobby's captures.
    "pill_in_recordings": False,
    # The menu bar item (``topbar``): a Dobby icon in the menu bar with a
    # Focus-style dropdown listing every running Dobby. Only the bar owner
    # shows it. ``chip_menu``: a ▾ on the name tag opens Dobby's menu.
    "menu_bar": True,
    "chip_menu": False,
    # Turbo: no Allow/Deny prompts and no take-over pause; only Stop/Pause
    # (bar, menu, hotkeys) halt Dobby. Risky steps still flash red.
    "turbo": False,
    # "modern" (compact sleeping bar, icon buttons, glass, live step and
    # timer, per-Dobby colours, state glow, light trail, tag flips at screen
    # edges) or "classic" (the look before those).
    "style": "modern",
    # Modern bar on macOS 26+: Liquid Glass (NSGlassEffectView) instead of the
    # frosted HUD material. "clear" (default) or "regular" where the OS has it; "off" never.
    "liquid_glass": "clear",
    # One colour per running Dobby (from the agent registry). Off when
    # DOBBY_OVERLAY_COLOR fixes the colour ("color_fixed").
    "agent_colors": True,
    "color_fixed": False,
    "trail_secs": 10.0,  # modern trail: dots fade out after this long
    # Modern look only: say what Dobby does ("Dobby is working · Click ‘Send’"
    # on the bar, the action on the pointer tag until the next one, numbered
    # trail marks, Dobby dots only while two or more are busy).
    "narrate": True,
    # Input lock (dobby.overlay.inputlock): "window" keeps the person's
    # clicks, scrolls and keys out of the window Dobby works in while it
    # works; "off" keeps take-over as before.
    "input_lock": "off",
}


# Classic arrow, flipped coordinates (y down), tip at (0, 0); scaled at build.
_ARROW = [(0, 0), (0, 18), (4.5, 14), (8, 21.5), (10.5, 20.5), (7, 13), (13, 13)]
_ARROW_SCALE = 1.25
_ARROW_H = 21.5 * _ARROW_SCALE
_ARROW_W = 13 * _ARROW_SCALE

# SF Symbols for the chip's state icon ("thinking" uses an animated arc).
STATE_SYMBOLS = {"typing": "keyboard", "waiting_user": "hand.raised.fill"}
_CHIP_H = 20.0
_CHIP_FONT = 11.5

_KEY_ESC, _KEY_P, _KEY_H = 53, 35, 4
_HOTKEY_MODS = NSEventModifierFlagControl | NSEventModifierFlagOption
# NSEventType -> kind for menu.takes_over: 1/3 mouse down, 6 left drag,
# 22 scroll wheel, 5 mouse moved, 10 key down.
_INPUT_KIND = {1: "click", 3: "click", 6: "drag", 22: "scroll", 5: "move", 10: "key"}


def _hex_rgb(value: str) -> tuple[float, float, float]:
    s = str(value).lstrip("#")
    if len(s) != 6:
        s = "7C5CFF"
    try:
        return tuple(int(s[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return (0x7C / 255.0, 0x5C / 255.0, 1.0)


def _cg(rgb: tuple[float, float, float], alpha: float = 1.0):
    return CGColorCreateGenericRGB(rgb[0], rgb[1], rgb[2], alpha)


def _agent_pid_cache():
    """Live pids of all Dobby servers (shared-control registry), or None."""
    try:
        from dobby.control import control_root
        from dobby.control.registry import AgentPidCache

        return AgentPidCache(control_root())
    except Exception:  # noqa: BLE001 - the overlay works without it
        return None


def _os_major() -> int:
    """MacOS major version (26 for Tahoe; Python built on old SDKs says 16)."""
    try:
        major = int((platform.mac_ver()[0] or "0").split(".")[0])
    except ValueError:
        return 0
    return 26 if major == 16 else major


def _reduce_motion() -> bool:
    try:
        return bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())
    except Exception:  # noqa: BLE001
        return False


def _short(text: str, limit: int = 42) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --------------------------------------------------------------------- AX
class _AXProbe:
    """Accessibility hit test: what element is at a point, and its frame."""

    def __init__(self) -> None:
        self.ok = False
        try:
            import ApplicationServices as AS

            self.AS = AS
            self.system = AS.AXUIElementCreateSystemWide()
            AS.AXUIElementSetMessagingTimeout(self.system, 0.25)
            self.ok = True
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"dobby.overlay: AX probe unavailable: {exc}\n")

    def _attr(self, el, name: str):
        try:
            err, value = self.AS.AXUIElementCopyAttributeValue(el, name, None)
            return value if err == 0 else None
        except Exception:  # noqa: BLE001
            return None

    def _point(self, value, kind):
        try:
            ok, pt = self.AS.AXValueGetValue(value, kind, None)
            return pt if ok else None
        except Exception:  # noqa: BLE001
            return None

    def at(self, x: float, y: float) -> tuple[str | None, geometry.Rect | None]:
        if not self.ok:
            return None, None
        try:
            err, el = self.AS.AXUIElementCopyElementAtPosition(
                self.system, float(x), float(y), None
            )
        except Exception:  # noqa: BLE001
            return None, None
        if err != 0 or el is None:
            return None, None
        try:
            perr, pid = self.AS.AXUIElementGetPid(el, None)
            if perr == 0 and pid == os.getpid():
                return None, None
        except Exception:  # noqa: BLE001
            pass
        name = None
        for attr in ("AXTitle", "AXDescription", "AXValue", "AXHelp"):
            v = self._attr(el, attr)
            if isinstance(v, str) and v.strip() and len(v.strip()) <= 60:
                name = v.strip()
                break
        if name is None:
            role = self._attr(el, "AXRoleDescription")
            if isinstance(role, str) and role.strip():
                name = role.strip()
        frame = None
        pos, size = self._attr(el, "AXPosition"), self._attr(el, "AXSize")
        if pos is not None and size is not None:
            p = self._point(pos, self.AS.kAXValueCGPointType)
            s = self._point(size, self.AS.kAXValueCGSizeType)
            if p is not None and s is not None:
                frame = (float(p.x), float(p.y), float(s.width), float(s.height))
        return name, frame


# ---------------------------------------------------------------- ObjC glue
class _Target(NSObject):
    """Objective-C target that forwards timers, notifications and clicks."""

    def initWithOwner_(self, owner):
        self = objc.super(_Target, self).init()
        if self is None:
            return None
        self.owner = owner
        return self

    def tick_(self, _timer):
        self.owner.on_tick()

    def screensChanged_(self, _note):
        self.owner.rebuild_panels()

    def pillPrimary_(self, _sender):
        self.owner.on_pill("primary")

    def pillSecondary_(self, _sender):
        self.owner.on_pill("secondary")

    def pillHide_(self, _sender):
        self.owner.set_pill_hidden(True)

    def pillMenu_(self, _sender):
        self.owner.on_pill_menu()

    def menuWake_(self, _sender):
        self.owner.wake()

    def menuSleep_(self, _sender):
        self.owner.sleep()

    def menuTogglePill_(self, _sender):
        self.owner.set_pill_hidden(not self.owner.pill_user_hidden)

    def menuPauseToggle_(self, _sender):
        self.owner.toggle_pause()

    def menuStop_(self, _sender):
        self.owner.request_stop()

    def menuToggleOverlay_(self, _sender):
        self.owner.toggle_overlay()

    def menuToggleTrail_(self, _sender):
        self.owner.toggle_trail()

    def menuClearTrail_(self, _sender):
        self.owner.clear_trail()

    def menuToggleTurbo_(self, _sender):
        self.owner.toggle_turbo()

    def menuAgentPauseToggle_(self, sender):
        self.owner.agent_pause_toggle(int(sender.representedObject()))

    def menuAgentStop_(self, sender):
        self.owner.agent_stop(int(sender.representedObject()))

    def menuAllPause_(self, _sender):
        self.owner.all_paused(True)

    def menuAllResume_(self, _sender):
        self.owner.all_paused(False)

    def menuAllStop_(self, _sender):
        self.owner.all_stop()

    @objc.typedSelector(b"v@:@")
    def menuNeedsUpdate_(self, menu):  # NSMenuDelegate: the top-bar menu is built as it opens
        self.owner.fill_topbar_menu(menu)


class _HotspotView(NSView):
    """Almost transparent view that still receives clicks.

    Borderless windows pass clicks through fully clear pixels, so it is filled at alpha
    0.01.
    """

    def acceptsFirstMouse_(self, _event):
        return True

    def mouseDown_(self, event):
        target = getattr(self, "dobby_target", None)
        if target is not None:
            target.owner.on_chip_click(self, event)


class _ChipHotspot:
    """Clickable window that follows the name tag, so its ▾ opens a menu.

    Only with cfg "chip_menu"; by default the menu is on the bar's ▾.

    Dobby clicks at the pointer tip and the tag sits below-right of the tip,
    so this never catches Dobby's own clicks. Hidden from every capture.
    """

    def __init__(self, target) -> None:
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        win = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (40, _CHIP_H)), style, NSBackingStoreBuffered, False
        )
        _configure_overlay_window(win, {"capture_visible": False}, click_through=False)
        view = _HotspotView.alloc().initWithFrame_(((0, 0), (40, _CHIP_H)))
        view.setWantsLayer_(True)
        view.layer().setBackgroundColor_(_cg((0, 0, 0), 0.01))
        view.dobby_target = target
        win.setContentView_(view)
        self.window, self.view = win, view
        self.rect: geometry.Rect | None = None
        self.shown = False

    def place(self, rect: geometry.Rect | None, primary_h: float) -> None:
        """Rect in global top-left points, or None to hide."""
        if rect is None or rect[2] <= 0:
            if self.shown:
                self.window.orderOut_(None)
                self.shown = False
            self.rect = None
            return
        # Windows snap to whole points; round outward so the click area
        # always covers the whole tag and self.rect matches the frame.
        x0, y0 = math.floor(rect[0]), math.floor(rect[1])
        rect = (x0, y0, math.ceil(rect[0] + rect[2]) - x0, math.ceil(rect[1] + rect[3]) - y0)
        if rect != self.rect:
            x, y, w, h = rect
            self.window.setFrame_display_(((x, primary_h - y - h), (w, h)), False)
            self.view.setFrame_(((0, 0), (w, h)))
            self.rect = rect
        if not self.shown:
            self.window.orderFrontRegardless()
            self.shown = True

    def close(self) -> None:
        self.window.orderOut_(None)
        self.window.close()


_TRACK_OPTS = 0x01 | 0x80 | 0x200  # mouse entered/exited, always active, in visible rect


def _track_hover(view) -> None:
    try:
        area = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            ((0, 0), (0, 0)), _TRACK_OPTS, view, None
        )
        view.addTrackingArea_(area)
    except Exception:  # noqa: BLE001 - hover is a nicety
        pass


class _PillView(NSView):
    """The bar's background view.

    A click away from its buttons wakes Dobby; hovering grows the compact sleeping bar.
    """

    def acceptsFirstMouse_(self, _event):
        return True

    def hitTest_(self, point):
        hit = objc.super(_PillView, self).hitTest_(point)
        if hit is not None and hit in (getattr(self, "dobby_backdrops", None) or ()):
            return self  # the glass and its tint are part of the background
        return hit

    def mouseDown_(self, _event):
        target = getattr(self, "dobby_target", None)
        if target is not None:
            target.owner.on_pill_label()

    def mouseEntered_(self, _event):
        target = getattr(self, "dobby_target", None)
        if target is not None:
            target.owner.on_pill_hover(True)

    def mouseExited_(self, _event):
        target = getattr(self, "dobby_target", None)
        if target is not None:
            target.owner.on_pill_hover(False)


_LABEL_TIPS = {"ready": "Click to wake Dobby", "awake": "Click to put Dobby to sleep"}


class _PillButton(NSButton):
    def acceptsFirstMouse_(self, _event):
        return True  # one click works even though the pill never becomes key

    def set_label(self, text: str, rgb=None) -> None:
        # The helper app is never active, so plain titles draw in the dim
        # inactive colour and vanish on the dark pill; force white.
        colour = NSColor.whiteColor() if rgb is None else _ns_color(rgb)
        attrs = {NSForegroundColorAttributeName: colour, NSFontAttributeName: self.font()}
        self.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(text, attrs))

    def mouseEntered_(self, event):
        if getattr(self, "dobby_hover", False) and self.layer() is not None:
            if getattr(self, "dobby_fill", False):
                self.layer().setBackgroundColor_(_cg(_FILL_HOVER_RGB, 1.0))
            else:
                r, g, b, a = getattr(self, "dobby_hover_rgba", (1, 1, 1, 0.16))
                self.layer().setBackgroundColor_(_cg((r, g, b), a))
        objc.super(_PillButton, self).mouseEntered_(event)

    def mouseExited_(self, event):
        if getattr(self, "dobby_hover", False) and self.layer() is not None:
            fill = getattr(self, "dobby_fill", False)
            self.layer().setBackgroundColor_(_cg(_FILL_RGB, 1.0) if fill else None)
        objc.super(_PillButton, self).mouseExited_(event)


def _configure_overlay_window(win, cfg: dict[str, Any], click_through: bool) -> None:
    win.setTitle_(protocol.OVERLAY_WINDOW_TITLE)
    win.setOpaque_(False)
    win.setBackgroundColor_(NSColor.clearColor())
    win.setHasShadow_(False)
    win.setIgnoresMouseEvents_(click_through)
    win.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorStationary
        | NSWindowCollectionBehaviorFullScreenAuxiliary
        | NSWindowCollectionBehaviorIgnoresCycle
    )
    # Visible to screen recordings and screen sharing by default; Dobby's
    # own captures hide the overlay via the hide/show handshake instead.
    win.setSharingType_(NSWindowSharingReadOnly if cfg["capture_visible"] else NSWindowSharingNone)
    win.setReleasedWhenClosed_(False)
    win.setHidesOnDeactivate_(False)
    win.setFloatingPanel_(True)
    win.setBecomesKeyOnlyIfNeeded_(True)
    # After setFloatingPanel_, which resets the level to floating (3) and
    # would put the overlay under the menu bar and open menus.
    win.setLevel_(NSScreenSaverWindowLevel)


# ------------------------------------------------------------------ panels
def _lighter(rgb, amount: float = 0.28):
    return tuple(min(1.0, c + (1.0 - c) * amount) for c in rgb)


def _text_width(text: str, font) -> float:
    try:
        attr = NSAttributedString.alloc().initWithString_attributes_(
            text, {NSFontAttributeName: font}
        )
        return float(attr.size().width)
    except Exception:  # noqa: BLE001
        return 6.6 * len(text)


def _ns_color(rgb, alpha: float = 1.0):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(rgb[0], rgb[1], rgb[2], alpha)


def _white_symbol(name: str, size: float = 10.0, rgb=None):
    """SF Symbol as a white (or ``rgb``) NSImage, or None when unavailable."""
    try:
        from AppKit import NSImageSymbolConfiguration

        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
        if img is None:
            return None
        cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_(size, 0.3)  # semibold
        try:
            tint = NSColor.whiteColor() if rgb is None else _ns_color(rgb)
            white = NSImageSymbolConfiguration.configurationWithPaletteColors_([tint])
            cfg = cfg.configurationByApplyingConfiguration_(white)
        except Exception:  # noqa: BLE001 - macOS < 12: template image
            pass
        return img.imageWithSymbolConfiguration_(cfg) or img
    except Exception:  # noqa: BLE001
        return None


def _symbol_contents(name: str, scale: float):
    """SF Symbol as white layer contents, or None when unavailable."""
    img = _white_symbol(name)
    if img is None:
        return None
    try:
        return img.layerContentsForContentsScale_(scale)
    except Exception:  # noqa: BLE001
        return None


class _Chip:
    """Draws the capsule name tag.

    Gradient fill, shine, white rim, state dot and label; while a step runs, also a ›
    breadcrumb and its detail (no chevron when idle).

    Gradient fill with a soft top highlight (an inlaid, glassy look), a thin
    light border and a faint shadow. The layers are click-through like the
    rest of the overlay. With menu_hint (cfg "chip_menu") the idle tag ends
    in a ▾ and a separate _ChipHotspot window over it opens Dobby's menu.
    """

    def __init__(self, parent, scale: float, rgb, menu_hint: bool = False) -> None:
        self.scale = scale
        self.menu_hint = menu_hint
        self.font = NSFont.systemFontOfSize_weight_(_CHIP_FONT, 0.3)
        self.root = CALayer.layer()
        self.root.setContentsScale_(scale)
        self.root.setAnchorPoint_((0.0, 1.0))
        self.root.setShadowColor_(_cg((0, 0, 0)))
        self.root.setShadowOpacity_(0.28)
        self.root.setShadowRadius_(5.0)
        self.root.setShadowOffset_((0, -2))
        self.bg = CAGradientLayer.layer()
        self.bg.setContentsScale_(scale)
        self.bg.setCornerRadius_(_CHIP_H / 2)
        self.bg.setMasksToBounds_(True)
        self.bg.setStartPoint_((0.5, 1.0))
        self.bg.setEndPoint_((0.5, 0.0))
        self.bg.setBorderWidth_(0.75)
        self.bg.setBorderColor_(_cg((1, 1, 1), 0.35))
        self.root.addSublayer_(self.bg)
        self.shine = CAGradientLayer.layer()
        self.shine.setContentsScale_(scale)
        self.shine.setStartPoint_((0.5, 1.0))
        self.shine.setEndPoint_((0.5, 0.0))
        self.shine.setColors_([_cg((1, 1, 1), 0.38), _cg((1, 1, 1), 0.0)])
        self.bg.addSublayer_(self.shine)
        self.icon = CALayer.layer()
        self.icon.setContentsScale_(scale)
        self.icon.setContentsGravity_("resizeAspect")
        self.bg.addSublayer_(self.icon)
        self.spin = CAShapeLayer.layer()
        self.spin.setContentsScale_(scale)
        self.spin.setFillColor_(None)
        self.spin.setStrokeColor_(_cg((1, 1, 1), 0.95))
        self.spin.setLineWidth_(1.6)
        self.spin.setLineCap_("round")
        self.spin.setStrokeEnd_(0.7)
        self.spin.setPath_(CGPathCreateWithEllipseInRect(CGRectMake(0, 0, 10, 10), None))
        self.spin.setBounds_(CGRectMake(0, 0, 10, 10))
        self.spin.setOpacity_(0.0)
        self.bg.addSublayer_(self.spin)
        self.label = self._text_layer()
        self.detail = self._text_layer()
        self.chev = CALayer.layer()
        self.chev.setContentsScale_(scale)
        self.chev.setContentsGravity_("resizeAspect")
        self.bg.addSublayer_(self.chev)
        self._chev_down = _symbol_contents("chevron.down", scale)
        self._chev_right = _symbol_contents("chevron.right", scale)
        self._icons: dict[str, Any] = {}
        self._state = None
        self.width = 0.0
        parent.addSublayer_(self.root)
        self.apply_color(rgb)

    def _text_layer(self):
        t = CATextLayer.layer()
        t.setContentsScale_(self.scale)
        t.setFont_(self.font)
        t.setFontSize_(_CHIP_FONT)
        t.setForegroundColor_(_cg((1, 1, 1)))
        t.setAlignmentMode_("left")
        t.setTruncationMode_("end")
        self.bg.addSublayer_(t)
        return t

    def apply_color(self, rgb) -> None:
        CATransaction.begin()
        CATransaction.setAnimationDuration_(0.25)
        self.bg.setColors_([_cg(_lighter(rgb)), _cg(rgb)])
        CATransaction.commit()

    def _icon_for(self, state: str):
        name = STATE_SYMBOLS.get(state)
        if name is None:
            return None
        if name not in self._icons:
            self._icons[name] = _symbol_contents(name, self.scale)
        return self._icons[name]

    def update(
        self,
        label: str,
        detail: str,
        state: str,
        x: float,
        y_top: float,
        animate: bool = True,
        flip: tuple[bool, bool] = (False, False),
    ) -> None:
        """``flip``: (left, up) puts the tag left of / above the arrow tip."""
        h, pad, gap = _CHIP_H, 9.0, 5.0
        icon = self._icon_for(state)
        thinking = state == "thinking"
        icon_w = 11.0 if (icon is not None or thinking) else 0.0
        lw = _text_width(label, self.font)
        dw = min(_text_width(detail, self.font), 300.0) if detail else 0.0
        # The › breadcrumb only separates a detail; ▾ only hints at a menu.
        chev_w = 8.0 if (detail or self.menu_hint) else 0.0
        w = (
            pad
            + (icon_w + gap if icon_w else 0)
            + lw
            + (gap + chev_w if chev_w else 0)
            + (gap + dw if detail else 0)
            + pad
        )
        CATransaction.begin()
        if animate:
            CATransaction.setAnimationDuration_(0.28)
            CATransaction.setAnimationTimingFunction_(
                CAMediaTimingFunction.functionWithControlPoints____(0.34, 1.3, 0.64, 1.0)
            )
        else:
            CATransaction.setDisableActions_(True)
        self.width = w
        if flip[0]:
            x = -w - 4.0
        if flip[1]:
            y_top = _ARROW_H + 4.0 + h
        self.root.setBounds_(CGRectMake(0, 0, w, h))
        self.root.setPosition_((x, y_top))
        self.root.setShadowPath_(
            CGPathCreateWithRoundedRect(CGRectMake(0, 0, w, h), h / 2, h / 2, None)
        )
        self.bg.setFrame_(CGRectMake(0, 0, w, h))
        self.shine.setFrame_(CGRectMake(0, h * 0.45, w, h * 0.55))
        cx = pad
        if icon_w:
            self.icon.setFrame_(CGRectMake(cx, (h - 11) / 2, 11, 11))
            self.spin.setPosition_((cx + 5.5, h / 2))
            cx += icon_w + gap
        text_y = (h - _CHIP_FONT * 1.25) / 2
        self.label.setFrame_(CGRectMake(cx, text_y, lw + 2, _CHIP_FONT * 1.3))
        cx += lw + gap
        self.chev.setFrame_(CGRectMake(cx, (h - 8) / 2, chev_w, 8))
        cx += (chev_w + gap) if chev_w else 0.0
        self.detail.setFrame_(CGRectMake(cx, text_y, dw + 2, _CHIP_FONT * 1.3))
        CATransaction.commit()
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        self.label.setString_(label)
        self.detail.setString_(detail)
        self.detail.setOpacity_(1.0 if detail else 0.0)
        self.icon.setContents_(icon)
        self.icon.setOpacity_(1.0 if icon is not None else 0.0)
        chev = self._chev_right if detail else (self._chev_down if self.menu_hint else None)
        self.chev.setContents_(chev)  # None: no chevron (or no SF Symbols)
        self.chev.setOpacity_(1.0 if chev is not None else 0.0)
        CATransaction.commit()
        if thinking != (self._state == "thinking"):
            self.spin.setOpacity_(1.0 if thinking else 0.0)
            if thinking:
                rot = CABasicAnimation.animationWithKeyPath_("transform.rotation.z")
                rot.setFromValue_(0.0)
                rot.setToValue_(-6.283185)
                rot.setDuration_(0.9)
                rot.setRepeatCount_(1e9)
                self.spin.addAnimation_forKey_(rot, "spin")
            else:
                self.spin.removeAnimationForKey_("spin")
        self._state = state


class _Panel:
    """One click-through overlay window covering one screen."""

    def __init__(self, screen, cfg: dict[str, Any], rgb) -> None:
        self.screen = screen
        fr = screen.frame()
        self.frame = (fr.origin.x, fr.origin.y, fr.size.width, fr.size.height)
        self.scale = float(screen.backingScaleFactor())
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        win = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            fr, style, NSBackingStoreBuffered, False
        )
        win.setFrame_display_(fr, False)
        _configure_overlay_window(win, cfg, click_through=True)

        view = NSView.alloc().initWithFrame_(((0, 0), (fr.size.width, fr.size.height)))
        view.setWantsLayer_(True)
        win.setContentView_(view)
        root = view.layer()

        # Glow: stroke + same-colour shadow, breathing.
        glow = CAShapeLayer.layer()
        glow.setContentsScale_(self.scale)
        glow.setFillColor_(None)
        glow.setLineWidth_(cfg["glow_width"])
        glow.setShadowOffset_((0, 0))
        glow.setShadowRadius_(cfg["glow_blur"])
        glow.setShadowOpacity_(0.55)
        glow.setOpacity_(0.0)
        root.addSublayer_(glow)
        # A short bright segment that runs around the glow while Dobby thinks.
        sweep = CAShapeLayer.layer()
        sweep.setContentsScale_(self.scale)
        sweep.setFillColor_(None)
        sweep.setLineWidth_(float(cfg["glow_width"]) + 0.5)
        sweep.setLineCap_("round")
        sweep.setShadowOffset_((0, 0))
        sweep.setShadowRadius_(6.0)
        sweep.setShadowOpacity_(0.9)
        sweep.setOpacity_(0.0)
        root.addSublayer_(sweep)

        # Target box around the element about to be clicked.
        box = CAShapeLayer.layer()
        box.setContentsScale_(self.scale)
        box.setLineWidth_(2.0)
        box.setOpacity_(0.0)
        root.addSublayer_(box)

        # Containers for transient layers.
        trail = CALayer.layer()
        trail.setContentsScale_(self.scale)
        root.addSublayer_(trail)
        effects = CALayer.layer()
        effects.setContentsScale_(self.scale)
        root.addSublayer_(effects)

        # Pointer: arrow + name tag; anchor at the arrow tip.
        pointer = CALayer.layer()
        pointer.setContentsScale_(self.scale)
        pointer.setBounds_(CGRectMake(0, 0, _ARROW_W, _ARROW_H))
        pointer.setAnchorPoint_((0.0, 1.0))
        pointer.setOpacity_(0.0)
        arrow = CAShapeLayer.layer()
        arrow.setContentsScale_(self.scale)
        path = CGPathCreateMutable()
        for i, (x, y) in enumerate(_ARROW):
            px, py = x * _ARROW_SCALE, _ARROW_H - y * _ARROW_SCALE
            if i == 0:
                CGPathMoveToPoint(path, None, px, py)
            else:
                CGPathAddLineToPoint(path, None, px, py)
        CGPathCloseSubpath(path)
        arrow.setPath_(path)
        arrow.setStrokeColor_(_cg((1, 1, 1)))
        arrow.setLineWidth_(1.5)
        arrow.setLineJoin_("round")
        arrow.setShadowColor_(_cg((0, 0, 0)))
        arrow.setShadowOpacity_(0.35)
        arrow.setShadowRadius_(2.0)
        arrow.setShadowOffset_((0, -1))
        pointer.addSublayer_(arrow)

        tag = _Chip(pointer, self.scale, rgb, menu_hint=bool(cfg.get("chip_menu")))
        root.addSublayer_(pointer)

        self.window, self.view, self.root = win, view, root
        self.glow, self.box, self.trail, self.effects = glow, box, trail, effects
        self.sweep = sweep
        self.pointer, self.arrow, self.tag = pointer, arrow, tag
        self.flip: tuple[bool, bool] = (False, False)
        self.apply_color(rgb)
        win.orderFrontRegardless()

    def apply_color(self, rgb) -> None:
        CATransaction.begin()
        CATransaction.setAnimationDuration_(0.2)
        self.arrow.setFillColor_(_cg(rgb))
        self.tag.apply_color(rgb)
        self.box.setStrokeColor_(_cg(rgb))
        self.box.setFillColor_(_cg(rgb, 0.08))
        CATransaction.commit()
        self.set_glow_color(rgb)

    def set_glow_color(self, rgb) -> None:
        CATransaction.begin()
        CATransaction.setAnimationDuration_(0.3)
        self.glow.setStrokeColor_(_cg(rgb, 0.95))
        self.glow.setShadowColor_(_cg(rgb))
        self.sweep.setStrokeColor_(_cg(_lighter(rgb, 0.55)))
        self.sweep.setShadowColor_(_cg(rgb))
        CATransaction.commit()

    @property
    def global_frame(self) -> geometry.Rect:
        """This screen in global (top-left origin) coordinates."""
        primary = NSScreen.screens()[0].frame().size.height if NSScreen.screens() else self.frame[3]
        return geometry.cocoa_rect_to_global(self.frame, float(primary))

    def set_tag(
        self, label: str, detail: str = "", state: str = "idle", animate: bool = True
    ) -> None:
        # Below and to the right of the arrow tip, in pointer-layer coordinates
        # (or flipped left / up near a screen edge, see self.flip).
        self.tag.update(label, detail, state, _ARROW_W - 4, -4, animate=animate, flip=self.flip)

    def close(self) -> None:
        self.window.orderOut_(None)
        self.window.close()


def _dots_image(dots, caption: str = ""):
    """The bar's Dobby dots: one per running Dobby, the one in control ringed.

    ``caption`` ("2 Dobbys") is drawn after the dots so they explain themselves.
    """
    d, gap, h = 9.0, 5.0, 14.0
    dots_w = len(dots) * (d + gap) - gap + 4.0
    text = None
    if caption:
        font = NSFont.systemFontOfSize_(11.0)
        text = NSAttributedString.alloc().initWithString_attributes_(
            caption,
            {
                NSFontAttributeName: font,
                NSForegroundColorAttributeName: NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.75),
            },
        )
    w = dots_w + (4.0 + float(text.size().width) if text is not None else 0.0)
    img = NSImage.alloc().initWithSize_((w, h))
    img.lockFocus()
    try:
        x = 2.0
        for _pid, colour, holding, own in dots:
            _ns_color(_hex_rgb(colour), 1.0 if (own or holding) else 0.7).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(((x, (h - d) / 2), (d, d))).fill()
            if holding:
                NSColor.whiteColor().setStroke()
                ring = NSBezierPath.bezierPathWithOvalInRect_(
                    ((x - 1.5, (h - d) / 2 - 1.5), (d + 3, d + 3))
                )
                ring.setLineWidth_(1.2)
                ring.stroke()
            x += d + gap
        if text is not None:
            text.drawAtPoint_((dots_w + 2.0, (h - float(text.size().height)) / 2))
    finally:
        img.unlockFocus()
    return img


_DANGER_RGB = (1.0, 0.42, 0.42)  # on the dark bar
_DANGER_LIGHT_RGB = style.hex_rgb01("#F04438")  # on the light bar
_FILL_RGB = style.hex_rgb01(style.FILL_COLOR)  # Take over, Allow: white text passes 4.5:1
_FILL_HOVER_RGB = style.hex_rgb01("#6A50EA")


def _desktop_dark() -> bool:
    """MacOS Dark Mode.

    Read from defaults: the helper app is never active, so its own effectiveAppearance does not
    follow the system.
    """
    try:
        return (
            str(NSUserDefaults.standardUserDefaults().stringForKey_("AppleInterfaceStyle") or "")
            == "Dark"
        )
    except Exception:  # noqa: BLE001
        return True


# Palette tint laid over Liquid Glass; the glass stays readable without a dark slab.
_GLASS_TINT_DARK = 0.18
_GLASS_TINT_LIGHT = 0.10
# Modern bar corners: a rounded rectangle like the Claude app's chat box, not a capsule.
_BAR_RADIUS = 12.0
_GLASS_STYLES = {"clear": 1, "regular": 0}  # NSGlassEffectViewStyle


def _liquid_glass(cfg: Dict[str, Any], height: float):
    """A clear (or regular) NSGlassEffectView (macOS 26+ Liquid Glass), or None where unavailable or off."""
    choice = str(cfg.get("liquid_glass", "clear")).lower()
    if choice in ("off", "false", "0", "no"):
        return None
    try:
        glass_cls = objc.lookUpClass("NSGlassEffectView")
    except objc.nosuchclass_error:
        return None
    try:
        glass = glass_cls.alloc().initWithFrame_(((0, 0), (300, height)))
        glass.setCornerRadius_(min(_BAR_RADIUS, height / 2))
        glass.setStyle_(_GLASS_STYLES.get(choice, _GLASS_STYLES["clear"]))
        glass.setAutoresizingMask_(18)  # width + height follow the bar
        return glass
    except Exception as exc:  # noqa: BLE001 - fall back to the frosted bar
        sys.stderr.write(f"dobby.overlay: no liquid glass: {exc}\n")
        return None


class _Pill:
    """Small clickable bar at the top of the main screen.

    Draws a :class:`style.BarSpec`. Modern: frosted glass with a coloured
    rim, icon buttons with hover highlights, a compact capsule while Dobby
    sleeps (it grows on hover), the live step and timer while working, and a
    soft shimmer while Dobby thinks. Classic: the flat bar with text buttons.
    """

    H = 34.0

    def __init__(self, cfg: dict[str, Any], target, animate: bool = False) -> None:
        self.cfg = cfg
        self.style = style.normalize_style(cfg.get("style"))
        self.modern = self.style == "modern"
        self.animate = animate
        # Bars of several Dobbys stack one row apart instead of overlapping;
        # a bar other than the plain idle one also tells the bar owner to
        # hide its "sleeping" bar (see barlock).
        lock = cfg.get("bar_lock", BAR_LOCK)
        self.rows = BarRow(lock)
        self.row = 0
        self.show_dir = showing_dir(lock)
        self.marked_showing = False
        win_style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        win = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (300, self.H)), win_style, NSBackingStoreBuffered, False
        )
        _configure_overlay_window(win, cfg, click_through=False)
        if not cfg.get("pill_in_recordings"):
            win.setSharingType_(NSWindowSharingNone)  # never in screenshots, so no capture blink
        self.dark = _desktop_dark()
        view = _PillView.alloc().initWithFrame_(((0, 0), (300, self.H)))
        view.dobby_target = target
        view.setWantsLayer_(True)
        layer = view.layer()
        layer.setCornerRadius_(_BAR_RADIUS if self.modern else self.H / 2)
        layer.setBorderWidth_(1.0)
        win.setContentView_(view)
        self.backdrop = None
        self.glass = None
        self.tint = None
        backdrops = []
        if self.modern:
            layer.setMasksToBounds_(True)
            self.glass = _liquid_glass(cfg, self.H)
        if self.glass is not None:
            # Liquid Glass draws its own rim and edge light, so no coloured border;
            # a soft window shadow lifts it off the desktop like a system control.
            layer.setBorderWidth_(0.0)
            win.setHasShadow_(True)
            view.addSubview_(self.glass)
            backdrops.append(self.glass)
        elif self.modern:
            try:
                fx = NSVisualEffectView.alloc().initWithFrame_(((0, 0), (300, self.H)))
                fx.setMaterial_(13)  # HUD window
                fx.setBlendingMode_(0)  # behind window
                fx.setState_(1)  # always active: the helper app never is
                fx.setAutoresizingMask_(18)  # width + height follow the bar
                view.addSubview_(fx)
                self.backdrop = fx
                backdrops.append(fx)
            except Exception as exc:  # noqa: BLE001 - glass is a nicety
                sys.stderr.write(f"dobby.overlay: no glass: {exc}\n")
        if self.modern:
            # Over Liquid Glass this stays clear and only hosts the status dot.
            tint = NSView.alloc().initWithFrame_(((0, 0), (300, self.H)))
            tint.setWantsLayer_(True)
            tint.layer().setMasksToBounds_(True)
            tint.setAutoresizingMask_(18)
            view.addSubview_(tint)
            self.tint = tint
            backdrops.append(tint)
        view.dobby_backdrops = tuple(backdrops)
        self.label = NSTextField.labelWithString_("")
        if self.modern:
            self.label.setFont_(
                NSFont.monospacedDigitSystemFontOfSize_weight_(12.5, 0.23)
            )  # medium
        else:
            self.label.setFont_(NSFont.systemFontOfSize_(12.5))
        view.addSubview_(self.label)
        self.primary = _PillButton.buttonWithTitle_target_action_("Pause", target, "pillPrimary:")
        self.secondary = _PillButton.buttonWithTitle_target_action_(
            "Stop", target, "pillSecondary:"
        )
        self.hide_btn = _PillButton.buttonWithTitle_target_action_("–", target, "pillHide:")
        self.hide_btn.setToolTip_("Hide this bar (⌃⌥H shows it again)")
        self.menu_btn = _PillButton.buttonWithTitle_target_action_("", target, "pillMenu:")
        self.menu_btn.setToolTip_("Dobby menu")
        self.dots_btn = _PillButton.buttonWithTitle_target_action_("", target, "pillMenu:")
        self.dots_btn.setBordered_(False)
        self.dots_btn.setImagePosition_(NSImageOnly)
        self.dots_btn.setToolTip_(
            "Running Dobbys (ringed: in control). Click for each one's Pause and Stop."
        )
        self.dots_btn.setHidden_(True)
        self._btns = {
            "primary": self.primary,
            "secondary": self.secondary,
            "hide": self.hide_btn,
            "menu": self.menu_btn,
        }
        for b in (self.primary, self.secondary, self.hide_btn, self.menu_btn, self.dots_btn):
            b.setFont_(NSFont.systemFontOfSize_(12.0))
            view.addSubview_(b)
            if self.modern:
                b.setWantsLayer_(True)
                b.layer().setCornerRadius_(7.0)
                b.dobby_hover = True
                _track_hover(b)
        self.menu_glyph = _white_symbol("chevron.down")
        if self.menu_glyph is not None:
            self.menu_btn.setImage_(self.menu_glyph)
            self.menu_btn.setImagePosition_(NSImageOnly)
        self._symbols: dict[tuple[str, str], Any] = {}
        self.dot = CALayer.layer()
        # Modern: above the glass and tint subviews, which would cover it on the view layer.
        (self.tint or view).layer().addSublayer_(self.dot)
        _track_hover(view)
        self.shimmer = None
        self.window, self.view = win, view
        self.mode = "hidden"
        self.spec: style.BarSpec | None = None
        self.ducked = False
        self.user_hidden = False
        self._apply_theme(self.dark)

    # ------------------------------------------------------------- theme
    def _apply_theme(self, dark: bool) -> None:
        """Palette "Elf Violet, tuned": light or dark bar, following macOS."""
        self.dark = dark
        t = style.bar_theme(dark)
        name = NSAppearanceNameDarkAqua if dark else "NSAppearanceNameAqua"
        self.window.setAppearance_(NSAppearance.appearanceNamed_(name))
        bg = tuple(c / 255.0 for c in t["bg"])
        if self.glass is not None:
            # A faint palette tint on the glass keeps Dobby's colour without hiding the lensing.
            try:
                self.glass.setTintColor_(_ns_color(bg, _GLASS_TINT_DARK if dark else _GLASS_TINT_LIGHT))
            except Exception:  # noqa: BLE001
                pass
            if self.tint is not None:
                self.tint.layer().setBackgroundColor_(None)
        elif self.tint is not None:
            alpha = (0.42 if dark else 0.72) if self.backdrop is not None else float(t["bg_alpha"])
            self.tint.layer().setBackgroundColor_(_cg(bg, alpha))
        else:
            self.view.layer().setBackgroundColor_(_cg(bg, float(t["bg_alpha"])))
        if self.backdrop is not None:
            try:
                self.backdrop.setMaterial_(13 if dark else 6)  # HUD window / popover
            except Exception:  # noqa: BLE001
                pass
        self.text_rgb = style.hex_rgb01(str(t["text"]))
        self.danger_rgb = _DANGER_RGB if dark else _DANGER_LIGHT_RGB
        self.label.setTextColor_(_ns_color(self.text_rgb))
        hover = t["hover"]
        for b in self._btns.values():
            b.dobby_hover_rgba = (hover[0] / 255.0, hover[1] / 255.0, hover[2] / 255.0, hover[3])
        self._symbols.clear()
        self.menu_glyph = _white_symbol("chevron.down", 10.0, self.text_rgb)
        if self.menu_glyph is not None:
            self.menu_btn.setImage_(self.menu_glyph)

    def _follow_theme(self) -> None:
        dark = _desktop_dark()
        if dark != self.dark:
            self._apply_theme(dark)

    # ------------------------------------------------------------- drawing
    def _symbol(self, name: str, role: str, filled: bool = False):
        key = (name, role, filled)
        if key not in self._symbols:
            rgb = (
                (1.0, 1.0, 1.0)
                if filled
                else (self.danger_rgb if role == "danger" else self.text_rgb)
            )
            self._symbols[key] = _white_symbol(name, 13.0 if self.modern else 10.0, rgb)
        return self._symbols[key]

    def _dress(self, btn, spec_btn: style.Button) -> None:
        icon = None
        filled = style.is_filled(spec_btn) and spec_btn.key in ("primary", "secondary")
        btn.dobby_fill = filled
        if filled and btn.layer() is None:
            btn.setWantsLayer_(True)
        if btn.layer() is not None:
            btn.layer().setBackgroundColor_(_cg(_FILL_RGB, 1.0) if filled else None)
            btn.layer().setCornerRadius_(7.0)
        if spec_btn.icon and (self.modern or spec_btn.key == "menu"):
            icon = self._symbol(spec_btn.icon, spec_btn.role, filled)
        if icon is not None:
            btn.setImage_(icon)
            btn.setImagePosition_(NSImageOnly)
            btn.set_label("")
            if self.modern:
                btn.setBordered_(False)
                btn.setFrameSize_((28.0, 24.0))
            else:
                btn.sizeToFit()
        else:
            btn.setImage_(None)
            btn.setImagePosition_(0)  # NSNoImage
            if filled:
                btn.setBordered_(False)
                btn.set_label(spec_btn.title, (1.0, 1.0, 1.0))
                btn.sizeToFit()
                size = btn.frame().size
                btn.setFrameSize_((size.width + 18.0, max(size.height, 22.0)))
            else:
                btn.setBordered_(True)
                danger = self.modern and spec_btn.role == "danger"
                btn.set_label(spec_btn.title, self.danger_rgb if danger else self.text_rgb)
                btn.sizeToFit()
        if spec_btn.tip:
            btn.setToolTip_(spec_btn.tip)

    def show(
        self,
        mode: str,
        text: str,
        primary: str,
        secondary: str,
        rgb,
        dot_alpha: float = 1.0,
        tip: str | None = None,
    ) -> None:
        """Old entry point: a bar_view tuple drawn in this bar's style."""
        if mode == "confirm":
            spec = style.confirm_spec(text)
        else:
            spec = style.bar_spec(
                (mode, text, primary, secondary, "ok", dot_alpha),
                style=self.style,
                narrate=bool(self.cfg.get("narrate", True)),
            )
        self.show_spec(spec, rgb, tip=tip)

    def _sync_presence(self) -> None:
        """Hold a screen row and the "showing" marker while visible; drop them when hidden."""
        visible = self.visible
        if visible:
            self.row = self.rows.claim()
        else:
            self.rows.release()
        showing = visible and self.mode != "ready"
        if showing != self.marked_showing:
            self.marked_showing = showing
            mark_waiting(self.show_dir, os.getpid(), showing)

    def show_spec(self, spec: style.BarSpec, rgb, dots=(), tip: str | None = None) -> None:
        self._follow_theme()
        self.mode = spec.mode
        self.spec = spec
        self._sync_presence()
        H = self.H
        tip = tip or spec.tip or _LABEL_TIPS.get(spec.mode)
        self.view.setToolTip_(tip)
        self.label.setToolTip_(tip)
        self.label.setStringValue_(spec.text)
        self.label.sizeToFit()
        wanted = {b.key: b for b in spec.buttons}
        for key, btn in self._btns.items():
            b = wanted.get(key)
            btn.setHidden_(b is None)
            if b is not None:
                self._dress(btn, b)
            if btn.layer() is not None and self.modern and not getattr(btn, "dobby_fill", False):
                btn.layer().setBackgroundColor_(None)
        # The compact capsule's round ends make the dot side look wider.
        x = 12.0 if spec.compact else 14.0
        dots = list(dots) if self.modern else []
        if dots:
            narrate = bool(self.cfg.get("narrate", True))
            img = _dots_image(dots, style.dots_caption(dots) if narrate else "")
            self.dots_btn.setImage_(img)
            dw = float(img.size().width) + 6.0
            self.dots_btn.setFrame_(((x - 3.0, (H - 22.0) / 2), (dw, 22.0)))
            self.dots_btn.setHidden_(False)
            self.dot.setOpacity_(0.0)
            x += dw - 3.0 + 6.0
        else:
            self.dots_btn.setHidden_(True)
            self.dot.setOpacity_(1.0)
            self.dot.setFrame_(CGRectMake(x, H / 2 - 4, 8, 8))
            self.dot.setCornerRadius_(4)
            x += 14.0
        self.dot.setBackgroundColor_(_cg(rgb, spec.dot_alpha))
        if self.glass is None:  # Liquid Glass draws its own edge
            self.view.layer().setBorderColor_(_cg(rgb, 0.55 if (self.modern and spec.compact) else 0.7))
        lh = self.label.frame().size.height
        self.label.setFrameOrigin_((x, (H - lh) / 2))
        x += self.label.frame().size.width
        visible = [self._btns[b.key] for b in spec.buttons if b.key in self._btns]
        gaps = [6.0, 4.0, 2.0] if not self.modern else [2.0, 2.0, 2.0]
        if visible:
            x += 14.0 if not self.modern else 10.0
        for i, btn in enumerate(visible):
            f = btn.frame()
            btn.setFrameOrigin_((x, (H - f.size.height) / 2))
            x += f.size.width + (gaps[min(i, len(gaps) - 1)] if i < len(visible) - 1 else 0.0)
        w = x + (12.0 if visible else (15.0 if spec.compact else 14.0))
        screens = NSScreen.screens() or []
        if screens:
            vf = screens[0].visibleFrame()
            fx = vf.origin.x + (vf.size.width - w) / 2
            fy = vf.origin.y + vf.size.height - H - 8 - self.row * (H + 6)
            frame = ((fx, fy), (w, H))
            old = self.window.frame()
            if self.animate and self.window.isVisible() and abs(old.size.width - w) > 0.5:
                NSAnimationContext.beginGrouping()
                NSAnimationContext.currentContext().setDuration_(0.22)
                self.window.animator().setFrame_display_(frame, True)
                NSAnimationContext.endGrouping()
            else:
                self.window.setFrame_display_(frame, True)
                self.view.setFrame_(((0, 0), (w, H)))
        self.set_shimmer(spec.shimmer and self.animate)
        self._apply_visibility()

    def set_shimmer(self, on: bool) -> None:
        if on == (self.shimmer is not None):
            return
        if not on:
            self.shimmer.removeFromSuperlayer()
            self.shimmer = None
            return
        host = (self.tint or self.view).layer()
        g = CAGradientLayer.layer()
        g.setColors_([_cg((1, 1, 1), 0.0), _cg((1, 1, 1), 0.13), _cg((1, 1, 1), 0.0)])
        g.setStartPoint_((0.0, 0.5))
        g.setEndPoint_((1.0, 0.5))
        g.setFrame_(CGRectMake(-140, 0, 140, self.H))
        anim = CABasicAnimation.animationWithKeyPath_("position.x")
        anim.setFromValue_(-70.0)
        anim.setToValue_(900.0)
        anim.setDuration_(1.8)
        anim.setRepeatCount_(1e9)
        g.addAnimation_forKey_(anim, "shimmer")
        host.addSublayer_(g)
        self.shimmer = g

    def hide(self) -> None:
        self.mode = "hidden"
        self._apply_visibility()

    def menu_anchor(self) -> tuple[float, float]:
        """Where Dobby's menu opens: just under the ▾ (or the Dobby dots), in self.view coordinates."""
        btn = self.menu_btn if not self.menu_btn.isHidden() else self.dots_btn
        if btn.isHidden():
            return (12.0, -2.0)
        f = btn.frame()
        return (f.origin.x, f.origin.y - 2)

    def flash(self, rgb) -> None:
        """Wake-up: the dot pops (1 → 1.6 → 1) and the border flashes."""
        pop = CAKeyframeAnimation.animationWithKeyPath_("transform.scale")
        pop.setValues_([1.0, 1.6, 1.0])
        pop.setDuration_(0.45)
        self.dot.addAnimation_forKey_(pop, "pop")
        layer = self.view.layer()
        border = CABasicAnimation.animationWithKeyPath_("borderColor")
        border.setFromValue_(_cg(rgb, 1.0))
        border.setToValue_(_cg(rgb, 0.7))
        width = CABasicAnimation.animationWithKeyPath_("borderWidth")
        width.setFromValue_(2.5)
        width.setToValue_(1.0)
        for a in (border, width):
            a.setDuration_(0.6)
            a.setTimingFunction_(
                CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionEaseInEaseOut)
            )
            layer.addAnimation_forKey_(a, a.keyPath())

    def duck(self, ducked: bool) -> None:
        """Get out of the way while Dobby's pointer is near the pill."""
        if ducked != self.ducked:
            self.ducked = ducked
            self._apply_visibility()

    def set_user_hidden(self, hidden: bool) -> None:
        self.user_hidden = hidden
        self._apply_visibility()

    @property
    def visible(self) -> bool:
        if self.mode == "hidden":
            return False
        return self.mode == "confirm" or not self.user_hidden

    def _apply_visibility(self) -> None:
        if not self.visible:
            self._sync_presence()
            self.window.setIgnoresMouseEvents_(True)
            self.window.orderOut_(None)
            return
        # Near Dobby's pointer the bar fades and lets clicks through instead
        # of disappearing, so Dobby can still click what is under it.
        ducked = self.ducked and self.mode != "confirm"
        self.window.setIgnoresMouseEvents_(ducked)
        self.view.setAlphaValue_(0.18 if ducked else 1.0)
        self.window.orderFrontRegardless()

    def near(self, cocoa_pt: tuple[float, float], margin: float = 60.0) -> bool:
        f = self.window.frame()
        return (
            f.origin.x - margin <= cocoa_pt[0] <= f.origin.x + f.size.width + margin
            and f.origin.y - margin <= cocoa_pt[1] <= f.origin.y + f.size.height + margin
        )

    def close(self) -> None:
        self.mode = "hidden"
        self._sync_presence()
        self.window.orderOut_(None)
        self.window.close()


# -------------------------------------------------------------- controller
class OverlayController:
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
        self.voice = Voice.from_config(self.cfg)  # Dobby's words (DOBBY_PERSONALITY)
        self.ok_hex = str(self.cfg["color"])
        self.os_major = _os_major()
        self.reduce_motion = _reduce_motion()
        # Other Dobbys (agent registry): colours, this one's name on the tag,
        # the bar's Dobby dots.
        self.agents: list[dict[str, Any]] = []
        self.agents_next = 0.0
        self.agent_label = str(self.cfg["label"])
        self.agent_dots: list[tuple[int, str, bool, bool]] = []
        # The task in progress, for the bar's live step and timer.
        self.task_started = 0.0
        self.task_steps = 0
        self.current_step = ""
        self.bar_second = -1
        self.pill_hovered = False
        self._glow_sig: tuple | None = None
        self._glow_r = 0.0
        self.out = out
        self.out_lock = threading.Lock()
        self.server_pid = int(os.environ.get("DOBBY_SERVER_PID", "0") or 0)
        self.agent_pids = _agent_pid_cache()  # every running Dobby, not just ours
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
        mode = self.cfg.get("pill", "always")
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
        self.event_tag = DOBBY_EVENT_TAG
        self.lock_mode = str(self.cfg.get("input_lock") or "off")
        self.lock_target: geometry.Rect | None = None
        self.lock_above: list[geometry.Rect] = []
        self.lock_front = False
        self.lock_note_next = 0.0
        self.lock_stopped = False  # Stop frees the window until Dobby acts again
        self.voiceover = False
        self.voiceover_checked = 0.0
        self.input_lock = None
        if interactive and self.lock_mode != "off":
            try:
                from .mac_lock import MacInputLock

                lock = MacInputLock(self, _configure_overlay_window, _white_symbol)
                self.input_lock = lock if lock.ok else None
            except Exception as exc:  # noqa: BLE001 - Dobby works without the lock
                sys.stderr.write(f"dobby.overlay: input lock unavailable: {exc}\n")
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
        self._write(json.dumps(obj, separators=(",", ":")) + "\n")

    def send_event(self, name: str) -> None:
        self._write(protocol.event(name))

    def ack(self, msg_id: Any, ok: bool = True, error: str | None = None) -> None:
        if msg_id is None:
            return
        self._write(protocol.ack(msg_id, ok, error))

    # --------------------------------------------------------------- panels
    def rebuild_panels(self) -> None:
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
        return self.agent_label, style.tag_detail(detail, self.cursor_state, self.narrate)

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
        """Take this Dobby's colour and name from the registry; update the bar's dots."""
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
        """The user clicked the name tag: open Dobby's menu under it."""
        menu = self._build_menu(self._status()[0])
        self.chip_menu_open = True
        try:
            self._pop_up(menu, view, (0, 0))
        finally:
            self.chip_menu_open = False
            self._update_hotspot()

    def on_pill_menu(self) -> None:
        """The user clicked the bar's ▾: open Dobby's menu under it."""
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
        """Modern glow: colour, opacity and motion follow Dobby's state.

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
        self._refresh_pill()  # "Dobby is sleeping" from the start

    def on_tick(self) -> None:
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
            self._refresh_pill()  # the "Dobby did it!" cheer is over
        if self.interactive and not self.bar_owner.owned and now >= self.bar_owner_retry:
            self.bar_owner_retry = now + 2.0
            self._claim_bar()
        if self.intent_text and (
            now >= self.intent_until
            or (self.narrate and now - self.last_action > float(self.cfg["active_secs"]))
        ):
            self.intent_text = ""  # narration: the action stays until Dobby goes idle
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
            # Step aside while another helper's AI waits or another Dobby's bar shows.
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
        self.wake_pointer = False  # Dobby drives the pointer now
        if self.awake_until:
            self._end_awake()  # working takes over; afterwards Dobby sleeps
        if was_sleeping and self.pill_user_hidden:
            # Hidden while Dobby slept: bring the bar back now that it works.
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
        """The user woke Dobby.

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
        self.pill_user_hidden = hidden
        if self.pill is not None:
            self.pill.set_user_hidden(hidden)
            if not hidden:
                self._refresh_pill()
        self._refresh_menu()

    def on_pill(self, which: str) -> None:
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
        if paused == self.paused:
            return
        self.paused = paused
        self.takeover_reported = False
        self.send_event("pause" if paused else "resume")
        self._refresh_pill()

    def toggle_pause(self) -> None:
        if self.paused or self.takeover:
            self.resume()
        else:
            self.set_paused(True)

    def resume(self) -> None:
        if self.takeover:
            self.takeover = False
            self.send_event("user_idle")
        if self.paused:
            self.set_paused(False)
        self._mark_active()
        self._refresh_pill()

    def request_stop(self) -> None:
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
            self.pill.show("stopped", "Dobby stopped", "OK", "Hide", self.colors["danger"])
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
        # The user answering is not Dobby waking up: keep a bar they hid hidden.
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
            sys.stderr.write(f"dobby.overlay: no input monitor: {exc}\n")

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
            if self._event_tag(event) == DOBBY_EVENT_TAG:
                return  # synthetic input from any Dobby process, not the person
            pid = self._event_pid(event)
            if pid and (
                pid in (self.server_pid, os.getpid())
                or (self.agent_pids is not None and pid in self.agent_pids)
            ):
                return  # synthetic input from this or another Dobby agent, not the person
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
                # The lock already swallowed what landed in Dobby's window;
                # what got here is the person using another app: no take-over.
                return
            self._on_user_input(_INPUT_KIND.get(etype, "key"))
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"dobby.overlay: input monitor: {exc}\n")

    def _on_user_input(self, kind: str = "click") -> None:
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
                self.send_event("user_idle")  # the pause already holds Dobby
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
    def is_dobby_pid(self, pid: int) -> bool:
        if pid in (self.server_pid, os.getpid()):
            return True
        return self.agent_pids is not None and pid in self.agent_pids

    def lock_armed(self) -> bool:
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
        return inputlock.blocks(
            kind,
            pt,
            target=self.lock_target,
            above=self.lock_above,
            own=self._own_rects(),
            target_front=self.lock_front,
        )

    def on_lock_swallowed(self, kind: str, pt: tuple[float, float] | None) -> None:
        """The person tried to use Dobby's window: point them at Take over."""
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
            sys.stderr.write(f"dobby.overlay: no menu bar item: {exc}\n")
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

    # ------------------------------------------------------- Dobby picker
    def _agents(self) -> list:
        try:
            from dobby.control import commands, control_root

            return commands.overview(control_root())
        except Exception as exc:  # noqa: BLE001 - the menu works without it
            sys.stderr.write(f"dobby.overlay: picker: {exc}\n")
            return []

    def _add_picker(self, menu) -> None:
        """Insert one submenu per running Dobby after the status line."""
        groups = picker_groups(self._agents(), self.server_pid, self.paused or self.takeover)
        if not groups:
            return
        at = 2  # after "Dobby: <status>" and its separator
        header = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Dobbys", None, "")
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
        from dobby.control import commands, control_root

        return commands, control_root()

    def agent_pause_toggle(self, pid: int) -> None:
        if pid == self.server_pid:
            self.toggle_pause()
            return
        commands, root = self._commands()
        commands.set_paused(root, pid, not commands.read(root, pid)["paused"])

    def agent_stop(self, pid: int) -> None:
        if pid == self.server_pid:
            self.request_stop()
            return
        commands, root = self._commands()
        commands.request_stop(root, pid)

    def all_paused(self, paused: bool) -> None:
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
        """Write what this Dobby does for whichever helper owns the menu bar item."""
        if not self.interactive or not self.server_pid:
            return
        now = time.monotonic()
        working = now - self.last_action <= float(self.cfg["active_secs"])
        mode = topbar.dobby_mode(
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
            sys.stderr.write(f"dobby.overlay: menu bar item: {exc}\n")

    def _row_image(self, color: str, shape: str):
        path = topbar.row_icon_path(color, shape, 52)
        image = NSImage.alloc().initWithContentsOfFile_(path) if path else None
        if image is not None:
            image.setSize_((26, 26))
        return image

    @staticmethod
    def _two_lines(first: str, second: str, bold: bool = False):
        """Attributed menu title: a name over a smaller grey line (Focus style)."""
        from AppKit import NSColor, NSMutableAttributedString

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
        """Build the dropdown as it opens: header, one row per Dobby, settings."""
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
                    selector = {"pause_toggle": "menuAgentPauseToggle:", "stop": "menuAgentStop:"}[
                        action
                    ]
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
            settings = NSMenu.alloc().initWithTitle_("Dobby Settings")
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
                "Dobby Settings", None, ""
            )
            parent.setSubmenu_(settings)
            menu.addItem_(parent)
        except Exception as exc:  # noqa: BLE001 - never break the menu bar
            sys.stderr.write(f"dobby.overlay: menu bar menu: {exc}\n")

    def all_stop(self) -> None:
        commands, root = self._commands()
        for agent in commands.overview(root):
            if agent["pid"] == self.server_pid:
                self.request_stop()
            else:
                commands.request_stop(root, agent["pid"])

    def toggle_overlay(self) -> None:
        self.user_visible = not self.user_visible
        self._apply_alpha()
        self._refresh_menu()

    def toggle_turbo(self) -> None:
        self.turbo = not self.turbo
        if self.turbo and self.takeover:
            self.takeover = False
            self.send_event("user_idle")
        self._refresh_pill()

    def toggle_trail(self) -> None:
        self.trail_on = not self.trail_on
        self._redraw_trail()
        self._refresh_menu()

    def clear_trail(self) -> None:
        self.trail_points = []
        self._redraw_trail()

    # ------------------------------------------------------------- commands
    def handle_line(self, line: str) -> None:
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
            sys.stderr.write(f"dobby.overlay: {exc}\n")
            self.ack(msg_id, False, str(exc))

    def _set_intent(self, text: str, ms: float | None = None) -> None:
        self.intent_text = _short(text) if text else ""
        secs = style.intent_secs(ms, float(self.cfg["intent_ms"]), self.narrate)
        if not text:
            self.intent_until = 0.0
        else:  # None: until the next action, or until Dobby is idle (see on_tick)
            self.intent_until = time.monotonic() + secs if secs is not None else float("inf")
        self._refresh_tag()

    def handle(self, msg: dict[str, Any]) -> None:
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
            if self.cursor_state in ("typing", "thinking"):
                self._mark_active()
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
            self._ai_wait(True, str(msg.get("reason") or ""))
            self.ack(msg_id)
        elif op == "sleep.done":
            self._ai_wait(False)
            self.ack(msg_id)
        elif op == "input.pass":
            self.ack(msg_id)  # the tap lets Dobby's tagged events through already
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
        sys.stderr.write(f"dobby.overlay: nothing to replay in {path}\n")
        AppHelper.callLater(0.1, controller.quit)
        return
    _schedule(controller, steps, tail=2.0)


def _arg(argv: list[str], name: str) -> str | None:
    if name in argv and argv.index(name) + 1 < len(argv):
        return argv[argv.index(name) + 1]
    return None


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or [])
    cfg: dict[str, Any] = {}
    raw = os.environ.get("DOBBY_OVERLAY_CONFIG")
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
        from dobby.parentwatch import watch_parent

        watch_parent(
            lambda: AppHelper.callAfter(controller.quit)
        )  # server died, pipe held elsewhere
    controller.emit({"ready": True, "version": protocol.PROTOCOL_VERSION, "pid": os.getpid()})
    AppHelper.runEventLoop(installInterrupt=True)
    return 0
