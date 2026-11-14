"""Wire protocol between the Pinlet server and the overlay helper.

One JSON object per line on the helper's stdin. Each message has an ``op``
and may carry an ``id``; the helper answers messages that carry an ``id``
with ``{"ack": <id>, "ok": true}`` (or ``"ok": false, "error": "..."``) on
stdout. On start the helper prints ``{"ready": true, "version": N}``.

Coordinates are global screen points with a top-left origin on the primary
display (the Quartz / Accessibility convention), not screenshot pixels.
"""

from __future__ import annotations

import json
from typing import Any

PROTOCOL_VERSION = 4

#: Name given to every overlay panel, so window listings can filter them out.
OVERLAY_WINDOW_TITLE = "Pinlet Overlay"

CURSOR_STATES = ("idle", "thinking", "typing", "keys", "scrolling", "dragging", "waiting_user")
#: States in which Pinlet is sending input right now: the pointer is on screen
#: with a badge for the kind of input, so every action shows where it lands.
ACTING_STATES = ("typing", "keys", "scrolling", "dragging")
RISK_LEVELS = ("ok", "caution", "danger")
CONFIRM_KINDS = ("danger", "access")

#: Events the helper sends unprompted: ``{"event": <name>, ...}``.
EVENTS = ("stop", "pause", "resume", "user_input", "user_idle", "wake", "sleep")

# op -> (required fields, optional fields)
OPS: dict[str, tuple] = {
    "cursor.move": (("x", "y"), ("ms", "verb", "probe", "app")),
    "cursor.click": ((), ("button", "count")),
    "cursor.state": (("state",), ()),
    "cursor.hide": ((), ()),
    "intent": (("text",), ("ms",)),
    # What the AI is trying to get done, in one line, for the bar ("" clears it).
    # elapsed / steps: the task was already under way (Pinlet restarted mid-task).
    "goal": (("text",), ("elapsed", "steps")),
    # Why the AI takes its next step, in a few words ("" clears it): the
    # companion's thought cloud. Plain text from the AI, shown with its name.
    "thought": (("text",), ()),
    # The AI let go of control: the bar stops saying it is working at once.
    "task.end": ((), ()),
    "target.box": (("x", "y", "w", "h"), ()),
    "target.clear": ((), ()),
    "risk": (("level",), ()),
    "scan": ((), ("x", "y", "w", "h")),
    "trail.clear": ((), ()),
    # kind "danger" (risky step, red) or "access" (may Pinlet use this app?).
    # detail: a second line; allow / deny: button titles.
    "confirm": (("text",), ("secs", "detail", "allow", "deny", "kind")),
    "confirm.cancel": ((), ()),
    "overlay.visible": (("on",), ()),
    "glow.track": ((), ("window_id", "pid")),
    "glow.clear": ((), ()),
    # An AI waits for the user to wake Pinlet (``wait_for_wake``), then is done.
    "sleep.wait": ((), ("reason",)),
    "sleep.done": ((), ()),
    # Input lock (``inputlock``): Pinlet's own input is about to run (on=True)
    # or is done (on=False). The X11 helper opens its shield and drops its
    # keyboard grab in between; macOS needs nothing (its tap knows Pinlet's
    # events) and just acks.
    "input.pass": (("on",), ()),
    # Turbo (no prompts, no take-over pause) on or off for the next actions:
    # a shared Pinlet serves turbo and secure clients through one helper.
    "turbo": (("on",), ()),
    "hide": ((), ()),
    "show": ((), ()),
    "ping": ((), ()),
    "bye": ((), ()),
}


class ProtocolError(ValueError):
    """A message that does not follow the overlay protocol."""


def validate(msg: dict[str, Any]) -> dict[str, Any]:
    """Check a message against :data:`OPS`; return it unchanged or raise."""
    if not isinstance(msg, dict):
        raise ProtocolError("message must be an object")
    op = msg.get("op")
    if op not in OPS:
        raise ProtocolError(f"unknown op: {op!r}")
    required, optional = OPS[op]
    allowed = set(required) | set(optional) | {"op", "id"}
    missing = [k for k in required if k not in msg]
    if missing:
        raise ProtocolError(f"{op}: missing {', '.join(missing)}")
    extra = sorted(set(msg) - allowed)
    if extra:
        raise ProtocolError(f"{op}: unexpected {', '.join(extra)}")
    for k in ("x", "y", "w", "h", "ms", "secs"):
        if k in msg and (isinstance(msg[k], bool) or not isinstance(msg[k], (int, float))):
            raise ProtocolError(f"{op}: {k} must be a number")
    for k in ("verb", "text", "reason", "app", "detail", "allow", "deny"):
        if k in msg and not isinstance(msg[k], str):
            raise ProtocolError(f"{op}: {k} must be a string")
    for k in ("probe", "on"):
        if k in msg and not isinstance(msg[k], bool):
            raise ProtocolError(f"{op}: {k} must be true or false")
    if op == "confirm" and msg.get("kind", "danger") not in CONFIRM_KINDS:
        raise ProtocolError(f"confirm: unknown kind {msg['kind']!r}")
    if op == "risk" and msg["level"] not in RISK_LEVELS:
        raise ProtocolError(f"risk: unknown level {msg['level']!r}")
    if op == "scan" and 0 < len({"x", "y", "w", "h"} & set(msg)) < 4:
        raise ProtocolError("scan: give all of x, y, w, h or none")
    if op == "cursor.state" and msg["state"] not in CURSOR_STATES:
        raise ProtocolError(f"cursor.state: unknown state {msg['state']!r}")
    if op == "glow.track" and msg.get("window_id") is None and msg.get("pid") is None:
        raise ProtocolError("glow.track: needs window_id or pid")
    return msg


def encode(op: str, msg_id: int | None = None, **fields: Any) -> str:
    """Encodes one command as a JSON line."""
    msg: dict[str, Any] = {"op": op}
    msg.update({k: v for k, v in fields.items() if v is not None})
    if msg_id is not None:
        msg["id"] = msg_id
    validate(msg)
    return json.dumps(msg, separators=(",", ":")) + "\n"


def decode(line: str) -> dict[str, Any]:
    """Decodes and validates one JSON line.

    Raises:
        ProtocolError: The line is not valid JSON or not a valid message.
    """
    try:
        msg = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"bad json: {exc}") from exc
    return validate(msg)


def ack(msg_id: Any, ok: bool = True, error: str | None = None) -> str:
    """Encodes an acknowledgement for the command ``msg_id``."""
    out: dict[str, Any] = {"ack": msg_id, "ok": ok}
    if error:
        out["error"] = error
    return json.dumps(out, separators=(",", ":")) + "\n"


def event(name: str, **fields: Any) -> str:
    """Encodes an event from the helper to the server as a JSON line.

    Raises:
        ProtocolError: ``name`` is not a known event.
    """
    if name not in EVENTS:
        raise ProtocolError(f"unknown event: {name!r}")
    out: dict[str, Any] = {"event": name}
    out.update(fields)
    return json.dumps(out, separators=(",", ":")) + "\n"


def window_id_int(window_id: Any) -> int | None:
    """Window ids arrive as int, decimal string or hex string ("0x1a2")."""
    if window_id is None:
        return None
    if isinstance(window_id, int):
        return window_id
    s = str(window_id).strip()
    try:
        return int(s, 16) if s.lower().startswith("0x") else int(s)
    except ValueError:
        return None
