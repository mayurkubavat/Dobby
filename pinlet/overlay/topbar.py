"""Pinlet's top-bar item: the macOS menu bar icon and the Linux panel tray icon.

Styled like the macOS Focus menu: an icon in the top bar, and a dropdown
with a header ("Pinlet" · "2 running · 1 working"), one row per running Pinlet
(a round coloured icon, its name, what it is doing) with Pause / Resume /
Stop, and Pinlet's settings at the bottom.

This module is pure (no AppKit, no GTK) so it is tested anywhere:

* :data:`PALETTE`: palette "Frost".
* :func:`publish` / :func:`read_states`: each overlay helper writes what its
  Pinlet is doing to ``~/.pinlet/overlay/status/<server pid>.json`` so the one
  helper that owns the top-bar item (the bar owner, see ``barlock``) can show
  every Pinlet, not only its own.
* :func:`topbar_view`: the dropdown's content.
* :func:`render_icon` / :func:`render_row_icon`: PNG icons drawn with Pillow,
  shared by both platforms. They live in :mod:`pinlet.overlay.topbar_icons`.

``PINLET_OVERLAY_MENU_BAR=0`` turns the top-bar item off (on by default).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from .palette import PALETTE
from .text import short as _short

# The icon drawing lived in this module before topbar_icons; kept importable from here.
from .topbar_icons import (  # noqa: F401
    ICON_DIR,
    bar_icon_path,
    icon_file,
    render_icon,
    render_row_icon,
    render_tile_icon,
    row_icon_path,
    tray_icon_path,
)

STATUS_DIR = os.path.expanduser("~/.pinlet/overlay/status")
HIDDEN_PATH = os.path.expanduser("~/.pinlet/overlay/menu_hidden.json")

# ------------------------------------------------------- per-Pinlet status
_FRESH_SECS = 30.0  # a helper republishes at least this often while it runs


def publish(
    server_pid: int,
    *,
    mode: str,
    step: str = "",
    steps: int = 0,
    elapsed: float = 0.0,
    turbo: bool = False,
    app: str = "",
    directory: str = STATUS_DIR,
) -> None:
    """Write this helper's Pinlet status (cheap; called on every bar refresh)."""
    if not server_pid:
        return
    rec = {
        "pid": int(server_pid),
        "helper": os.getpid(),
        "mode": str(mode),
        "step": str(step)[:120],
        "steps": int(steps),
        "elapsed": float(elapsed),
        "turbo": bool(turbo),
        "app": str(app)[:60],
        "at": time.time(),
    }
    try:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{int(server_pid)}.json")
        tmp = os.path.join(directory, f".{int(server_pid)}.{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        os.replace(tmp, path)
    except OSError:
        pass


def clear(server_pid: int, directory: str = STATUS_DIR) -> None:
    """Deletes the published status file of the Pinlet with ``server_pid``."""
    try:
        os.unlink(os.path.join(directory, f"{int(server_pid)}.json"))
    except OSError:
        pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # alive, owned by someone else
    return True


def read_states(directory: str = STATUS_DIR, now: float | None = None) -> dict[int, dict]:
    """Published statuses by server pid. Drops files whose helper has exited."""
    now = time.time() if now is None else now
    out: dict[int, dict] = {}
    try:
        names = os.listdir(directory)
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json") or name.startswith("."):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
            pid = int(rec.get("pid", 0))
            helper = int(rec.get("helper", 0))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if helper and not _alive(helper):
            try:
                os.unlink(path)
            except OSError:
                pass
            continue
        if now - float(rec.get("at", 0) or 0) > _FRESH_SECS and rec.get("mode") == "working":
            rec["mode"] = "ready"  # a stale "working" (helper hung) reads as idle
        out[pid] = rec
    return out


# ------------------------------------------------------ tidying the list
# A Pinlet lives as long as its AI client, so a day of work leaves a pile of
# sleeping rows. Stopped Pinlets, "Clear finished" and Pinlets asleep for a
# while drop out of the dropdown; any of them comes back the moment it works
# (or asks) again. "Show N hidden" brings them all back.
_ACTIVE_STATES = ("working", "awake", "confirm", "paused", "takeover", "listening")
_FINISHED_STATES = ("ready", "waiting", "stopped")
_last_active: dict[int, float] = {}  # pid -> last time this process saw it busy


def _idle_hide_default() -> float:
    """``PINLET_MENU_HIDE_IDLE``: seconds asleep before a row hides (0 = never)."""
    try:
        return max(0.0, float(os.environ.get("PINLET_MENU_HIDE_IDLE", "900")))
    except ValueError:
        return 900.0


@dataclass
class Hiding:
    """Which rows the dropdown leaves out (shared through ``menu_hidden.json``)."""

    pids: dict[int, float] = field(default_factory=dict)  # hidden by hand (Stop, Clear finished) at
    shown_at: float = 0.0  # "Show hidden": nothing hides automatically before this
    idle_secs: float = 900.0  # asleep this long hides a row; 0 turns it off
    path: str | None = None  # where it is saved; None keeps it in memory (tests)

    @classmethod
    def load(cls, path: str | None = None, idle_secs: float | None = None) -> Hiding:
        """Reads the saved hiding state, or starts empty if there is none."""
        path = path or HIDDEN_PATH
        out = cls(idle_secs=_idle_hide_default() if idle_secs is None else idle_secs, path=path)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            out.pids = {int(k): float(v) for k, v in (data.get("pids") or {}).items()}
            out.shown_at = float(data.get("shown_at") or 0.0)
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return out

    def save(self) -> None:
        """Writes the hiding state atomically; does nothing without a path."""
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = f"{self.path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(
                    {"pids": {str(k): v for k, v in self.pids.items()}, "shown_at": self.shown_at},
                    fh,
                )
            os.replace(tmp, self.path)
        except OSError:
            pass

    def hide(self, pids: Iterable[int], now: float | None = None) -> None:
        """Hides the rows of ``pids`` until those Pinlets work again."""
        now = time.time() if now is None else now
        for pid in pids:
            self.pids[int(pid)] = now
        self.save()

    def show_all(self, now: float | None = None) -> None:
        """Shows every row again, including ones hidden for being asleep."""
        self.pids.clear()
        self.shown_at = time.time() if now is None else now
        self.save()

    def prune(self, live: Iterable[int]) -> None:
        """Forgets hidden pids that are no longer running."""
        live = {int(p) for p in live}
        gone = [p for p in self.pids if p not in live]
        for p in gone:
            del self.pids[p]
        if gone:
            self.save()

    def hidden(self, pid: int, state: str, last_active: float, now: float) -> bool:
        """True when the row for ``pid`` should be left out of the menu.

        Rows that need the person (Allow/Deny, paused, taken over) are always shown.
        """
        if state in ("confirm", "paused", "takeover"):
            return False  # it needs the person: always on show
        at = self.pids.get(pid)
        if at is not None and last_active <= at:
            return (
                True  # hidden, and nothing new since (a stopped Pinlet can read "working" briefly)
            )
        if state in _ACTIVE_STATES:
            return False
        if self.idle_secs > 0 and state in ("ready", "waiting"):
            since = max(last_active, self.shown_at)
            return since > 0 and now - since >= self.idle_secs
        return False


def finished_pids(view: View) -> list[int]:
    """The rows "Clear finished" hides: asleep, waiting for a turn or stopped."""
    return [r.pid for r in view.rows if r.state in _FINISHED_STATES]


def menu_action(
    key: str, view: View | None = None, pid: int | None = None, hiding: Hiding | None = None
) -> bool:
    """Handle the list-tidying keys. True when ``key`` was one of them."""
    if key not in ("clear_finished", "show_hidden", "hide"):
        return False
    hiding = hiding or Hiding.load()
    if key == "show_hidden":
        hiding.show_all()
    elif key == "hide" and pid is not None:
        hiding.hide([pid])
    elif key == "clear_finished" and view is not None:
        hiding.hide(finished_pids(view))
    return True


# ------------------------------------------------------------- the view
# Row states, most urgent first. (key, label, icon shape, colour key)
_STATES = {
    "confirm": ("Needs you · Allow or Deny", "alert", "caution"),
    "stopped": ("Stopped", "stop", "danger"),
    "paused": ("Paused", "pause", "caution"),
    "takeover": ("You're in control", "hand", "caution"),
    "working": ("Working", "spark", "agent"),
    "awake": ("Awake · handing back to the AI", "spark", "agent"),
    "listening": ("Waiting for you · wake it or tell it what to do", "moon", "ok"),
    "waiting": ("Waiting for its turn", "clock", "idle"),
    "ready": ("Sleeping", "moon", "idle"),
}
_ORDER = list(_STATES)


@dataclass
class Row:
    """One agent row in the menu."""

    pid: int
    name: str
    state: str  # a key of _STATES
    title: str  # "Mail Pinlet"
    detail: str  # "Working · Click ‘Send’ · step 7 · 0:42"
    color: str  # icon fill (hex)
    shape: str  # icon glyph: spark, moon, pause, alert, hand, stop, clock
    own: bool  # the Pinlet whose helper draws this menu
    actions: list[tuple[str, str]] = field(default_factory=list)  # (title, action key)
    queued: int = 0  # instructions from the person the AI has not picked up yet
    failures: int = 0  # tool calls in a row that failed (shared sessions)
    reason: str = ""  # what a listening Pinlet's AI said it waits for


@dataclass
class View:
    """The menu's content, drawn the same way by the Mac status item and the Linux tray."""

    header: str
    subtitle: str
    rows: list[Row]
    running: int
    working: int
    attention: bool  # some Pinlet is paused, stopped or asks Allow/Deny
    all_actions: list[tuple[str, str]] = field(default_factory=list)
    hidden: int = 0  # rows left out (stopped, cleared or long asleep)

    def to_dict(self) -> dict:
        """Plain JSON for a native shell (the Swift Pinlet.app) to draw as-is."""
        from dataclasses import asdict

        d = asdict(self)
        d["icon_state"], d["icon_title"], d["tooltip"] = (
            icon_state(self),
            icon_title(self),
            tooltip(self),
        )
        d["palette"] = dict(PALETTE)
        return d

    def signature(self) -> tuple:
        """Changes when the dropdown would look different (rebuild only then)."""
        return (
            self.subtitle,
            tuple(
                (r.pid, r.title, r.detail, r.color, r.shape, tuple(r.actions)) for r in self.rows
            ),
        )


def _clock(secs: float) -> str:
    secs = max(0, int(secs))
    return (
        f"{secs // 60}:{secs % 60:02d}"
        if secs < 3600
        else f"{secs // 3600}h {secs % 3600 // 60:02d}m"
    )


def _state_of(agent: Mapping[str, object], status: Mapping[str, object]) -> str:
    mode = str(status.get("mode") or "ready")
    if mode == "confirm":
        return "confirm"
    if agent.get("paused") or mode == "paused":
        return "paused"
    if mode in ("takeover", "stopped", "working", "awake"):
        return mode
    if agent.get("listening"):
        return "listening"  # its AI sits in wait_for_wake
    if agent.get("waiting"):
        return "waiting"
    return "ready"


def _detail(state: str, status: Mapping[str, object]) -> str:
    label = _STATES[state][0]
    if state != "working":
        return label
    parts = [label]
    if status.get("turbo"):
        parts[0] = "Working ⚡"
    step = _short(str(status.get("step") or ""), 40)
    if step:
        parts.append(step)
    if status.get("app"):
        parts.append(f"in {_short(str(status['app']), 20)}")
    if int(status.get("steps") or 0):
        parts.append(f"step {int(status['steps'])}")
    if float(status.get("elapsed") or 0) >= 1:
        parts.append(_clock(float(status["elapsed"])))
    return " · ".join(parts)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def topbar_view(
    agents: Iterable[Mapping[str, object]],
    states: Mapping[int, Mapping[str, object]],
    own_pid: int = 0,
    palette: Mapping[str, str] = PALETTE,
    hiding: Hiding | None = None,
    now: float | None = None,
) -> View:
    """Builds the dropdown menu.

    ``agents`` comes from ``pinlet.control.commands.overview`` and ``states`` from
    :func:`read_states`. With ``hiding``, stopped, cleared and long-asleep Pinlets
    are left out (see :class:`Hiding`).
    """
    now = time.time() if now is None else now
    hidden = 0
    from .style import agent_colors  # pure module

    agents = list(agents)
    colors = agent_colors(agents)
    rows: list[Row] = []
    for a in agents:
        pid = int(a.get("pid", 0) or 0)
        status = states.get(pid, {})
        state = _state_of(a, status)
        _label, shape, color_key = _STATES[state]
        agent_color = colors.get(pid) or str(a.get("color") or palette["accent"])
        color = agent_color if color_key == "agent" else palette[color_key]
        if status.get("last_call"):
            # A shared session: its last tool call is the real activity time
            # ("working" lingers for a while after it).
            _last_active[pid] = max(_last_active.get(pid, 0.0), float(status["last_call"]))
        elif state in _ACTIVE_STATES:
            _last_active[pid] = now
        if hiding is not None:
            last = max(_last_active.get(pid, 0.0), float(a.get("started") or 0.0))
            if hiding.hidden(pid, state, last, now):
                hidden += 1
                continue
        halted = state in ("paused", "takeover")
        actions = [("Resume" if halted else "Pause", "pause_toggle"), ("Stop", "stop")]
        if state == "listening":
            actions.insert(0, ("Wake", "wake"))
        queued = int(a.get("queued") or 0)
        detail = _detail(state, status)
        if queued:
            detail += f" · {_plural(queued, 'note')} waiting"
        rows.append(
            Row(
                pid=pid,
                name=str(a.get("name") or f"Pinlet-{pid}"),
                state=state,
                title=_short(str(a.get("name") or f"Pinlet-{pid}"), 32),
                detail=detail,
                color=color,
                shape=shape,
                own=pid == own_pid,
                actions=actions,
                queued=queued,
                failures=int(status.get("failures") or 0),
                reason=_short(str(status.get("reason") or ""), 120) if state == "listening" else "",
            )
        )
    rows.sort(key=lambda r: (_ORDER.index(r.state), r.name.lower(), r.pid))
    running = len(rows)
    working = sum(r.state in ("working", "awake") for r in rows)
    attention = any(r.state in ("confirm", "paused", "takeover", "stopped") for r in rows)
    if not running:
        subtitle = "Nothing running" if not hidden else "Nothing going on"
    else:
        bits = [f"{running} running" if running > 1 else "1 running"]
        if working:
            bits.append(f"{working} working")
        needs = sum(r.state == "confirm" for r in rows)
        paused = sum(r.state in ("paused", "takeover") for r in rows)
        if needs:
            bits.append(f"{needs} need{'s' if needs == 1 else ''} you")
        if paused:
            bits.append(f"{paused} paused")
        listening = sum(r.state == "listening" for r in rows)
        if listening:
            bits.append(f"{listening} waiting for you")
        if not working and not needs and not paused and not listening:
            bits.append("all sleeping" if running > 1 else "sleeping")
        subtitle = " · ".join(bits)
    if hidden:
        subtitle += f" · {hidden} hidden"
    all_actions: list[tuple[str, str]] = []
    if running > 1:
        # Only the group actions that would do something right now.
        halted = sum(r.state in ("paused", "takeover") for r in rows)
        if halted < running:
            all_actions.append(("Pause all", "pause_all"))
        if halted:
            all_actions.append(("Resume all", "resume_all"))
        all_actions.append(("Stop all", "stop_all"))
    if hiding is not None:
        if any(r.state in _FINISHED_STATES for r in rows):
            all_actions.append(("Clear finished", "clear_finished"))
        if hidden:
            all_actions.append((f"Show {hidden} hidden", "show_hidden"))
    return View(
        header="Pinlet",
        subtitle=subtitle,
        rows=rows,
        running=running,
        working=working,
        attention=attention,
        all_actions=all_actions,
        hidden=hidden,
    )


def icon_state(view: View) -> str:
    """Which top-bar icon: "attention" (amber), "working" (cyan) or "idle"."""
    if view.attention:
        return "attention"
    if view.working:
        return "working"
    return "idle"


def icon_title(view: View) -> str:
    """Text beside the icon: the number of running Pinlets when more than one."""
    return str(view.running) if view.running > 1 else ""


def tooltip(view: View) -> str:
    """Tooltip for the menu bar icon."""
    return f"Pinlet: {view.subtitle}"


def pinlet_mode(
    *, paused: bool, takeover: bool, confirming: bool, stopped: bool, working: bool, awake: bool
) -> str:
    """The helper's own mode for :func:`publish`."""
    if confirming:
        return "confirm"
    if stopped:
        return "stopped"
    if paused:
        return "paused"
    if takeover:
        return "takeover"
    if working:
        return "working"
    if awake:
        return "awake"
    return "ready"


__all__: Sequence[str] = (
    "HIDDEN_PATH",
    "PALETTE",
    "STATUS_DIR",
    "Hiding",
    "Row",
    "View",
    "bar_icon_path",
    "clear",
    "finished_pids",
    "icon_state",
    "icon_title",
    "menu_action",
    "pinlet_mode",
    "publish",
    "read_states",
    "render_icon",
    "render_row_icon",
    "row_icon_path",
    "tooltip",
    "topbar_view",
)
