"""The other AI agents using this computer's Pinlet: who they are and how to reach them.

Backs the ``list_agents``, ``send_to_agent`` and ``hand_control_to`` tools.
Agents are the live entries of :mod:`pinlet.control.registry` (shared-Pinlet
clients and private Pinlets alike). A message lands in the target's
:mod:`pinlet.control.inbox` marked with the sender, and reaches that AI with
its next tool result, or at once when it waits in ``wait_for_wake``.

A message is a request from one AI to another, never the person's word: it
cannot answer an Allow/Deny card, change a mode or grant an app, and the
receiving AI is told so (see ``pinlet.server.AGENT_NOTE``).
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from . import inbox, names, registry
from .lease import LeaseStore, file_lock, polling

RATE_PER_MIN = 10  # messages one agent may send per minute
RECENT_KEEP = 30  # agent-to-agent messages kept for the activity panel
_sent: dict[str, deque[float]] = {}
_sent_lock = threading.Lock()


def roster(root: Path, me_pid: int | None = None, now: float | None = None) -> list[dict[str, Any]]:
    """Every live agent: name, first name, pid, whether it holds control, waits or listens."""
    now = time.time() if now is None else now
    try:
        state = LeaseStore(root).snapshot()
    except OSError:
        state = {}
    holder = (state.get("holder") or {}).get("id")
    waiters = state.get("waiters") or {}
    out = []
    for rec in registry.list_agents(root):
        pid = int(rec.get("pid", 0) or 0)
        aid = str(rec.get("id") or "")
        name = str(rec.get("name") or f"Pinlet-{pid}")
        try:
            listening = bool(inbox.read(root, pid)["listening"])
        except OSError:
            listening = False
        w = waiters.get(aid)
        out.append(
            {
                "name": name,
                "first_name": name.split(names.SEPARATOR)[0],
                "pid": pid,
                "holds_control": aid == holder and bool(holder),
                "waiting_for_control": bool(w) and polling(w, now, _settings()),
                "waiting_for_wake": listening,
                "is_you": me_pid is not None and pid == me_pid,
                "_id": aid,
            }
        )
    return out


def _settings() -> Any:
    from . import settings_from_env

    return settings_from_env()


def public(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows without internal fields."""
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]


def find(rows: list[dict[str, Any]], to: Any) -> dict[str, Any]:
    """The one agent ``to`` names: a pid, a first name ("Pip") or a full name.

    Raises:
        LookupError: nobody, or more than one agent, matches; the message lists the names.
    """
    query = str(to or "").strip()
    others = [r for r in rows if not r["is_you"]]
    if query.isdigit():
        hits = [r for r in others if r["pid"] == int(query)]
    else:
        low = query.lower()
        hits = [r for r in others if r["name"].lower() == low] or [
            r for r in others if r["first_name"].lower() == low
        ]
        if not hits and low:
            hits = [r for r in others if low in r["name"].lower()]
    if len(hits) == 1:
        return hits[0]
    choices = ", ".join(r["name"] for r in others) or "no other agents are connected"
    if not hits:
        raise LookupError(f"No agent called {query!r}. Other agents: {choices}.")
    raise LookupError(f"{query!r} matches several agents ({choices}); use one name or a pid.")


def allow_send(sender_key: str, now: float | None = None) -> bool:
    """At most :data:`RATE_PER_MIN` messages per minute per sender."""
    now = time.monotonic() if now is None else now
    with _sent_lock:
        q = _sent.setdefault(sender_key, deque())
        while q and now - q[0] > 60.0:
            q.popleft()
        if len(q) >= RATE_PER_MIN:
            return False
        q.append(now)
        return True


def deliver(
    root: Path, target: dict[str, Any], text: str, sender: dict[str, Any], kind: str = "message"
) -> dict[str, Any]:
    """Queues ``text`` for ``target`` from ``sender``; wakes it when it waits for a wake."""
    pid = int(target["pid"])
    inbox.add(root, pid, text, sender=sender, kind=kind)
    _remember(root, sender, target, text, kind)
    woke = bool(inbox.read(root, pid)["listening"])
    if woke:
        inbox.request_wake(root, pid)
    return {
        "sent": True,
        "to": target["name"],
        "delivery": "now (it was waiting)" if woke else "with its next Pinlet tool call",
    }


def _recent_path(root: Path) -> Path:
    return Path(root) / "agent_messages.json"


def _remember(
    root: Path, sender: dict[str, Any], target: dict[str, Any], text: str, kind: str
) -> None:
    """Keeps the message for the person's activity panel (the last ``RECENT_KEEP``)."""
    path = _recent_path(root)
    entry = {
        "t": time.time(),
        "from": str(sender.get("name") or "An agent"),
        "to": str(target.get("name") or "an agent"),
        "kind": kind,
        "text": " ".join(str(text).split())[:300],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(path.with_suffix(".lock")):
            kept = recent_messages(root) + [entry]
            tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(kept[-RECENT_KEEP:], fh)
            os.replace(tmp, path)
    except OSError:
        pass  # the message itself was delivered; the panel just misses it


def recent_messages(root: Path, limit: int = RECENT_KEEP) -> list[dict[str, Any]]:
    """The latest messages agents sent each other, oldest first (``from``, ``to``, ``text``)."""
    try:
        data = json.loads(_recent_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = (
        [m for m in data if isinstance(m, dict) and m.get("text")] if isinstance(data, list) else []
    )
    return rows[-max(0, limit) :] if limit else []
