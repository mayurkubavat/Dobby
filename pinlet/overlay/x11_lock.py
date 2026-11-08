"""X11 input lock for the overlay helper (see ``inputlock`` for the rules).

A transparent ``InputOnly`` window (the shield) sits exactly over the window
Pinlet works in, stacked just above it, so windows the person brings in front
of it stay usable. Its cursor is the theme's "not-allowed" sign, so over
Pinlet's window the pointer shows it cannot click; clicks and scrolls there
land on the shield and go nowhere. While Pinlet's window has the keyboard
focus the keyboard is grabbed too, except for the Stop / Pause hotkeys.

Pinlet's own XTest input would hit the shield like the person's, so the
server sends ``input.pass`` around each action: the shield steps aside and
the keyboard grab is dropped for those few milliseconds.

Everything here is a plain X resource of the helper's own connection: if the
helper exits or crashes, the X server removes the shield and every grab, so
the person can never be locked out.

Pure python-xlib on its own connection, driven from the GTK main loop
(:meth:`X11InputLock.fileno` + :meth:`X11InputLock.pump`), no threads.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable

from . import inputlock

Rect = tuple[int, int, int, int]

_SCROLL_BUTTONS = (4, 5, 6, 7)


class X11InputLock:
    """X11 input lock: shields Pinlet's target window from the person's mouse and keys.

    Swallowed input is reported through ``on_swallowed``; insisting (several clicks
    or a long press) calls ``on_insist`` so the person can take over.
    """

    def __init__(
        self,
        on_swallowed: Callable[[str], None],
        on_insist: Callable[[], None],
        on_hotkey: Callable[[str], None],
        cursor_xid: int | None = None,
        display_name: str | None = None,
    ) -> None:
        from Xlib import XK, X
        from Xlib import display as xdisplay

        self.X = X
        self.on_swallowed = on_swallowed
        self.on_insist = on_insist
        self.on_hotkey = on_hotkey
        self.d = xdisplay.Display(display_name)
        self.root = self.d.screen().root
        self.insist = inputlock.Insist()
        if cursor_xid:
            cursor = cursor_xid
        else:  # no theme cursor from GDK: the classic X-shaped one
            font = self.d.open_font("cursor")
            cursor = font.create_glyph_cursor(font, 0, 1, (65535, 0, 0), (65535, 65535, 65535))
        self.shield = self.root.create_window(
            0,
            0,
            1,
            1,
            0,
            0,
            X.InputOnly,
            X.CopyFromParent,
            override_redirect=True,
            cursor=cursor,
            event_mask=X.ButtonPressMask | X.ButtonReleaseMask,
        )
        self.shield.set_wm_name("Pinlet Overlay lock")
        # Same combos as the helper's global hotkeys (Ctrl+Alt+End is Stop
        # where the desktop owns Ctrl+Alt+Esc); with the keyboard grabbed only
        # this connection sees them.
        self.hotkeys = {
            self.d.keysym_to_keycode(XK.string_to_keysym(name)): which
            for name, which in (("Escape", "stop"), ("End", "stop"), ("p", "pause"))
        }
        self._active_atom = self.d.intern_atom("_NET_ACTIVE_WINDOW")
        self.rect: Rect | None = None
        self.frame: int | None = None
        self.client: int | None = None
        self.armed = False
        self.passing = False
        self.mapped = False
        self.keys_grabbed = False
        self.pressed: set = set()  # buttons whose press the shield swallowed
        self.d.sync()

    # ------------------------------------------------------------- state
    def fileno(self) -> int:
        """File descriptor of the X connection, for the GLib main loop."""
        return self.d.fileno()

    def _frame_of(self, wid: int) -> int | None:
        """The top-level ancestor (WM frame) of client window ``wid``."""
        try:
            win = self.d.create_resource_object("window", wid)
            for _ in range(16):
                tree = win.query_tree()
                if tree.parent == 0 or tree.parent.id == self.root.id:
                    return win.id
                win = tree.parent
        except Exception:  # noqa: BLE001 - window gone
            return None
        return None

    def _active(self) -> int | None:
        try:
            prop = self.root.get_full_property(self._active_atom, self.X.AnyPropertyType)
            return int(prop.value[0]) if prop is not None and len(prop.value) else None
        except Exception:  # noqa: BLE001
            return None

    def target_front(self) -> bool:
        """True when the locked window (or its frame) is the active window."""
        active = self._active()
        if active is None or self.client is None:
            return False
        return active == self.client or (
            self.frame is not None and self._frame_of(active) == self.frame
        )

    def update(self, rect: Rect | None, client: int | None, armed: bool) -> None:
        """Place (or remove) the shield over ``rect`` (global pixels) of window ``client``."""
        if client != self.client:
            self.client = client
            self.frame = self._frame_of(client) if client else None
        self.rect = tuple(int(v) for v in rect) if rect else None  # type: ignore[assignment]
        self.armed = bool(armed and self.rect and self.rect[2] > 0 and self.rect[3] > 0)
        if not self.armed:
            self.insist.reset()
        self._apply()

    def set_pass(self, on: bool) -> None:
        """Pinlet's own input runs now (True) or is done (False)."""
        self.passing = bool(on)
        self._apply()

    def _apply(self) -> None:
        X = self.X
        show = self.armed and not self.passing
        try:
            if show:
                x, y, w, h = self.rect  # type: ignore[misc]
                kw = dict(x=x, y=y, width=max(1, w), height=max(1, h))
                if self.frame is not None:
                    kw.update(sibling=self.frame, stack_mode=X.Above)
                else:
                    kw.update(stack_mode=X.Above)
                try:
                    self.shield.configure(**kw)
                except Exception:  # noqa: BLE001 - frame gone: just raise
                    self.shield.configure(
                        x=x, y=y, width=max(1, w), height=max(1, h), stack_mode=X.Above
                    )
                if not self.mapped:
                    self.shield.map()
                    self.mapped = True
            elif self.mapped:
                self.shield.unmap()
                self.mapped = False
                self.pressed.clear()
            want_keys = show and self.target_front()
            if want_keys and not self.keys_grabbed:
                status = self.root.grab_keyboard(
                    False, X.GrabModeAsync, X.GrabModeAsync, X.CurrentTime
                )
                self.keys_grabbed = status == X.GrabSuccess
            elif not want_keys and self.keys_grabbed:
                self.d.ungrab_keyboard(X.CurrentTime)
                self.keys_grabbed = False
            self.d.sync()
        except Exception as exc:  # noqa: BLE001 - never leave a half-applied lock
            sys.stderr.write(f"pinlet.overlay: input lock: {exc}\n")
            self._release_all()

    def _release_all(self) -> None:
        try:
            if self.keys_grabbed:
                self.d.ungrab_keyboard(self.X.CurrentTime)
            if self.mapped:
                self.shield.unmap()
            self.d.sync()
        except Exception:  # noqa: BLE001
            pass
        self.keys_grabbed = False
        self.mapped = False

    # ------------------------------------------------------------- events
    def pump(self) -> None:
        """Handle every queued event (call when the connection is readable)."""
        X = self.X
        while self.d.pending_events():
            ev = self.d.next_event()
            if ev.type == X.ButtonPress:
                self._press(ev)
            elif ev.type == X.ButtonRelease:
                if ev.detail in self.pressed:
                    self.pressed.discard(ev.detail)
                    if self.insist.release(time.monotonic()):
                        self._insist(None)
            elif ev.type == X.KeyPress:
                mods = X.ControlMask | X.Mod1Mask
                if (ev.state & mods) == mods and ev.detail in self.hotkeys:
                    self.on_hotkey(self.hotkeys[ev.detail])
                else:
                    self.on_swallowed("key")

    def _press(self, ev) -> None:
        if ev.detail in _SCROLL_BUTTONS:
            self.on_swallowed("scroll")
            return
        self.pressed.add(ev.detail)
        if self.insist.press(time.monotonic()):
            self._insist((ev.root_x, ev.root_y, ev.detail))
            return
        self.on_swallowed("click")

    def _insist(self, replay: tuple[int, int, int] | None) -> None:
        """They mean it: hand over, and give the window the click it was denied."""
        self.on_insist()  # the owner pauses Pinlet, which disarms the lock
        self.armed = False
        self.pressed.clear()
        self._apply()
        if replay is None:
            return
        try:
            from Xlib.ext import xtest

            _x, _y, button = replay
            xtest.fake_input(self.d, self.X.ButtonPress, button)
            xtest.fake_input(self.d, self.X.ButtonRelease, button)
            self.d.sync()
        except Exception:  # noqa: BLE001 - the next click works anyway
            pass

    def tick(self) -> None:
        """From the helper's timer: a press held long enough is a take-over."""
        if self.pressed and self.insist.held(time.monotonic()):
            self._insist(None)

    def close(self) -> None:
        """Releases every grab and removes the shield window."""
        self._release_all()
        try:
            self.shield.destroy()
            self.d.close()
        except Exception:  # noqa: BLE001
            pass
