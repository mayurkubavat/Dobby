"""File-backed control lease shared by every Pinlet process of one user.

Each AI client starts its own Pinlet server, so two agents mean two processes
driving one mouse and keyboard. This module is the referee between them. The
state lives in ``lease.json`` and is only read or changed while holding an OS
file lock on ``lease.lock`` (``fcntl`` on macOS/Linux, ``msvcrt`` on Windows).
The lock is held for a few milliseconds per call, never across an action.

Policy (see :func:`decide`):

* One holder may act at a time. A holder with nobody waiting keeps the lease.
* A waiter takes over only between the holder's steps (``busy`` is false) and
  only when the holder released, has been idle for ``idle_grace`` seconds,
  has held for a full ``slice``, or (``preempt``) has a lower priority.
  ``hold_until`` suspends the idle and slice rules for a fragile flow.
* Among waiters the highest effective priority wins: priority plus one point
  per ``aging`` seconds waited, earliest first on a tie.
* A holder whose process is gone loses the lease at once.
* A holder waiting for the person (an Allow/Deny card, ``asking``) yields at
  once: one unanswered card must not freeze every other agent.
* A waiter that gave up after ``max_wait`` keeps its place (its ``since``) for
  ``reserve`` seconds, so calling again does not send it to the back of the
  line. Only waiters that are polling right now can be handed the lease.
* ``world`` counts every finished action by any agent. An agent that gets the
  lease from someone else compares it with the ``world`` it last observed;
  if they differ the screen may have changed, so it must look again.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

State = dict[str, Any]


@dataclass
class ControlSettings:
    """Timing and policy knobs for the shared control lease (all times in seconds)."""

    # Seconds without an action before a waiter may take over. AI agents think
    # between calls: on real logs 19% of gaps exceed 10 s but 10% exceed 20 s,
    # so 10 s handed control away mid-task about every fifth step.
    idle_grace: float = 20.0
    slice: float = 60.0  # longest continuous hold while someone waits
    max_wait: float = 45.0  # how long a tool waits for the lease before giving up
    hold_cap: float = 120.0  # upper bound for hold_control(seconds)
    preempt: bool = False  # higher priority may take over at the next step boundary
    aging: float = 15.0  # seconds of waiting worth one priority point
    busy_timeout: float = 300.0  # a step that runs longer than this counts as hung
    waiter_ttl: float = 5.0  # a waiter that stopped polling this long ago is not in line now
    reserve: float = 120.0  # a waiter that gave up keeps its place this long
    poll: float = 0.1
    pause_max: float = 600.0  # a Pinlet paused from the picker gives up after this


@dataclass
class Agent:
    """One Pinlet process taking part in the lease."""

    id: str
    name: str
    pid: int
    priority: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def pid_alive(pid: int) -> bool:
    """True when a process with ``pid`` is still running."""
    if pid <= 0:
        return False
    if sys.platform == "win32":  # pragma: no cover - no signal 0 on Windows
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:  # noqa: BLE001
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@contextlib.contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Exclusive advisory lock; the OS drops it if the process dies."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+b")
    try:
        if sys.platform == "win32":  # pragma: no cover
            import msvcrt

            fh.seek(0)
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.01)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


def empty_state() -> State:
    """A fresh lease state: no holder, no waiters, world counter at zero."""
    return {
        "version": 1,
        "epoch": 0,
        "world": 0,
        "holder": None,
        "last_holder": None,
        "waiters": {},
    }


def _agent_record(agent: Agent) -> dict[str, Any]:
    return {"id": agent.id, "name": agent.name, "pid": agent.pid, "priority": agent.priority}


def effective_priority(waiter: dict[str, Any], now: float, settings: ControlSettings) -> float:
    """A waiter's priority plus one point per ``settings.aging`` seconds it has waited."""
    waited = max(0.0, now - float(waiter.get("since", now)))
    aging = settings.aging if settings.aging > 0 else float("inf")
    return float(waiter.get("priority", 0)) + waited / aging


def prune(
    state: State, now: float, settings: ControlSettings, alive: Callable[[int], bool] = pid_alive
) -> None:
    """Drops a dead holder, hung steps and waiters that stopped polling, in place."""
    holder = state.get("holder")
    if holder is not None and not alive(int(holder.get("pid", 0))):
        state["holder"] = None
    waiters = state.setdefault("waiters", {})
    for wid in list(waiters):
        w = waiters[wid]
        stale = now - float(w.get("seen", 0)) > settings.waiter_ttl
        if not alive(int(w.get("pid", 0))) or (stale and now > float(w.get("reserved_until", 0))):
            del waiters[wid]


def polling(waiter: dict[str, Any], now: float, settings: ControlSettings) -> bool:
    """True when ``waiter`` is waiting right now (not just keeping its place)."""
    return now - float(waiter.get("seen", 0)) <= settings.waiter_ttl


def yield_in(holder: dict[str, Any] | None, now: float, settings: ControlSettings) -> float | None:
    """Seconds until ``holder`` must let a waiter in, or None when it is busy with a step."""
    if holder is None or holder.get("asking"):
        return 0.0
    busy = bool(holder.get("busy")) and now - float(holder.get("last", now)) < settings.busy_timeout
    if busy:
        return None
    idle_left = settings.idle_grace - (now - float(holder.get("last", now)))
    slice_left = settings.slice - (now - float(holder.get("since", now)))
    left = max(0.0, min(idle_left, slice_left))
    return max(left, float(holder.get("hold_until", 0)) - now)


def place_in_line(state: State, me_id: str, now: float, settings: ControlSettings) -> int:
    """1 when ``me_id`` is next, 2 when one agent is ahead, ... (0 when not waiting)."""
    waiters = state.get("waiters", {})
    if me_id not in waiters:
        return 0
    order = sorted(
        waiters.values(),
        key=lambda w: (-effective_priority(w, now, settings), float(w.get("since", now))),
    )
    return 1 + [w.get("id") for w in order].index(me_id)


def holder_yields(
    holder: dict[str, Any], me: Agent, now: float, settings: ControlSettings
) -> tuple[bool, str]:
    """May ``me`` take the lease from ``holder`` right now?"""
    if holder.get("asking"):
        return True, "asking"
    busy = bool(holder.get("busy")) and now - float(holder.get("last", now)) < settings.busy_timeout
    if busy:
        return False, "busy"
    if float(holder.get("hold_until", 0)) > now:
        return False, "holding"
    if now - float(holder.get("last", now)) >= settings.idle_grace:
        return True, "idle"
    if now - float(holder.get("since", now)) >= settings.slice:
        return True, "slice"
    if settings.preempt and me.priority > int(holder.get("priority", 0)):
        return True, "preempt"
    return False, "working"


def decide(
    state: State,
    me: Agent,
    now: float,
    settings: ControlSettings,
    alive: Callable[[int], bool] = pid_alive,
) -> tuple[bool, str]:
    """Try to give ``me`` the lease and mark it busy. Mutates ``state``.

    Returns ``(granted, reason)``. Reasons when granted: ``held`` (already
    ours), ``free``, ``idle``, ``slice``, ``preempt``. When refused: ``busy``,
    ``holding``, ``working``, ``queued``.
    """
    prune(state, now, settings, alive)
    waiters = state.setdefault("waiters", {})
    holder = state.get("holder")
    if holder is not None and holder.get("id") == me.id:
        holder.update(busy=True, last=now, name=me.name, priority=me.priority, asking=False)
        waiters.pop(me.id, None)
        return True, "held"

    mine = waiters.get(me.id)
    if mine is None:
        mine = waiters[me.id] = {**_agent_record(me), "since": now}
    mine.update(seen=now, name=me.name, priority=me.priority)
    mine.pop("reserved_until", None)

    reason = "free"
    if holder is not None:
        ok, reason = holder_yields(holder, me, now, settings)
        if not ok:
            return False, reason

    best = max(
        (w for w in waiters.values() if polling(w, now, settings)),
        key=lambda w: (effective_priority(w, now, settings), -float(w.get("since", now))),
    )
    if best.get("id") != me.id:
        return False, "queued"

    waiters.pop(me.id, None)
    if state.get("last_holder") != me.id:
        state["epoch"] = int(state.get("epoch", 0)) + 1
    state["holder"] = {
        **_agent_record(me),
        "since": now,
        "last": now,
        "busy": True,
        "hold_until": 0.0,
        "asking": False,
    }
    state["last_holder"] = me.id
    return True, reason


class LeaseStore:
    """Reads and writes ``lease.json`` under the file lock."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = self.root / "lease.json"
        self.lock_path = self.root / "lease.lock"

    def _load(self) -> State:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                base = empty_state()
                base.update(data)
                return base
        except (OSError, ValueError):
            pass
        return empty_state()

    def _save(self, state: State) -> None:
        tmp = self.path.with_name(f"lease.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    @contextlib.contextmanager
    def transaction(self) -> Iterator[State]:
        """Context manager that loads the state under the file lock and saves changes on exit."""
        with file_lock(self.lock_path):
            state = self._load()
            before = json.dumps(state, sort_keys=True)
            yield state
            if json.dumps(state, sort_keys=True) != before:
                self._save(state)

    def snapshot(self) -> State:
        """Reads the current state under the file lock, without changing it."""
        with file_lock(self.lock_path):
            return self._load()
