"""Allow/Deny prompts that Dobby.app shows as native cards and notifications.

When the Mac app owns the shared Dobby (``DOBBY_APP_OWNED=1``) and is
polling the control socket, :meth:`OverlayClient.confirm` hands the question
here instead of drawing the overlay's pill. The app reads
:func:`pending` (sent with every ``topbar`` reply), shows the card and answers
with the control op ``answer``. No answer before the deadline is a Deny, as
with the pill.

If the app stops polling (quit, crashed, an old build) the overlay's own pill
is used again, so a prompt is never shown nowhere.
"""

from __future__ import annotations

import itertools
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

LISTEN_WINDOW_S = 3.0  # the app polls every second; three misses means it is gone

_ids = itertools.count(1)
_lock = threading.Lock()
_pending: Dict[int, "Prompt"] = {}
_last_poll = 0.0


@dataclass
class Prompt:
    id: int
    text: str
    detail: str = ""
    allow: str = "Allow"
    deny: str = "Deny"
    kind: str = "danger"      # "danger" (a risky step) or "access" (may Dobby use an app?)
    pid: Optional[int] = None  # the client whose Dobby asks, when known
    name: str = ""             # "Tibble · Claude Code"
    created: float = field(default_factory=time.time)
    deadline: float = 0.0
    answer: Optional[bool] = None
    done: threading.Event = field(default_factory=threading.Event, repr=False)

    def as_dict(self, now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        return {"id": self.id, "text": self.text, "detail": self.detail, "allow": self.allow,
                "deny": self.deny, "kind": self.kind, "pid": self.pid, "name": self.name,
                "secs_left": round(max(0.0, self.deadline - now), 1)}


def enabled() -> bool:
    return os.environ.get("DOBBY_APP_OWNED") == "1" and os.environ.get("DOBBY_APP_PROMPTS", "1") != "0"


def note_poll(now: Optional[float] = None) -> None:
    """The app asked for the menu: it is alive and will show prompts."""
    global _last_poll
    _last_poll = time.monotonic() if now is None else now


def app_listening(now: Optional[float] = None) -> bool:
    now = time.monotonic() if now is None else now
    return enabled() and _last_poll > 0 and now - _last_poll <= LISTEN_WINDOW_S


def _who() -> Dict[str, Any]:
    try:
        from . import sessions

        info = sessions.CURRENT.get()
    except Exception:  # noqa: BLE001
        info = None
    if info is None:
        return {}
    return {"pid": info.pid, "name": info.name}


def ask(text: str, secs: float = 30.0, detail: str = "", allow: str = "Allow", deny: str = "Deny",
        kind: str = "danger") -> bool:
    """Show the question in the app and wait for it. False on Deny or timeout."""
    secs = max(1.0, float(secs))
    p = Prompt(id=next(_ids), text=text, detail=detail or "", allow=allow or "Allow",
               deny=deny or "Deny", kind=kind or "danger", **_who())
    p.deadline = p.created + secs
    with _lock:
        _pending[p.id] = p
    try:
        p.done.wait(secs)
    finally:
        with _lock:
            _pending.pop(p.id, None)
    return bool(p.answer)


def answer(prompt_id: int, ok: bool) -> bool:
    """The app's answer. False when that prompt is no longer waiting."""
    with _lock:
        p = _pending.get(int(prompt_id))
    if p is None or p.done.is_set():
        return False
    p.answer = bool(ok)
    p.done.set()
    return True


def deny_all() -> int:
    """Stop / Quit: every waiting question is a Deny."""
    with _lock:
        waiting = list(_pending.values())
    for p in waiting:
        if not p.done.is_set():
            p.answer = False
            p.done.set()
    return len(waiting)


def pending(now: Optional[float] = None) -> List[Dict[str, Any]]:
    with _lock:
        waiting = sorted(_pending.values(), key=lambda p: p.id)
    return [p.as_dict(now) for p in waiting if not p.done.is_set()]


def _reset_for_tests() -> None:
    global _last_poll
    deny_all()
    with _lock:
        _pending.clear()
    _last_poll = 0.0
