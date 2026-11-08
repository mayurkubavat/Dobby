"""Shared control: let several AI agents drive one desktop without racing.

Each AI client starts its own Pinlet server. A file-locked lease in
``~/.pinlet/control`` makes sure only one of them moves the mouse or types at a
time and hands control over when the holder releases it, goes idle, or uses up
its time slice while another agent waits. Reading the screen never needs the
lease. See ``lease.py`` for the exact rules.

Environment (set per client in its MCP config):

* ``PINLET_AGENT_NAME`` - name shown to the other agent and on the overlay
  (default: the MCP client's name, else ``Pinlet-<pid>``).
* ``PINLET_AGENT_PRIORITY`` - integer, higher wins the queue (default 0).
* ``PINLET_CONTROL=0|1`` - off disables the lease (default on; off under pytest
  and cassette replay).
* ``PINLET_CONTROL_DIR`` (default ``~/.pinlet/control``),
  ``PINLET_CONTROL_IDLE_GRACE`` (10 s), ``PINLET_CONTROL_SLICE`` (60 s),
  ``PINLET_CONTROL_MAX_WAIT`` (45 s), ``PINLET_CONTROL_HOLD_CAP`` (120 s),
  ``PINLET_CONTROL_PREEMPT=1`` (higher priority takes over at the next step).
"""

from __future__ import annotations

import contextlib
import contextvars
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from . import names
from .controller import ControlBusy, ControlChanged, ControlError, Controller, ControlStopped
from .hal import ACTING, OBSERVING, ControlHAL, find_control_hal, unwrap_control
from .lease import ControlSettings

__all__ = [
    "ACTING",
    "OBSERVING",
    "ControlBusy",
    "ControlChanged",
    "ControlError",
    "ControlHAL",
    "ControlSettings",
    "ControlStopped",
    "Controller",
    "adopt_client_name",
    "agent_name",
    "control_enabled",
    "control_root",
    "find_control_hal",
    "get_controller",
    "set_controller",
    "settings_from_env",
    "unwrap_control",
    "wrap_hal",
]

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_CONTROLLER: Controller | None = None


def control_enabled(env: dict[str, str] | None = None) -> bool:
    """True unless ``PINLET_CONTROL`` turns shared control off."""
    env = os.environ if env is None else env
    flag = env.get("PINLET_CONTROL", "").strip().lower()
    if flag in _FALSE:
        return False
    if flag in _TRUE:
        return True
    return not ("PYTEST_CURRENT_TEST" in env or env.get("PINLET_REPLAY", "").lower() in _TRUE)


def control_root(env: dict[str, str] | None = None) -> Path:
    """Directory for the shared control state (``PINLET_CONTROL_DIR``, default ``~/.pinlet/control``)."""
    env = os.environ if env is None else env
    return Path(os.path.expanduser(env.get("PINLET_CONTROL_DIR") or "~/.pinlet/control"))


def _num(env: dict[str, str], key: str, default: float, low: float = 0.0) -> float:
    try:
        return max(low, float(env[key]))
    except (KeyError, ValueError):
        return default


def settings_from_env(env: dict[str, str] | None = None) -> ControlSettings:
    """Builds :class:`ControlSettings` from the ``PINLET_CONTROL_*`` environment variables."""
    env = os.environ if env is None else env
    s = ControlSettings()
    s.idle_grace = _num(env, "PINLET_CONTROL_IDLE_GRACE", s.idle_grace, 0.5)
    s.slice = _num(env, "PINLET_CONTROL_SLICE", s.slice, 1.0)
    s.max_wait = _num(env, "PINLET_CONTROL_MAX_WAIT", s.max_wait, 0.0)
    s.hold_cap = _num(env, "PINLET_CONTROL_HOLD_CAP", s.hold_cap, 0.0)
    s.preempt = env.get("PINLET_CONTROL_PREEMPT", "").strip().lower() in _TRUE
    return s


def agent_name(env: dict[str, str] | None = None) -> str | None:
    """The configured agent name (``PINLET_AGENT_NAME``), or None."""
    env = os.environ if env is None else env
    name = (env.get("PINLET_AGENT_NAME") or "").strip()
    return name or None


# The shared Pinlet (``pinlet serve``) runs one Controller per connected client,
# so the lease sees each AI as its own agent even inside one process.
_SESSION_CONTROLLER: contextvars.ContextVar[Controller | None] = contextvars.ContextVar(
    "pinlet_session_controller", default=None
)


@contextlib.contextmanager
def session_controller(ctl: Controller | None) -> Iterator[None]:
    """Make ``ctl`` this session's controller for the block (and its tool calls)."""
    token = _SESSION_CONTROLLER.set(ctl)
    try:
        yield
    finally:
        _SESSION_CONTROLLER.reset(token)


def new_session_controller(
    name: str, pid: int | None = None, first_name: str | None = None
) -> Controller | None:
    """A controller for one client of the shared Pinlet, or None when control is off."""
    if not control_enabled():
        return None
    try:
        priority = int(os.environ.get("PINLET_AGENT_PRIORITY", "0") or 0)
    except ValueError:
        priority = 0
    first = first_name or pick_first_name(pid if pid is not None else os.getpid())
    ctl = Controller(
        control_root(),
        name=names.display(first, name),
        priority=priority,
        settings=settings_from_env(),
        pid=pid,
    )
    ctl.first_name = first  # type: ignore[attr-defined]
    ctl.name_locked = False  # type: ignore[attr-defined]
    if _HAL_FOR_SESSIONS is not None:
        _wire_overlay(_HAL_FOR_SESSIONS, ctl)
    return ctl


_HAL_FOR_SESSIONS: Any = None


def pick_first_name(pid: int) -> str:
    """An elf name no running Pinlet uses yet (see :mod:`pinlet.control.names`)."""
    try:
        from . import registry

        taken = [
            str(r.get("name") or "").split(names.SEPARATOR)[0]
            for r in registry.list_agents(control_root())
        ]
    except OSError:
        taken = []
    try:
        from pinlet.hub import sessions

        taken += [str(s.get("first_name") or "") for s in sessions.snapshot()]
    except Exception:  # noqa: BLE001
        pass
    return names.pick(taken, pid)


def get_controller(create: bool = True) -> Controller | None:
    """Returns this session's controller.

    In a shared Pinlet this is the session's own controller; otherwise the process-wide
    one, created on first use when control is enabled.
    """
    session = _SESSION_CONTROLLER.get()
    if session is not None:
        return session
    global _CONTROLLER
    if _CONTROLLER is None and create and control_enabled():
        env = os.environ
        try:
            priority = int(env.get("PINLET_AGENT_PRIORITY", "0") or 0)
        except ValueError:
            priority = 0
        first = agent_name() or pick_first_name(os.getpid())
        _CONTROLLER = Controller(
            control_root(),
            name=first,
            priority=priority,
            settings=settings_from_env(),
        )
        _CONTROLLER.first_name = first  # type: ignore[attr-defined]
        _CONTROLLER.name_locked = agent_name() is not None  # type: ignore[attr-defined]
    return _CONTROLLER


def set_controller(ctl: Controller | None) -> None:
    """Install (or clear) the process controller; for tests and embedding."""
    global _CONTROLLER
    _CONTROLLER = ctl


def wrap_hal(hal: Any, controller: Controller | None = None) -> Any:
    """Wrap ``hal`` in :class:`ControlHAL` when shared control is enabled."""
    if isinstance(hal, ControlHAL):
        return hal
    global _HAL_FOR_SESSIONS
    ctl = controller if controller is not None else get_controller()
    if ctl is None:
        return hal
    _wire_overlay(hal, ctl)
    _HAL_FOR_SESSIONS = hal
    return ControlHAL(hal, ctl)


def _wire_overlay(hal: Any, ctl: Controller) -> None:
    """Show "Waiting for <agent>" on this agent's overlay while it queues."""
    try:
        from pinlet.overlay import find_overlay_hal
    except Exception:  # noqa: BLE001
        return
    ov_hal = find_overlay_hal(hal)
    if ov_hal is None:
        return
    overlay = ov_hal.overlay

    def on_wait(holder: dict[str, Any] | None) -> None:
        try:
            if holder is None:
                overlay.intent("", ms=0)
            else:
                overlay.intent(f"Waiting for {holder.get('name') or 'another agent'}", ms=60000)
        except Exception:  # noqa: BLE001 - cosmetic
            pass

    def on_paused(paused: bool) -> None:
        try:
            overlay.intent(
                "Paused from the Pinlet menu" if paused else "", ms=600000 if paused else 0
            )
        except Exception:  # noqa: BLE001 - cosmetic
            pass

    ctl.on_wait = on_wait
    ctl.on_paused = on_paused


def adopt_client_name(name: str | None) -> None:
    """Use the MCP client's name (``clientInfo.name``) unless PINLET_AGENT_NAME is set."""
    ctl = get_controller(create=False)
    if ctl is None or not name or getattr(ctl, "name_locked", False):
        return
    ctl.set_name(names.display(getattr(ctl, "first_name", "") or name, name))
