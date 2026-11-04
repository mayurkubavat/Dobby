"""HAL decorator that shows the overlay around real actions.

``OverlayHAL(inner, overlay)`` wraps any Dobby HAL. It forwards every call to
``inner`` and adds overlay side effects:

* before every action, :meth:`overlay.checkpoint` honours Stop, Pause and
  user take-over (it may wait, or raise ``OverlayStopped`` /
  ``OverlayInterrupted``);
* mouse actions: the agent pointer glides to the target with a verb
  ("Click", "Double-click", ...) and the helper boxes the element under it
  and names it ("Click 'Run Synthesis'"), then the real action fires, then a
  ripple and a numbered trail dot are shown;
* keyboard: the name tag says what is typed or pressed; dangerous shortcuts
  (quit, close, delete) turn the overlay red and ask Allow/Deny first;
* ``focus_window`` and clicks move the glow to the window being worked on;
* ``capture_screen``: the overlay is hidden for the capture, then a
  "looking" shimmer runs while Dobby reads the screen;
* ``list_windows``: overlay panels are filtered out.

Tool handlers that know more can say so with :meth:`OverlayHAL.step`::

    with hal.step("Run synthesis", risk="caution"):
        hal.mouse_click(x, y)

Anything not listed here is forwarded untouched through ``__getattr__``.
"""

from __future__ import annotations

import contextlib
import re
from typing import Any, Callable, Iterator, List, Optional, Sequence, Tuple

from .client import NullOverlay
from .grants import AppBlocked, AppGrants
from .protocol import OVERLAY_WINDOW_TITLE
from .style import action_text, app_label

ToPoints = Callable[[float, float], Tuple[float, float]]

_MOD_ALIASES = {
    "cmd": "cmd", "command": "cmd", "meta": "cmd", "super": "cmd", "win": "cmd",
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt", "option": "alt", "opt": "alt",
    "shift": "shift",
}
_MOD_SYMBOLS = {"ctrl": "⌃", "alt": "⌥", "shift": "⇧", "cmd": "⌘"}
_MOD_ORDER = ("ctrl", "alt", "shift", "cmd")

# (modifiers, key) combinations that close, quit, delete or force things.
_DANGER = {
    (frozenset({"cmd"}), "q"): "quit the app",
    (frozenset({"cmd"}), "w"): "close the window",
    (frozenset({"cmd", "alt"}), "w"): "close all windows",
    (frozenset({"cmd"}), "delete"): "move to Trash",
    (frozenset({"cmd"}), "backspace"): "move to Trash",
    (frozenset({"cmd", "shift"}), "delete"): "empty the Trash",
    (frozenset({"cmd", "shift"}), "backspace"): "empty the Trash",
    (frozenset({"cmd", "alt", "shift"}), "delete"): "empty the Trash",
    (frozenset({"cmd", "alt"}), "escape"): "force quit",
    (frozenset({"cmd", "alt"}), "esc"): "force quit",
    (frozenset({"ctrl", "cmd"}), "q"): "lock the screen",
    (frozenset({"cmd", "shift"}), "q"): "log out",
}


def _split_combo(keys: Sequence[str]) -> Tuple[frozenset, str]:
    mods, key = set(), ""
    for k in keys:
        name = str(k).strip().lower()
        if name in _MOD_ALIASES:
            mods.add(_MOD_ALIASES[name])
        else:
            key = name
    return frozenset(mods), key


def combo_label(keys: Sequence[str]) -> str:
    mods, key = _split_combo(keys)
    sym = "".join(_MOD_SYMBOLS[m] for m in _MOD_ORDER if m in mods)
    return sym + (key.upper() if len(key) == 1 else key.capitalize())


def danger_reason(keys: Sequence[str]) -> Optional[str]:
    return _DANGER.get(_split_combo(keys))


# Buttons whose click has effects that are hard to undo: sending, paying,
# deleting, publishing. A click on one asks Allow/Deny first, like ⌘Q does.
_CONSEQUENTIAL = re.compile(
    r"^\W*(send|submit|delete|remove|erase|discard|destroy|pay|buy|purchase|place order|check ?out|"
    r"confirm (purchase|payment|order)|publish|post|share|overwrite|replace( all)?|format|wipe|"
    r"empty (the )?trash|move to (the )?trash|uninstall|transfer|deploy|merge|force push|"
    r"sign out|log out|reset|revoke|unsubscribe|cancel (subscription|order|account)|close account)\b",
    re.I)
_PRESSABLE_ROLES = ("button", "menu item", "link", "check menu item", "radio menu item")
_EDITABLE_ROLES = ("text", "entry", "text field", "textfield", "search field", "combo box", "editbar",
                   "password text", "terminal", "document text", "paragraph", "text area")


def consequential_label(role: Optional[str], name: Optional[str]) -> Optional[str]:
    """The button text when clicking it is a consequential step, else None."""
    name = " ".join(str(name or "").split())
    role = str(role or "").lower()
    if not name or len(name) > 48:
        return None
    if not any(r in role for r in _PRESSABLE_ROLES):
        return None
    return name if _CONSEQUENTIAL.match(name) else None


_SECRETISH = re.compile(r"^\S{8,}$")


def typed_label(text: str, limit: int = 24) -> str:
    """Intent text for typing; hides strings that look like passwords/tokens."""
    one_line = " ".join(text.split())
    classes = sum(bool(re.search(p, one_line)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^\w\s]"))
    if _SECRETISH.match(one_line) and classes >= 3:
        return f"Type •••• ({len(text)} characters)"
    if len(one_line) > limit:
        one_line = one_line[: limit - 1] + "…"
    return f"Type “{one_line}”"


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


_VERBS = {("left", 1): "Click", ("left", 2): "Double-click", ("left", 3): "Triple-click",
          ("right", 1): "Right-click", ("middle", 1): "Middle-click"}


class OverlayHAL:
    def __init__(self, inner: Any, overlay: Optional[NullOverlay] = None,
                 to_points: Optional[ToPoints] = None, confirm_danger: bool = True,
                 grants: Optional[AppGrants] = None,
                 hit_test: Optional[Callable[[int, int], Optional[dict]]] = None,
                 confirm_clicks: bool = True) -> None:
        self._inner = inner
        self.overlay = overlay or NullOverlay()
        # Which apps Dobby may use (None: no check; wrap_hal passes one).
        self.grants = grants
        # Accessibility hit test (x, y) -> {"role", "name", "bbox", "secure"}:
        # names the element a click lands on, for Allow/Deny and the bar.
        self._hit_test = hit_test
        self._confirm_clicks = confirm_clicks
        self._field: Optional[dict] = None  # last element clicked, for "Type … into ‘Search’"
        # HAL coordinates -> global points. Identity when the HAL already
        # works in points (Quartz events do); pass a converter otherwise.
        self._to_points: ToPoints = to_points or (lambda x, y: (x, y))
        self._tracked: Optional[str] = None
        self._confirm_danger = confirm_danger
        self._in_step = 0

    @property
    def inner(self) -> Any:
        return self._inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def set_hit_test(self, hit_test: Optional[Callable[[int, int], Optional[dict]]]) -> None:
        """Give the overlay an accessibility hit test (the server does, once perception is up)."""
        self._hit_test = hit_test

    # ------------------------------------------------------------- helpers
    def _window_app_at(self, x: float, y: float) -> Optional[str]:
        """The app whose window is on top at HAL point (x, y), or the active app."""
        lister = getattr(self._inner, "list_windows", None)
        if lister is not None:
            try:
                for w in lister():  # front to back
                    title = str(getattr(w, "title", "") or "")
                    if title.startswith(OVERLAY_WINDOW_TITLE):
                        continue
                    b = getattr(w, "bbox", None)
                    if b is None:
                        continue
                    if b.x <= x < b.x + b.width and b.y <= y < b.y + b.height:
                        return getattr(w, "app_name", None) or None
            except Exception:  # noqa: BLE001 - fall back to the active window
                pass
        return self._active_app()

    def _active_app(self) -> Optional[str]:
        getter = getattr(self._inner, "get_active_window", None)
        if getter is None:
            return None
        try:
            win = getter()
        except Exception:  # noqa: BLE001
            return None
        return (getattr(win, "app_name", None) or None) if win is not None else None

    def _ask_access(self, app: str, detail: str) -> bool:
        name = app_label(app) or app
        return self.overlay.confirm(f"Let Dobby use {name}?", secs=60.0, detail=detail,
                                    allow="Allow", deny="Don’t Allow", kind="access")

    def _gate(self, app: Optional[str], action: str) -> None:
        """Per-app grants: ask once per app, refuse what its tier forbids."""
        if self.grants is None or not app:
            return
        from .client import OverlayBlocked

        try:
            self.grants.check(app, action, self._ask_access)
        except AppBlocked as exc:
            self.overlay.intent(f"Not allowed in {app_label(app) or app}", ms=2500)
            raise OverlayBlocked(str(exc), app=app, tier=exc.tier) from None

    def _probe(self, x: float, y: float) -> Optional[dict]:
        if self._hit_test is None:
            return None
        try:
            return self._hit_test(int(x), int(y))
        except Exception:  # noqa: BLE001 - naming is best effort
            return None

    def _confirm_consequential(self, hit: Optional[dict], verb: str, app: Optional[str]) -> None:
        """Allow/Deny before pressing Send, Delete, Pay and the like."""
        from .client import OverlayDenied

        if not self._confirm_clicks or hit is None:
            return
        label = consequential_label(hit.get("role"), hit.get("name"))
        if label is None:
            return
        where = app_label(app)
        text = f"{verb.lower()} ‘{label}’" + (f" in {where}" if where else "")
        self.overlay.risk("danger")
        self.overlay.intent(action_text(verb, label, app), ms=60000)
        try:
            if not self.overlay.confirm(f"Allow Dobby to {text}?",
                                        detail="This step may send, pay, publish or delete something."):
                raise OverlayDenied(f"The user denied: {text}")
        finally:
            self.overlay.risk("ok")
            self.overlay.intent("", ms=0)

    def _type_target(self) -> Optional[dict]:
        """The field Dobby clicked last, if it takes text."""
        field = self._field
        if field is None:
            return None
        role = str(field.get("role") or "").lower()
        return field if field.get("secure") or any(r in role for r in _EDITABLE_ROLES) else None

    # ------------------------------------------------------------- helpers
    def _follow_active_window(self) -> None:
        """Move the glow to whatever window is active after an action."""
        getter = getattr(self._inner, "get_active_window", None)
        if getter is None:
            return
        try:
            win = getter()
        except Exception:  # noqa: BLE001 - glow is cosmetic
            return
        wid = getattr(win, "window_id", None) if win is not None else None
        if wid is not None and str(wid) != self._tracked:
            self._tracked = str(wid)
            self.overlay.track(window_id=wid)

    def _reclaim_target(self) -> None:
        """Input lock: bring Dobby's window back to the front before acting.

        With the lock on, the person may click into other apps while Dobby
        works. Dobby still drives the real mouse and keyboard, so before its
        next step it puts its own window back in front; otherwise a click
        could land on the person's window or keys go to their app.
        """
        if not getattr(self.overlay, "lock_on", False) or self._tracked is None:
            return
        getter = getattr(self._inner, "get_active_window", None)
        focus = getattr(self._inner, "focus_window", None)
        if getter is None or focus is None:
            return
        try:
            win = getter()
        except Exception:  # noqa: BLE001 - best effort
            return
        wid = getattr(win, "window_id", None) if win is not None else None
        if wid is not None and str(wid) == self._tracked:
            return
        try:
            focus(self._tracked)
        except Exception:  # noqa: BLE001 - the step still runs; the AI looks again on failure
            pass

    def _background_click(self, x: int, y: int, button: str, click_count: int, verb: str,
                          app: Optional[str] = None) -> bool:
        """Input lock: press a button through accessibility, without the real mouse.

        Only for a plain left click in Dobby's own window when the HAL can do
        it (``background_click``). Dobby's drawn pointer still glides there and
        ripples; the person's pointer and front app stay as they are. False:
        nothing was pressed, click with the real mouse instead.
        """
        if not getattr(self.overlay, "background_on", False) or self._tracked is None:
            return False
        if button != "left" or click_count != 1:
            return False
        press = getattr(self._inner, "background_click", None)
        if press is None:
            return False
        self._point_to(x, y, verb=verb, probe=True, app=app)
        try:
            done = bool(press(x, y, self._tracked))
        except Exception:  # noqa: BLE001 - fall back to the real mouse
            done = False
        if done:
            self.overlay.click(button=button, count=click_count)
        return done

    def _after_keys(self) -> None:
        """Input lock: a key may switch windows (new window, app switch): follow it."""
        if getattr(self.overlay, "lock_on", False):
            self._follow_active_window()

    def _point_to(self, x: float, y: float, ms: Optional[int] = None, wait: bool = True,
                  verb: Optional[str] = None, probe: bool = False, app: Optional[str] = None) -> None:
        px, py = self._to_points(x, y)
        # Inside a step() the tool already set the intent text; still box it.
        if app:
            self.overlay.move(px, py, ms=ms, wait=wait, verb=None if self._in_step else verb, probe=probe,
                              app=app)
        else:
            self.overlay.move(px, py, ms=ms, wait=wait, verb=None if self._in_step else verb, probe=probe)

    def _intent(self, text: str) -> None:
        if not self._in_step:
            self.overlay.intent(text)

    @contextlib.contextmanager
    def _passing(self) -> Iterator[None]:
        """Let this action's real input past the input lock (X11 shield)."""
        self.overlay.input_pass(True)
        try:
            yield
        finally:
            self.overlay.input_pass(False)

    @contextlib.contextmanager
    def step(self, text: str, risk: Optional[str] = None, confirm: bool = False) -> Iterator[None]:
        """Name the next action(s) for the overlay; optionally colour by risk.

        With ``confirm=True`` (or risk "danger") the user is asked Allow/Deny
        first; a deny raises ``OverlayDenied`` (an ``OverlayStopped``).
        """
        from .client import OverlayDenied

        self.overlay.checkpoint()
        self.overlay.intent(text, ms=60000)
        if risk:
            self.overlay.risk(risk)
        try:
            if confirm or risk == "danger":
                if not self.overlay.confirm(f"Allow Dobby to {text[:1].lower() + text[1:]}?"):
                    raise OverlayDenied(f"The user denied: {text}")
            self._in_step += 1
            try:
                yield
            finally:
                self._in_step -= 1
        finally:
            if risk and risk != "ok":
                self.overlay.risk("ok")
            self.overlay.intent("", ms=0)

    # --------------------------------------------------------------- mouse
    def mouse_move(self, x: int, y: int, *args: Any, **kwargs: Any) -> Any:
        self.overlay.checkpoint()
        self._point_to(x, y, verb="Move")
        with self._passing():
            return self._inner.mouse_move(x, y, *args, **kwargs)

    def mouse_click(self, x: int, y: int, button: str = "left", click_count: int = 1,
                    *args: Any, **kwargs: Any) -> Any:
        self.overlay.checkpoint()
        verb = _VERBS.get((button, click_count), "Click")
        app = self._window_app_at(x, y)
        self._gate(app, "click")
        hit = self._probe(x, y)
        self._field = hit
        self._confirm_consequential(hit, verb, app)
        if self._background_click(x, y, button, click_count, verb, app=app):
            return None
        self._reclaim_target()
        self._point_to(x, y, verb=verb, probe=True, app=app)
        with self._passing():
            result = self._inner.mouse_click(x, y, button, click_count, *args, **kwargs)
        self.overlay.click(button=button, count=click_count)
        self._follow_active_window()
        return result

    def mouse_drag(self, start_x: int, start_y: int, end_x: int, end_y: int,
                   duration_sec: float = 0.3, *args: Any, **kwargs: Any) -> Any:
        self.overlay.checkpoint()
        app = self._window_app_at(start_x, start_y)
        self._gate(app, "drag")
        self._reclaim_target()
        self._point_to(start_x, start_y, verb="Drag", probe=True, app=app)
        # Follow the drag in parallel with the real one.
        self._point_to(end_x, end_y, ms=int(duration_sec * 1000), wait=False)
        with self._passing():
            result = self._inner.mouse_drag(start_x, start_y, end_x, end_y, duration_sec, *args, **kwargs)
        self.overlay.clear_box()
        return result

    def mouse_scroll(self, dx: int, dy: int, *args: Any, **kwargs: Any) -> Any:
        self.overlay.checkpoint()
        self._reclaim_target()
        app = self._active_app()
        self._gate(app, "scroll")
        direction = ("down" if dy < 0 else "up") if dy else ("right" if dx > 0 else "left")
        self._intent(action_text(f"Scroll {direction}", None, app))
        with self._passing():
            return self._inner.mouse_scroll(dx, dy, *args, **kwargs)

    # ------------------------------------------------------------ keyboard
    def keyboard_type(self, text: str, *args: Any, **kwargs: Any) -> Any:
        from .client import OverlayBlocked

        self.overlay.checkpoint()
        self._reclaim_target()
        app = self._active_app()
        self._gate(app, "type")
        field = self._type_target()
        if field is not None and field.get("secure"):
            self.overlay.intent("Your turn: type the password", ms=4000)
            raise OverlayBlocked("This is a password field. Dobby does not type passwords: ask the person "
                                 "to type it, then carry on.", app=app or "")
        label = typed_label(text)
        if field is not None and field.get("name"):
            label = f"{label} into ‘{_clip(field['name'], 20)}’"
        self._intent(label)
        self.overlay.state("typing")
        try:
            with self._passing():
                return self._inner.keyboard_type(text, *args, **kwargs)
        finally:
            self.overlay.state("idle")

    def keyboard_press_key(self, key_name: str, *args: Any, **kwargs: Any) -> Any:
        self.overlay.checkpoint()
        self._reclaim_target()
        app = self._active_app()
        self._gate(app, "keys")
        self._intent(action_text(f"Press {combo_label([key_name])}", None, app))
        try:
            with self._passing():
                return self._inner.keyboard_press_key(key_name, *args, **kwargs)
        finally:
            self._after_keys()

    def keyboard_hotkey(self, keys: List[str], *args: Any, **kwargs: Any) -> Any:
        from .client import OverlayDenied

        self.overlay.checkpoint()
        self._reclaim_target()
        app = self._active_app()
        self._gate(app, "keys")
        label = combo_label(keys)
        reason = danger_reason(keys) if self._confirm_danger else None
        if reason is None:
            self._intent(action_text(f"Press {label}", None, app))
            try:
                with self._passing():
                    return self._inner.keyboard_hotkey(keys, *args, **kwargs)
            finally:
                self._after_keys()
        self.overlay.risk("danger")
        self.overlay.intent(f"Press {label} ({reason})", ms=60000)
        try:
            if not self.overlay.confirm(f"Allow Dobby to press {label} and {reason}?"):
                raise OverlayDenied(f"The user denied {label} ({reason})")
            with self._passing():
                return self._inner.keyboard_hotkey(keys, *args, **kwargs)
        finally:
            self._after_keys()
            self.overlay.risk("ok")
            self.overlay.intent("", ms=0)

    # ------------------------------------------------------------- windows
    def focus_window(self, window_id: str, *args: Any, **kwargs: Any) -> Any:
        self.overlay.checkpoint()
        ok = self._inner.focus_window(window_id, *args, **kwargs)
        if ok:
            self._field = None
            self._tracked = str(window_id)
            self.overlay.track(window_id=window_id)
        return ok

    def list_windows(self, *args: Any, **kwargs: Any) -> List[Any]:
        windows = self._inner.list_windows(*args, **kwargs)
        # MacHAL already keeps only layer-0 windows (the overlay sits far above),
        # so this is a second line of defence for other HALs.
        return [w for w in windows if not str(getattr(w, "title", "") or "").startswith(OVERLAY_WINDOW_TITLE)]

    # ------------------------------------------------------------- capture
    def capture_screen(self, *args: Any, **kwargs: Any) -> Any:
        if not self.overlay.active:
            return self._inner.capture_screen(*args, **kwargs)
        self.overlay.hide()
        try:
            return self._inner.capture_screen(*args, **kwargs)
        finally:
            self.overlay.show()
            region = kwargs.get("region", args[0] if args else None)
            rect = None
            if region:
                x, y, w, h = region
                px, py = self._to_points(x, y)
                qx, qy = self._to_points(x + w, y + h)
                rect = (px, py, qx - px, qy - py)
            self.overlay.scan(rect)  # helper uses the glowing window when rect is None
            self.overlay.state("thinking")


def unwrap(hal: Any) -> Any:
    """Return the innermost HAL (for isinstance checks against concrete HALs)."""
    while isinstance(hal, OverlayHAL):
        hal = hal.inner
    return hal
