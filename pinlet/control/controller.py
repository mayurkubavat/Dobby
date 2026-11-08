"""Per-process side of shared control: one ``Controller`` per Pinlet server."""

from __future__ import annotations

import atexit
import contextlib
import random
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from . import commands, inbox, registry
from .lease import (
    Agent,
    ControlSettings,
    LeaseStore,
    decide,
    pid_alive,
    place_in_line,
    prune,
    yield_in,
)


class ControlError(RuntimeError):
    """Base class for shared-control refusals (reported to the agent as a tool error)."""


class ControlBusy(ControlError):
    """Another agent kept the desktop for longer than ``max_wait``."""


class ControlStopped(ControlError):
    """The person stopped this Pinlet from the Pinlet picker."""


class ControlChanged(ControlError):
    """Another agent acted since this agent last looked; the step was skipped."""


def _fmt_secs(s: float) -> str:
    return f"{int(round(s))} s"


class Controller:
    """Acquire, renew and release the shared lease for this agent.

    Actions run inside :meth:`action` (one HAL call) and, when the server
    wraps a tool call, inside :meth:`tool_scope` so a tool with several steps
    (click then type) keeps the lease across all of them.
    """

    def __init__(
        self,
        root: Path,
        name: str,
        priority: int = 0,
        settings: ControlSettings | None = None,
        pid: int | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        alive: Callable[[int], bool] = pid_alive,
        register: bool = True,
        agent_id: str | None = None,
    ) -> None:
        import os

        self.root = Path(root)
        self.settings = settings or ControlSettings()
        pid = os.getpid() if pid is None else pid
        # ``agent_id`` is stable across shared-Pinlet restarts (the connector's
        # agent key), so a client that reconnects keeps its place in the lease.
        self.agent = Agent(
            id=agent_id or f"{pid}-{uuid.uuid4().hex[:6]}",
            name=name,
            pid=pid,
            priority=priority,
        )
        self.store = LeaseStore(self.root)
        self._clock = clock
        self._sleep = sleep
        self._alive = alive
        self._lock = threading.RLock()
        self._depth = 0  # nested action scopes in this process
        self._tool_scopes = 0  # tool calls in progress
        self._tool_holding = False  # a tool call acquired and keeps the lease busy
        self._seen_world: int | None = None
        self._name_from_env = name
        self.on_wait: Callable[[dict[str, Any] | None], None] | None = None
        self.on_paused: Callable[[bool], None] | None = None
        self._stop_seen = commands.read(self.root, pid)["stop"]
        self._registered = False
        if register:
            self._register()

    # ------------------------------------------------------------ identity
    def _register(self) -> None:
        try:
            registry.register(
                self.root, self.agent.id, self.agent.name, self.agent.pid, self.agent.priority
            )
            self._registered = True
            atexit.register(self.close)
        except OSError:
            pass

    def set_name(self, name: str) -> None:
        """Adopt a name (e.g. the MCP client's) unless one was configured."""
        name = (name or "").strip()
        if not name or name == self.agent.name:
            return
        self.agent.name = name
        if self._registered:
            try:
                registry.register(
                    self.root, self.agent.id, name, self.agent.pid, self.agent.priority
                )
            except OSError:
                pass

    def close(self) -> None:
        """Releases the lease and unregisters this agent; safe to call more than once."""
        try:
            self.release()
        except Exception:  # noqa: BLE001
            pass
        if self._registered:
            registry.unregister(self.root, self.agent.pid)
            commands.clear(self.root, self.agent.pid)
            inbox.clear(self.root, self.agent.pid)
            self._registered = False

    # --------------------------------------------------------------- lease
    def _try(self) -> tuple[bool, str, dict[str, Any]]:
        now = self._clock()
        with self.store.transaction() as state:
            prev = state.get("last_holder")
            ok, reason = decide(state, self.agent, now, self.settings, self._alive)
            info = {
                "holder": dict(state["holder"]) if state.get("holder") else None,
                "world": int(state.get("world", 0)),
                "epoch": int(state.get("epoch", 0)),
                "handoff": ok and prev not in (None, self.agent.id),
                "now": now,
                "place": 0 if ok else place_in_line(state, self.agent.id, now, self.settings),
            }
        return ok, reason, info

    def _keep_place(self) -> None:
        """Gave up waiting for now: stay in line (same ``since``) for ``settings.reserve``."""
        now = self._clock()
        with self.store.transaction() as state:
            mine = state.setdefault("waiters", {}).get(self.agent.id)
            if mine is not None:
                mine["reserved_until"] = now + self.settings.reserve

    def _busy_message(self, info: dict[str, Any], wait: float) -> str:
        holder = info.get("holder") or {}
        now = info["now"]
        who = holder.get("name") or "another agent"
        held = now - float(holder.get("since", now))
        place = int(info.get("place") or 0)
        line = (
            "You are next in line"
            if place == 1
            else f"{place - 1} agent(s) are ahead of you"
            if place > 1
            else "You are in line"
        )
        left = yield_in(holder or None, now, self.settings)
        when = (
            "it is in the middle of a step"
            if left is None
            else f"it must let the next agent in within about {_fmt_secs(left)}"
            if left > 0
            else "it can hand over now"
        )
        return (
            f"Another agent, {who}, is using the screen (for {_fmt_secs(held)}; {when}). "
            f"Waited {_fmt_secs(wait)}. {line}, and you keep your place for "
            f"{_fmt_secs(self.settings.reserve)}: call the same tool again to keep waiting, "
            "or do work that does not need the mouse or keyboard meanwhile. Reading the "
            "screen is always allowed. Look again before you act: the screen may change."
        )

    def acquire(self, max_wait: float | None = None) -> dict[str, Any]:
        """Block until this agent holds the lease (marked busy), or raise ``ControlBusy``."""
        wait = self.settings.max_wait if max_wait is None else max_wait
        deadline = self._clock() + wait
        notified = False
        while True:
            ok, _reason, info = self._try()
            if ok:
                if notified and self.on_wait:
                    self.on_wait(None)
                self._check_world(info)
                return info
            if not notified and self.on_wait:
                notified = True
                self.on_wait(info.get("holder"))
            if self._clock() >= deadline:
                self._keep_place()
                if notified and self.on_wait:
                    self.on_wait(None)
                raise ControlBusy(self._busy_message(info, wait))
            self._sleep(self.settings.poll * (0.75 + random.random() * 0.5))

    def _check_world(self, info: dict[str, Any]) -> None:
        world = info["world"]
        seen = self._seen_world
        self._seen_world = world
        if info["handoff"] and seen is not None and seen != world:
            self._finish_without_world()  # keep the lease, not busy: look, then act
            raise ControlChanged(
                "Another agent used the desktop since your last look. This step was skipped "
                "and you now have control: take a new screenshot and decide again."
            )

    def _finish(self) -> None:
        now = self._clock()
        with self.store.transaction() as state:
            holder = state.get("holder")
            if holder is not None and holder.get("id") == self.agent.id:
                holder.update(busy=False, last=now)
                state["world"] = int(state.get("world", 0)) + 1
                self._seen_world = state["world"]

    def gate(self) -> None:
        """Honour Pause/Stop sent from a Pinlet picker. Call before each action.

        While paused, a lease held between this tool's steps is left not busy
        so another Pinlet can work; it is taken back after Resume.
        """
        cmd = commands.read(self.root, self.agent.pid)
        if cmd["stop"] > self._stop_seen:
            self._stop_seen = cmd["stop"]
            raise ControlStopped(
                "Stopped by the person from the Pinlet menu. Do not retry this task "
                "unless they ask again."
            )
        if not cmd["paused"]:
            return
        holding = self._depth > 0
        if holding:
            self._finish_without_world()
        if self.on_paused:
            self.on_paused(True)
        deadline = self._clock() + self.settings.pause_max
        try:
            while True:
                self._sleep(max(self.settings.poll, 0.2))
                cmd = commands.read(self.root, self.agent.pid)
                if cmd["stop"] > self._stop_seen:
                    self._stop_seen = cmd["stop"]
                    raise ControlStopped("Stopped by the person from the Pinlet menu while paused.")
                if not cmd["paused"]:
                    break
                if self._clock() >= deadline:
                    raise ControlStopped("This Pinlet stayed paused too long; stopping the task.")
        finally:
            if self.on_paused:
                self.on_paused(False)
        if holding:
            self.acquire()

    def begin(self) -> None:
        """Enter an action or tool scope; acquires on the outermost entry."""
        with self._lock:
            if self._depth == 0:
                self.acquire()
            self._depth += 1

    def end(self) -> None:
        """Leaves an action or tool scope; finishes the step on the outermost exit."""
        with self._lock:
            self._depth = max(0, self._depth - 1)
            if self._depth == 0:
                self._finish()

    def action(self) -> _ActionScope:
        """Context manager that holds the lease for one input action."""
        return _ActionScope(self)

    def tool_scope(self):
        """Lazy scope for a whole tool call: acquire only if the tool acts."""
        self.ensure_registered()
        return _LazyToolScope(self)

    def ensure_registered(self) -> None:
        """Restores this agent's registry file if something removed it.

        Keeps the other Pinlets and the overlay counting this one as an agent.
        """
        if not self._registered:
            return
        try:
            if not registry.is_registered(self.root, self.agent.pid):
                registry.register(
                    self.root, self.agent.id, self.agent.name, self.agent.pid, self.agent.priority
                )
        except OSError:
            pass

    def note_observation(self) -> None:
        """Record what the world looked like when this agent last read the screen."""
        try:
            self._seen_world = int(self.store.snapshot().get("world", 0))
        except OSError:
            pass

    def release(self) -> bool:
        """Gives the lease back if this agent holds it.

        Never releases in the middle of a step.

        Returns:
            True when this agent held the lease and released it.
        """
        with self._lock:
            if self._depth:
                return False  # never mid-step
            with self.store.transaction() as state:
                state.setdefault("waiters", {}).pop(self.agent.id, None)
                holder = state.get("holder")
                if holder is not None and holder.get("id") == self.agent.id:
                    state["holder"] = None
                    return True
            return False

    def hold(self, seconds: float) -> dict[str, Any]:
        """Keep the lease for ``seconds`` (capped) even when idle or past the slice."""
        secs = max(0.0, min(float(seconds), self.settings.hold_cap))
        with self._lock:
            if self._depth == 0:
                self.acquire()
                self._finish_without_world()
            now = self._clock()
            with self.store.transaction() as state:
                holder = state.get("holder")
                if holder is None or holder.get("id") != self.agent.id:
                    raise ControlError("Lost control before the hold started; try again.")
                holder["hold_until"] = now + secs
        return {"held_for": secs}

    @contextlib.contextmanager
    def waiting_for_person(self) -> Iterator[Any]:
        """Let other agents use the desktop while this one waits for the person.

        Wrap an Allow/Deny question asked in the middle of a step. The lease is
        marked ``asking`` (a waiter may take it at once) and taken back after the
        answer. If another agent acted meanwhile, :class:`ControlChanged` is
        raised: the step is skipped and this agent must look again. Set
        ``turn.reacquire = False`` on the yielded object when the step will not
        run (a Deny), so the answer is reported as it is.
        """
        import types

        turn = types.SimpleNamespace(reacquire=True)
        with self._lock:
            mine = self._depth > 0
        if not mine:
            yield turn
            return
        now = self._clock()
        with self.store.transaction() as state:
            holder = state.get("holder")
            mine = holder is not None and holder.get("id") == self.agent.id
            if mine:
                holder.update(busy=False, last=now, asking=True)
        if not mine:
            yield turn
            return
        try:
            yield turn
        finally:
            with self.store.transaction() as state:
                holder = state.get("holder")
                if holder is not None and holder.get("id") == self.agent.id:
                    holder["asking"] = False
        if turn.reacquire:
            self.acquire()

    def _finish_without_world(self) -> None:
        now = self._clock()
        with self.store.transaction() as state:
            holder = state.get("holder")
            if holder is not None and holder.get("id") == self.agent.id:
                holder.update(busy=False, last=now)

    def status(self) -> dict[str, Any]:
        """Describes who holds control, who is waiting, and this agent's place."""
        now = self._clock()
        with self.store.transaction() as state:
            prune(state, now, self.settings, self._alive)
            holder = state.get("holder")
            waiters = sorted(
                state.get("waiters", {}).values(), key=lambda w: float(w.get("since", now))
            )
            out: dict[str, Any] = {
                "you": {"name": self.agent.name, "priority": self.agent.priority},
                "you_hold_control": bool(holder and holder.get("id") == self.agent.id),
                "holder": None,
                "waiting": [
                    {
                        "name": w.get("name"),
                        "priority": w.get("priority", 0),
                        "waiting_s": round(now - float(w.get("since", now)), 1),
                    }
                    for w in waiters
                ],
                "epoch": state.get("epoch", 0),
                "agents": [
                    {"name": a.get("name"), "pid": a.get("pid")}
                    for a in registry.list_agents(self.root)
                ],
                "rules": {
                    "idle_grace_s": self.settings.idle_grace,
                    "slice_s": self.settings.slice,
                    "max_wait_s": self.settings.max_wait,
                    "preempt": self.settings.preempt,
                },
            }
            if holder:
                out["holder"] = {
                    "name": holder.get("name"),
                    "held_s": round(now - float(holder.get("since", now)), 1),
                    "idle_s": round(now - float(holder.get("last", now)), 1),
                    "busy": bool(holder.get("busy")),
                    "hold_left_s": round(max(0.0, float(holder.get("hold_until", 0)) - now), 1),
                }
        return out


class _ActionScope:
    """One HAL action.

    Inside a tool call the first action acquires and the lease stays busy until the tool
    returns; outside, each action stands alone.
    """

    def __init__(self, ctl: Controller) -> None:
        self.ctl = ctl
        self._owns = False

    def __enter__(self) -> Controller:
        ctl = self.ctl
        ctl.gate()
        with ctl._lock:
            if ctl._tool_scopes:
                if not ctl._tool_holding:
                    ctl.begin()
                    ctl._tool_holding = True
            else:
                ctl.begin()
                self._owns = True
        return ctl

    def __exit__(self, *exc: Any) -> None:
        if self._owns:
            self.ctl.end()


class _LazyToolScope:
    """Marks a tool call in progress; see :class:`_ActionScope`."""

    def __init__(self, ctl: Controller) -> None:
        self.ctl = ctl

    def __enter__(self) -> _LazyToolScope:
        with self.ctl._lock:
            self.ctl._tool_scopes += 1
        return self

    def __exit__(self, *exc: Any) -> None:
        ctl = self.ctl
        with ctl._lock:
            ctl._tool_scopes = max(0, ctl._tool_scopes - 1)
            if ctl._tool_scopes == 0 and ctl._tool_holding:
                ctl._tool_holding = False
                ctl.end()
