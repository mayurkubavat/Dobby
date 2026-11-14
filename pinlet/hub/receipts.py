"""Receipts for acting tool calls, so a call cut off by a Pinlet restart is not done twice.

When the shared Pinlet restarts in the middle of ``type_text``, the connector
answers the AI "Pinlet restarted, it may or may not have acted, look again".
Many AIs just send the same call again, and the text is typed twice.

Before an acting tool runs, the hub writes a receipt (``started``) for the
calling client's agent key; after it returns, the receipt says ``done``. The
receipts live in a small file per agent key under the control directory, so
the next Pinlet can read what the last one was doing.

When a client's first identical call (same tool, same arguments) reaches a new
Pinlet within :data:`WINDOW_S` of the old receipt, the call is not run.
The AI gets :func:`blocked_result` instead: look at the screen, and call
again if it did not happen. The receipt is then cleared, so that next call
runs. Repeats within one Pinlet are never blocked: pressing Tab twice is
normal.

Modelled on T3 Code's command receipts (``clientRequestId``), but keyed by
what the call does because MCP clients send no request id of their own.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

# Tools whose repeat changes the world (typing, clicking, running commands).
GUARDED = frozenset(
    {
        "act_on_element",
        "choose_option",
        "click",
        "click_and_type",
        "drag_and_drop",
        "execute_crystallized_skill",
        "execute_trajectory",
        "find_and_click",
        "hotkey",
        "invoke_plugin_feature",
        "press_key",
        "run_terminal_command",
        "type_text",
    }
)
WINDOW_S = 180.0  # a receipt older than this is history, not an interrupted call
KEEP = 20


def _dir(root: Path) -> Path:
    return Path(root) / "receipts"


def _path(root: Path, agent_key: str) -> Path:
    return _dir(root) / f"{agent_key[:32]}.json"


def fingerprint(tool: str, arguments: dict[str, Any] | None) -> str:
    """A short, stable hash of what a call does (its text never lands on disk)."""
    raw = json.dumps([tool, arguments or {}], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _read(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def _write(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(records[-KEEP:]), encoding="utf-8")
    os.replace(tmp, path)


def check(
    root: Path, agent_key: str, tool: str, fp: str, now: float | None = None
) -> dict[str, Any] | None:
    """The old Pinlet's receipt this call repeats, or None when the call may run.

    A match is consumed: the AI's next identical call runs.
    """
    if not agent_key or tool not in GUARDED:
        return None
    now = time.time() if now is None else now
    path = _path(root, agent_key)
    records = _read(path)
    me = os.getpid()
    for i in range(len(records) - 1, -1, -1):
        rec = records[i]
        if rec.get("hub") == me or now - float(rec.get("at", 0)) > WINDOW_S:
            continue
        if rec.get("fp") == fp and rec.get("tool") == tool:
            del records[i]
            _write(path, records)
            return rec
    return None


def started(root: Path, agent_key: str, tool: str, fp: str, now: float | None = None) -> None:
    """Records that ``tool`` is about to act for this agent."""
    if not agent_key or tool not in GUARDED:
        return
    path = _path(root, agent_key)
    records = _read(path)
    records.append(
        {
            "tool": tool,
            "fp": fp,
            "state": "started",
            "at": time.time() if now is None else now,
            "hub": os.getpid(),
        }
    )
    _write(path, records)


def finished(
    root: Path, agent_key: str, tool: str, fp: str, ok: bool, now: float | None = None
) -> None:
    """Marks the newest matching ``started`` receipt done (or failed)."""
    if not agent_key or tool not in GUARDED:
        return
    path = _path(root, agent_key)
    records = _read(path)
    me = os.getpid()
    for rec in reversed(records):
        if rec.get("fp") == fp and rec.get("hub") == me and rec.get("state") == "started":
            rec["state"] = "done" if ok else "failed"
            rec["ended"] = time.time() if now is None else now
            break
    _write(path, records)


SETTLE_S = 2.0  # an answer this old has reached the client


def settle(root: Path, now: float | None = None) -> None:
    """This Pinlet is quitting in good order: forget the calls it has answered.

    A receipt guards against running a call twice when its answer was lost.
    A call that ended more than ``SETTLE_S`` ago has had its answer sent, so
    the AI sending the same call again after the restart means it: it runs.
    Calls still running, or ended a moment ago, keep their receipts. Never
    raises; after a crash nothing is settled and every receipt stands.
    """
    now = time.time() if now is None else now
    me = os.getpid()
    try:
        paths = list(_dir(root).glob("*.json"))
    except OSError:
        return
    for path in paths:
        try:
            records = _read(path)
            kept = [
                r
                for r in records
                if not (
                    r.get("hub") == me
                    and r.get("state") in ("done", "failed")
                    and now - float(r.get("ended", now)) > SETTLE_S
                )
            ]
            if len(kept) != len(records):
                _write(path, kept)
        except (OSError, ValueError, TypeError):
            continue


def blocked_result(rec: dict[str, Any]) -> dict[str, Any]:
    """What the AI gets instead of running a call that may already have happened."""
    state = rec.get("state")
    if state == "done":
        what = "It finished before Pinlet restarted, but its answer was lost."
    elif state == "failed":
        what = "It failed before Pinlet restarted."
    else:
        what = "It was running when Pinlet restarted, so it may or may not have happened."
    return {
        "ok": False,
        "repeat_blocked": True,
        "tool": rec.get("tool"),
        "note": (
            f"Pinlet did not run this call again: you sent the same {rec.get('tool')} just "
            f"before Pinlet restarted. {what} Look at the screen first. If it did not "
            "happen, call it again and it will run."
        ),
    }
