"""The control connection: what the Dobby app (menu bar / tray) talks to.

Same socket as the AI clients, different hello::

    app    -> {"dobby_control": 1}
    server -> {"dobby_ready": 1, "control": 1, "server_pid": 3900}

then one JSON request per line, each answered with the same ``id``::

    {"id": 1, "op": "status"}                    -> {"id": 1, "ok": true, "server_pid": .., "sessions": [..], "agents": [..]}
    {"id": 2, "op": "topbar"}                    -> {"id": 2, "ok": true, "view": {..}}
    {"id": 3, "op": "action", "key": "stop", "pid": 4242}
    {"id": 4, "op": "quit"}                      -> the shared Dobby exits

``action`` keys: ``pause_toggle``, ``pause``, ``resume``, ``stop`` (need
``pid``), ``pause_all``, ``resume_all``, ``stop_all``. They go through
:mod:`dobby.control.commands`, the same files the overlay's Dobby picker
writes, so they reach private Dobbys too.

``view`` is the menu's content as plain JSON (header, subtitle, rows,
all_actions, icon_state, icon_title, tooltip, palette): the Swift status item
and the Linux tray draw the same thing.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Optional

from . import sessions

ACTION_KEYS = ("pause_toggle", "pause", "resume", "stop", "pause_all", "resume_all", "stop_all")


def _root():
    from dobby.control import control_root

    return control_root()


def agents() -> List[Dict[str, Any]]:
    """Every Dobby agent on this computer (shared sessions and private ones)."""
    try:
        from dobby.control import commands

        return commands.overview(_root())
    except Exception:  # noqa: BLE001
        return []


def status() -> Dict[str, Any]:
    return {"server_pid": os.getpid(), "sessions": sessions.snapshot(), "agents": agents()}


def action(key: str, pid: Optional[int] = None) -> Dict[str, Any]:
    from dobby.control import commands

    if key not in ACTION_KEYS:
        raise ValueError(f"unknown action {key!r}")
    root = _root()
    if key.endswith("_all"):
        for agent in agents():
            p = int(agent["pid"])
            if key == "stop_all":
                commands.request_stop(root, p)
            else:
                commands.set_paused(root, p, key == "pause_all")
        return {"done": key}
    if pid is None:
        raise ValueError(f"{key} needs a pid")
    pid = int(pid)
    if key == "stop":
        commands.request_stop(root, pid)
    elif key == "pause_toggle":
        commands.set_paused(root, pid, not commands.read(root, pid)["paused"])
    else:
        commands.set_paused(root, pid, key == "pause")
    return {"done": key, "pid": pid, "paused": commands.read(root, pid)["paused"]}


# ------------------------------------------------------------------ the menu
WORKING_WINDOW_S = 15.0  # a session counts as working this long after its last tool call


def session_states(snapshot: Optional[List[Dict[str, Any]]] = None,
                   now: Optional[float] = None) -> Dict[int, Dict[str, Any]]:
    """Map shared-Dobby sessions onto the menu's per-pid states."""
    snapshot = sessions.snapshot() if snapshot is None else snapshot
    now = time.time() if now is None else now
    out: Dict[int, Dict[str, Any]] = {}
    for s in snapshot:
        if not s.get("pid"):
            continue
        recent = bool(s.get("last_call")) and now - float(s["last_call"]) < WORKING_WINDOW_S
        out[int(s["pid"])] = {
            "mode": "working" if recent else "ready",
            "turbo": bool(s.get("turbo")),
            "step": s.get("last_tool") or "",
            "steps": int(s.get("steps") or 0),
            "elapsed": float(s.get("elapsed") or 0),
            "app": s.get("app") or "",
        }
    return out


def topbar() -> Dict[str, Any]:
    """The menu as JSON (``dobby.overlay.topbar.View.to_dict``)."""
    from dobby.overlay import topbar as tb

    try:
        states: Dict[int, Dict[str, Any]] = dict(tb.read_states())  # private Dobbys' overlays
    except Exception:  # noqa: BLE001
        states = {}
    states.update(session_states())
    return tb.topbar_view(agents(), states).to_dict()


def handle(request: Dict[str, Any], quit_cb: Optional[Callable[[], None]] = None) -> Dict[str, Any]:
    rid = request.get("id")
    op = request.get("op")
    try:
        if op == "status":
            body = status()
        elif op == "topbar":
            body = {"view": topbar()}
        elif op == "action":
            body = action(str(request.get("key") or ""), request.get("pid"))
        elif op == "quit":
            if quit_cb is not None:
                quit_cb()
            body = {"quitting": True}
        elif op == "ping":
            body = {"pong": True}
        else:
            raise ValueError(f"unknown op {op!r}")
    except Exception as exc:  # noqa: BLE001
        return {"id": rid, "ok": False, "error": str(exc)}
    return {"id": rid, "ok": True, **body}
