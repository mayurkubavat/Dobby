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
    {"id": 7, "op": "companion", "visible": true, "frame": [x, y, w, h], "bar": true}
                                                         -> {"avoid": null, "stage": {..}}

``companion`` is Rime's heartbeat (Pinlet.app only): while it is fresh the
floating bar stays hidden; the reply says where to step aside and what the
bar would show (``stage``, also returned by ``topbar``).

``activity`` returns ``lines``: the activity journal grouped into sentences
("Tibble · Cursor clicked 4 times in Safari"), oldest first (see :mod:`pinlet.journal`),
and ``messages``: the last messages agents sent each other (``from``, ``to``, ``text``,
``kind``, ``t``; see :mod:`pinlet.control.peers`), and ``tasks``: the open and claimed
tasks on the shared board, each with a one-line ``line`` (see :mod:`pinlet.control.tasks`).
``tasks`` returns the board alone (``"all": true`` adds finished tasks).

``topbar`` also returns ``prompts``: the Allow/Deny questions waiting for the
app's card (see :mod:`pinlet.hub.prompts`).

``tell`` queues an instruction from the person for that Pinlet's AI (see
:mod:`pinlet.control.inbox`); it reaches the AI with its next tool result, and
wakes a Pinlet whose AI waits in ``wait_for_wake``.

``action`` keys: ``pause_toggle``, ``pause``, ``resume``, ``step``, ``stop``, ``hide``, ``wake`` (need
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

import logging
import os
import sys
import time
from collections.abc import Callable, Mapping
from typing import Any

from . import sessions

log = logging.getLogger("pinlet.hub.control")

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
    "step",
    "allow",
    "deny",
)


def _root():
    from pinlet.control import control_root

    return control_root()


def _board(include_finished: bool = False) -> list[dict[str, Any]]:
    """The shared task board for the app: open and claimed tasks (all with ``include_finished``)."""
    from pinlet.control import tasks

    rows = tasks.board(_root())
    if not include_finished:
        rows = [t for t in rows if t["status"] in (tasks.OPEN, tasks.CLAIMED)]
    return [{**t, "line": tasks.line(t)} for t in rows]


def agents() -> list[dict[str, Any]]:
    """Every Pinlet agent on this computer (shared sessions and private ones)."""
    try:
        from pinlet.control import commands

        return commands.overview(_root())
    except Exception:  # noqa: BLE001
        return []


def is_standin(platform: str = sys.platform) -> bool:
    """True for an engine Pinlet.app should replace: one it did not start, on a Mac where it is installed.

    That is an engine a connector started while the app would not open (it sets
    ``PINLET_STANDIN=1``), including one started by an older connector that does not.
    ``PINLET_APP=0`` keeps a ``pinlet serve`` you started yourself.
    """
    if os.environ.get("PINLET_APP_OWNED") == "1":
        return False
    if os.environ.get("PINLET_STANDIN") == "1":
        return True
    from . import connector

    return connector.find_app(platform=platform) is not None


def status() -> dict[str, Any]:
    """Server pid, open sessions and every agent, for the menu."""
    return {
        "server_pid": os.getpid(),
        "python": sys.executable,
        "app_owned": os.environ.get("PINLET_APP_OWNED") == "1",
        "standin": is_standin(),
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
                _end_task_rules(p)
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
        _end_task_rules(pid)
        tb.Hiding.load().hide([pid])
    elif key == "step":
        commands.step_once(root, pid)
    elif key == "pause_toggle":
        commands.set_paused(root, pid, not commands.read(root, pid)["paused"])
    else:
        commands.set_paused(root, pid, key == "pause")
    if key in ("stop", "hide"):
        return {"done": key, "pid": pid}
    return {"done": key, "pid": pid, "paused": commands.read(root, pid)["paused"]}


def _end_task_rules(pid: int) -> None:
    """Stop also ends that AI's "for this task" rules."""
    try:
        from pinlet.overlay import rules

        rules.book().end_task(pid)
    except Exception:  # noqa: BLE001 - Stop itself must never fail
        log.debug("could not end task rules for %s", pid, exc_info=True)


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
        recent = (
            bool(s.get("last_call"))
            and now - float(s["last_call"]) < WORKING_WINDOW_S
            and not s.get("done")
        )
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


def companion_beat(request: Mapping[str, Any]) -> dict[str, Any]:
    """The companion's heartbeat (see :mod:`pinlet.overlay.companion`).

    Only Pinlet.app may send it: a fresh heartbeat hides the floating bar.
    The reply carries ``avoid``, a point the companion should step away from
    (or None), and ``stage``, what the bar would show.
    """
    from pinlet.overlay import companion

    frame = request.get("frame")
    companion.beat(
        visible=bool(request.get("visible")),
        frame=frame if isinstance(frame, list) else None,
        bar=bool(request.get("bar")),
    )
    return {"avoid": companion.avoid_request(), "stage": stage()}


def stage() -> dict[str, Any]:
    """What the bar shows right now, for the companion (``topbar.stage_of``)."""
    from pinlet.overlay import topbar as tb

    try:
        return tb.stage_of(tb.read_states())
    except Exception:  # noqa: BLE001
        return {}


def topbar() -> dict[str, Any]:
    """The menu as JSON (``pinlet.overlay.topbar.View.to_dict``)."""
    from pinlet.overlay import topbar as tb

    return _view(tb.Hiding.load()).to_dict()


# Requests that stand for the person: answering a question, speaking to the AI
# as the person, and letting a paused AI go on. Only Pinlet.app may send them.
_PERSON_ACTIONS = frozenset({"allow", "resume", "resume_all", "pause_toggle", "step"})
NOT_THE_APP = "only Pinlet.app can do this, for the person at the Mac"


def from_app(peer_pid: int | None, env: Mapping[str, str] | None = None) -> bool:
    """True when a control connection may speak for the person.

    When Pinlet.app owns this engine it passes its process id in
    ``PINLET_APP_PID``; the kernel says which process is on the other end of
    the socket, so only the app itself passes. Any other process of the same
    user (an AI's shell command, a script) can still read status and Pause or
    Stop, but cannot Allow, Resume or "tell". Without the app (Linux, a
    private ``pinlet serve``) there is nothing to compare with and every
    connection is taken as the person's, as before.
    """
    from pinlet.overlay.offlimits import app_pid

    app = app_pid(env)
    if app is None:
        return True
    return peer_pid is not None and int(peer_pid) == app


def _needs_person(request: Mapping[str, Any]) -> bool:
    op = request.get("op")
    if op in ("answer", "tell", "rules_key", "companion"):
        return True
    return op == "action" and str(request.get("key") or "") in _PERSON_ACTIONS


def handle(
    request: dict[str, Any],
    quit_cb: Callable[[], None] | None = None,
    trusted: bool = True,
) -> dict[str, Any]:
    """Answers one request on the control socket.

    ``trusted`` is False for a connection that is not Pinlet.app's (see
    :func:`from_app`): requests that stand for the person are refused.

    Never raises: errors come back as ``{"ok": False, "error": ...}``.
    """
    rid = request.get("id")
    op = request.get("op")
    if not trusted and _needs_person(request):
        log.warning("refused control request %r from a process that is not Pinlet.app", op)
        return {"id": rid, "ok": False, "error": NOT_THE_APP}
    try:
        if op == "status":
            body = status()
        elif op == "topbar":
            from . import prompts

            prompts.note_poll()  # the app is alive: Allow/Deny goes to its card
            body = {"view": topbar(), "prompts": prompts.pending(), "stage": stage()}
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
                "tasks": _board(),
            }
        elif op == "tasks":
            body = {"tasks": _board(include_finished=bool(request.get("all")))}
        elif op == "answer":
            from . import prompts

            body = {
                "answered": prompts.answer(
                    int(request.get("prompt_id") or 0),
                    bool(request.get("ok")),
                    always=bool(request.get("always")),
                    reason=str(request.get("reason") or ""),
                    remember=str(request.get("remember") or ""),
                    wide=bool(request.get("wide")),
                )
            }
        elif op == "rules":
            from pinlet.overlay import grants, rules

            body = {
                "rules": rules.book().listing(),
                "apps": grants.remembered(),
                "can_save": rules.book().can_save,
            }
        elif op == "rules_key":
            from pinlet.overlay import rules

            try:
                key = bytes.fromhex(str(request.get("key") or ""))
            except ValueError:
                key = b""
            rules.book().set_key(key)
            body = {"can_save": rules.book().can_save}
        elif op == "revoke":
            from pinlet.overlay import grants, rules

            if request.get("all"):
                body = {"revoked": rules.book().revoke_all() + grants.forget_all()}
            elif request.get("app"):
                body = {"revoked": int(grants.forget(str(request.get("app"))))}
            else:
                body = {"revoked": int(rules.book().revoke(str(request.get("rule") or "")))}
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
        elif op == "companion":
            body = companion_beat(request)
        elif op == "seen":
            from pinlet.overlay import companion as _companion

            body = {"items": _companion.read_seen()}
        elif op == "ping":
            body = {"pong": True}
        else:
            raise ValueError(f"unknown op {op!r}")
    except Exception as exc:  # noqa: BLE001
        return {"id": rid, "ok": False, "error": str(exc)}
    return {"id": rid, "ok": True, **body}
