"""
macOS Desktop Hardware Abstraction Layer.

* Coordinates are logical points everywhere (what CGEvent and the
  Accessibility API use). Screenshots from ``screencapture`` arrive at the
  Retina backing scale (usually 2x) and are resized to points, so a pixel in a
  captured image is a point on screen and no caller has to rescale.
* Input: Quartz CGEvent (mouse, scroll, keyboard, Unicode typing).
* Windows: CGWindowListCopyWindowInfo, frontmost app via ``lsappinfo``
  (NSWorkspace caches it until a run loop turns, which a server never does),
  activation via NSRunningApplication with an AX raise as fallback, verified.

Needs pyobjc (``pip install -e ".[macos]"``) and these macOS permissions for
the process that runs the server (Terminal, Claude, Antigravity, ...):
Accessibility (input and AX tree) and Screen Recording (capture).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from PIL import Image

from dobby.config import CONFIG
from dobby.hal.base import IDesktopHAL
from dobby.types import BoundingBox, DisplayMetrics, DisplayTopology, WindowInfo

# US ANSI virtual key codes (Carbon kVK_*)
KEYCODES: Dict[str, int] = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26,
    "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35,
    "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44,
    "n": 45, "m": 46, ".": 47, "`": 50,
    "enter": 36, "return": 36, "tab": 48, "space": 49, "backspace": 51, "delete": 51,
    "escape": 53, "esc": 53, "forward_delete": 117, "del": 117,
    "home": 115, "end": 119, "page_up": 116, "pageup": 116, "page_down": 121, "pagedown": 121,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100,
    "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}

# CGEventFlags masks
MODIFIER_FLAGS: Dict[str, int] = {
    "cmd": 0x100000, "command": 0x100000, "super": 0x100000, "meta": 0x100000, "win": 0x100000,
    "shift": 0x20000,
    "ctrl": 0x40000, "control": 0x40000,
    "alt": 0x80000, "option": 0x80000, "opt": 0x80000,
}

MODIFIER_KEYCODES: Dict[int, int] = {0x100000: 55, 0x20000: 56, 0x40000: 59, 0x80000: 58}

# Quartz constants (stable ABI values; kept here so tests can run off-Mac)
_kCGHIDEventTap = 0
_kCGEventLeftMouseDown, _kCGEventLeftMouseUp = 1, 2
_kCGEventRightMouseDown, _kCGEventRightMouseUp = 3, 4
_kCGEventMouseMoved, _kCGEventLeftMouseDragged = 5, 6
_kCGMouseButtonLeft, _kCGMouseButtonRight = 0, 1
_kCGMouseEventClickState = 1
_kCGScrollEventUnitLine = 1
_kCGWindowListOptionOnScreenOnly = 1
_kCGWindowListExcludeDesktopElements = 16
_kCGNullWindowID = 0
_kCGEventSourceStatePrivate = -1
_kCGEventSourceUserData = 42

# Every event Dobby posts carries this in kCGEventSourceUserData, so the overlay
# (any Dobby's) can tell its synthetic input from the person's.
DOBBY_EVENT_TAG = 0x444F4242  # "DOBB"


def _load_quartz():
    import Quartz  # type: ignore

    return Quartz


def _load_appkit():
    import AppKit  # type: ignore

    return AppKit


_LSAPPINFO = "/usr/bin/lsappinfo"
_PID_RE = re.compile(r'"?pid"?\s*=\s*(\d+)')

# focus_window: how long to wait for macOS to report the target in front, per attempt
_FOCUS_WAIT_S = 0.5
_FOCUS_POLL_S = 0.05


def _lsappinfo_front_pid() -> Optional[int]:
    """The frontmost app's pid, asked fresh from the launch services daemon (~10 ms)."""
    asn = subprocess.run([_LSAPPINFO, "front"], capture_output=True, text=True, timeout=2).stdout.strip()
    if not asn:
        return None
    out = subprocess.run([_LSAPPINFO, "info", "-only", "pid", asn],
                         capture_output=True, text=True, timeout=2).stdout
    m = _PID_RE.search(out)
    return int(m.group(1)) if m else None


def _load_ax():
    import ApplicationServices  # type: ignore

    return ApplicationServices


def _screencapture(region: Optional[Tuple[int, int, int, int]]) -> Image.Image:
    fd, path = tempfile.mkstemp(suffix=".png", prefix="dc_cap_")
    os.close(fd)
    try:
        args = ["/usr/sbin/screencapture", "-x", "-t", "png"]
        if region:
            x, y, w, h = region
            args.append(f"-R{int(x)},{int(y)},{int(w)},{int(h)}")
        args.append(path)
        subprocess.run(args, check=True, timeout=15)
        with Image.open(path) as im:
            return im.convert("RGB")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


class MacHAL(IDesktopHAL):
    """macOS HAL in logical points: screencapture + CGEvent + CGWindowList."""

    def __init__(self, quartz: Any = None, appkit: Any = None, capture_fn=None,
                 front_pid_fn: Optional[Callable[[], Optional[int]]] = None, ax: Any = None) -> None:
        self.Q = quartz if quartz is not None else _load_quartz()
        self._appkit = appkit
        # Fresh frontmost lookup. With injected fakes (tests) the fake AppKit answers instead.
        if front_pid_fn is None and appkit is None and os.path.exists(_LSAPPINFO):
            front_pid_fn = _lsappinfo_front_pid
        self._front_pid_fn = front_pid_fn
        self._ax = ax
        # Why the last focus_window returned False, for the tool result.
        self.last_focus_error: Optional[str] = None
        self._capture = capture_fn or _screencapture
        self._is_wayland = False  # diagnostics compatibility
        self.display_server = "Quartz"
        # A private event source: our key events never pick up modifier state from
        # the real keyboard or from earlier synthetic chords (stuck Cmd).
        self._src = None
        try:
            create = getattr(self.Q, "CGEventSourceCreate", None)
            if create is not None:
                self._src = create(_kCGEventSourceStatePrivate)
        except Exception:  # noqa: BLE001
            self._src = None

    # ------------------------------------------------------------ helpers

    @property
    def AK(self):
        if self._appkit is None:
            self._appkit = _load_appkit()
        return self._appkit

    def _check_failsafe(self, x: int, y: int) -> None:
        if CONFIG.failsafe_corner and x == 0 and y == 0:
            raise RuntimeError("VisionMCP Failsafe Triggered: Cursor moved to (0, 0). Aborting execution.")

    def _post(self, event) -> None:
        try:
            self.Q.CGEventSetIntegerValueField(event, _kCGEventSourceUserData, DOBBY_EVENT_TAG)
        except Exception:  # noqa: BLE001
            pass
        self.Q.CGEventPost(_kCGHIDEventTap, event)

    def _cooldown(self) -> None:
        time.sleep(CONFIG.action_cooldown_ms / 1000.0)

    # ------------------------------------------------------------- screen

    def get_screen_size(self) -> Tuple[int, int]:
        bounds = self.Q.CGDisplayBounds(self.Q.CGMainDisplayID())
        return int(bounds.size.width), int(bounds.size.height)

    def capture_screen(self, region: Optional[Tuple[int, int, int, int]] = None) -> Image.Image:
        """Captures in logical points: a 2x Retina capture is resized to the region size."""
        img = self._capture(region)
        if region:
            target = (max(1, int(region[2])), max(1, int(region[3])))
        else:
            target = self.get_screen_size()
        if img.size != target:
            img = img.resize(target, Image.LANCZOS)
        return img

    def display_topology(self) -> DisplayTopology:
        """Primary display in logical points.

        Scale stays 1.0 here. ``backing_scale()`` touches NSScreen and aborts
        when this process has no window-server connection, so session boot
        must not call it.
        """
        width, height = self.get_screen_size()
        return DisplayTopology(
            displays=[
                DisplayMetrics(
                    display_id="main",
                    origin=(0, 0),
                    size=(int(width), int(height)),
                    scale=1.0,
                )
            ],
            primary_id="main",
        )

    def backing_scale(self) -> float:
        try:
            return float(self.AK.NSScreen.mainScreen().backingScaleFactor())
        except Exception:
            return 1.0

    # -------------------------------------------------------------- mouse

    def get_cursor_position(self) -> Tuple[int, int]:
        loc = self.Q.CGEventGetLocation(self.Q.CGEventCreate(None))
        return int(loc.x), int(loc.y)

    def mouse_move(self, x: int, y: int) -> None:
        self._check_failsafe(x, y)
        ev = self.Q.CGEventCreateMouseEvent(None, _kCGEventMouseMoved, (x, y), _kCGMouseButtonLeft)
        self._post(ev)

    def mouse_click(self, x: int, y: int, button: str = "left", click_count: int = 1) -> None:
        self._check_failsafe(x, y)
        if button == "right":
            down, up, btn = _kCGEventRightMouseDown, _kCGEventRightMouseUp, _kCGMouseButtonRight
        else:
            down, up, btn = _kCGEventLeftMouseDown, _kCGEventLeftMouseUp, _kCGMouseButtonLeft
        self.mouse_move(x, y)
        time.sleep(0.01)
        for n in range(1, max(1, click_count) + 1):
            for etype in (down, up):
                ev = self.Q.CGEventCreateMouseEvent(None, etype, (x, y), btn)
                self.Q.CGEventSetIntegerValueField(ev, _kCGMouseEventClickState, n)
                self._post(ev)
                time.sleep(0.01)
        self._cooldown()

    def mouse_drag(self, start_x: int, start_y: int, end_x: int, end_y: int, duration_sec: float = 0.3) -> None:
        self._check_failsafe(start_x, start_y)
        self.mouse_move(start_x, start_y)
        self._post(self.Q.CGEventCreateMouseEvent(None, _kCGEventLeftMouseDown, (start_x, start_y), _kCGMouseButtonLeft))
        steps = max(2, int(duration_sec / 0.016))
        for i in range(1, steps + 1):
            px = start_x + (end_x - start_x) * i / steps
            py = start_y + (end_y - start_y) * i / steps
            self._post(self.Q.CGEventCreateMouseEvent(None, _kCGEventLeftMouseDragged, (px, py), _kCGMouseButtonLeft))
            time.sleep(duration_sec / steps)
        self._post(self.Q.CGEventCreateMouseEvent(None, _kCGEventLeftMouseUp, (end_x, end_y), _kCGMouseButtonLeft))
        self._cooldown()

    def mouse_scroll(self, dx: int, dy: int) -> None:
        # Positive dy scrolls content up (same sign convention as the Linux HAL wheel)
        ev = self.Q.CGEventCreateScrollWheelEvent(None, _kCGScrollEventUnitLine, 2, int(dy), int(dx))
        self._post(ev)
        self._cooldown()

    # ----------------------------------------------------------- keyboard

    def keyboard_type(self, text: str, delay_ms: float = 12.0) -> None:
        """Types Unicode text directly (layout-independent, handles non-ASCII)."""
        for ch in text:
            if ch == "\n":
                self.keyboard_press_key("return")
                continue
            for down in (True, False):
                ev = self.Q.CGEventCreateKeyboardEvent(self._src, 0, down)
                self.Q.CGEventSetFlags(ev, 0)  # plain text: never inherit Cmd/Ctrl/Alt
                self.Q.CGEventKeyboardSetUnicodeString(ev, len(ch.encode("utf-16-le")) // 2, ch)
                self._post(ev)
            time.sleep(delay_ms / 1000.0)
        self._cooldown()

    @staticmethod
    def _resolve(key: str) -> Tuple[Optional[int], int]:
        """Returns (keycode or None, modifier flag or 0) for a key name."""
        k = key.lower().strip()
        if k in MODIFIER_FLAGS:
            return None, MODIFIER_FLAGS[k]
        if k in KEYCODES:
            return KEYCODES[k], 0
        raise ValueError(f"Unknown key '{key}' for macOS HAL")

    def _key_event(self, code: int, down: bool, flags: int) -> None:
        ev = self.Q.CGEventCreateKeyboardEvent(self._src, code, down)
        self.Q.CGEventSetFlags(ev, flags)  # always explicit, 0 included
        self._post(ev)

    def release_modifiers(self) -> None:
        """Posts key-up for Cmd, Shift, Ctrl and Alt with no flags, clearing any stuck modifier."""
        for code in MODIFIER_KEYCODES.values():
            self._key_event(code, False, 0)

    def keyboard_press_key(self, key_name: str) -> None:
        code, flag = self._resolve(key_name)
        if code is None:
            code = MODIFIER_KEYCODES[flag]
        self._key_event(code, True, flag)
        time.sleep(0.02)
        self._key_event(code, False, 0)
        self._cooldown()

    def keyboard_hotkey(self, keys: List[str]) -> None:
        """Chord such as ['cmd', 's']. Note: 'ctrl' is Control, not Command, on macOS.

        Presses the modifier keys for real, then the keys, then releases everything in
        reverse, ending with no modifier flags, so nothing stays held afterwards.
        """
        mods: List[int] = []
        codes: List[int] = []
        for k in keys:
            code, flag = self._resolve(k)
            if code is None:
                if flag not in mods:
                    mods.append(flag)
            else:
                codes.append(code)
        flags = 0
        try:
            for f in mods:
                flags |= f
                self._key_event(MODIFIER_KEYCODES[f], True, flags)
                time.sleep(0.01)
            for c in codes:
                self._key_event(c, True, flags)
                time.sleep(0.01)
            time.sleep(0.03)
            for c in reversed(codes):
                self._key_event(c, False, flags)
                time.sleep(0.01)
        finally:
            for f in reversed(mods):
                flags &= ~f
                self._key_event(MODIFIER_KEYCODES[f], False, flags)
                time.sleep(0.01)
        self._cooldown()

    # ------------------------------------------------------------ windows

    def _frontmost_pid(self) -> Optional[int]:
        if self._front_pid_fn is not None:
            try:
                pid = self._front_pid_fn()
                if pid:
                    return int(pid)
            except Exception:  # noqa: BLE001 - fall back to NSWorkspace
                pass
        try:
            app = self.AK.NSWorkspace.sharedWorkspace().frontmostApplication()
            return int(app.processIdentifier()) if app else None
        except Exception:
            return None

    def _window_dicts(self) -> List[Dict[str, Any]]:
        opts = _kCGWindowListOptionOnScreenOnly | _kCGWindowListExcludeDesktopElements
        return list(self.Q.CGWindowListCopyWindowInfo(opts, _kCGNullWindowID) or [])

    def list_windows(self) -> List[WindowInfo]:
        """Normal on-screen windows (layer 0), front to back. The frontmost app's top window is active."""
        front_pid = self._frontmost_pid()
        results: List[WindowInfo] = []
        active_assigned = False
        for w in self._window_dicts():
            if int(w.get("kCGWindowLayer", 0)) != 0:
                continue
            b = w.get("kCGWindowBounds") or {}
            width, height = int(b.get("Width", 0)), int(b.get("Height", 0))
            if width < 20 or height < 20:
                continue
            owner = str(w.get("kCGWindowOwnerName", "") or "")
            title = str(w.get("kCGWindowName", "") or "")
            pid = int(w.get("kCGWindowOwnerPID", -1))
            is_active = (not active_assigned) and pid == front_pid
            active_assigned = active_assigned or is_active
            results.append(
                WindowInfo(
                    window_id=str(int(w.get("kCGWindowNumber", 0))),
                    title=f"{title} - {owner}" if title else owner,
                    bbox=BoundingBox(x=int(b.get("X", 0)), y=int(b.get("Y", 0)), width=width, height=height),
                    is_active=is_active,
                    app_name=owner,
                )
            )
        return results

    def get_active_window(self) -> Optional[WindowInfo]:
        for w in self.list_windows():
            if w.is_active:
                return w
        return None

    def window_pid(self, window_id: str) -> Optional[int]:
        for w in self._window_dicts():
            if str(int(w.get("kCGWindowNumber", -1))) == str(window_id):
                return int(w.get("kCGWindowOwnerPID", -1))
        return None

    def background_click(self, x: int, y: int, window_id: str) -> bool:
        """Press the button at (x, y) in window ``window_id``'s app without the
        real mouse (see ``dobby.hal.background``). False: click normally."""
        pid = self.window_pid(window_id)
        if pid is None or pid <= 0:
            return False
        try:
            ax = self._ax if self._ax is not None else _load_ax()
            self._ax = ax
        except Exception:  # noqa: BLE001
            return False
        from .background import ax_press_at

        if ax_press_at(ax, pid, x, y) is None:
            return False
        self._cooldown()
        return True

    def focus_window(self, window_id: str) -> bool:
        """
        Activates the owning app, then checks that the window really is in front.
        macOS may refuse activation and still report success, so when the window
        is not active after a short wait, Dobby raises it through AX
        (AXFrontmost + AXRaise) and checks again. ``last_focus_error`` says why
        a False came back.
        """
        self.last_focus_error = None
        pid = self.window_pid(window_id)
        if pid is None:
            self.last_focus_error = f"window {window_id} is not on screen"
            return False
        try:
            app = self.AK.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            if app is None:
                self.last_focus_error = f"no running app with pid {pid}"
                return False
            app.activateWithOptions_(self.AK.NSApplicationActivateIgnoringOtherApps)
        except Exception as e:  # noqa: BLE001
            self.last_focus_error = f"activation failed: {e}"
            return False
        if self._wait_active(window_id) or (self._ax_raise(pid, window_id) and self._wait_active(window_id)):
            self._cooldown()
            return True
        active = self.get_active_window()
        self.last_focus_error = (
            f"macOS did not bring window {window_id} to the front; "
            f"active is {active.title + ' (' + active.window_id + ')' if active else 'unknown'}"
        )
        return False

    def _wait_active(self, window_id: str) -> bool:
        for _ in range(max(1, int(_FOCUS_WAIT_S / _FOCUS_POLL_S))):
            active = self.get_active_window()
            if active is not None and active.window_id == str(window_id):
                return True
            time.sleep(_FOCUS_POLL_S)
        return False

    def _ax_raise(self, pid: int, window_id: str) -> bool:
        """Fallback raise through the Accessibility API. False if AX is unavailable."""
        try:
            ax = self._ax if self._ax is not None else _load_ax()
            self._ax = ax
            root = ax.AXUIElementCreateApplication(pid)
            ax.AXUIElementSetAttributeValue(root, "AXFrontmost", True)
            target = None
            bounds = next((w.bbox for w in self.list_windows() if w.window_id == str(window_id)), None)
            err, wins = ax.AXUIElementCopyAttributeValue(root, "AXWindows", None)
            for win in (wins or []) if err == 0 else []:
                if bounds is None or self._ax_frame(ax, win) == (bounds.x, bounds.y, bounds.width, bounds.height):
                    target = win
                    break
            if target is not None:
                ax.AXUIElementPerformAction(target, "AXRaise")
            return True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _ax_frame(ax: Any, win: Any) -> Optional[Tuple[int, int, int, int]]:
        try:
            _, pos = ax.AXUIElementCopyAttributeValue(win, "AXPosition", None)
            _, size = ax.AXUIElementCopyAttributeValue(win, "AXSize", None)
            _, p = ax.AXValueGetValue(pos, ax.kAXValueCGPointType, None)
            _, s = ax.AXValueGetValue(size, ax.kAXValueCGSizeType, None)
            return int(p.x), int(p.y), int(s.width), int(s.height)
        except Exception:  # noqa: BLE001
            return None


def build_hal() -> MacHAL:
    return MacHAL()
