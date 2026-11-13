"""Background mode: which window each agent works in.

With ``PINLET_BACKGROUND=1`` an agent does not take the screen. It picks one
window (``focus_window``) and from then on sees, clicks and types into that
window only, while the person keeps the real pointer, the keyboard and the
front app (see ``pinlet.hal.macos_background``).

This module only remembers the choice: one target per agent session, keyed by
whatever ``set_key_fn`` returns for the current call (the server passes the
shared Pinlet's session id; a private Pinlet has one key, ``None``).

Stdlib only, so perception can ask for the target without loading a backend.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass

_TRUE = frozenset({"1", "true", "yes", "on"})
POINTER_MODES = ("refuse", "idle")


@dataclass(frozen=True)
class Target:
    """The window an agent works in, and the process that owns it."""

    window_id: str
    pid: int
    app_name: str = ""


_lock = threading.Lock()
_targets: dict[Hashable, Target] = {}
_key_fn: Callable[[], Hashable] | None = None
_frame_fn: Callable[[Target], tuple[int, int, int, int] | None] | None = None


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether background mode is switched on (``PINLET_BACKGROUND``)."""
    env = os.environ if env is None else env
    return env.get("PINLET_BACKGROUND", "").strip().lower() in _TRUE


def pointer_mode(env: Mapping[str, str] | None = None) -> str:
    """What to do when a step needs the real pointer (``PINLET_BACKGROUND_POINTER``).

    ``refuse`` (default): tell the AI the step cannot be done in the
    background. ``idle``: wait until the person has not touched the Mac for a
    few seconds, do it with the real pointer, and put things back.
    """
    env = os.environ if env is None else env
    raw = env.get("PINLET_BACKGROUND_POINTER", "").strip().lower()
    return raw if raw in POINTER_MODES else "refuse"


def set_key_fn(fn: Callable[[], Hashable] | None) -> None:
    """Sets how the current agent session is told apart (None: one shared key)."""
    global _key_fn
    _key_fn = fn


def _key() -> Hashable:
    if _key_fn is None:
        return None
    try:
        return _key_fn()
    except Exception:  # noqa: BLE001 - an unknown session shares the default key
        return None


def get() -> Target | None:
    """The current agent's target window, or None when it has not picked one."""
    with _lock:
        return _targets.get(_key())


def choose(window_id: str, pid: int, app_name: str = "") -> Target:
    """Makes ``window_id`` the current agent's target."""
    target = Target(str(window_id), int(pid), app_name or "")
    with _lock:
        _targets[_key()] = target
    return target


def clear(key: Hashable = None, *, current: bool = False) -> None:
    """Forgets one agent's target (``current=True``: the calling agent's)."""
    with _lock:
        _targets.pop(_key() if current else key, None)


def reset() -> None:
    """Forgets every target, the key function and the frame function (tests)."""
    global _key_fn, _frame_fn
    with _lock:
        _targets.clear()
    _key_fn = None
    _frame_fn = None


def set_frame_fn(fn: Callable[[Target], tuple[int, int, int, int] | None] | None) -> None:
    """Sets how a target window's frame (x, y, width, height) is looked up; the HAL does."""
    global _frame_fn
    _frame_fn = fn


def current_frame() -> tuple[int, int, int, int] | None:
    """Where the current agent's window is now, or None when unknown.

    An app's accessibility tree does not carry window ids, so readers match
    the agent's window by this frame.
    """
    chosen = get()
    if chosen is None or _frame_fn is None:
        return None
    try:
        return _frame_fn(chosen)
    except Exception:  # noqa: BLE001 - unknown is fine: readers then take every window
        return None


def same_frame(a: tuple | None, b: tuple | None, slack: int = 2) -> bool:
    """Whether two frames are the same window, allowing a point or two of rounding."""
    if a is None or b is None or len(a) != 4 or len(b) != 4:
        return False
    return all(abs(int(p) - int(q)) <= slack for p, q in zip(a, b, strict=True))


def current_pid() -> int | None:
    """The process that owns the current agent's target window, or None."""
    target = get()
    return target.pid if target is not None else None
