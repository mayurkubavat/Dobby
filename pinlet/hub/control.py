"""The control connection: what the Pinlet app (menu bar / tray) talks to.

Same socket as the AI clients, different hello::

    app    -> {"pinlet_control": 1}
    server -> {"pinlet_ready": 1, "control": 1, "server_pid": 3900}

then one JSON request per line, each answered with the same ``id``::

    {"id": 1, "op": "status"}                    -> {"id": 1, "ok": true, "server_pid": .., "sessions": [..], "agents": [..]}
    {"id": 2, "op": "topbar"}                    -> {"id": 2, "ok": true, "view": {..}}
    {"id": 3, "op": "action", "key": "stop", "pid": 4242}
    {"id": 4, "op": "quit"}                      -> the shared Pinlet exits
    {"id": 5, "op": "answer", "prompt_id": 3, "ok": false, "reason": "use the test account"}
                                                         -> an Allow/Deny card's answer
    {"id": 6, "op": "tell", "pid": 4242, "text": "use the March file"}  -> {"queued": 1, "woke": false}

``activity`` returns ``lines``: the activity journal grouped into sentences
("Tibble · Cursor clicked 4 times in Safari"), oldest first (see :mod:`pinlet.journal`),
and ``messages``: the last messages agents sent each other (``from``, ``to``, ``text``,
``kind``, ``t``; see :mod:`pinlet.control.peers`).

``topbar`` also returns ``prompts``: the Allow/Deny questions waiting for the
app's card (see :mod:`pinlet.hub.prompts`).

``tell`` queues an instruction from the person for that Pinlet's AI (see
:mod:`pinlet.control.inbox`); it reaches the AI with its next tool result, and
wakes a Pinlet whose AI waits in ``wait_for_wake``.

``action`` keys: ``pause_toggle``, ``pause``, ``resume``, ``stop``, ``hide``, ``wake`` (need
``pid``), ``pause_all``, ``resume_all``, ``stop_all``, ``clear_finished``,
``show_hidden``; ``allow`` / ``deny`` take ``prompt_id``. Stopped and
cleared Pinlets leave the menu until they work again. They go through
:mod:`pinlet.control.commands`, the same files the overlay's Pinlet picker
writes, so they reach private Pinlets too.

``view`` is the menu's content as plain JSON (header, subtitle, rows,
all_actions, icon_state, icon_title, tooltip, palette): the Swift status item
and the Linux tray draw the same thing.
"""

from __future__ import annotations

import os
import sys
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
    "wake",
    "allow",
    "deny",
)


def _root():
    from pinlet.control import control_root

    return control_root()


def agents() -> list[dict[str, Any]]:
    """Every Pinlet agent on this computer (shared sessions and private ones)."""
    try:
        from pinlet.control import commands

        return commands.overview(_root())
    except Exception:  # noqa: BLE001
        return []


def status() -> dict[str, Any]:
    """Server pid, open sessions and every agent, for the menu."""
    return {
        "server_pid": os.getpid(),
        "python": sys.executable,
        "app_owned": os.environ.get("PINLET_APP_OWNED") == "1",
        "sessions": sessions.snapshot(),
        "agents": agents(),
    }


def action(key: str, pid: int | None = None, prompt_id: int | None = None) -> dict[str, Any]:
    """Runs a menu action for one agent, every agent, or a pending prompt.

    Per-agent keys need ``pid``; ``allow`` and ``deny`` need ``prompt_id``; the
    ``_all`` forms, ``clear_finished`` and ``show_hidden`` need neither.

    Raises:
        ValueError: ``key`` is not one of ``ACTION_KEYS``, or a needed id is missing.
    """
    from pinlet.control import commands
    from pinlet.overlay import topbar as tb

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
    if key == "wake":
        from pinlet.control import inbox

        return {"done": key, "pid": pid, "wake": inbox.request_wake(root, pid)}
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


def tell(pid: int | None, text: str) -> dict[str, Any]:
    """Queues the person's instruction for the Pinlet with ``pid``.

    A Pinlet whose AI waits in ``wait_for_wake`` is woken too, so the AI reads
    the instruction at once instead of after the next wake.

    Raises:
        ValueError: no ``pid``, or ``text`` is empty.
    """
    from pinlet.control import inbox

    if pid is None:
        raise ValueError("tell needs a pid")
    root = _root()
    pid = int(pid)
    queued = inbox.add(root, pid, text)
    woke = inbox.read(root, pid)["listening"]
    if woke:
        inbox.request_wake(root, pid)
    return {"pid": pid, "queued": queued, "woke": woke}


# ------------------------------------------------------------------ the menu
WORKING_WINDOW_S = 15.0  # a session counts as working this long after its last tool call


def session_states(
    snapshot: list[dict[str, Any]] | None = None, now: float | None = None
) -> dict[int, dict[str, Any]]:
    """Map shared-Pinlet sessions onto the menu's per-pid states."""
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
            "failures": int(s.get("failures") or 0),
            "reason": str(s.get("waiting_for") or ""),
        }
    return out


def _view(hiding=None):
    from pinlet.overlay import topbar as tb

    from . import prompts

    try:
        states: dict[int, dict[str, Any]] = dict(tb.read_states())  # private Pinlets' overlays
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
    """The menu as JSON (``pinlet.overlay.topbar.View.to_dict``)."""
    from pinlet.overlay import topbar as tb

    return _view(tb.Hiding.load()).to_dict()


def handle(request: dict[str, Any], quit_cb: Callable[[], None] | None = None) -> dict[str, Any]:
    """Answers one request on the control socket.

    Never raises: errors come back as ``{"ok": False, "error": ...}``.
    """
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
        elif op == "activity":
            from pinlet import journal
            from pinlet.control import peers

            limit = int(request.get("limit") or 200)
            body = {
                "lines": journal.summarize(journal.read(max(1, min(limit, 2000)))),
                "messages": peers.recent_messages(_root(), 10),
            }
        elif op == "answer":
            from . import prompts

            body = {
                "answered": prompts.answer(
                    int(request.get("prompt_id") or 0),
                    bool(request.get("ok")),
                    always=bool(request.get("always")),
                    reason=str(request.get("reason") or ""),
                )
            }
        elif op == "action":
            body = action(
                str(request.get("key") or ""), request.get("pid"), request.get("prompt_id")
            )
        elif op == "tell":
            body = tell(request.get("pid"), str(request.get("text") or ""))
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
