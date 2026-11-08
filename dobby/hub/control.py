"""The control connection: what the Dobby app (menu bar / tray) talks to.

Same socket as the AI clients, different hello::

    app    -> {"dobby_control": 1}
    server -> {"dobby_ready": 1, "control": 1, "server_pid": 3900}

then one JSON request per line, each answered with the same ``id``::

    {"id": 1, "op": "status"}                    -> {"id": 1, "ok": true, "server_pid": .., "sessions": [..], "agents": [..]}
    {"id": 2, "op": "topbar"}                    -> {"id": 2, "ok": true, "view": {..}}
    {"id": 3, "op": "action", "key": "stop", "pid": 4242}
    {"id": 4, "op": "quit"}                      -> the shared Dobby exits
    {"id": 5, "op": "answer", "prompt_id": 3, "ok": true}  -> an Allow/Deny card's answer

``topbar`` also returns ``prompts``: the Allow/Deny questions waiting for the
app's card (see :mod:`dobby.hub.prompts`).

``action`` keys: ``pause_toggle``, ``pause``, ``resume``, ``stop``, ``hide`` (need
``pid``), ``pause_all``, ``resume_all``, ``stop_all``, ``clear_finished``,
``show_hidden``; ``allow`` / ``deny`` take ``prompt_id``. Stopped and
cleared Dobbys leave the menu until they work again. They go through
:mod:`dobby.control.commands`, the same files the overlay's Dobby picker
writes, so they reach private Dobbys too.

``view`` is the menu's content as plain JSON (header, subtitle, rows,
all_actions, icon_state, icon_title, tooltip, palette): the Swift status item
and the Linux tray draw the same thing.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

from . import sessions

ACTION_KEYS = (
    "pause_toggle",
    "pause",
    "resume",
    "stop",
    "pause_all",
    "resume_all",
    "stop_all",
    "clear_finished",
    "show_hidden",
    "hide",
    "allow",
    "deny",
)


def _root():
    from dobby.control import control_root

    return control_root()


def agents() -> list[dict[str, Any]]:
    """Every Dobby agent on this computer (shared sessions and private ones)."""
    try:
        from dobby.control import commands

        return commands.overview(_root())
    except Exception:  # noqa: BLE001
        return []


def status() -> dict[str, Any]:
    return {"server_pid": os.getpid(), "sessions": sessions.snapshot(), "agents": agents()}


def action(key: str, pid: int | None = None, prompt_id: int | None = None) -> dict[str, Any]:
    from dobby.control import commands
    from dobby.overlay import topbar as tb

    from . import prompts

    if key not in ACTION_KEYS:
        raise ValueError(f"unknown action {key!r}")
    root = _root()
    if key in ("clear_finished", "show_hidden"):
        tb.menu_action(key, view=_view(tb.Hiding.load()))
        return {"done": key}
    if key in ("allow", "deny"):
        if prompt_id is None:
            raise ValueError(f"{key} needs a prompt id")
        return {"done": key, "answered": prompts.answer(int(prompt_id), key == "allow")}
    if key.endswith("_all"):
        everyone = agents()
        for agent in everyone:
            p = int(agent["pid"])
            if key == "stop_all":
                commands.request_stop(root, p)
            else:
                commands.set_paused(root, p, key == "pause_all")
        if key == "stop_all":
            prompts.deny_all()
            tb.Hiding.load().hide(int(a["pid"]) for a in everyone)  # stopped: out of the list
        return {"done": key}
    if pid is None:
        raise ValueError(f"{key} needs a pid")
    pid = int(pid)
    if key == "hide":
        tb.Hiding.load().hide([pid])
    elif key == "stop":
        commands.request_stop(root, pid)
        tb.Hiding.load().hide([pid])
    elif key == "pause_toggle":
        commands.set_paused(root, pid, not commands.read(root, pid)["paused"])
    else:
        commands.set_paused(root, pid, key == "pause")
    if key in ("stop", "hide"):
        return {"done": key, "pid": pid}
    return {"done": key, "pid": pid, "paused": commands.read(root, pid)["paused"]}


# ------------------------------------------------------------------ the menu
WORKING_WINDOW_S = 15.0  # a session counts as working this long after its last tool call


def session_states(
    snapshot: list[dict[str, Any]] | None = None, now: float | None = None
) -> dict[int, dict[str, Any]]:
    """Map shared-Dobby sessions onto the menu's per-pid states."""
    snapshot = sessions.snapshot() if snapshot is None else snapshot
    now = time.time() if now is None else now
    out: dict[int, dict[str, Any]] = {}
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
            "last_call": float(s.get("last_call") or 0.0),
        }
    return out


def _view(hiding=None):
    from dobby.overlay import topbar as tb

    from . import prompts

    try:
        states: dict[int, dict[str, Any]] = dict(tb.read_states())  # private Dobbys' overlays
    except Exception:  # noqa: BLE001
        states = {}
    states.update(session_states())
    for p in prompts.pending():  # a shared session waiting on Allow/Deny in the app
        if p.get("pid"):
            states.setdefault(int(p["pid"]), {})["mode"] = "confirm"
    everyone = agents()
    if hiding is not None:
        hiding.prune(int(a["pid"]) for a in everyone)
    return tb.topbar_view(everyone, states, hiding=hiding)


def topbar() -> dict[str, Any]:
    """The menu as JSON (``dobby.overlay.topbar.View.to_dict``)."""
    from dobby.overlay import topbar as tb

    return _view(tb.Hiding.load()).to_dict()


def handle(request: dict[str, Any], quit_cb: Callable[[], None] | None = None) -> dict[str, Any]:
    rid = request.get("id")
    op = request.get("op")
    try:
        if op == "status":
            body = status()
        elif op == "topbar":
            from . import prompts

            prompts.note_poll()  # the app is alive: Allow/Deny goes to its card
            body = {"view": topbar(), "prompts": prompts.pending()}
        elif op == "prompts":
            from . import prompts

            prompts.note_poll()
            body = {"prompts": prompts.pending()}
        elif op == "answer":
            from . import prompts

            body = {
                "answered": prompts.answer(
                    int(request.get("prompt_id") or 0), bool(request.get("ok"))
                )
            }
        elif op == "action":
            body = action(
                str(request.get("key") or ""), request.get("pid"), request.get("prompt_id")
            )
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
