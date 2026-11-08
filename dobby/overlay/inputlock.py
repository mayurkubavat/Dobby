"""Input lock: keep the person's clicks out of the window Dobby works in.

Like an agent browser that locks the tab it drives, the overlay helper can
block the person's mouse and keyboard inside Dobby's target window while
Dobby works, and leave every other window alone. Set ``DOBBY_INPUT_LOCK``:

* ``off`` (default): today's behaviour; real input during work is a take-over.
* ``window`` (or ``on``): clicks, drags and scrolls inside the tracked window
  are swallowed while Dobby works, and keys too while that window is in
  front. Pointer moves always pass, so the person can reach other windows,
  the bar, or the screen corner.

The helpers do the OS work (an event tap on macOS, a shield window on X11).
This module holds the rules both share, so they can be tested anywhere.

Ways out, always:

* the bar's **Take over** button (the existing Pause) and the hotkeys
  (Ctrl+Option/Alt+Esc stops, +P pauses) are never blocked;
* insisting: :data:`INSIST_CLICKS` clicks within :data:`INSIST_SECS`, or one
  press held :data:`HOLD_SECS`, is a take-over and that click goes through;
* the lock lapses by itself when Dobby has not acted for ``active_secs``,
  while paused, taken over, asking Allow/Deny, or when the helper exits.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

Rect = tuple[float, float, float, float]
Point = tuple[float, float]

MODES = ("off", "window")
INSIST_CLICKS = 3
INSIST_SECS = 2.0
HOLD_SECS = 1.5

_TRUE = {"1", "true", "yes", "on", "window"}


def lock_mode(env: Mapping[str, str]) -> str:
    """``DOBBY_INPUT_LOCK`` as one of :data:`MODES` (anything unknown is off)."""
    raw = str(env.get("DOBBY_INPUT_LOCK", "")).strip().lower()
    return "window" if raw in _TRUE else "off"


def _inside(rect: Rect | None, pt: Point) -> bool:
    if rect is None:
        return False
    x, y, w, h = rect
    return w > 0 and h > 0 and x <= pt[0] < x + w and y <= pt[1] < y + h


def armed(
    *,
    mode: str,
    working: bool,
    paused: bool,
    takeover: bool,
    confirming: bool,
    has_target: bool,
    screen_reader: bool = False,
) -> bool:
    """Is the lock on right now?

    Only while Dobby is actually working on a known window. Never while it
    is paused or taken over (the person is in control), while an Allow/Deny
    prompt waits for them, or while a screen reader drives the computer.
    """
    if mode != "window" or not has_target:
        return False
    if paused or takeover or confirming or screen_reader:
        return False
    return working


def blocks(
    kind: str,
    pt: Point | None,
    *,
    target: Rect | None,
    above: Iterable[Rect] = (),
    own: Iterable[Rect] = (),
    target_front: bool = False,
) -> bool:
    """Should this real input from the person be swallowed (lock armed)?

    ``kind`` is "move", "click", "drag", "scroll" or "key". ``target`` is the
    tracked window's frame; ``above`` the frames of other windows on top of
    it (a click there is for that window, not Dobby's); ``own`` the overlay's
    own clickable windows (the bar). ``target_front``: Dobby's window has the
    keyboard focus.
    """
    if kind == "move":
        return False
    if kind == "key":
        return target_front
    if pt is None or not _inside(target, pt):
        return False
    if any(_inside(r, pt) for r in own):
        return False
    return not any(_inside(r, pt) for r in above)


class Insist:
    """Tells a person who means it from a stray click.

    ``press(now)`` for each swallowed button press: True when it is the
    :data:`INSIST_CLICKS`-th within :data:`INSIST_SECS` (let it through and
    take over). ``held(now)``: True once a swallowed press has been held
    :data:`HOLD_SECS` (checked on the release, or from the helper's timer).
    """

    def __init__(
        self, clicks: int = INSIST_CLICKS, secs: float = INSIST_SECS, hold_secs: float = HOLD_SECS
    ) -> None:
        self.clicks = max(1, int(clicks))
        self.secs = float(secs)
        self.hold_secs = float(hold_secs)
        self.times: list = []
        self.down_at: float | None = None

    def press(self, now: float) -> bool:
        self.times = [t for t in self.times if now - t <= self.secs] + [now]
        self.down_at = now
        if len(self.times) >= self.clicks:
            self.reset()
            return True
        return False

    def held(self, now: float) -> bool:
        if self.down_at is not None and now - self.down_at >= self.hold_secs:
            self.reset()
            return True
        return False

    def release(self, now: float) -> bool:
        long_press = self.held(now)
        self.down_at = None
        return long_press

    def reset(self) -> None:
        self.times = []
        self.down_at = None


def frontmost_owner(windows: Iterable[Mapping[str, object]], skip_pid: int = 0) -> int | None:
    """PID owning the frontmost normal window in a Quartz window list (front to back)."""
    for info in windows:
        if info.get("kCGWindowLayer", 0) != 0:
            continue
        pid = info.get("kCGWindowOwnerPID")
        if pid == skip_pid:
            continue
        try:
            if float(info.get("kCGWindowAlpha", 1) or 0) <= 0:  # type: ignore[arg-type]
                continue
        except (TypeError, ValueError):
            pass
        return int(pid) if pid is not None else None  # type: ignore[arg-type]
    return None
