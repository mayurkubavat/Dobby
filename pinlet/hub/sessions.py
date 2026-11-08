"""The clients connected to the shared Pinlet, one :class:`SessionInfo` each.

``snapshot()`` is what status views read (``control_status``, and later the
Pinlet app's menu bar / tray). Fields that only the overlay knows (step,
steps, elapsed, app) are filled in when the overlay reports them.
"""

from __future__ import annotations

import contextvars
import itertools
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

_ids = itertools.count(1)


@dataclass
class SessionInfo:
    """One connected client session of the shared Pinlet."""

    id: int
    name: str
    pid: int | None
    mode: str
    connected_at: float = field(default_factory=time.time)
    last_call: float | None = None
    last_tool: str | None = None
    calls: int = 0
    app: str | None = None
    step: int | None = None
    steps: int | None = None
    elapsed: float | None = None
    first_name: str = ""  # "Tibble": shown as "Tibble · <client>"
    client: str = ""

    @property
    def turbo(self) -> bool:
        """True unless the session asked for secure mode."""
        return self.mode != "secure"

    def as_dict(self) -> dict[str, Any]:
        """The session as a JSON-ready dict, including ``turbo``."""
        out = asdict(self)
        out["turbo"] = self.turbo
        return out


_lock = threading.Lock()
_sessions: dict[int, SessionInfo] = {}
CURRENT: contextvars.ContextVar[SessionInfo | None] = contextvars.ContextVar(
    "pinlet_session", default=None
)


def open_session(name: str, pid: int | None, mode: str) -> SessionInfo:
    """Registers a new client session and returns its info."""
    info = SessionInfo(id=next(_ids), name=name, pid=pid, mode=mode)
    with _lock:
        _sessions[info.id] = info
    return info


def close_session(info: SessionInfo) -> None:
    """Forgets a session when its client disconnects."""
    with _lock:
        _sessions.pop(info.id, None)
    _run_forget_hooks(info.id)


def note_call(tool: str) -> None:
    """Records a tool call against the current session."""
    info = CURRENT.get()
    if info is None:
        return
    info.calls += 1
    info.last_call = time.time()
    info.last_tool = tool


def rename(name: str | None) -> None:
    """The client told us who it is (MCP ``clientInfo.name``)."""
    from pinlet.control.names import display

    info = CURRENT.get()
    if info is not None and name:
        info.client = name
        info.name = display(info.first_name, name) if info.first_name else name


def count() -> int:
    """Number of open sessions."""
    with _lock:
        return len(_sessions)


def snapshot() -> list[dict[str, Any]]:
    """All open sessions as dicts, oldest first."""
    with _lock:
        return [s.as_dict() for s in sorted(_sessions.values(), key=lambda s: s.id)]


def current_id() -> int:
    """The current client session's id, or 0 outside the shared Pinlet (one private client)."""
    info = CURRENT.get()
    return info.id if info is not None else 0


# Per-session engine state (screenshot marks, element ids, tool profiles) is
# kept by whoever owns it; they register a forget hook so a closed session's
# state goes away with it.
_forget_hooks: list[Any] = []


def on_close(hook: Any) -> None:
    """Call ``hook(session_id)`` whenever a session closes."""
    _forget_hooks.append(hook)


def _run_forget_hooks(session_id: int) -> None:
    for hook in list(_forget_hooks):
        try:
            hook(session_id)
        except Exception:  # noqa: BLE001 - cleanup must never break a disconnect
            pass
