"""macOS background mode: work in one window without taking the screen.

``PINLET_BACKGROUND=1`` swaps :class:`MacHAL` for :class:`BackgroundMacHAL`.
An agent first picks its window with ``focus_window`` (nothing comes to the
front; the window may be covered or on another Desktop). From then on:

* screenshots show that window only, drawn where it sits on the screen, so
  every coordinate still means what it always meant;
* a click presses, focuses or selects through accessibility;
* typing and key presses go straight to the window's app;
* scrolling moves the scroll bar through accessibility.

The real pointer, the keyboard focus and the front app stay with the person.

Some steps need a real pointer: drags, clicks on a canvas, apps without
accessibility. ``PINLET_BACKGROUND_POINTER`` says what happens then (see
``pinlet.hal.target.pointer_mode``): refuse and tell the AI (default), or
wait until the person is idle, borrow the pointer and put everything back.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Iterator
from typing import Any

from PIL import Image
from typing_extensions import override

from pinlet.hal import target
from pinlet.hal.background import ax_action_at, ax_click_at, ax_scroll_at
from pinlet.hal.macos import MacHAL, _load_ax, _screencapture
from pinlet.types import WindowInfo

# Behind the window in a screenshot: plain, so the AI does not read it as content.
_BACKDROP = (38, 38, 40)
# Off-screen windows worth listing: titled and big enough to be a real window.
_MIN_W, _MIN_H = 100, 60
# Menus, popovers and sheets of the same app drawn over the window.
_MAX_EXTRAS = 4
_IDLE_SECS = 3.0
_IDLE_WAIT_SECS = 45.0
_HID_STATE = 1  # kCGEventSourceStateHIDSystemState
_ANY_INPUT = 0xFFFFFFFF  # kCGAnyInputEventType


class NeedsPointer(RuntimeError):
    """The step cannot be done in the background; it needs the real pointer."""


class NoTarget(RuntimeError):
    """The agent has not picked the window it works in yet."""


def _no_target() -> NoTarget:
    return NoTarget(
        "Background mode: pick the window you work in first. Call list_windows, then "
        "focus_window(window_id). Nothing comes to the front; the person keeps the screen."
    )


class BackgroundMacHAL(MacHAL):
    """A :class:`MacHAL` that sees and acts on the agent's own window only."""

    background = True

    def __init__(
        self, *args: Any, window_capture_fn: Callable[[str], Image.Image] | None = None, **kw: Any
    ) -> None:
        super().__init__(*args, **kw)
        self._capture_window = window_capture_fn or (lambda wid: _screencapture(None, wid))
        # True while a step borrows the real pointer: behave like a plain MacHAL.
        self._borrowing = False

    # ------------------------------------------------------------- helpers
    def _ax_api(self) -> Any:
        if self._ax is None:
            self._ax = _load_ax()
        return self._ax

    def _need_target(self) -> target.Target:
        chosen = target.get()
        if chosen is None:
            raise _no_target()
        return chosen

    @contextlib.contextmanager
    def _to_app(self, pid: int) -> Iterator[None]:
        """Events posted inside go to app ``pid`` only."""
        before, self._post_pid = self._post_pid, int(pid)
        try:
            yield
        finally:
            self._post_pid = before

    def _target_window(self, chosen: target.Target) -> WindowInfo | None:
        for w in self._window_dicts(every_desktop=True):
            if str(int(w.get("kCGWindowNumber", -1))) == chosen.window_id:
                return self._window_info(w, is_active=True)
        return None

    # ------------------------------------------------------------- windows
    @override
    def list_windows(self) -> list[WindowInfo]:
        """Windows on every Desktop; the agent's own window is the active one."""
        if self._borrowing:
            return super().list_windows()
        chosen = target.get()
        if chosen is None:
            front_pid = self._frontmost_pid()
        results: list[WindowInfo] = []
        active_assigned = False
        for w in self._window_dicts(every_desktop=True):
            wid = str(int(w.get("kCGWindowNumber", 0)))
            on_screen = bool(w.get("kCGWindowIsOnscreen", False))
            if chosen is not None:
                is_active = wid == chosen.window_id
            else:
                is_active = (
                    on_screen
                    and not active_assigned
                    and int(w.get("kCGWindowOwnerPID", -1)) == front_pid
                )
            info = self._window_info(w, is_active)
            if info is None:
                continue
            if not on_screen and not is_active:
                # Apps keep many hidden helper windows; list only real-looking ones.
                b = info.bbox
                try:
                    alpha = float(w.get("kCGWindowAlpha", 1) or 0)
                except (TypeError, ValueError):
                    alpha = 1.0
                if not str(w.get("kCGWindowName", "") or "") or alpha <= 0:
                    continue
                if b.width < _MIN_W or b.height < _MIN_H:
                    continue
            active_assigned = active_assigned or is_active
            results.append(info)
        return results

    @override
    def window_pid(self, window_id: str) -> int | None:
        """Process id that owns the window, on any Desktop."""
        for w in self._window_dicts(every_desktop=True):
            if str(int(w.get("kCGWindowNumber", -1))) == str(window_id):
                return int(w.get("kCGWindowOwnerPID", -1))
        return None

    @override
    def focus_window(self, window_id: str) -> bool:
        """Makes the window this agent's target. Nothing is brought to the front."""
        if self._borrowing:
            return super().focus_window(window_id)
        self.last_focus_error = None
        for w in self._window_dicts(every_desktop=True):
            if str(int(w.get("kCGWindowNumber", -1))) != str(window_id):
                continue
            info = self._window_info(w)
            pid = int(w.get("kCGWindowOwnerPID", -1))
            if info is None or pid <= 0:
                break
            target.choose(info.window_id, pid, info.app_name or "")
            self._make_main(pid, info)
            return True
        self.last_focus_error = f"window {window_id} does not exist"
        return False

    def _make_main(self, pid: int, info: WindowInfo) -> None:
        """Keys sent to an app go to its main window: make ours that one (best effort).

        Only inside the app; the app itself is not activated.
        """
        try:
            ax = self._ax_api()
            root = ax.AXUIElementCreateApplication(pid)
            err, wins = ax.AXUIElementCopyAttributeValue(root, "AXWindows", None)
            wins = list(wins or []) if err == 0 else []
            if len(wins) < 2:
                return
            b = info.bbox
            for win in wins:
                if self._ax_frame(ax, win) == (b.x, b.y, b.width, b.height):
                    ax.AXUIElementSetAttributeValue(win, "AXMain", True)
                    return
        except Exception:  # noqa: BLE001 - keys still reach the app
            return

    # ------------------------------------------------------------- capture
    def _extras(self, chosen: target.Target, win: WindowInfo) -> list[WindowInfo]:
        """Menus, popovers and sheets of the target's app that overlap it, back to front."""
        found: list[WindowInfo] = []
        t = win.bbox
        for w in self._window_dicts(every_desktop=True):
            if int(w.get("kCGWindowOwnerPID", -1)) != chosen.pid:
                continue
            wid = str(int(w.get("kCGWindowNumber", 0)))
            if wid == chosen.window_id:
                continue
            layer = int(w.get("kCGWindowLayer", 0))
            if layer == 0 and str(w.get("kCGWindowName", "") or ""):
                continue  # another document window, not part of ours
            if layer == 0 and not w.get("kCGWindowIsOnscreen", False):
                continue  # a hidden helper window
            info = self._window_info({**w, "kCGWindowLayer": 0})
            if info is None:
                continue
            b = info.bbox
            apart = (
                b.x >= t.x + t.width
                or b.x + b.width <= t.x
                or b.y >= t.y + t.height
                or b.y + b.height <= t.y
            )
            if apart or b.width * b.height > t.width * t.height:
                continue
            found.append(info)
            if len(found) >= _MAX_EXTRAS:
                break
        return list(reversed(found))

    @override
    def capture_screen(self, region: tuple[int, int, int, int] | None = None) -> Image.Image:
        """The agent's window by itself, placed where it sits on the screen.

        Before a window is picked this is the ordinary screenshot, so the AI
        can look around and choose.
        """
        chosen = target.get()
        if chosen is None or self._borrowing:
            return super().capture_screen(region)
        win = self._target_window(chosen)
        if win is None:
            target.clear(current=True)
            raise NoTarget(
                f"The window you were working in ({chosen.app_name or chosen.window_id}) is "
                "gone. Call list_windows, then focus_window(window_id) to pick one."
            )
        canvas = Image.new("RGB", self.get_screen_size(), _BACKDROP)
        for info in [win, *self._extras(chosen, win)]:
            try:
                shot = self._capture_window(info.window_id)
            except Exception:  # noqa: BLE001 - a popup that closed meanwhile
                if info is win:
                    raise
                continue
            size = (max(1, info.bbox.width), max(1, info.bbox.height))
            if shot.size != size:
                shot = shot.resize(size, Image.LANCZOS)
            canvas.paste(shot.convert("RGB"), (info.bbox.x, info.bbox.y))
        if region:
            x, y, w, h = (int(v) for v in region)
            return canvas.crop((x, y, x + max(1, w), y + max(1, h)))
        return canvas

    # --------------------------------------------------------------- mouse
    @override
    def mouse_move(self, x: int, y: int) -> None:
        if self._borrowing:
            return super().mouse_move(x, y)
        self._need_target()  # hovering is not possible without the pointer; nothing to do
        return None

    @override
    def mouse_click(self, x: int, y: int, button: str = "left", click_count: int = 1) -> None:
        if self._borrowing:
            return super().mouse_click(x, y, button, click_count)
        chosen = self._need_target()
        ax = self._ax_api()
        done: Any
        if button == "right":
            done = ax_action_at(ax, chosen.pid, x, y, "AXShowMenu")
        elif click_count >= 2:
            done = ax_action_at(ax, chosen.pid, x, y, "AXOpen")
        else:
            done = ax_click_at(ax, chosen.pid, x, y)
        if done:
            self._cooldown()
            return None
        kind = (
            "right-click" if button == "right" else "double-click" if click_count >= 2 else "click"
        )
        return self._borrow(
            f"a {kind} at ({x}, {y})",
            lambda: MacHAL.mouse_click(self, x, y, button, click_count),
        )

    @override
    def mouse_drag(
        self, start_x: int, start_y: int, end_x: int, end_y: int, duration_sec: float = 0.3
    ) -> None:
        if self._borrowing:
            return super().mouse_drag(start_x, start_y, end_x, end_y, duration_sec)
        self._need_target()
        return self._borrow(
            "a drag", lambda: MacHAL.mouse_drag(self, start_x, start_y, end_x, end_y, duration_sec)
        )

    @override
    def mouse_scroll(self, dx: int, dy: int) -> None:
        """Scrolls the middle of the agent's window (the wheel has no pointer to follow)."""
        if self._borrowing:
            return super().mouse_scroll(dx, dy)
        chosen = self._need_target()
        win = self._target_window(chosen)
        if win is not None:
            cx = win.bbox.x + win.bbox.width // 2
            cy = win.bbox.y + win.bbox.height // 2
            if ax_scroll_at(self._ax_api(), chosen.pid, cx, cy, dx, dy):
                self._cooldown()
                return None
        # No scroll bar to move (web pages often): send the wheel to the app itself.
        with self._to_app(chosen.pid):
            return super().mouse_scroll(dx, dy)

    # ------------------------------------------------------------ keyboard
    @override
    def keyboard_type(self, text: str, delay_ms: float = 12.0) -> None:
        if self._borrowing:
            return super().keyboard_type(text, delay_ms)
        with self._to_app(self._need_target().pid):
            return super().keyboard_type(text, delay_ms)

    @override
    def keyboard_press_key(self, key_name: str) -> None:
        if self._borrowing:
            return super().keyboard_press_key(key_name)
        with self._to_app(self._need_target().pid):
            return super().keyboard_press_key(key_name)

    @override
    def keyboard_hotkey(self, keys: list[str]) -> None:
        if self._borrowing:
            return super().keyboard_hotkey(keys)
        with self._to_app(self._need_target().pid):
            return super().keyboard_hotkey(keys)

    @override
    def release_modifiers(self) -> None:
        chosen = target.get()
        if chosen is None or self._borrowing:
            return super().release_modifiers()
        with self._to_app(chosen.pid):
            return super().release_modifiers()

    # ------------------------------------------------- the real pointer
    def person_idle_secs(self) -> float:
        """Seconds since the person last touched the mouse or keyboard."""
        try:
            return float(self.Q.CGEventSourceSecondsSinceLastEventType(_HID_STATE, _ANY_INPUT))
        except Exception:  # noqa: BLE001 - unknown: treat them as busy
            return 0.0

    def _borrow(self, what: str, act: Callable[[], Any]) -> Any:
        """Does a step that needs the real pointer, or says why not."""
        chosen = self._need_target()
        if target.pointer_mode() != "idle":
            raise NeedsPointer(
                f"Background mode cannot do {what}: it needs the real pointer, and the person "
                "is using it. Try another way (a keyboard shortcut, a menu item or button "
                "found with get_accessibility_tree or find_element), or tell the person this "
                "step needs them."
            )
        waited = 0.0
        while self.person_idle_secs() < _IDLE_SECS:
            if waited >= _IDLE_WAIT_SECS:
                raise NeedsPointer(
                    f"Background mode needs the real pointer for {what}, and the person kept "
                    "using the Mac. Try again later or try another way."
                )
            time.sleep(0.5)
            waited += 0.5
        where = self.get_cursor_position()
        front = self._frontmost_pid()
        self._borrowing = True
        try:
            if not MacHAL.focus_window(self, chosen.window_id):
                raise NeedsPointer(f"Could not bring the window forward for {what}.")
            return act()
        finally:
            self._borrowing = False
            self._give_back(front, where)

    def _give_back(self, front_pid: int | None, where: tuple[int, int]) -> None:
        """Puts the person's app back in front and the pointer where it was."""
        try:
            if front_pid:
                app = self.AK.NSRunningApplication.runningApplicationWithProcessIdentifier_(
                    front_pid
                )
                if app is not None:
                    app.activateWithOptions_(self.AK.NSApplicationActivateIgnoringOtherApps)
            self.Q.CGWarpMouseCursorPosition(where)
        except Exception:  # noqa: BLE001 - best effort; the step itself is done
            pass
