"""What the person sends to one running Dobby: typed instructions and wake-ups.

The Dobby app's activity panel (and the ``tell`` / ``wake`` control actions)
write ``inbox/<pid>.json`` for the Dobby with that pid::

    {"notes": [{"text": "use the March invoice", "at": 1762570000.0}],
     "wake": 2, "listening": true}

* ``notes``: instructions waiting for the AI. The server hands them over with
  the next tool result (see ``dobby.server._attach_person_notes``) and takes
  them off the list, so each one reaches the AI once.
* ``wake``: grows by one each time the person wakes this Dobby from the menu or
  the panel. ``wait_for_wake`` returns when it grows (the bar no longer has to
  be on screen to wake Dobby).
* ``listening``: the AI is in ``wait_for_wake`` right now, so the menu can
  offer Wake for that Dobby.

Plain files keep it cross-process like :mod:`dobby.control.commands`: a shared
Dobby and private Dobbys read the same place. Notes come only from the person
(the app talks over the owner-only control socket); text on screen never
lands here.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .lease import file_lock

MAX_CHARS = 2000  # one instruction
MAX_QUEUED = 10  # older ones drop off first


def _path(root: Path, pid: int) -> Path:
    return Path(root) / "inbox" / f"{int(pid)}.json"


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"notes": [], "wake": 0, "listening": False}
    if not isinstance(data, dict):
        data = {}
    notes = [n for n in data.get("notes") or [] if isinstance(n, dict) and n.get("text")]
    try:
        wake = int(data.get("wake") or 0)
    except (TypeError, ValueError):
        wake = 0
    return {"notes": notes, "wake": wake, "listening": bool(data.get("listening"))}


def _save(path: Path, state: dict[str, Any], pid: int) -> None:
    tmp = path.with_name(f".{int(pid)}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, path)


def _change(root: Path, pid: int, fn: Any) -> dict[str, Any]:
    path = _path(root, pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path.with_suffix(".lock")):
        state = _load(path)
        out = fn(state)
        _save(path, state, pid)
        return out if out is not None else state


def read(root: Path, pid: int) -> dict[str, Any]:
    """The inbox of the Dobby with ``pid``: ``notes``, ``wake`` and ``listening``."""
    return _load(_path(root, pid))


def add(root: Path, pid: int, text: str, now: float | None = None) -> int:
    """Queues an instruction for the Dobby with ``pid``; returns how many now wait.

    Raises:
        ValueError: ``text`` is empty once whitespace is stripped.
    """
    text = str(text).strip()[:MAX_CHARS]
    if not text:
        raise ValueError("nothing to send")
    at = time.time() if now is None else now

    def put(state: dict[str, Any]) -> int:
        state["notes"] = ([*state["notes"], {"text": text, "at": at}])[-MAX_QUEUED:]
        return len(state["notes"])

    return _change(root, pid, put)


def take(root: Path, pid: int) -> list[str]:
    """Removes and returns the waiting instructions, oldest first (cheap when none)."""
    path = _path(root, pid)
    if not path.exists():
        return []
    if not _load(path)["notes"]:
        return []

    def grab(state: dict[str, Any]) -> list[str]:
        texts = [str(n["text"]) for n in state["notes"]]
        state["notes"] = []
        return texts

    return _change(root, pid, grab)


def request_wake(root: Path, pid: int) -> int:
    """Wakes the Dobby with ``pid`` from wherever (menu, panel); returns the new count."""

    def bump(state: dict[str, Any]) -> int:
        state["wake"] += 1
        return state["wake"]

    return _change(root, pid, bump)


def set_listening(root: Path, pid: int, on: bool) -> None:
    """Marks whether this Dobby's AI waits in ``wait_for_wake`` right now."""

    def mark(state: dict[str, Any]) -> None:
        state["listening"] = bool(on)

    _change(root, pid, mark)


def clear(root: Path, pid: int) -> None:
    """Deletes the inbox (and its lock) for ``pid``."""
    for p in (_path(root, pid), _path(root, pid).with_suffix(".lock")):
        try:
            p.unlink()
        except OSError:
            pass
