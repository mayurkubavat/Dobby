"""The companion standing in for the bar: its heartbeat and its "step aside" requests.

Pinlet.app draws the companion (Rime). While the person has chosen
"Show progress with: Rime", the app sends a heartbeat about every 250 ms
through the control socket (op ``companion``): whether the companion is on
screen and where. The overlay helper hides its floating bar only while that
heartbeat is fresh, so if the app hangs, crashes or loses the window, the bar
(and its Pause button) comes back by itself within :data:`STALE_S`.

Before the agent pointer glides to a point under the companion, the helper
asks it to step aside (:func:`ask_avoid`); the app reads the request in the
reply to its next heartbeat and moves the companion away, and the helper
waits until the heartbeat shows it gone (at most a fraction of a second).

Plain files, pure functions: tested anywhere, shared by every helper.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping, Sequence
from typing import Any

DIR = os.path.expanduser("~/.pinlet/overlay")
HEART_PATH = os.path.join(DIR, "companion.json")
AVOID_PATH = os.path.join(DIR, "companion_avoid.json")
SEEN_PATH = os.path.join(DIR, "seen.json")

#: Seconds without a heartbeat before the bar comes back.
STALE_S = 2.0
#: How close (points) the pointer may come to the companion before it steps aside.
MARGIN = 28.0
#: A step-aside request older than this is ignored.
AVOID_S = 1.5
#: The longest the pointer waits for the companion to move.
AVOID_WAIT_S = 0.45


def _write(path: str, data: Mapping[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(dict(data), fh)
        os.replace(tmp, path)
    except OSError:
        pass


def _read(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _frame(value: object) -> list[float] | None:
    try:
        x, y, w, h = (float(v) for v in value)  # type: ignore[union-attr]
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return [x, y, w, h]


def beat(
    *,
    visible: bool,
    frame: Sequence[float] | None,
    bar: bool,
    now: float | None = None,
    path: str = HEART_PATH,
) -> None:
    """Record the app's heartbeat: on screen or not, where, and whether it replaces the bar."""
    _write(
        path,
        {
            "visible": bool(visible),
            "frame": _frame(frame),
            "bar": bool(bar),
            "at": time.time() if now is None else float(now),
        },
    )


def read(now: float | None = None, path: str = HEART_PATH) -> dict[str, Any]:
    """The last heartbeat with ``fresh`` added; {} when there never was one."""
    rec = _read(path)
    if not rec:
        return {}
    now = time.time() if now is None else now
    age = now - float(rec.get("at") or 0.0)
    rec["frame"] = _frame(rec.get("frame"))
    rec["fresh"] = 0.0 <= age <= STALE_S
    return rec


def stands_in_for_bar(rec: Mapping[str, Any]) -> bool:
    """True while the companion, fresh and on screen, replaces the floating bar."""
    return bool(rec.get("fresh") and rec.get("visible") and rec.get("bar") and rec.get("frame"))


def covers(rec: Mapping[str, Any], point: Sequence[float], margin: float = MARGIN) -> bool:
    """True when ``point`` (global top-left points) lands on or next to the companion."""
    frame = rec.get("frame")
    if not rec.get("fresh") or not rec.get("visible") or not frame:
        return False
    x, y, w, h = frame
    px, py = float(point[0]), float(point[1])
    return x - margin <= px <= x + w + margin and y - margin <= py <= y + h + margin


def ask_avoid(point: Sequence[float], now: float | None = None, path: str = AVOID_PATH) -> None:
    """Ask the companion to step away from ``point``."""
    _write(
        path,
        {"point": [float(point[0]), float(point[1])], "at": time.time() if now is None else now},
    )


def avoid_request(now: float | None = None, path: str = AVOID_PATH) -> list[float] | None:
    """The point the companion should step away from, or None."""
    rec = _read(path)
    try:
        x, y = (float(v) for v in rec["point"])
        at = float(rec["at"])
    except (KeyError, TypeError, ValueError):
        return None
    now = time.time() if now is None else now
    return [x, y] if 0.0 <= now - at <= AVOID_S else None


def clear_avoid(path: str = AVOID_PATH) -> None:
    """Forget the step-aside request (the pointer has gone past)."""
    try:
        os.unlink(path)
    except OSError:
        pass


#: What the AI found on its last look, kept this long for "Show what it sees".
SEEN_S = 300.0
SEEN_MAX = 40


def note_seen(
    items: Sequence[Mapping[str, Any]], now: float | None = None, path: str = SEEN_PATH
) -> None:
    """Remember the elements the AI found on its last look at the screen.

    Each item has ``label``, ``role`` and ``rect`` (global top-left points).
    Labels are trimmed; at most :data:`SEEN_MAX` are kept.
    """
    from .text import clean_thought

    out = []
    for item in list(items)[:SEEN_MAX]:
        rect = _frame(item.get("rect"))
        if rect is None:
            continue
        out.append(
            {
                "label": clean_thought(str(item.get("label") or ""), 40),
                "role": clean_thought(str(item.get("role") or ""), 20),
                "rect": [round(v, 1) for v in rect],
            }
        )
    _write(path, {"items": out, "at": time.time() if now is None else now})


def read_seen(now: float | None = None, path: str = SEEN_PATH) -> list[dict[str, Any]]:
    """The elements from the AI's last look, or [] when that look is too old."""
    rec = _read(path)
    now = time.time() if now is None else now
    if not rec or not 0.0 <= now - float(rec.get("at") or 0.0) <= SEEN_S:
        return []
    items = rec.get("items")
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


__all__ = (
    "AVOID_S",
    "AVOID_WAIT_S",
    "MARGIN",
    "STALE_S",
    "ask_avoid",
    "avoid_request",
    "beat",
    "clear_avoid",
    "covers",
    "note_seen",
    "read",
    "read_seen",
    "stands_in_for_bar",
)
