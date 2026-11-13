"""HAL decorator that shows the overlay around real actions.

``OverlayHAL(inner, overlay)`` wraps any Pinlet HAL. It forwards every call to
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
  "looking" shimmer runs while Pinlet reads the screen;
* ``list_windows``: overlay panels are filtered out.

Tool handlers that know more can say so with :meth:`OverlayHAL.step`::

    with hal.step("Run synthesis", risk="caution"):
        hal.mouse_click(x, y)

Anything not listed here is forwarded untouched through ``__getattr__``.
"""

from __future__ import annotations

import contextlib
import inspect
import os
import re
from collections.abc import Callable, Iterator, Sequence
from typing import Any

from pinlet.envflags import is_false

from .client import NullOverlay
from .grants import AppBlocked, AppGrants
from .protocol import OVERLAY_WINDOW_TITLE
from .style import action_text, app_label
from .text import short as _clip

ToPoints = Callable[[float, float], tuple[float, float]]

_MOD_ALIASES = {
    "cmd": "cmd",
    "command": "cmd",
    "meta": "cmd",
    "super": "cmd",
    "win": "cmd",
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "option": "alt",
    "opt": "alt",
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


def _split_combo(keys: Sequence[str]) -> tuple[frozenset, str]:
    mods, key = set(), ""
    for k in keys:
        name = str(k).strip().lower()
        if name in _MOD_ALIASES:
            mods.add(_MOD_ALIASES[name])
        else:
            key = name
    return frozenset(mods), key


def combo_label(keys: Sequence[str]) -> str:
    """Readable label for a key combo, e.g. ``⌘⇧S``."""
    mods, key = _split_combo(keys)
    sym = "".join(_MOD_SYMBOLS[m] for m in _MOD_ORDER if m in mods)
    return sym + (key.upper() if len(key) == 1 else key.capitalize())


def danger_reason(keys: Sequence[str]) -> str | None:
    """Why a hotkey is dangerous (e.g. it quits or deletes), or None for a safe one."""
    return _DANGER.get(_split_combo(keys))


# Buttons whose click has effects that are hard to undo: sending, paying,
# deleting, publishing. A click on one asks Allow/Deny first, like ⌘Q does.
_CONSEQUENTIAL = re.compile(
    r"^\W*(send|submit|delete|remove|erase|discard|destroy|pay|buy|purchase|place order|check ?out|"
    r"confirm (purchase|payment|order)|publish|post|share|overwrite|replace( all)?|format|wipe|"
    r"empty (the )?trash|move to (the )?trash|uninstall|transfer|deploy|merge|force push|"
    r"sign out|log out|reset|revoke|unsubscribe|cancel (subscription|order|account)|close account)\b",
    re.I,
)
# Buttons that spend money. Pinlet never clicks these, in any mode: paying is
# the person's step (``PINLET_BLOCK_PAYMENTS=0`` lifts the rule).
_PAYMENT = re.compile(
    r"^\W*(pay|buy|purchase|place (your |my )?order|order now|complete (your |my )?(order|purchase|"
    r"payment)|confirm (and pay|purchase|payment|order)|check ?out|proceed to (checkout|payment)|"
    r"subscribe|donate|send money|add funds|top up|transfer (money|funds)|authorize payment)\b",
    re.I,
)
_PRESSABLE_ROLES = ("button", "menu item", "link", "check menu item", "radio menu item")
# A text box where Return sends what was typed (a chat, a reply, a comment).
# Pressing Return there is a Send, so it asks like the Send button does.
_RETURN_SENDS = re.compile(
    r"\b(message|reply|comment|chat|post|tweet|send|compose|write to|say something|"
    r"type a message|new message|direct message|dm)\b",
    re.I,
)


def return_sends(key_name: str | None, field: dict | None) -> str | None:
    """The field's name when Return in it would send something, else None."""
    if str(key_name or "").strip().lower() not in ("return", "enter", "kp_enter"):
        return None
    name = " ".join(str((field or {}).get("name") or "").split())
    return name if name and _RETURN_SENDS.search(name) else None


_EDITABLE_ROLES = (
    "text",
    "entry",
    "text field",
    "textfield",
    "search field",
    "combo box",
    "editbar",
    "password text",
    "terminal",
    "document text",
    "paragraph",
    "text area",
)


def consequential_label(role: str | None, name: str | None) -> str | None:
    """The button text when clicking it is a consequential step, else None."""
    name = " ".join(str(name or "").split())
    role = str(role or "").lower()
    if not name or len(name) > 48:
        return None
    if not any(r in role for r in _PRESSABLE_ROLES):
        return None
    return name if _CONSEQUENTIAL.match(name) else None


def payment_label(role: str | None, name: str | None) -> str | None:
    """The button text when clicking it would pay for something, else None."""
    name = " ".join(str(name or "").split())
    role = str(role or "").lower()
    if not name or len(name) > 48:
        return None
    if not any(r in role for r in _PRESSABLE_ROLES):
        return None
    return name if _PAYMENT.match(name) else None


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


_VERBS = {
    ("left", 1): "Click",
    ("left", 2): "Double-click",
    ("left", 3): "Triple-click",
    ("right", 1): "Right-click",
    ("middle", 1): "Middle-click",
}


class OverlayHAL:
    """HAL wrapper that shows every action on the overlay and asks Allow/Deny when needed.

    It moves the drawn pointer before each input, checks app grants, waits while
    the person has paused Pinlet, and asks before risky hotkeys or buttons. Every
    other attribute is passed through to the wrapped HAL.
    """

    def __init__(
        self,
        inner: Any,
        overlay: NullOverlay | None = None,
        to_points: ToPoints | None = None,
        confirm_danger: bool = True,
        grants: AppGrants | None = None,
        hit_test: Callable[[int, int], dict | None] | None = None,
        confirm_clicks: bool = True,
        secure: bool = False,
        policy: Callable[[], bool] | None = None,
        access_policy: Callable[[], bool] | None = None,
    ) -> None:
        self._inner = inner
        self.overlay = overlay or NullOverlay()
        # Which apps Pinlet may use (None: no check; wrap_hal passes one).
        self.grants = grants
        # Accessibility hit test (x, y) -> {"role", "name", "bbox", "secure"}:
        # names the element a click lands on, for Allow/Deny and the bar.
        self._hit_test = hit_test
        # Secure mode (``pinlet --secure``): Allow/Deny for Send/Delete-like
        # buttons and no typing into password fields. Off in turbo.
        self._secure = secure
        # policy() -> True when the current call is in secure mode. Set by
        # wrap_hal so one shared Pinlet can serve turbo and secure clients;
        # None keeps the fixed ``secure`` / ``confirm_danger`` flags.
        self._policy = policy
        # access_policy() -> True when the current call asks "Let Pinlet use …?"
        # per app (secure, not careful). None: the same as ``secure``.
        self._access_policy = access_policy
        self._turbo_sent: bool | None = None
        from .pace import Pacer

        self._pacer = Pacer()
        self._pace = None
        self._confirm_clicks = confirm_clicks
        self._field: dict | None = None  # last element clicked, for "Type … into ‘Search’"
        # HAL coordinates -> global points. Identity when the HAL already
        # works in points (Quartz events do); pass a converter otherwise.
        self._to_points: ToPoints = to_points or (lambda x, y: (x, y))
        self._tracked: str | None = None
        self._confirm_danger = confirm_danger
        self._in_step = 0
        self._block_payments = not is_false(os.environ, "PINLET_BLOCK_PAYMENTS")

    @property
    def inner(self) -> Any:
        """The wrapped HAL."""
        return self._inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    @property
    def secure(self) -> bool:
        """True when the current call runs in secure mode.

        With a policy callback it asks the callback (failing safe to True); otherwise
        it uses the fixed flag.
        """
        if self._policy is not None:
            try:
                return bool(self._policy())
            except Exception:  # noqa: BLE001 - fail safe
                return True
        return self._secure

    @secure.setter
    def secure(self, value: bool) -> None:
        self._secure, self._policy = bool(value), None

    @property
    def asks_app_access(self) -> bool:
        """True when the current call asks before using each app (secure mode)."""
        if self._access_policy is not None:
            try:
                return bool(self._access_policy())
            except Exception:  # noqa: BLE001 - fail safe
                return True
        return self.secure

    def _danger_prompts(self) -> bool:
        return self._confirm_danger and (self._policy is None or self.secure)

    def _sync_turbo(self) -> None:
        """Tells the helper whether this call is turbo before acting.

        In a shared Pinlet, clients may differ: turbo means no prompts and no take-over pause.
        """
        if self._policy is None:
            return
        turbo = not self.secure
        if turbo != self._turbo_sent:
            self._turbo_sent = turbo
            setter = getattr(self.overlay, "set_turbo", None)
            if setter is not None:
                setter(turbo)

    def set_hit_test(self, hit_test: Callable[[int, int], dict | None] | None) -> None:
        """Give the overlay an accessibility hit test (the server does, once perception is up)."""
        self._hit_test = hit_test

    # ------------------------------------------------------------- helpers
    def _window_app_at(self, x: float, y: float) -> str | None:
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

    def _active_app(self) -> str | None:
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
        return self.overlay.confirm(
            f"Let Pinlet use {name}?",
            secs=60.0,
            detail=detail,
            allow="Allow",
            deny="Don’t Allow",
            kind="access",
            app=app,
        )

    def _gate(self, app: str | None, action: str) -> None:
        """Per-app grants: ask once per app, refuse what its tier forbids."""
        if self.grants is None or not app:
            return
        if not self.asks_app_access and self.grants.admin(app) is None:
            return  # turbo and careful: only the admin's app tiers apply
        from .client import OverlayBlocked

        try:
            self.grants.check(app, action, self._ask_access)
        except AppBlocked as exc:
            self.overlay.intent(f"Not allowed in {app_label(app) or app}", ms=2500)
            raise OverlayBlocked(str(exc), app=app, tier=exc.tier) from None

    # ------------------------------------------------- locked screen, pace
    def _before_action(self) -> None:
        """Every action starts here: never on a locked screen, and never faster than the pace."""
        locked = getattr(self._inner, "screen_locked", None)
        try:
            is_locked = bool(locked()) if locked is not None else False
        except Exception:  # noqa: BLE001 - cannot tell: carry on
            is_locked = False
        if is_locked:
            self._refuse(
                "The screen is locked. Pinlet does nothing on a locked screen: wait for the "
                "person to unlock it (wait_for_wake), then carry on.",
                say="Screen locked: Pinlet waits",
            )
        from . import pace

        self._pace = pace.current()
        self._pacer.before(self._pace)

    def _dwell(self) -> None:
        """Human pace: let the person see the target (or read the step) before it happens."""
        pace = getattr(self, "_pace", None)
        if pace is not None:
            self._pacer.dwell(pace.quiet_dwell_s if self._quiet else pace.dwell_s)

    # ---------------------------------------------------------- off limits
    def _own_pids(self) -> set[int]:
        from . import offlimits

        pids = {os.getpid(), offlimits.app_pid(), getattr(self.overlay, "pid", None)}
        return {int(p) for p in pids if p}

    def _refuse(self, why: str, app: str | None = None, say: str = "Off limits for Pinlet") -> None:
        from .client import OverlayBlocked

        self.overlay.intent(say, ms=4000)
        raise OverlayBlocked(why, app=app or "")

    def _guard_point(self, x: float, y: float) -> None:
        """Refuse a click or drag that would land on Pinlet itself or a macOS security prompt.

        Holds at every asking level. A HAL that cannot list every window
        (Linux, tests) is not checked.
        """
        from . import offlimits

        lister = getattr(self._inner, "window_records", None)
        if lister is None or self._quiet:
            return  # background mode acts on the agent's own window only, through accessibility
        try:
            px, py = self._to_points(x, y)
            found = offlimits.refusal_at(
                lister(), px, py, self._own_pids(), getattr(self.overlay, "pid", None)
            )
        except Exception:  # noqa: BLE001 - a listing error must not stop ordinary work
            return
        if found:
            self._refuse(found[0], app=found[1])

    def _guard_keys(self) -> None:
        """Refuse keys while Pinlet.app or a macOS security prompt is the frontmost app."""
        from . import offlimits

        front = getattr(self._inner, "frontmost_app", None)
        if front is None or self._quiet:
            return
        try:
            pid, name = front()
            why = offlimits.refusal({"pid": pid, "owner": name, "title": ""}, self._own_pids())
        except Exception:  # noqa: BLE001
            return
        if why:
            self._refuse(why, app=name)

    def _probe(self, x: float, y: float) -> dict | None:
        if self._hit_test is None:
            return None
        try:
            return self._hit_test(int(x), int(y))
        except Exception:  # noqa: BLE001 - naming is best effort
            return None

    def _refuse_payment(self, hit: dict | None, app: str | None) -> None:
        """Pinlet does not click buttons that pay, whatever the mode.

        Raises:
            OverlayBlocked: The click would land on a Pay / Buy / Place order button.
        """
        from .client import OverlayBlocked

        if not self._block_payments or hit is None:
            return
        label = payment_label(hit.get("role"), hit.get("name"))
        if label is None:
            return
        self.overlay.intent(f"Your turn: ‘{_clip(label, 24)}’ pays", ms=4000)
        raise OverlayBlocked(
            f"‘{label}’ looks like a payment button. Pinlet does not click payment buttons: "
            "tell the person everything is ready and ask them to click it themselves.",
            app=app or "",
        )

    def _confirm_consequential(self, hit: dict | None, verb: str, app: str | None) -> None:
        """Allow/Deny before pressing Send, Delete, Pay and the like."""
        from .client import OverlayDenied

        if not (self.secure and self._confirm_clicks) or hit is None:
            return
        label = consequential_label(hit.get("role"), hit.get("name"))
        if label is None:
            return
        from . import rules

        kind = rules.kind_of(label)
        if self._allowed_by_rule(kind, app):
            return
        where = app_label(app)
        text = f"{verb.lower()} ‘{label}’" + (f" in {where}" if where else "")
        self.overlay.risk("danger")
        self.overlay.intent(action_text(verb, label, app), ms=60000)
        try:
            if not self._ask(
                f"Allow Pinlet to {text}?",
                detail="This step may send, publish or delete something.",
                app=where or app or "",
                action=kind,
                image=self._target_picture(hit),
            ):
                raise OverlayDenied(f"The user denied: {text}")
        finally:
            self.overlay.risk("ok")
            self.overlay.intent("", ms=0)

    def _confirm_return(self, key_name: str, app: str | None) -> None:
        """Allow/Deny before Return in a message box: there, Return is the Send button."""
        from .client import OverlayDenied

        if not (self.secure and self._confirm_clicks):
            return
        box = return_sends(key_name, self._type_target())
        if box is None or self._allowed_by_rule("send", app):
            return
        where = app_label(app)
        text = f"press Return in ‘{_clip(box, 24)}’" + (f" in {where}" if where else "")
        self.overlay.risk("danger")
        self.overlay.intent(f"Press Return to send in ‘{_clip(box, 24)}’", ms=60000)
        try:
            if not self._ask(
                f"Allow Pinlet to {text}?",
                detail="Return sends what was typed here.",
                app=where or app or "",
                action="send",
            ):
                raise OverlayDenied(f"The user denied: {text}")
        finally:
            self.overlay.risk("ok")
            self.overlay.intent("", ms=0)

    def _allowed_by_rule(self, kind: str, app: str | None) -> bool:
        """True when the person's saved "don't ask again" covers this step; says so on the bar."""
        from . import rules

        try:
            pid, agent, _client = rules.asker()
            rule = rules.book().match(kind, app_label(app) or app, agent, pid)
        except Exception:  # noqa: BLE001 - when in doubt, ask
            return False
        if rule is None:
            return False
        self.overlay.intent(f"Allowed by your rule: {rule.describe()}", ms=3000)
        return True

    def _ask(self, text: str, **extra: Any) -> bool:
        """``overlay.confirm`` with the extras this overlay understands (older ones take fewer)."""
        try:
            params = inspect.signature(self.overlay.confirm).parameters
        except (TypeError, ValueError):
            params = {}
        if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
            extra = {k: v for k, v in extra.items() if k in params}
        return self.overlay.confirm(text, **extra)

    def _target_picture(self, hit: dict | None) -> str:
        """A small PNG of the control about to be pressed, base64, for the card ("" when none)."""
        box = (hit or {}).get("bbox")
        try:
            if isinstance(box, dict):
                x, y, w, h = (int(box[k]) for k in ("x", "y", "width", "height"))
            else:
                x, y, w, h = (int(v) for v in box)  # type: ignore[union-attr]
            if w <= 0 or h <= 0:
                return ""
            pad_x, pad_y = max(40, (320 - w) // 2), max(24, (120 - h) // 2)
            region = (max(0, x - pad_x), max(0, y - pad_y), w + 2 * pad_x, h + 2 * pad_y)
            image = self._inner.capture_screen(region=region)
            if image.width > 640:
                image = image.resize((640, max(1, image.height * 640 // image.width)))
            import base64
            import io

            out = io.BytesIO()
            image.convert("RGB").save(out, format="PNG", optimize=True)
            return base64.b64encode(out.getvalue()).decode()
        except Exception:  # noqa: BLE001 - the picture is a nicety
            return ""

    def _type_target(self) -> dict | None:
        """The field Pinlet clicked last, if it takes text."""
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
            if not self._quiet:
                self.overlay.track(window_id=wid)

    def _reclaim_target(self) -> None:
        """Input lock: bring Pinlet's window back to the front before acting.

        With the lock on, the person may click into other apps while Pinlet
        works. Pinlet still drives the real mouse and keyboard, so before its
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

    def _background_click(
        self, x: int, y: int, button: str, click_count: int, verb: str, app: str | None = None
    ) -> bool:
        """Input lock: press a button through accessibility, without the real mouse.

        Only for a plain left click in Pinlet's own window when the HAL can do
        it (``background_click``). Pinlet's drawn pointer still glides there and
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
        self._dwell()
        try:
            done = bool(press(x, y, self._tracked))
        except Exception:  # noqa: BLE001 - fall back to the real mouse
            done = False
        if done:
            self.overlay.click(button=button, count=click_count)
            self._pacer.done()
        return done

    def _after_keys(self) -> None:
        """Input lock: a key may switch windows (new window, app switch): follow it."""
        if getattr(self.overlay, "lock_on", False):
            self._follow_active_window()

    @property
    def _quiet(self) -> bool:
        """Background mode: draw nothing over the person's screen but the bar."""
        return bool(getattr(self.overlay, "config", {}).get("background"))

    def _point_to(
        self,
        x: float,
        y: float,
        ms: int | None = None,
        wait: bool = True,
        verb: str | None = None,
        probe: bool = False,
        app: str | None = None,
    ) -> None:
        if self._quiet:
            # The agent's window may be on another Desktop: a pointer drawn at
            # this spot would land on whatever the person is looking at.
            if verb and not self._in_step:
                self.overlay.intent(action_text(verb, None, app))
            return
        px, py = self._to_points(x, y)
        # Inside a step() the tool already set the intent text; still box it.
        if app:
            self.overlay.move(
                px, py, ms=ms, wait=wait, verb=None if self._in_step else verb, probe=probe, app=app
            )
        else:
            self.overlay.move(
                px, py, ms=ms, wait=wait, verb=None if self._in_step else verb, probe=probe
            )

    def _intent(self, text: str) -> None:
        if not self._in_step:
            self.overlay.intent(text)

    @contextlib.contextmanager
    def _cue(self, state: str) -> Iterator[None]:
        """Badge Pinlet's pointer for this kind of input (keys, scrolling, dragging).

        The helper also brings the pointer on screen where the real one is, so
        every action shows where it lands, not only clicks.
        """
        self.overlay.state(state)
        try:
            yield
        finally:
            self.overlay.state("idle")

    @contextlib.contextmanager
    def _passing(self) -> Iterator[None]:
        """Let this action's real input past the input lock (X11 shield)."""
        self._dwell()
        self.overlay.input_pass(True)
        try:
            yield
        finally:
            self.overlay.input_pass(False)
            self._pacer.done()

    @contextlib.contextmanager
    def step(self, text: str, risk: str | None = None, confirm: bool = False) -> Iterator[None]:
        """Name the next action(s) for the overlay; optionally colour by risk.

        With ``confirm=True`` (or risk "danger") the user is asked Allow/Deny
        first; a deny raises ``OverlayDenied`` (an ``OverlayStopped``).
        """
        from .client import OverlayDenied

        self._sync_turbo()
        self.overlay.checkpoint()
        self._before_action()
        self.overlay.intent(text, ms=60000)
        if risk:
            self.overlay.risk(risk)
        try:
            if confirm or risk == "danger":
                if not self.overlay.confirm(f"Allow Pinlet to {text[:1].lower() + text[1:]}?"):
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
        """Shows the move on the overlay, then moves the real pointer."""
        self._sync_turbo()
        self.overlay.checkpoint()
        self._before_action()
        self._point_to(x, y, verb="Move")
        with self._passing():
            return self._inner.mouse_move(x, y, *args, **kwargs)

    def mouse_click(
        self, x: int, y: int, button: str = "left", click_count: int = 1, *args: Any, **kwargs: Any
    ) -> Any:
        """Checks the app and target, shows the click, asks if risky, then clicks."""
        self._sync_turbo()
        self.overlay.checkpoint()
        self._before_action()
        verb = _VERBS.get((button, click_count), "Click")
        self._guard_point(x, y)
        app = self._window_app_at(x, y)
        self._gate(app, "click")
        hit = self._probe(x, y)
        self._field = hit
        self._refuse_payment(hit, app)
        self._confirm_consequential(hit, verb, app)
        if self._background_click(x, y, button, click_count, verb, app=app):
            return None
        self._reclaim_target()
        self._point_to(x, y, verb=verb, probe=True, app=app)
        with self._passing():
            result = self._inner.mouse_click(x, y, button, click_count, *args, **kwargs)
        if not self._quiet:
            self.overlay.click(button=button, count=click_count)
        self._follow_active_window()
        return result

    def mouse_drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration_sec: float = 0.3,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Checks the app, shows the drag on the overlay, then drags."""
        self._sync_turbo()
        self.overlay.checkpoint()
        self._before_action()
        self._guard_point(start_x, start_y)
        self._guard_point(end_x, end_y)
        app = self._window_app_at(start_x, start_y)
        self._gate(app, "drag")
        self._reclaim_target()
        self._point_to(start_x, start_y, verb="Drag", probe=True, app=app)
        # Follow the drag in parallel with the real one.
        self._point_to(end_x, end_y, ms=int(duration_sec * 1000), wait=False)
        with self._passing():
            result = self._inner.mouse_drag(
                start_x, start_y, end_x, end_y, duration_sec, *args, **kwargs
            )
        self.overlay.clear_box()
        return result

    def mouse_scroll(self, dx: int, dy: int, *args: Any, **kwargs: Any) -> Any:
        """Shows the scroll on the overlay, then scrolls."""
        self._sync_turbo()
        self.overlay.checkpoint()
        self._before_action()
        self._reclaim_target()
        app = self._active_app()
        self._gate(app, "scroll")
        direction = ("down" if dy < 0 else "up") if dy else ("right" if dx > 0 else "left")
        self._intent(action_text(f"Scroll {direction}", None, app))
        with self._cue("scrolling"), self._passing():
            return self._inner.mouse_scroll(dx, dy, *args, **kwargs)

    # ------------------------------------------------------------ keyboard
    def keyboard_type(self, text: str, *args: Any, **kwargs: Any) -> Any:
        """Checks the app and field, shows what will be typed, then types it.

        Raises:
            OverlayBlocked: The target is a password field, or the frontmost app is off limits.
        """
        from .client import OverlayBlocked

        self._sync_turbo()
        self.overlay.checkpoint()
        self._before_action()
        self._reclaim_target()
        self._guard_keys()
        app = self._active_app()
        self._gate(app, "type")
        field = self._type_target()
        if field is not None and field.get("secure"):  # every level: passwords are the person's
            self.overlay.intent("Your turn: type the password", ms=4000)
            raise OverlayBlocked(
                "This is a password field. Pinlet does not type passwords: ask the person "
                "to type it, then carry on.",
                app=app or "",
            )
        label = typed_label(text)
        if field is not None and field.get("name"):
            label = f"{label} into ‘{_clip(field['name'], 20)}’"
        self._intent(label)
        self.overlay.state("typing")
        try:
            if self._pace is not None and not args and "delay_ms" not in kwargs:
                with contextlib.suppress(TypeError, ValueError):
                    if "delay_ms" in inspect.signature(self._inner.keyboard_type).parameters:
                        kwargs["delay_ms"] = self._pace.typing_delay_ms(text)
            with self._passing():
                return self._inner.keyboard_type(text, *args, **kwargs)
        finally:
            self.overlay.state("idle")

    def keyboard_press_key(self, key_name: str, *args: Any, **kwargs: Any) -> Any:
        """Shows the key on the overlay, then presses it."""
        self._sync_turbo()
        self.overlay.checkpoint()
        self._before_action()
        self._reclaim_target()
        self._guard_keys()
        app = self._active_app()
        self._gate(app, "keys")
        self._confirm_return(key_name, app)
        self._intent(action_text(f"Press {combo_label([key_name])}", None, app))
        try:
            with self._cue("keys"), self._passing():
                return self._inner.keyboard_press_key(key_name, *args, **kwargs)
        finally:
            self._after_keys()

    def keyboard_hotkey(self, keys: list[str], *args: Any, **kwargs: Any) -> Any:
        """Shows the combo, asks Allow/Deny for a dangerous one, then presses it.

        Raises:
            OverlayDenied: The person denied the hotkey.
        """
        from .client import OverlayDenied

        self._sync_turbo()
        self.overlay.checkpoint()
        self._before_action()
        self._reclaim_target()
        self._guard_keys()
        app = self._active_app()
        self._gate(app, "keys")
        label = combo_label(keys)
        reason = danger_reason(keys) if self._danger_prompts() else None
        if reason is None:
            self._intent(action_text(f"Press {label}", None, app))
            try:
                with self._cue("keys"), self._passing():
                    return self._inner.keyboard_hotkey(keys, *args, **kwargs)
            finally:
                self._after_keys()
        from . import rules

        kind = rules.kind_of_hotkey(reason)
        if self._allowed_by_rule(kind, app):
            try:
                with self._passing():
                    return self._inner.keyboard_hotkey(keys, *args, **kwargs)
            finally:
                self._after_keys()
        self.overlay.risk("danger")
        self.overlay.intent(f"Press {label} ({reason})", ms=60000)
        try:
            if not self._ask(
                f"Allow Pinlet to press {label} and {reason}?",
                app=app_label(app) or app or "",
                action=kind,
            ):
                raise OverlayDenied(f"The user denied {label} ({reason})")
            with self._passing():
                return self._inner.keyboard_hotkey(keys, *args, **kwargs)
        finally:
            self._after_keys()
            self.overlay.risk("ok")
            self.overlay.intent("", ms=0)

    # ------------------------------------------------------------- windows
    def focus_window(self, window_id: str, *args: Any, **kwargs: Any) -> Any:
        """Focuses the window and points the overlay at it."""
        self._sync_turbo()
        self.overlay.checkpoint()
        self._before_action()
        ok = self._inner.focus_window(window_id, *args, **kwargs)
        if ok:
            self._field = None
            self._tracked = str(window_id)
            if not self._quiet:
                self.overlay.track(window_id=window_id)
        return ok

    def list_windows(self, *args: Any, **kwargs: Any) -> list[Any]:
        """Lists windows, leaving out the overlay's own."""
        windows = self._inner.list_windows(*args, **kwargs)
        # MacHAL already keeps only layer-0 windows (the overlay sits far above),
        # so this is a second line of defence for other HALs.
        return [
            w
            for w in windows
            if not str(getattr(w, "title", "") or "").startswith(OVERLAY_WINDOW_TITLE)
        ]

    # ------------------------------------------------------------- capture
    def capture_screen(self, *args: Any, **kwargs: Any) -> Any:
        """Hides the overlay for the capture so screenshots never show it."""
        if not self.overlay.active:
            return self._inner.capture_screen(*args, **kwargs)
        if self._quiet:
            # One window is captured by itself: the bar is never in the picture,
            # so it need not blink off, and nothing is scanned on the person's screen.
            try:
                return self._inner.capture_screen(*args, **kwargs)
            finally:
                self.overlay.state("thinking")
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
