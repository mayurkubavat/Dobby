"""Linux Desktop Hardware Abstraction Layer implementation.

Native imports (mss, pynput, Xlib) happen on first instantiation, not at
module import, so collecting this module on macOS or Windows does not
require those packages.
"""

import os
import time
from typing import Any

from PIL import Image

from pinlet.config import CONFIG
from pinlet.hal.base import IDesktopHAL
from pinlet.types import BoundingBox, DisplayMetrics, DisplayTopology, WindowInfo

_native_loaded = False
mss: Any = None
MouseController: Any = None
Button: Any = None
KeyboardController: Any = None
Key: Any = None
X: Any = None
display: Any = None
xlib_error: Any = None
protocol_event: Any = None


def _load_native() -> None:
    """Imports Linux desktop libraries once."""
    global _native_loaded, mss, MouseController, Button, KeyboardController, Key
    global X, display, xlib_error, protocol_event
    if _native_loaded:
        return
    try:
        import mss as _mss
        from pynput.keyboard import Controller as _KeyboardController
        from pynput.keyboard import Key as _Key
        from pynput.mouse import Button as _Button
        from pynput.mouse import Controller as _MouseController
        from Xlib import X as _X
        from Xlib import display as _display
        from Xlib import error as _xlib_error
        from Xlib.protocol import event as _protocol_event
    except ImportError as exc:
        raise ImportError("Linux HAL needs the 'linux' extra: pip install -e '.[linux]'") from exc
    mss = _mss
    MouseController = _MouseController
    Button = _Button
    KeyboardController = _KeyboardController
    Key = _Key
    X = _X
    display = _display
    xlib_error = _xlib_error
    protocol_event = _protocol_event
    _native_loaded = True


class LinuxHAL(IDesktopHAL):
    """Linux Desktop HAL supporting X11 natively and Wayland via compatible protocols.

    Uses mss for high-speed screen capture, pynput for mouse/keyboard injection, and python-xlib
    for window hierarchy querying.
    """

    def __init__(self) -> None:
        _load_native()
        self.mouse = MouseController()
        self.keyboard = KeyboardController()
        self._sct = mss.MSS()
        self._is_wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
        self._x_display: display.Display | None = None
        # Open X whenever DISPLAY is set. Under XWayland both DISPLAY and
        # WAYLAND_DISPLAY are set and X11 clients are still reachable.
        if os.environ.get("DISPLAY"):
            try:
                self._x_display = display.Display()
            except Exception:
                self._x_display = None
        if self._x_display is None:
            self.display_server = "none"
        elif self._is_wayland:
            self.display_server = "XWayland"
        else:
            self.display_server = "X11"

    def _check_failsafe(self, x: int, y: int) -> None:
        """Emergency abort if pointer is directed to top-left (0, 0)."""
        if CONFIG.failsafe_corner and x == 0 and y == 0:
            raise RuntimeError(
                "VisionMCP Failsafe Triggered: Cursor moved to (0, 0). Aborting execution."
            )

    def display_topology(self) -> DisplayTopology:
        """Logical virtual desktop from mss monitors. Monitor 0 is the union."""
        displays: list[DisplayMetrics] = []
        monitors = self._sct.monitors
        indexed = monitors[1:] if len(monitors) > 1 else monitors
        for i, mon in enumerate(indexed):
            displays.append(
                DisplayMetrics(
                    display_id=f"mss-{i + 1}",
                    origin=(int(mon.get("left", 0)), int(mon.get("top", 0))),
                    size=(int(mon["width"]), int(mon["height"])),
                    scale=1.0,
                )
            )
        if not displays:
            displays.append(DisplayMetrics(display_id="primary", origin=(0, 0), size=(0, 0)))
        return DisplayTopology(displays=displays, primary_id=displays[0].display_id)

    @property
    def ui_scale(self) -> float:
        """Desktop UI scale from the Xft.dpi resource (96 dpi = 1.0).

        Diagnostics only: on X11, input, capture and window geometry all use
        the same physical pixels, so display scale stays 1.0 in the topology.
        """
        try:
            res = (
                self._x_display.screen().root.get_full_property(
                    self._x_display.intern_atom("RESOURCE_MANAGER"), X.AnyPropertyType
                )
                if self._x_display
                else None
            )
            text = res.value.decode("utf-8", "ignore") if res is not None else ""
            for line in text.splitlines():
                if line.startswith("Xft.dpi:"):
                    return round(float(line.split(":", 1)[1].strip()) / 96.0, 2)
        except Exception:
            pass
        return 1.0

    def get_screen_size(self) -> tuple[int, int]:
        """Returns primary monitor dimensions in pixels."""
        if len(self._sct.monitors) > 1:
            # Monitor 1 is the primary monitor in mss
            mon = self._sct.monitors[1]
            return mon["width"], mon["height"]
        mon = self._sct.monitors[0]
        return mon["width"], mon["height"]

    def capture_screen(self, region: tuple[int, int, int, int] | None = None) -> Image.Image:
        """Captures full screen or region (x, y, width, height)."""
        if region:
            x, y, w, h = region
            # X refuses (BadMatch) a grab that leaves the root window, e.g. a window dragged
            # partly off screen: grab the on-screen part and pad the rest black, so the image
            # still lines up with ``region``.
            root = self._sct.monitors[0]
            x0, y0 = max(x, root["left"]), max(y, root["top"])
            x1 = min(x + w, root["left"] + root["width"])
            y1 = min(y + h, root["top"] + root["height"])
            if x1 <= x0 or y1 <= y0:
                return Image.new("RGB", (w, h))
            raw = self._sct.grab({"top": y0, "left": x0, "width": x1 - x0, "height": y1 - y0})
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            if img.size == (w, h):
                return img
            canvas = Image.new("RGB", (w, h))
            canvas.paste(img, (x0 - x, y0 - y))
            return canvas
        mon = self._sct.monitors[1] if len(self._sct.monitors) > 1 else self._sct.monitors[0]
        raw = self._sct.grab(mon)

        # Convert raw BGRA bytes to RGB PIL Image
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        return img

    def get_cursor_position(self) -> tuple[int, int]:
        """Returns current pointer position."""
        pos = self.mouse.position
        return int(pos[0]), int(pos[1])

    def mouse_move(self, x: int, y: int) -> None:
        """Moves cursor to absolute coordinates."""
        self._check_failsafe(x, y)
        self.mouse.position = (x, y)
        time.sleep(CONFIG.action_cooldown_ms / 1000.0)

    def mouse_click(self, x: int, y: int, button: str = "left", click_count: int = 1) -> None:
        """Moves cursor and executes button click(s)."""
        self.mouse_move(x, y)
        btn = (
            Button.left
            if button == "left"
            else (Button.right if button == "right" else Button.middle)
        )
        self.mouse.click(btn, click_count)
        time.sleep(CONFIG.action_cooldown_ms / 1000.0)

    def mouse_drag(
        self, start_x: int, start_y: int, end_x: int, end_y: int, duration_sec: float = 0.3
    ) -> None:
        """Smoothly drags cursor between two points."""
        self.mouse_move(start_x, start_y)
        self.mouse.press(Button.left)
        try:
            steps = max(int(duration_sec * 60), 10)
            step_delay = duration_sec / steps
            for i in range(1, steps + 1):
                cur_x = int(start_x + (end_x - start_x) * (i / steps))
                cur_y = int(start_y + (end_y - start_y) * (i / steps))
                self.mouse.position = (cur_x, cur_y)
                time.sleep(step_delay)
        finally:
            self.mouse.release(Button.left)
            time.sleep(CONFIG.action_cooldown_ms / 1000.0)

    def mouse_scroll(self, dx: int, dy: int) -> None:
        """Scrolls wheel."""
        self.mouse.scroll(dx, dy)
        time.sleep(CONFIG.action_cooldown_ms / 1000.0)

    # ----------------------------------------------------------- keyboard
    #
    # Keys go straight to XTest with python-xlib rather than through pynput:
    # pynput's character typing is ignored by GTK 4 apps (Enter works, letters
    # do not), while raw XTest events reach every toolkit. Characters with no
    # key in the current layout are typed by briefly binding them to a spare
    # keycode, the same trick xdotool uses.

    _KEYSYM_NAMES = {
        "enter": "Return",
        "return": "Return",
        "tab": "Tab",
        "space": "space",
        "backspace": "BackSpace",
        "delete": "Delete",
        "del": "Delete",
        "escape": "Escape",
        "esc": "Escape",
        "up": "Up",
        "down": "Down",
        "left": "Left",
        "right": "Right",
        "home": "Home",
        "end": "End",
        "pageup": "Prior",
        "page_up": "Prior",
        "pgup": "Prior",
        "pagedown": "Next",
        "page_down": "Next",
        "pgdn": "Next",
        "insert": "Insert",
        "ins": "Insert",
        "ctrl": "Control_L",
        "control": "Control_L",
        "lctrl": "Control_L",
        "rctrl": "Control_R",
        "alt": "Alt_L",
        "option": "Alt_L",
        "opt": "Alt_L",
        "altgr": "ISO_Level3_Shift",
        "shift": "Shift_L",
        "lshift": "Shift_L",
        "rshift": "Shift_R",
        "super": "Super_L",
        "meta": "Super_L",
        "cmd": "Super_L",
        "command": "Super_L",
        "win": "Super_L",
        "windows": "Super_L",
        "capslock": "Caps_Lock",
        "caps_lock": "Caps_Lock",
        "numlock": "Num_Lock",
        "scrolllock": "Scroll_Lock",
        "menu": "Menu",
        "apps": "Menu",
        "pause": "Pause",
        "printscreen": "Print",
        "print_screen": "Print",
        "print": "Print",
        "volumeup": "XF86AudioRaiseVolume",
        "volumedown": "XF86AudioLowerVolume",
        "volumemute": "XF86AudioMute",
        "mute": "XF86AudioMute",
        "playpause": "XF86AudioPlay",
        "nexttrack": "XF86AudioNext",
        "prevtrack": "XF86AudioPrev",
        **{f"f{i}": f"F{i}" for i in range(1, 25)},
    }

    def _keysym_for(self, key: str) -> int:
        """X keysym for a key name ("enter", "ctrl", "F5") or a single character."""
        from Xlib import XK

        if len(key) == 1:
            code = ord(key)
            if key == "\n":
                return XK.string_to_keysym("Return")
            if key == "\t":
                return XK.string_to_keysym("Tab")
            # Latin-1 keysyms equal their code point; everything else uses the Unicode range.
            return code if 0x20 <= code <= 0x7E or 0xA0 <= code <= 0xFF else 0x01000000 + code
        name = key.strip()
        low = name.lower().replace(" ", "").replace("-", "")
        mapped = self._KEYSYM_NAMES.get(low) or self._KEYSYM_NAMES.get(name.lower())
        sym = XK.string_to_keysym(mapped or name)
        if not sym:
            raise ValueError(f"Unknown key name: {key!r}")
        return sym

    def _keycode_for(self, keysym: int) -> tuple[int | None, bool]:
        """(keycode, needs_shift) for keysym in the current layout, or (None, False)."""
        best = None
        for keycode, index in self._x_display.keysym_to_keycodes(keysym):
            if index in (0, 1) and (best is None or index < best[1]):
                best = (keycode, index)
        if best is None:
            return None, False
        return best[0], best[1] == 1

    def _spare_keycode(self) -> int | None:
        cached = self.__dict__.get("_spare")
        if cached:
            return cached
        d = self._x_display
        first = d.display.info.min_keycode
        count = d.display.info.max_keycode - first + 1
        mapping = d.get_keyboard_mapping(first, count)
        for i in range(count - 1, -1, -1):  # high keycodes are the usual empty ones
            if not any(mapping[i]):
                self._spare = first + i
                return self._spare
        return None

    def _fake(self, keycode: int, down: bool) -> None:
        from Xlib.ext import xtest

        xtest.fake_input(self._x_display, X.KeyPress if down else X.KeyRelease, keycode)
        self._x_display.sync()

    def _tap_keysym(self, keysym: int, hold: float = 0.01) -> None:
        keycode, shift = self._keycode_for(keysym)
        if keycode is not None:
            shift_code = self._keycode_for(self._keysym_for("shift"))[0] if shift else None
            if shift_code:
                self._fake(shift_code, True)
            self._fake(keycode, True)
            time.sleep(hold)
            self._fake(keycode, False)
            if shift_code:
                self._fake(shift_code, False)
            return
        spare = self._spare_keycode()
        if spare is None:
            raise RuntimeError(f"No key for keysym 0x{keysym:x} and no spare keycode to bind it to")
        d = self._x_display
        d.change_keyboard_mapping(spare, [(keysym, keysym)])
        d.sync()
        time.sleep(0.03)  # let clients process MappingNotify
        self._fake(spare, True)
        time.sleep(hold)
        self._fake(spare, False)
        time.sleep(0.03)
        d.change_keyboard_mapping(spare, [(0, 0)])
        d.sync()

    def keyboard_type(self, text: str, delay_ms: float = 12.0) -> None:
        """Types text, including characters that have no key in the current layout."""
        delay_sec = max(delay_ms / 1000.0, 0.001)
        if not self._x_display:
            for char in text:
                self.keyboard.type(char)
                time.sleep(delay_sec)
        else:
            for char in text:
                self._tap_keysym(self._keysym_for(char))
                time.sleep(delay_sec)
        time.sleep(CONFIG.action_cooldown_ms / 1000.0)

    def keyboard_press_key(self, key_name: str) -> None:
        """Presses and releases a single key ("enter", "f5", "a")."""
        if not self._x_display:
            raise RuntimeError("No X display for keyboard input")
        self._tap_keysym(self._keysym_for(key_name), hold=0.02)
        time.sleep(CONFIG.action_cooldown_ms / 1000.0)

    def keyboard_hotkey(self, keys: list[str]) -> None:
        """Presses a chord (["ctrl", "shift", "t"]) and releases it in reverse order."""
        if not self._x_display:
            raise RuntimeError("No X display for keyboard input")
        codes = []
        for k in keys:
            keysym = self._keysym_for(k.lower() if len(k) == 1 else k)
            keycode, _shift = self._keycode_for(keysym)
            if keycode is None:
                raise ValueError(f"Key {k!r} has no keycode in the current layout")
            codes.append(keycode)
        pressed = []
        try:
            for c in codes:
                self._fake(c, True)
                pressed.append(c)
                time.sleep(0.01)
            time.sleep(0.05)
        finally:
            for c in reversed(pressed):
                self._fake(c, False)
                time.sleep(0.01)
        time.sleep(CONFIG.action_cooldown_ms / 1000.0)

    # ------------------------------------------------------------ windows
    #
    # EWMH (_NET_*) properties set by every mainstream X11 window manager.
    # Under XWayland only X11 clients are visible; native Wayland windows are not.

    _SKIP_TYPES = (
        "_NET_WM_WINDOW_TYPE_DOCK",
        "_NET_WM_WINDOW_TYPE_DESKTOP",
        "_NET_WM_WINDOW_TYPE_NOTIFICATION",
        "_NET_WM_WINDOW_TYPE_TOOLTIP",
    )

    def _atom(self, name: str) -> int:
        cache = self.__dict__.setdefault("_atoms", {})
        if name not in cache:
            cache[name] = self._x_display.intern_atom(name)
        return cache[name]

    def _prop(self, win: Any, name: str) -> Any:
        try:
            p = win.get_full_property(self._atom(name), X.AnyPropertyType)
        except Exception:
            return None
        return p.value if p is not None else None

    def _active_id(self) -> int | None:
        root = self._x_display.screen().root
        val = self._prop(root, "_NET_ACTIVE_WINDOW")
        try:
            wid = int(val[0]) if val is not None and len(val) else 0
        except Exception:
            wid = 0
        return wid or None

    def _stacked_ids(self) -> list[int]:
        """Managed windows, front to back (stacking order when the WM provides it)."""
        root = self._x_display.screen().root
        stacking = self._prop(root, "_NET_CLIENT_LIST_STACKING")
        if stacking is not None and len(stacking):
            return [int(w) for w in reversed(list(stacking))]  # EWMH lists bottom to top
        clients = self._prop(root, "_NET_CLIENT_LIST")
        return [int(w) for w in clients] if clients is not None else []

    def _window_info(self, win_id: int, active_id: int | None) -> WindowInfo | None:
        win = self._x_display.create_resource_object("window", win_id)
        types = self._prop(win, "_NET_WM_WINDOW_TYPE")
        if types is not None:
            skip = {self._atom(t) for t in self._SKIP_TYPES}
            if any(int(t) in skip for t in types):
                return None
        state = self._prop(win, "_NET_WM_STATE")
        if state is not None and self._atom("_NET_WM_STATE_HIDDEN") in [int(a) for a in state]:
            return None  # minimised
        attrs = win.get_attributes()
        if getattr(attrs, "map_state", X.IsViewable) != X.IsViewable:
            return None

        name = self._prop(win, "_NET_WM_NAME")
        if name is None:
            name = self._prop(win, "WM_NAME")
        if isinstance(name, bytes):
            title = name.decode("utf-8", errors="ignore")
        else:
            title = str(name) if name is not None else ""

        app_name = None
        try:
            wm_class = win.get_wm_class()
            if wm_class:
                app_name = wm_class[-1] or wm_class[0]
        except Exception:
            pass

        geom = win.get_geometry()
        root = self._x_display.screen().root
        coords = root.translate_coords(win, 0, 0)
        x, y, w, h = self._without_csd_shadow(
            win, int(coords.x), int(coords.y), int(geom.width), int(geom.height)
        )
        # Include the WM decoration (title bar, borders) so occlusion matches what is drawn.
        extents = self._prop(win, "_NET_FRAME_EXTENTS")
        if extents is not None and len(extents) == 4:
            left, right, top, bottom = (int(v) for v in extents)
            x, y, w, h = x - left, y - top, w + left + right, h + top + bottom
        if w < 20 or h < 20:
            return None

        return WindowInfo(
            window_id=hex(win_id),
            title=title or (app_name or "Untitled"),
            bbox=BoundingBox(x=x, y=y, width=w, height=h),
            is_active=(win_id == active_id),
            app_name=app_name,
        )

    def _without_csd_shadow(self, win, x: int, y: int, w: int, h: int) -> tuple[int, int, int, int]:
        """Drop the invisible shadow border a client-side-decorated (GTK) window draws around itself.

        _GTK_FRAME_EXTENTS is (left, right, top, bottom). GTK 4 widget positions are relative
        to the area inside it, and the shadow is not part of the window the person sees.
        """
        extents = self._prop(win, "_GTK_FRAME_EXTENTS")
        if extents is not None and len(extents) == 4:
            left, right, top, bottom = (int(v) for v in extents)
            if w > left + right and h > top + bottom:
                return x + left, y + top, w - left - right, h - top - bottom
        return x, y, w, h

    def client_windows(self) -> list[dict]:
        """Front-to-back client areas (no WM frame) with pid and title.

        The AT-SPI inspector uses these to place GTK 4 widgets, which only
        report window-relative coordinates.
        """
        if not self._x_display:
            return []
        out: list[dict] = []
        try:
            root = self._x_display.screen().root
            for win_id in self._stacked_ids():
                try:
                    win = self._x_display.create_resource_object("window", win_id)
                    geom = win.get_geometry()
                    coords = root.translate_coords(win, 0, 0)
                    pid = self._prop(win, "_NET_WM_PID")
                    name = self._prop(win, "_NET_WM_NAME")
                    if name is None:
                        name = self._prop(win, "WM_NAME")
                    title = (
                        name.decode("utf-8", "ignore")
                        if isinstance(name, bytes)
                        else str(name or "")
                    )
                    x, y, w, h = self._without_csd_shadow(
                        win, int(coords.x), int(coords.y), int(geom.width), int(geom.height)
                    )
                    out.append(
                        {
                            "window_id": hex(win_id),
                            "pid": int(pid[0]) if pid is not None and len(pid) else None,
                            "title": title,
                            "x": x,
                            "y": y,
                            "width": w,
                            "height": h,
                        }
                    )
                except (xlib_error.BadWindow, xlib_error.BadDrawable, xlib_error.BadMatch):
                    continue
        except Exception:
            return out
        return out

    def list_windows(self) -> list[WindowInfo]:
        """Visible top-level windows, front to back, docks and minimised windows excluded."""
        if not self._x_display:
            return []
        try:
            active_id = self._active_id()
            results: list[WindowInfo] = []
            for win_id in self._stacked_ids():
                try:
                    info = self._window_info(win_id, active_id)
                except (xlib_error.BadWindow, xlib_error.BadDrawable, xlib_error.BadMatch):
                    continue
                if info is not None:
                    results.append(info)
            return results
        except Exception:
            return []

    def get_active_window(self) -> WindowInfo | None:
        """The window named by _NET_ACTIVE_WINDOW, if it is a visible top-level window."""
        if not self._x_display:
            return None
        try:
            active_id = self._active_id()
            if active_id is None:
                return None
            return self._window_info(active_id, active_id)
        except Exception:
            return None

    def window_pid(self, window_id: str) -> int | None:
        """Owning process id from _NET_WM_PID (set by GTK, Qt, Electron and most toolkits)."""
        if not self._x_display:
            return None
        try:
            win = self._x_display.create_resource_object("window", _parse_window_id(window_id))
            val = self._prop(win, "_NET_WM_PID")
            return int(val[0]) if val is not None and len(val) else None
        except Exception:
            return None

    def background_click(self, x: int, y: int, window_id: str) -> bool:
        """Presses the button at (x, y) in window ``window_id`` through AT-SPI.

        Does not move the real pointer; this is the Linux side of background clicks.

        False: nothing pressable there, so the caller clicks normally.
        """
        pid = self.window_pid(window_id)
        if pid is None or pid <= 0:
            return False
        inspector = getattr(self, "_press_inspector", None)
        if inspector is None:
            try:
                from pinlet.perception.atspi import ATSPIInspector

                inspector = ATSPIInspector(hal=self)
            except Exception:  # noqa: BLE001
                return False
            self._press_inspector = inspector
        if not inspector.is_available or inspector.press_at(int(x), int(y), pid) is None:
            return False
        time.sleep(CONFIG.action_cooldown_ms / 1000.0)
        return True

    def focus_window(self, window_id: str) -> bool:
        """Activates the window via _NET_ACTIVE_WINDOW and reports whether focus actually moved."""
        if not self._x_display:
            return False
        try:
            win_int = _parse_window_id(window_id)
            root = self._x_display.screen().root
            win = self._x_display.create_resource_object("window", win_int)
            # Source indication 2 = pager/automation: WMs honour it without focus-stealing checks.
            event = protocol_event.ClientMessage(
                window=win,
                client_type=self._atom("_NET_ACTIVE_WINDOW"),
                data=(32, [2, X.CurrentTime, 0, 0, 0]),
            )
            root.send_event(event, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
            self._x_display.flush()

            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                if self._active_id() == win_int:
                    break
                time.sleep(0.02)
            else:
                # No (or a stubborn) window manager: raise and focus directly.
                try:
                    win.map()
                    win.configure(stack_mode=X.Above)
                    self._x_display.set_input_focus(win, X.RevertToParent, X.CurrentTime)
                    self._x_display.sync()
                except Exception:
                    pass
            time.sleep(CONFIG.action_cooldown_ms / 1000.0)
            active = self._active_id()
            if active is not None:
                return active == win_int
            # No _NET_ACTIVE_WINDOW (no EWMH window manager): trust the direct focus only
            # for a window that exists and is on screen.
            return win.get_attributes().map_state == X.IsViewable
        except Exception:
            return False


def _parse_window_id(window_id: str) -> int:
    s = str(window_id).strip()
    return int(s, 16) if s.lower().startswith("0x") else int(s)
