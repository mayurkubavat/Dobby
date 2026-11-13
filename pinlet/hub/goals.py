"""The goal each agent last stated, kept on disk so it outlives an engine restart.

An AI states its goal once, with ``request_control(goal=...)``. When the
shared Pinlet restarts in the middle of that task (an update, a crash), the
agent reconnects with the same ``agent_key`` and carries on, and the bar
should still say what it is doing. One small file per agent, read once when
the agent's first call reaches the new engine.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

MAX_AGE_S = 600.0  # older than this, the task it named is most likely over
MAX_CHARS = 200


def _path(root: Path, agent_key: str) -> Path:
    safe = "".join(c for c in agent_key[:32] if c.isalnum() or c in "-_")
    return Path(root) / "goals" / f"{safe or 'agent'}.json"


def save(root: Path, agent_key: str, text: str, now: float | None = None) -> None:
    """Remember the goal; an empty text forgets it. Never raises."""
    if not agent_key:
        return
    path = _path(root, agent_key)
    text = " ".join(str(text).split())[:MAX_CHARS]
    with contextlib.suppress(OSError):
        if not text:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"text": text, "at": time.time() if now is None else now}, fh)
        os.replace(tmp, path)


def load(root: Path, agent_key: str, now: float | None = None) -> str:
    """The goal saved for this agent, or "" when none or too old."""
    if not agent_key:
        return ""
    try:
        data = json.loads(_path(root, agent_key).read_text(encoding="utf-8"))
        age = (time.time() if now is None else now) - float(data["at"])
        text = str(data["text"])
    except (OSError, ValueError, KeyError, TypeError):
        return ""
    return text[:MAX_CHARS] if 0 <= age <= MAX_AGE_S else ""
