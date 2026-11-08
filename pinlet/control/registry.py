"""Which Pinlet agents are running, by pid.

Every Pinlet server writes ``agents/<pid>.json`` (name, priority, start time).
The overlay helper reads it so input from ANY Pinlet process counts as an
agent, not as the person taking over. Without this, two agents would keep
pausing each other.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .lease import pid_alive


def agents_dir(root: Path) -> Path:
    """Directory holding one ``<pid>.json`` file per running Pinlet."""
    return Path(root) / "agents"


def _pick_color(root: Path, pid: int) -> str:
    """Keep this pid's colour on re-register; else the first one nobody uses."""
    from pinlet.overlay.style import pick_agent_color  # pure module, no GUI imports

    taken = []
    for rec in list_agents(root):
        if int(rec.get("pid", 0) or 0) == pid and rec.get("color"):
            return str(rec["color"])
        taken.append(str(rec.get("color") or ""))
    return pick_agent_color(taken)


def register(
    root: Path, agent_id: str, name: str, pid: int, priority: int = 0, color: str | None = None
) -> Path:
    """Write ``agents/<pid>.json``.

    ``color`` is this Pinlet's overlay colour: by default the first palette colour no other
    running Pinlet uses.
    """
    d = agents_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{pid}.json"
    tmp = d / f".{pid}.tmp"
    started = time.time()
    try:
        prev = json.loads(path.read_text(encoding="utf-8"))
        started = float(prev.get("started", started))
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    try:
        color = color or _pick_color(root, pid)
    except Exception:  # noqa: BLE001 - a colour is cosmetic
        color = None
    tmp.write_text(
        json.dumps(
            {
                "id": agent_id,
                "name": name,
                "pid": pid,
                "priority": priority,
                "started": started,
                "color": color,
            }
        ),
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return path


def unregister(root: Path, pid: int) -> None:
    """Removes the registry file for ``pid``; a missing file is not an error."""
    try:
        (agents_dir(root) / f"{pid}.json").unlink()
    except OSError:
        pass


def list_agents(root: Path) -> list[dict[str, object]]:
    """Lists every registered agent whose process is still alive."""
    out = []
    d = agents_dir(root)
    if not d.is_dir():
        return out
    for path in sorted(d.glob("*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            pid = int(rec.get("pid", 0))
        except (OSError, ValueError, AttributeError):
            continue
        if pid_alive(pid):
            out.append(rec)
        else:
            try:  # a Pinlet that died without cleaning up: drop its file
                path.unlink()
            except OSError:
                pass
    return out


def is_registered(root: Path, pid: int) -> bool:
    """True when a registry file exists for ``pid``."""
    return (agents_dir(root) / f"{pid}.json").is_file()


class AgentPidCache:
    """Cheap repeated lookups of live Pinlet pids (refreshed at most every ``ttl`` s)."""

    def __init__(self, root: Path, ttl: float = 1.0) -> None:
        self.root = Path(root)
        self.ttl = ttl
        self._pids: set[int] = set()
        self._at: float | None = None

    def pids(self) -> set[int]:
        """The pids of registered Pinlets, refreshed at most once per ``ttl``."""
        now = time.monotonic()
        if self._at is None or now - self._at >= self.ttl:
            self._at = now
            try:
                self._pids = {int(r["pid"]) for r in list_agents(self.root)}
            except Exception:  # noqa: BLE001 - never break the input monitor
                pass
        return self._pids

    def __contains__(self, pid: int) -> bool:
        return pid in self.pids()
