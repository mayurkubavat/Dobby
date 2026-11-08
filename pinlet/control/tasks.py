"""A shared task board for the AI agents using this computer's Pinlet.

Backs the ``post_task``, ``list_tasks``, ``claim_task`` and ``finish_task``
tools. Any agent can post a task (optionally for one named agent), any agent
can claim an open one, and the owner finishes it with a result or a failure
code. The poster hears about the finish through its inbox, like a message, and
an agent a task was posted for hears about the new task the same way.

The board is one JSON file under the control root, changed under a file lock,
so private Pinlets and the shared one see the same tasks. A claimed task whose
owner's process is gone goes back to open. Finished tasks are kept for a day,
and at most :data:`KEEP` tasks are kept in all.

A task is work agents agreed on among themselves, never the person's word: a
task cannot approve an Allow/Deny card, and the agent doing it is told who
posted it.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .lease import file_lock
from .registry import pid_alive

OPEN, CLAIMED, DONE, FAILED = "open", "claimed", "done", "failed"
STATUSES = (OPEN, CLAIMED, DONE, FAILED)
# Why a task failed, so the poster can decide what to do next.
FAILURES = {
    "needs_person": "it needs the person (a login, a choice, an approval)",
    "blocked": "Pinlet may not act there, or the person said Deny",
    "not_found": "what it needed was not there",
    "error": "something went wrong",
    "gave_up": "the owner gave it back without finishing",
}
KEEP = 50
DONE_KEEP_S = 24 * 3600
MAX_TITLE = 200
MAX_TEXT = 2000


def _path(root: Path) -> Path:
    return Path(root) / "tasks.json"


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    tasks = [t for t in data.get("tasks") or [] if isinstance(t, dict) and t.get("id")]
    return {"next": int(data.get("next") or 1), "tasks": tasks}


def _save(path: Path, state: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)


def _tidy(state: dict[str, Any], now: float, alive: Any) -> None:
    """Reopens tasks whose owner is gone; drops old finished ones; keeps at most KEEP."""
    kept = []
    for t in state["tasks"]:
        if t["status"] == CLAIMED and not alive(int((t.get("owner") or {}).get("pid") or 0)):
            t.update(status=OPEN, owner=None, updated=now, note="its owner left; open again")
        if t["status"] in (DONE, FAILED) and now - float(t.get("updated") or 0) > DONE_KEEP_S:
            continue
        kept.append(t)
    state["tasks"] = kept[-KEEP:]


def _change(root: Path, fn: Any, now: float | None = None, alive: Any = pid_alive) -> Any:
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time() if now is None else now
    with file_lock(path.with_suffix(".lock")):
        state = _load(path)
        _tidy(state, now, alive)
        out = fn(state, now)
        _save(path, state)
        return out


def _who(agent: dict[str, Any]) -> dict[str, Any]:
    return {"name": str(agent.get("name") or "An agent"), "pid": int(agent.get("pid") or 0)}


def _clean(text: Any, limit: int) -> str:
    return str(text or "").strip()[:limit]


def _find(state: dict[str, Any], task_id: Any) -> dict[str, Any]:
    try:
        wanted = int(task_id)
    except (TypeError, ValueError):
        raise LookupError(f"{task_id!r} is not a task number.") from None
    for t in state["tasks"]:
        if t["id"] == wanted:
            return t
    raise LookupError(f"There is no task {wanted}. list_tasks shows the board.")


def post(
    root: Path,
    by: dict[str, Any],
    title: str,
    detail: str = "",
    for_agent: dict[str, Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Adds an open task and returns it.

    Raises:
        ValueError: the title is empty.
    """
    title = " ".join(_clean(title, MAX_TITLE).split())
    if not title:
        raise ValueError("A task needs a title: one line saying what should be done.")

    def add(state: dict[str, Any], at: float) -> dict[str, Any]:
        task = {
            "id": state["next"],
            "title": title,
            "detail": _clean(detail, MAX_TEXT),
            "status": OPEN,
            "by": _who(by),
            "for": _who(for_agent) if for_agent else None,
            "owner": None,
            "result": "",
            "failure": "",
            "created": at,
            "updated": at,
        }
        state["next"] += 1
        state["tasks"].append(task)
        return dict(task)

    return _change(root, add, now)


def claim(
    root: Path, who: dict[str, Any], task_id: Any, now: float | None = None, alive: Any = pid_alive
) -> dict[str, Any]:
    """Makes ``who`` the owner of an open task.

    Raises:
        LookupError: no such task.
        PermissionError: it is not open, or it was posted for another agent.
    """
    me = _who(who)

    def take(state: dict[str, Any], at: float) -> dict[str, Any]:
        t = _find(state, task_id)
        owner = t.get("owner") or {}
        if t["status"] == CLAIMED and owner.get("pid") == me["pid"]:
            return dict(t)
        if t["status"] != OPEN:
            held = f" by {owner['name']}" if owner.get("name") else ""
            raise PermissionError(f"Task {t['id']} is {t['status']}{held}, not open.")
        meant = t.get("for") or {}
        if meant.get("pid") and meant["pid"] != me["pid"] and alive(int(meant["pid"])):
            raise PermissionError(f"Task {t['id']} was posted for {meant['name']}.")
        t.update(status=CLAIMED, owner=me, updated=at)
        return dict(t)

    return _change(root, take, now, alive)


def finish(
    root: Path,
    who: dict[str, Any],
    task_id: Any,
    result: str = "",
    failure: str = "",
    now: float | None = None,
    alive: Any = pid_alive,
) -> dict[str, Any]:
    """Closes a task its owner holds: done with ``result``, or failed with a ``failure`` code.

    ``failure="gave_up"`` puts the task back on the board as open instead.

    Raises:
        LookupError: no such task.
        PermissionError: ``who`` does not own it.
        ValueError: an unknown failure code.
    """
    me = _who(who)
    if failure and failure not in FAILURES:
        raise ValueError(f"Unknown failure {failure!r}; one of {', '.join(FAILURES)}.")

    def close(state: dict[str, Any], at: float) -> dict[str, Any]:
        t = _find(state, task_id)
        if t["status"] != CLAIMED or (t.get("owner") or {}).get("pid") != me["pid"]:
            raise PermissionError(f"You do not own task {t['id']}; claim_task it first.")
        text = _clean(result, MAX_TEXT)
        if failure == "gave_up":
            t.update(status=OPEN, owner=None, note=text or "given back", updated=at)
        else:
            t.update(
                status=FAILED if failure else DONE,
                failure=failure,
                result=text,
                updated=at,
            )
        return dict(t)

    return _change(root, close, now, alive)


def board(
    root: Path, status: str = "", now: float | None = None, alive: Any = pid_alive
) -> list[dict[str, Any]]:
    """Tasks on the board, oldest first; ``status`` keeps only those in that state."""
    tasks = _change(root, lambda state, _at: [dict(t) for t in state["tasks"]], now, alive)
    return [t for t in tasks if not status or t["status"] == status]


def line(t: dict[str, Any]) -> str:
    """One task in a sentence: "3 · Fill the form · Pip is on it"."""
    who = (t.get("owner") or {}).get("name", "").split(" · ")[0]
    state = {
        OPEN: "open" + (f", for {t['for']['name'].split(' · ')[0]}" if t.get("for") else ""),
        CLAIMED: f"{who or 'someone'} is on it",
        DONE: "done",
        FAILED: f"failed ({str(t.get('failure') or 'error').replace('_', ' ')})",
    }[t["status"]]
    return f"{t['id']} · {t['title']} · {state}"


def main(argv: list[str] | None = None) -> int:
    """``pinlet tasks [--json] [--all]``: the board (open and claimed tasks unless --all)."""
    from . import control_root

    argv = list(argv or [])
    tasks = board(control_root())
    if "--all" not in argv:
        tasks = [t for t in tasks if t["status"] in (OPEN, CLAIMED)]
    if "--json" in argv:
        print(json.dumps({"ok": True, "tasks": tasks}))
        return 0
    if not tasks:
        print("No tasks on the board.")
    for t in tasks:
        print(line(t))
    return 0
