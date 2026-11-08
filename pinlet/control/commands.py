"""Per-Pinlet Pause, Resume and Stop sent from any control bar.

Each Pinlet server owns ``commands/<pid>.json``: ``{"paused": bool, "stop": n}``.
The overlay's Pinlet picker writes it; that server's :class:`Controller` reads
it before every action, waits while paused and stops when ``stop`` grows.
Plain files keep it cross-process and cross-platform, like the lease.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import inbox, registry
from .lease import LeaseStore, file_lock


def _path(root: Path, pid: int) -> Path:
    return Path(root) / "commands" / f"{int(pid)}.json"


def read(root: Path, pid: int) -> dict[str, Any]:
    """Reads the pending commands (pause, stop) for the Pinlet with ``pid``.

    Returns an empty dict when there are none or the file cannot be read.
    """
    try:
        data = json.loads(_path(root, pid).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {"paused": bool(data.get("paused")), "stop": int(data.get("stop", 0))}
    except (OSError, ValueError, TypeError):
        pass
    return {"paused": False, "stop": 0}


def _update(root: Path, pid: int, **change: Any) -> dict[str, Any]:
    path = _path(root, pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path.with_suffix(".lock")):
        cur = read(root, pid)
        if "stop" in change:
            cur["stop"] += 1
            cur["paused"] = False
            change.pop("stop")
        cur.update(change)
        tmp = path.with_name(f".{pid}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(cur), encoding="utf-8")
        os.replace(tmp, path)
        return cur


def set_paused(root: Path, pid: int, paused: bool) -> dict[str, Any]:
    """Asks the Pinlet with ``pid`` to pause or resume; returns the new command state."""
    return _update(root, pid, paused=bool(paused))


def request_stop(root: Path, pid: int) -> dict[str, Any]:
    """Asks the Pinlet with ``pid`` to stop its current task; returns the new command state."""
    return _update(root, pid, stop=True)


def clear(root: Path, pid: int) -> None:
    """Deletes the command file (and its lock) for ``pid``."""
    for p in (_path(root, pid), _path(root, pid).with_suffix(".lock")):
        try:
            p.unlink()
        except OSError:
            pass


def overview(root: Path) -> list[dict[str, Any]]:
    """Running Pinlets for the picker and menu.

    Each has name, pid, paused, holding, waiting, started, color, and from the
    :mod:`~pinlet.control.inbox`: ``listening`` (its AI waits for a wake) and
    ``queued`` (instructions from the person not yet handed to the AI).
    """
    root = Path(root)
    try:
        state = LeaseStore(root).snapshot()
    except OSError:
        state = {}
    holder = (state.get("holder") or {}).get("pid")
    waiting = {int(w.get("pid", 0)) for w in (state.get("waiters") or {}).values()}
    out = []
    for rec in registry.list_agents(root):
        pid = int(rec.get("pid", 0))
        box = inbox.read(root, pid)
        out.append(
            {
                "pid": pid,
                "name": str(rec.get("name") or f"Pinlet-{pid}"),
                "paused": read(root, pid)["paused"],
                "holding": pid == holder,
                "waiting": pid in waiting,
                "started": float(rec.get("started", 0) or 0),
                "color": rec.get("color"),
                "listening": box["listening"],
                "queued": len(box["notes"]),
            }
        )
    out.sort(key=lambda a: (a["name"].lower(), a["pid"]))
    return out


def find(root: Path, pid: int) -> dict[str, Any] | None:
    """The overview entry for the agent with ``pid``, or None."""
    return next((a for a in overview(root) if a["pid"] == pid), None)
