"""The clients connected to the shared Dobby, one :class:`SessionInfo` each.

``snapshot()`` is what status views read (``control_status``, and later the
Dobby app's menu bar / tray). Fields that only the overlay knows (step,
steps, elapsed, app) are filled in when the overlay reports them.
"""

from __future__ import annotations

import contextvars
import itertools
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

_ids = itertools.count(1)


@dataclass
class SessionInfo:
    id: int
    name: str
    pid: Optional[int]
    mode: str
    connected_at: float = field(default_factory=time.time)
    last_call: Optional[float] = None
    last_tool: Optional[str] = None
    calls: int = 0
    app: Optional[str] = None
    step: Optional[int] = None
    steps: Optional[int] = None
    elapsed: Optional[float] = None

    @property
    def turbo(self) -> bool:
        return self.mode != "secure"

    def as_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["turbo"] = self.turbo
        return out


_lock = threading.Lock()
_sessions: Dict[int, SessionInfo] = {}
CURRENT: contextvars.ContextVar[Optional[SessionInfo]] = contextvars.ContextVar("dobby_session", default=None)


def open_session(name: str, pid: Optional[int], mode: str) -> SessionInfo:
    info = SessionInfo(id=next(_ids), name=name, pid=pid, mode=mode)
    with _lock:
        _sessions[info.id] = info
    return info


def close_session(info: SessionInfo) -> None:
    with _lock:
        _sessions.pop(info.id, None)


def note_call(tool: str) -> None:
    info = CURRENT.get()
    if info is None:
        return
    info.calls += 1
    info.last_call = time.time()
    info.last_tool = tool


def rename(name: Optional[str]) -> None:
    info = CURRENT.get()
    if info is not None and name:
        info.name = name


def count() -> int:
    with _lock:
        return len(_sessions)


def snapshot() -> List[Dict[str, Any]]:
    with _lock:
        return [s.as_dict() for s in sorted(_sessions.values(), key=lambda s: s.id)]
