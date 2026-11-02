"""Dobby's voice: short house-elf lines for the overlay bar and the startup log.

``DOBBY_PERSONALITY`` picks how much of it you get:

- ``off``: the overlay says exactly what it said before this module.
- ``subtle`` (default): a few fixed, gentle words ("Dobby is on it",
  "Dobby is resting", "Dobby is done!"), nothing that rotates.
- ``elf``: lines rotate (at most one new line every 20 seconds, none of the
  last five repeat), a cheer when a task ends, and a few rare surprises.

Only words the person reads change. MCP tool results, the pointer tag, the
Allow/Deny prompt, "You're in control" and the stop hotkey stay plain.

``DOBBY_USER_NAME`` lets Dobby address you by name instead of "sir".
``DOBBY_PERSONALITY_LINES`` names a JSON file ``{"moment": ["line", ...]}``
whose lists replace that moment's lines. ``{name}`` in a line becomes the
Dobby's name (``DOBBY_AGENT_NAME``), ``{you}`` becomes ", Sam" or ", sir".

The lines are written for this project in Dobby's way of talking; they are
not quotes from the books or films.
"""

from __future__ import annotations

import collections
import datetime as _dt
import json
import os
import random
import time
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

LEVELS = ("off", "subtle", "elf")
DEFAULT_LEVEL = "subtle"

COOLDOWN_SECS = 20.0  # at most one new line this often
NO_REPEAT = 5  # none of the last five lines comes back
DONE_SECS = 3.0  # how long "Dobby did it!" stays on the bar

# Kept short: the working lead shares the bar with the step, its count and the timer.
SUBTLE: Dict[str, str] = {
    "startup": "{name} is ready.",
    "sleeping": "{name} is resting",
    "working": "{name} is on it",
    "thinking": "{name} is thinking…",
    "done": "{name} is done!",
}

ELF: Dict[str, Tuple[str, ...]] = {
    "startup": (
        "{name} is here, and {name} is ready!",
        "{name} is honoured to help{you}!",
        "{name} has polished the cursor{you}!",
        "{name} is awake and eager to serve!",
    ),
    "sleeping": (
        "{name} is resting",
        "{name} waits for a task",
        "{name} is napping",
        "{name} is ready when you are",
        "{name} sits quietly",
    ),
    "awake": (
        "{name} is awake and eager!",
        "{name} is up{you}!",
    ),
    "working": (
        "{name} is on it!",
        "{name} is busy!",
        "{name} works hard",
        "{name} is careful",
        "{name} is quick!",
        "{name} loves to help",
    ),
    "thinking": (
        "{name} is thinking hard…",
        "{name} asks the little model…",
        "{name} is pondering…",
        "Hmm… {name} is thinking…",
        "{name} reads the screen…",
    ),
    "done": (
        "{name} did it! {name} is so happy!",
        "All done! {name} is proud!",
        "Done! {name} was glad to help{you}!",
        "{name} finished{you}!",
    ),
    "paused": (
        "{name} is paused · {name} waits",
        "{name} is paused for you{you}",
    ),
    "stopped": (
        "{name} stopped, as you asked",
        "{name} stops at once{you}!",
    ),
    # Rare surprises, each at most once a day.
    "egg_steps": ("One hundred steps! {name} is amazed!",),
    "egg_hour": ("An hour! {name} needs no rest!",),
    "egg_late": ("So late! {name} works very quietly",),
}

MOMENTS = tuple(ELF)


def normalize_level(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    if v in ("0", "false", "no", "none"):
        return "off"
    if v in ("1", "true", "yes", "on", "full", "fun"):
        return "elf"
    return v if v in LEVELS else DEFAULT_LEVEL


def load_lines(path: Optional[str]) -> Dict[str, Tuple[str, ...]]:
    """Read a ``{"moment": ["line", ...]}`` JSON file; bad files and keys are ignored."""
    if not path:
        return {}
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    out: Dict[str, Tuple[str, ...]] = {}
    if isinstance(data, dict):
        for key, lines in data.items():
            if key in ELF and isinstance(lines, list):
                good = tuple(str(x) for x in lines if isinstance(x, str) and x.strip())
                if good:
                    out[key] = good
    return out


class Voice:
    """Picks Dobby's words. Pure logic: the clock, the dice and the date are injectable."""

    def __init__(self, level: str = DEFAULT_LEVEL, user: str = "", lines: Optional[Mapping[str, Sequence[str]]] = None,
                 seed: Optional[int] = None, clock: Callable[[], float] = time.monotonic,
                 today: Callable[[], _dt.datetime] = _dt.datetime.now,
                 cooldown: float = COOLDOWN_SECS, done_secs: float = DONE_SECS) -> None:
        self.level = normalize_level(level)
        self.user = " ".join(str(user or "").split())
        self.pools: Dict[str, Tuple[str, ...]] = dict(ELF)
        for key, pool in (lines or {}).items():
            if key in self.pools and pool:
                self.pools[key] = tuple(pool)
        self.subtle = dict(SUBTLE)
        for key in self.subtle:
            if lines and lines.get(key):
                self.subtle[key] = tuple(lines[key])[0]
        self.rng = random.Random(seed)
        self.clock = clock
        self.today = today
        self.cooldown = float(cooldown)
        self.done_secs = float(done_secs)
        self._current: Dict[str, Tuple[str, float]] = {}  # moment -> (line, picked at)
        self._recent: collections.deque = collections.deque(maxlen=NO_REPEAT)
        self._eggs_shown: Dict[str, str] = {}  # egg -> date shown
        self._last_mode = ""
        self._task_steps = 0
        self._done_until = 0.0
        self._done_refresh = False

    @classmethod
    def from_config(cls, cfg: Mapping[str, object]) -> "Voice":
        seed = cfg.get("personality_seed")
        return cls(level=str(cfg.get("personality") or DEFAULT_LEVEL), user=str(cfg.get("user_name") or ""),
                   lines=load_lines(str(cfg.get("personality_lines") or "")),
                   seed=int(seed) if isinstance(seed, (int, float, str)) and str(seed).lstrip("-").isdigit() else None)

    @property
    def on(self) -> bool:
        return self.level != "off"

    # ---------------------------------------------------------------- words
    def _fill(self, line: str, name: str) -> str:
        you = f", {self.user}" if self.user else ", sir"
        return line.replace("{name}", name or "Dobby").replace("{you}", you)

    def _pick(self, moment: str) -> str:
        pool = [p for p in self.pools.get(moment, ()) if p not in self._recent] or list(self.pools.get(moment, ()))
        if not pool:
            return ""
        line = self.rng.choice(pool)
        self._recent.append(line)
        return line

    def line(self, moment: str, default: str, name: str = "Dobby", rotate: bool = True) -> str:
        """Dobby's words for ``moment``, or ``default`` when the personality is off.

        ``elf``: the line stays the same until ``cooldown`` seconds pass (then,
        with ``rotate``, a new one is picked); a moment seen again after a
        different one gets a fresh line.
        """
        if self.level == "off":
            return default
        if self.level == "subtle":
            raw = self.subtle.get(moment)
            return self._fill(raw, name) if raw else default
        now = self.clock()
        held = self._current.get(moment)
        if held is not None and (not rotate or now - held[1] < self.cooldown):
            return self._fill(held[0], name)
        raw = self._pick(moment)
        if not raw:
            return default
        self._current[moment] = (raw, now)
        return self._fill(raw, name)

    def forget(self, moment: str) -> None:
        """The next ``line(moment)`` picks anew (Dobby fell asleep again, a new task)."""
        self._current.pop(moment, None)

    def egg(self, *, steps: int, elapsed: float, name: str = "Dobby") -> Optional[str]:
        """A rare surprise for the working bar (``elf`` only), each at most once a day."""
        if self.level != "elf":
            return None
        now = self.clock()
        for key, held in list(self._current.items()):
            if key.startswith("egg_") and now - held[1] < self.cooldown:
                return self._fill(held[0], name)  # keep showing the one that fired
        day = self.today().date().isoformat()
        hour = self.today().hour
        for key, fires in (("egg_steps", steps >= 100), ("egg_hour", elapsed >= 3600.0),
                           ("egg_late", 0 <= hour < 5 and steps >= 1)):
            if fires and self._eggs_shown.get(key) != day:
                self._eggs_shown[key] = day
                raw = self._pick(key)
                if raw:
                    self._current[key] = (raw, now)
                    return self._fill(raw, name)
        return None

    def startup(self, name: str = "Dobby") -> str:
        """A greeting for the server log, or "" when the personality is off."""
        return self.line("startup", "", name, rotate=False) if self.on else ""

    # ------------------------------------------------------- task tracking
    def observe(self, mode: str, steps: int = 0) -> None:
        """Follow the bar's mode, to cheer once when a task ends."""
        now = self.clock()
        if mode == "working":
            if self._last_mode != "working":
                self.forget("working")
                self.forget("thinking")
            self._task_steps = max(self._task_steps, int(steps))
        elif mode in ("ready", "awake", "hidden", ""):
            if self._last_mode == "working" and self._task_steps > 0 and self.on:
                self._done_until = now + self.done_secs
                self._done_refresh = True
                self.forget("done")
            if mode != self._last_mode and mode == "ready":
                self.forget("sleeping")
            self._task_steps = 0
        elif mode != "paused":  # stopped, takeover: no cheer
            self._task_steps = 0
            self._done_until = 0.0
        self._last_mode = mode

    def done(self, name: str = "Dobby") -> Optional[str]:
        """The cheer while it lasts (a few seconds after a task), else None."""
        if self._done_until and self.clock() < self._done_until:
            return self.line("done", "", name, rotate=False) or None
        return None

    def refresh_due(self) -> bool:
        """True once, when the cheer has run out and the bar should redraw."""
        if self._done_refresh and self.clock() >= self._done_until:
            self._done_refresh = False
            return True
        return False

    # ----------------------------------------------------------- bar views
    def dress(self, view: Optional[tuple], name: str = "Dobby", steps: int = 0) -> Optional[tuple]:
        """Reword ``menu.bar_view``'s answer. Safety texts (takeover, Allow/Deny) never change."""
        if view is None:
            self.observe("hidden")
            return None
        mode, text, primary, secondary, color, dot_alpha = view
        self.observe(mode, steps)
        if not self.on:
            return view
        if mode in ("ready", "awake"):
            cheer = self.done(name)
            if cheer:
                tail = " · click to wake" if "click to wake" in text else ""
                return (mode, cheer + tail, primary, secondary, color, dot_alpha)
        if mode == "ready" and text.startswith("Dobby is sleeping"):
            text = self.line("sleeping", "Dobby is sleeping", name, rotate=False) + text[len("Dobby is sleeping"):]
        elif mode == "awake" and text.startswith("Dobby is awake · no AI"):
            text = self.line("awake", "Dobby is awake", name, rotate=False) + text[len("Dobby is awake"):]
        elif mode == "paused" and text == "Dobby is paused":
            text = self.line("paused", text, name, rotate=False)
        elif mode == "stopped" and text == "Dobby stopped":
            text = self.line("stopped", text, name, rotate=False)
        elif mode == "working" and text.startswith("Dobby is working"):
            text = self.line("working", "Dobby is working", name) + text[len("Dobby is working"):]
        return (mode, text, primary, secondary, color, dot_alpha)


def voice_from_env(env: Optional[Mapping[str, str]] = None, seed: Optional[int] = None) -> Voice:
    env = os.environ if env is None else env
    return Voice(level=env.get("DOBBY_PERSONALITY", DEFAULT_LEVEL), user=env.get("DOBBY_USER_NAME", ""),
                 lines=load_lines(env.get("DOBBY_PERSONALITY_LINES", "")), seed=seed)


def startup_line(env: Optional[Mapping[str, str]] = None) -> str:
    """The greeting for logs and scripts ("" when ``DOBBY_PERSONALITY=off``)."""
    env = os.environ if env is None else env
    name = (env.get("DOBBY_AGENT_NAME") or "Dobby").strip() or "Dobby"
    return voice_from_env(env).startup(name)


def all_lines() -> List[str]:
    """Every built-in line, for tests (length, no film quotes)."""
    out = list(SUBTLE.values())
    for pool in ELF.values():
        out.extend(pool)
    return out
