"""The pieces Pinlet's macOS overlay is drawn from.

The pointer chip, glow panels and the control bar (pill), the small AppKit
views and targets behind them, the Accessibility hit test, and the drawing
helpers they share. :class:`pinlet.overlay.mac_helper.OverlayController` puts
them together and owns all behaviour; nothing here reads commands or decides
anything.

macOS only (imports AppKit and Quartz).
"""

from __future__ import annotations

import math
import os
import platform
import sys
from typing import Any

import objc
from AppKit import (
    NSAnimationContext,
    NSAppearance,
    NSAppearanceNameDarkAqua,
    NSAttributedString,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSButton,
    NSColor,
    NSEventModifierFlagControl,
    NSEventModifierFlagOption,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSImage,
    NSImageOnly,
    NSPanel,
    NSScreen,
    NSScreenSaverWindowLevel,
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
from Foundation import NSObject, NSUserDefaults
from Quartz import (
    CABasicAnimation,
    CAGradientLayer,
    CAKeyframeAnimation,
    CALayer,
    CAMediaTimingFunction,
    CAShapeLayer,
    CATextLayer,
    CATransaction,
    CGColorCreateGenericRGB,
    CGPathAddLineToPoint,
    CGPathCloseSubpath,
    CGPathCreateMutable,
    CGPathCreateWithEllipseInRect,
    CGPathCreateWithRoundedRect,
    CGPathMoveToPoint,
    CGRectMake,
    kCAMediaTimingFunctionEaseInEaseOut,
)

from . import geometry, protocol, style
from .barlock import (
    BAR_LOCK,
    BarRow,
    mark_waiting,
    showing_dir,
)
from .text import short

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
        s = "0EA5C6"
    try:
        return tuple(int(s[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return (0x7C / 255.0, 0x5C / 255.0, 1.0)


def _cg(rgb: tuple[float, float, float], alpha: float = 1.0):
    return CGColorCreateGenericRGB(rgb[0], rgb[1], rgb[2], alpha)


def _agent_pid_cache():
    """Live pids of all Pinlet servers (shared-control registry), or None."""
    try:
        from pinlet.control import control_root
        from pinlet.control.registry import AgentPidCache

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
    return short(text, limit)


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
            sys.stderr.write(f"pinlet.overlay: AX probe unavailable: {exc}\n")

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

    def menuAgentWake_(self, sender):
        self.owner.agent_wake(int(sender.representedObject()))

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
        target = getattr(self, "pinlet_target", None)
        if target is not None:
            target.owner.on_chip_click(self, event)


class _ChipHotspot:
    """Clickable window that follows the name tag, so its ▾ opens a menu.

    Only with cfg "chip_menu"; by default the menu is on the bar's ▾.

    Pinlet clicks at the pointer tip and the tag sits below-right of the tip,
    so this never catches Pinlet's own clicks. Hidden from every capture.
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
        view.pinlet_target = target
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

    A click away from its buttons wakes Pinlet; hovering grows the compact sleeping bar.
    """

    def acceptsFirstMouse_(self, _event):
        return True

    def hitTest_(self, point):
        hit = objc.super(_PillView, self).hitTest_(point)
        if hit is not None and hit in (getattr(self, "pinlet_backdrops", None) or ()):
            return self  # the glass and its tint are part of the background
        return hit

    def mouseDown_(self, _event):
        target = getattr(self, "pinlet_target", None)
        if target is not None:
            target.owner.on_pill_label()

    def mouseEntered_(self, _event):
        target = getattr(self, "pinlet_target", None)
        if target is not None:
            target.owner.on_pill_hover(True)

    def mouseExited_(self, _event):
        target = getattr(self, "pinlet_target", None)
        if target is not None:
            target.owner.on_pill_hover(False)


_LABEL_TIPS = {"ready": "Click to wake Pinlet", "awake": "Click to put Pinlet to sleep"}


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
        if getattr(self, "pinlet_hover", False) and self.layer() is not None:
            if getattr(self, "pinlet_fill", False):
                self.layer().setBackgroundColor_(_cg(_FILL_HOVER_RGB, 1.0))
            else:
                r, g, b, a = getattr(self, "pinlet_hover_rgba", (1, 1, 1, 0.16))
                self.layer().setBackgroundColor_(_cg((r, g, b), a))
        objc.super(_PillButton, self).mouseEntered_(event)

    def mouseExited_(self, event):
        if getattr(self, "pinlet_hover", False) and self.layer() is not None:
            fill = getattr(self, "pinlet_fill", False)
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
    # Visible to screen recordings and screen sharing by default; Pinlet's
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
    in a ▾ and a separate _ChipHotspot window over it opens Pinlet's menu.
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
        # The tag carries white text: Pinlet cyan is too light for it, so use the fill.
        if all(abs(a - b) < 0.01 for a, b in zip(rgb, _ACCENT_RGB)):
            rgb = _FILL_RGB
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
        # A short bright segment that runs around the glow while Pinlet thinks.
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
    """The bar's Pinlet dots: one per running Pinlet, the one in control ringed.

    ``caption`` ("2 Pinlets") is drawn after the dots so they explain themselves.
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
_ACCENT_RGB = style.hex_rgb01(style.AGENT_PALETTE[0])  # Pinlet cyan, too light for white text
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


# Modern bar corners: a rounded rectangle like the Claude app's chat box, not a capsule.
_BAR_RADIUS = 12.0
_GLASS_STYLES = {"clear": 1, "regular": 0}  # NSGlassEffectViewStyle


def _liquid_glass(cfg: dict[str, Any], height: float):
    """A Liquid Glass NSGlassEffectView (macOS 26+), or None where unavailable or off.

    It starts clear (the sleeping chip) unless ``regular`` is pinned; ``_Pill.show_spec``
    switches the style as the bar gains or loses text (``style.glass_style``).
    """
    choice = style.normalize_glass(cfg.get("liquid_glass", "auto"))
    if choice == "off":
        return None
    try:
        glass_cls = objc.lookUpClass("NSGlassEffectView")
    except objc.nosuchclass_error:
        return None
    try:
        glass = glass_cls.alloc().initWithFrame_(((0, 0), (300, height)))
        glass.setCornerRadius_(min(_BAR_RADIUS, height / 2))
        glass.setStyle_(_GLASS_STYLES[style.glass_style(choice, compact=True)])
        glass.setAutoresizingMask_(18)  # width + height follow the bar
        return glass
    except Exception as exc:  # noqa: BLE001 - fall back to the frosted bar
        sys.stderr.write(f"pinlet.overlay: no liquid glass: {exc}\n")
        return None


class _Pill:
    """Small clickable bar at the top of the main screen.

    Draws a :class:`style.BarSpec`. Modern: frosted glass with a coloured
    rim, icon buttons with hover highlights, a compact capsule while Pinlet
    sleeps (it grows on hover), the live step and timer while working, and a
    soft shimmer while Pinlet thinks. Classic: the flat bar with text buttons.
    """

    H = 34.0

    def __init__(self, cfg: dict[str, Any], target, animate: bool = False) -> None:
        self.cfg = cfg
        self.style = style.normalize_style(cfg.get("style"))
        self.modern = self.style == "modern"
        self.animate = animate
        # Bars of several Pinlets stack one row apart instead of overlapping;
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
        self.dark = self._wants_dark()
        self.glass_kind = "clear"
        view = _PillView.alloc().initWithFrame_(((0, 0), (300, self.H)))
        view.pinlet_target = target
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
                sys.stderr.write(f"pinlet.overlay: no glass: {exc}\n")
        if self.modern:
            # Over Liquid Glass this stays clear and only hosts the status dot.
            tint = NSView.alloc().initWithFrame_(((0, 0), (300, self.H)))
            tint.setWantsLayer_(True)
            tint.layer().setMasksToBounds_(True)
            tint.setAutoresizingMask_(18)
            view.addSubview_(tint)
            self.tint = tint
            backdrops.append(tint)
        view.pinlet_backdrops = tuple(backdrops)
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
        self.menu_btn.setToolTip_("Pinlet menu")
        self.dots_btn = _PillButton.buttonWithTitle_target_action_("", target, "pillMenu:")
        self.dots_btn.setBordered_(False)
        self.dots_btn.setImagePosition_(NSImageOnly)
        self.dots_btn.setToolTip_(
            "Running Pinlets (ringed: in control). Click for each one's Pause and Stop."
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
                b.pinlet_hover = True
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
    def _wants_dark(self) -> bool:
        """The dark look unless the theme setting says light, or auto on a light desktop."""
        theme = self.cfg.get("theme", "dark")
        return style.overlay_dark(theme, _desktop_dark() if theme == "auto" else False)

    def _apply_theme(self, dark: bool) -> None:
        """Palette "Frost": the dark or light bar (``theme``)."""
        self.dark = dark
        t = style.bar_theme(dark)
        name = NSAppearanceNameDarkAqua if dark else "NSAppearanceNameAqua"
        self.window.setAppearance_(NSAppearance.appearanceNamed_(name))
        bg = tuple(c / 255.0 for c in t["bg"])
        if self.glass is not None:
            # A faint palette tint on the glass keeps Pinlet's colour without hiding the lensing.
            self._tint_glass()
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
            b.pinlet_hover_rgba = (hover[0] / 255.0, hover[1] / 255.0, hover[2] / 255.0, hover[3])
        self._symbols.clear()
        self.menu_glyph = _white_symbol("chevron.down", 10.0, self.text_rgb)
        if self.menu_glyph is not None:
            self.menu_btn.setImage_(self.menu_glyph)

    def _tint_glass(self) -> None:
        """Lay the palette tint for the current appearance and glass style over the glass."""
        bg = tuple(c / 255.0 for c in style.bar_theme(self.dark)["bg"])
        try:
            self.glass.setTintColor_(_ns_color(bg, style.glass_tint(self.dark, self.glass_kind)))
        except Exception:  # noqa: BLE001
            pass

    def _set_glass(self, compact: bool, rgb) -> None:
        """Clear glass on the sleeping chip, regular once there is text; a thin rim in Pinlet's colour."""
        kind = style.glass_style(self.cfg.get("liquid_glass", "auto"), compact)
        if kind != self.glass_kind:
            self.glass_kind = kind
            try:
                self.glass.setStyle_(_GLASS_STYLES[kind])
            except Exception:  # noqa: BLE001
                pass
            self._tint_glass()
        rim = style.glass_rim(self.dark, compact)
        layer = self.view.layer()
        layer.setBorderWidth_(1.0 if rim else 0.0)
        if rim:
            layer.setBorderColor_(_cg(rgb, rim))

    def _follow_theme(self) -> None:
        dark = self._wants_dark()
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
        btn.pinlet_fill = filled
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
            if btn.layer() is not None and self.modern and not getattr(btn, "pinlet_fill", False):
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
        if self.glass is not None:
            self._set_glass(spec.compact, rgb)
        else:
            self.view.layer().setBorderColor_(
                _cg(rgb, 0.55 if (self.modern and spec.compact) else 0.7)
            )
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
        """Where Pinlet's menu opens: just under the ▾ (or the Pinlet dots), in self.view coordinates."""
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
        """Get out of the way while Pinlet's pointer is near the pill."""
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
        # Near Pinlet's pointer the bar fades and lets clicks through instead
        # of disappearing, so Pinlet can still click what is under it.
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
