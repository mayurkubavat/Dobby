"""The activity journal: who did what, where, one JSON line per tool call.

``~/.pinlet/logs/activity.ndjson`` (``PINLET_JOURNAL``; ``PINLET_JOURNAL=0``
turns it off), owner-only, rotated at 10 MB with 5 old files kept. Every line
names the agent, the tool, the app, the outcome and how long it took::

    {"t": "2026-11-08T20:15:02Z", "agent": "9f2c1a7b3d4e", "session": 3,
     "name": "Tibble · Cursor", "client": "cursor-vscode", "tool": "click",
     "app": "Safari", "outcome": "ok", "ms": 412}

It never holds what a tool was given or returned: no typed text, no shell
commands, no screenshots, no screen text. ``outcome`` is ``ok``, ``error``,
``repeat_blocked`` or the handoff kind (``denied``, ``stopped``…).

:func:`summarize` turns runs of calls into lines a person reads at a glance,
like T3 Code's activity log: "Tibble · Cursor clicked 4 times and typed once
in Safari". ``pinlet activity`` prints them, and the control op ``activity``
serves them to the app.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

MAX_BYTES = 10 * 1024 * 1024
BACKUPS = 5
_lock = threading.Lock()
_OFF = {"0", "false", "no", "off"}


def path(env: dict[str, str] | None = None) -> Path | None:
    """Where the journal goes, or None when it is off."""
    env = os.environ if env is None else env
    raw = (env.get("PINLET_JOURNAL") or "").strip()
    if raw.lower() in _OFF:
        return None
    return Path(os.path.expanduser(raw or "~/.pinlet/logs/activity.ndjson"))


def _rotate(p: Path) -> None:
    for i in range(BACKUPS - 1, 0, -1):
        older = p.with_name(f"{p.name}.{i}")
        if older.exists():
            os.replace(older, p.with_name(f"{p.name}.{i + 1}"))
    os.replace(p, p.with_name(f"{p.name}.1"))


def record(event: dict[str, Any], where: Path | None = None) -> None:
    """Appends one event (a ``t`` timestamp is added). Never raises."""
    p = where if where is not None else path()
    if p is None:
        return
    line = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **event}
    data = (json.dumps(line, default=str, ensure_ascii=False) + "\n").encode()
    try:
        with _lock:
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                if p.stat().st_size + len(data) > MAX_BYTES:
                    _rotate(p)
            except FileNotFoundError:
                pass
            fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
    except OSError:
        pass


def outcome_of(result: Any, failed: bool = False) -> str:
    """``ok``, ``error``, ``repeat_blocked`` or the handoff kind of an MCP tool result."""
    if failed:
        return "error"
    payload = getattr(result, "structured_content", None)
    if isinstance(payload, dict) and set(payload) == {"result"}:
        payload = payload["result"]
    if isinstance(payload, dict) and payload.get("handoff"):
        return str(payload["handoff"])
    is_error = bool(getattr(result, "is_error", getattr(result, "isError", False)))
    if is_error:
        for block in getattr(result, "content", None) or []:
            text = getattr(block, "text", "") or ""
            if '"repeat_blocked": true' in text:
                return "repeat_blocked"
            if text.startswith("[") and "]" in text:  # "[denied] ..." from a non-dict tool
                kind = text[1 : text.index("]")]
                if kind.isidentifier():
                    return kind
        return "error"
    return "ok"


def read(limit: int = 200, where: Path | None = None) -> list[dict[str, Any]]:
    """The newest ``limit`` events, oldest first (bad lines are skipped)."""
    p = where if where is not None else path()
    if p is None:
        return []
    try:
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 400 * max(1, limit)))
            lines = f.read().splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for raw in lines[-limit:]:
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        if isinstance(ev, dict):
            out.append(ev)
    return out


# What each tool counts as, most telling first (acting before looking).
_VERBS: tuple[tuple[str, str, str, frozenset[str]], ...] = (
    ("ran", "ran a command", "ran {n} commands", frozenset({"run_terminal_command"})),
    (
        "typed",
        "typed once",
        "typed {n} times",
        frozenset({"type_text", "click_and_type"}),
    ),
    (
        "clicked",
        "clicked once",
        "clicked {n} times",
        frozenset({"click", "find_and_click", "act_on_element", "choose_option"}),
    ),
    ("keys", "pressed keys once", "pressed keys {n} times", frozenset({"press_key", "hotkey"})),
    ("dragged", "dragged once", "dragged {n} times", frozenset({"drag_and_drop"})),
    (
        "scrolled",
        "scrolled once",
        "scrolled {n} times",
        frozenset({"scroll", "scroll_until_visible"}),
    ),
    (
        "looked",
        "looked at the screen once",
        "looked at the screen {n} times",
        frozenset(
            {
                "take_screenshot",
                "capture_window",
                "read_screen_text",
                "get_accessibility_tree",
                "find_element",
                "ask_screen",
                "get_screen_state",
                "find_visual_element",
                "list_windows",
                "get_active_window",
            }
        ),
    ),
)
_QUIET = frozenset(
    {"control_status", "request_control", "release_control", "hold_control", "manage_toolsets"}
)


def _phrase(tools: list[str]) -> str:
    counts: dict[str, int] = {}
    other = 0
    for tool in tools:
        for key, _one, _many, names in _VERBS:
            if tool in names:
                counts[key] = counts.get(key, 0) + 1
                break
        else:
            other += 1
    parts: list[str] = []
    for key, one, many, _names in _VERBS:
        n = counts.get(key, 0)
        if n:
            parts.append(one if n == 1 else many.format(n=n))
    shown, rest = parts[:2], len(parts) - 2 + other
    if rest > 0:
        shown.append(f"{rest} more step{'s' if rest != 1 else ''}")
    if not shown:
        return "worked"
    return shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " and " + shown[-1]


def summarize(events: Iterable[dict[str, Any]], gap_s: float = 120.0) -> list[dict[str, Any]]:
    """Groups consecutive calls of one agent in one app into lines, oldest first.

    Each line: ``{"name", "app", "text", "steps", "problems", "start", "end"}``.
    A new line starts when the agent or app changes, or after ``gap_s`` quiet.
    """
    groups: list[dict[str, Any]] = []
    for ev in events:
        tool = str(ev.get("tool") or "")
        if not tool or tool in _QUIET or tool == "wait_for_wake":
            continue
        who = str(ev.get("name") or ev.get("agent") or "An agent")
        app = str(ev.get("app") or "")
        when = _epoch(ev.get("t"))
        last = groups[-1] if groups else None
        if (
            last is None
            or last["name"] != who
            or last["app"] != app
            or (when is not None and last["_end"] is not None and when - last["_end"] > gap_s)
        ):
            last = {"name": who, "app": app, "_tools": [], "problems": [], "start": ev.get("t")}
            last["_end"] = when
            groups.append(last)
        last["_tools"].append(tool)
        last["end"] = ev.get("t")
        last["_end"] = when if when is not None else last["_end"]
        outcome = str(ev.get("outcome") or "ok")
        if outcome != "ok":
            last["problems"].append(outcome)
    out = []
    for g in groups:
        text = f"{g['name']} {_phrase(g['_tools'])}" + (f" in {g['app']}" if g["app"] else "")
        if g["problems"]:
            kinds = sorted(set(g["problems"]))
            text += f" ({', '.join(k.replace('_', ' ') for k in kinds)})"
        out.append(
            {
                "name": g["name"],
                "app": g["app"],
                "text": text,
                "steps": len(g["_tools"]),
                "problems": g["problems"],
                "start": g["start"],
                "end": g.get("end"),
            }
        )
    return out


def _epoch(stamp: Any) -> float | None:
    try:
        return float(time.mktime(time.strptime(str(stamp), "%Y-%m-%dT%H:%M:%SZ")))
    except (TypeError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    """``pinlet activity [--raw] [-n N]``: what the agents did lately."""
    argv = list(argv or [])
    raw = "--raw" in argv
    n = 200
    if "-n" in argv:
        try:
            n = max(1, int(argv[argv.index("-n") + 1]))
        except (IndexError, ValueError):
            print("pinlet activity: -n needs a number")
            return 2
    events = read(n)
    if not events:
        where = path()
        print(f"No activity yet ({where})." if where else "The activity journal is off.")
        return 0
    if raw:
        for ev in events:
            print(json.dumps(ev, ensure_ascii=False))
        return 0
    for line in summarize(events):
        stamp = str(line.get("end") or "")[11:16]
        print(f"{stamp}  {line['text']}")
    return 0
