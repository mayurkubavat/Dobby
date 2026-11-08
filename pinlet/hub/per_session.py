"""State that must belong to one client of the shared Pinlet, not to all of them.

One engine serves every AI client, so anything an agent "remembers between
calls" (the marks on its last screenshot, the e1/e2 ids from its last
get_screen_state, its tool profile) must be kept per session. Otherwise agent
A's ``click(mark_id=7)`` lands on agent B's mark 7.

:class:`PerSession` is a descriptor: ``obj.attr`` reads and writes a slot of
the current session (:func:`pinlet.hub.sessions.current_id`; 0 outside the
shared Pinlet), so existing code keeps using plain attributes.
"""

from __future__ import annotations

import copy
import threading
import weakref
from collections.abc import Callable
from typing import Any

from . import sessions

_MISSING = object()


class PerSession:
    """An attribute whose value is kept separately for each client session.

    Args:
        default: Value a session starts with (deep-copied, so ``{}`` is safe).
        factory: Called instead of copying ``default``.
    """

    def __init__(self, default: Any = None, factory: Callable[[], Any] | None = None) -> None:
        self.default = default
        self.factory = factory
        self.name = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    def _slots(self, obj: Any) -> dict[int, Any]:
        store = obj.__dict__.get("_per_session_store")
        if store is None:
            store = obj.__dict__.setdefault("_per_session_store", {})
            _track(obj)
        return store.setdefault(self.name, {})

    def __get__(self, obj: Any, owner: type | None = None) -> Any:
        if obj is None:
            return self
        slots = self._slots(obj)
        key = sessions.current_id()
        value = slots.get(key, _MISSING)
        if value is _MISSING:
            value = self.factory() if self.factory else copy.deepcopy(self.default)
            value = slots.setdefault(key, value)
        return value

    def __set__(self, obj: Any, value: Any) -> None:
        self._slots(obj)[sessions.current_id()] = value


_owners: weakref.WeakSet[Any] = weakref.WeakSet()
_owners_lock = threading.Lock()


def _track(obj: Any) -> None:
    try:
        with _owners_lock:
            _owners.add(obj)
    except TypeError:  # not weak-referenceable: its state just lives as long as it does
        pass


def forget(session_id: int) -> None:
    """Drops every per-session value kept for ``session_id`` (it disconnected)."""
    if session_id == 0:
        return
    with _owners_lock:
        owners = list(_owners)
    for obj in owners:
        for slots in obj.__dict__.get("_per_session_store", {}).values():
            slots.pop(session_id, None)


sessions.on_close(forget)
