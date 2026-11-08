"""Which apps Dobby may use, and how much.

Before Dobby clicks or types in an app for the first time in a session, it
asks the person once on the overlay: "Let Dobby use Vivado?". Allow lasts
until Dobby's server exits; Deny lasts as long, so Dobby does not ask again
and the AI is told plainly that the app is off limits.

Three levels (tiers):

* ``full``   click, type, press keys, drag and scroll.
* ``click``  click and scroll only: no typing, keys or drags. Good for
             terminals and chat apps, where typed text has effects.
* ``read``   Dobby may look but never act. Password managers start here.

Standing rules live in ``~/.dobby/apps.json`` (re-read when it changes)::

    {
      "apps": {"Vivado": "full", "Terminal": "click", "1Password": "read"},
      "default": "ask"
    }

``default`` is what happens for an app not listed: ``ask`` (the default) or
a tier, for example ``"full"`` to never be asked. ``DOBBY_APP_GRANTS=off``
turns the whole check off. App names match without case and without
endings such as ``.app`` or ``-bin``.

An admin policy (``dobby.policy``) can set tiers too. Those win over
``apps.json``, the built-in list and the person's "Allow", and they apply
even with ``DOBBY_APP_GRANTS=off``.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable
from pathlib import Path

FULL, CLICK, READ = "full", "click", "read"
TIERS = (FULL, CLICK, READ)
ASK = "ask"

# What each action needs. ``click`` covers single, double and right clicks.
NEEDS = {"click": CLICK, "scroll": CLICK, "type": FULL, "keys": FULL, "drag": FULL}
_RANK = {READ: 0, CLICK: 1, FULL: 2}

# Apps that start read-only unless apps.json says otherwise: where a stray
# click or keystroke could leak or change secrets.
BUILTIN: dict[str, str] = {
    "1password": READ,
    "1password 7": READ,
    "bitwarden": READ,
    "keepassxc": READ,
    "keepass": READ,
    "dashlane": READ,
    "keychain access": READ,
    "passwords": READ,
    "seahorse": READ,
    "enpass": READ,
    "lastpass": READ,
    "proton pass": READ,
}

# Apps where typing has effects beyond the window: said in the prompt.
_TYPING_RUNS = re.compile(
    r"(terminal|iterm|konsole|xterm|alacritty|kitty|wezterm|tilix|warp|ghostty|terminator|"
    r"guake|yakuake|urxvt|foot|st$)",
    re.I,
)

DEFAULT_PATH = Path(os.path.expanduser("~/.dobby/apps.json"))


def normalize(app: str | None) -> str:
    name = " ".join(str(app or "").split()).lower()
    for suffix in (".app", ".exe", ".bin", "-bin", "-server", "-wrapped"):
        if name.endswith(suffix) and len(name) > len(suffix):
            name = name[: -len(suffix)]
    return name


def allows(tier: str, action: str) -> bool:
    """True when ``tier`` lets Dobby do ``action`` (see :data:`NEEDS`)."""
    return _RANK.get(tier, -1) >= _RANK[NEEDS.get(action, FULL)]


class AppBlocked(Exception):
    """Dobby may not do this in this app. ``tier`` is what the app allows."""

    def __init__(self, app: str, action: str, tier: str | None, asked: bool) -> None:
        self.app, self.action, self.tier, self.asked = app, action, tier, asked
        super().__init__(blocked_message(app, action, tier, asked))


def blocked_message(app: str, action: str, tier: str | None, asked: bool) -> str:
    if tier is None:
        how = (
            "The person said no when Dobby asked to use it"
            if asked
            else "The person has not allowed it"
        )
        return f"Dobby may not use {app}. {how}."
    what = {READ: "only look at it", CLICK: "only click and scroll there"}.get(tier, "not do this")
    doing = {"type": "type", "keys": "press keys", "drag": "drag"}.get(action, action)
    return f"Dobby may {what} in {app}, so it cannot {doing} there."


class AppGrants:
    """Session grants plus standing rules from ``apps.json``. Thread safe."""

    def __init__(self, path: Path | None = None, env: dict[str, str] | None = None) -> None:
        env = os.environ if env is None else env
        self.enabled = env.get("DOBBY_APP_GRANTS", "").strip().lower() not in (
            "0",
            "off",
            "false",
            "no",
        )
        self.path = Path(path) if path is not None else DEFAULT_PATH
        self._lock = threading.Lock()
        self._session: dict[str, str] = {}  # app -> tier the person allowed this session
        self._denied: set[str] = set()
        self._rules: dict[str, str] = {}
        self._default = ASK
        self._mtime: float | None = None

    # ------------------------------------------------------------ rules
    def _load(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self._rules, self._default, self._mtime = {}, ASK, None
            return
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return  # keep the last good rules
        rules = data.get("apps", {}) if isinstance(data, dict) else {}
        self._rules = {
            normalize(k): str(v).lower()
            for k, v in rules.items()
            if str(v).lower() in TIERS + (ASK,)
        }
        default = str(data.get("default", ASK)).lower() if isinstance(data, dict) else ASK
        self._default = default if default in TIERS + (ASK,) else ASK

    @staticmethod
    def admin(app: str) -> str | None:
        """The tier the admin policy sets for ``app``, or None."""
        from dobby.policy import current

        return current().app_tier(normalize(app))

    def standing(self, app: str) -> str | None:
        """The tier set by the admin policy, apps.json or the built-in list, or None to ask."""
        key = normalize(app)
        admin = self.admin(app)
        if admin is not None:
            return admin
        with self._lock:
            self._load()
            rule = self._rules.get(key)
            if rule is None:
                rule = BUILTIN.get(key)
            if rule is None and self._default != ASK:
                rule = self._default
        return None if rule in (None, ASK) else rule

    def tier(self, app: str) -> str | None:
        """What Dobby may do in ``app`` right now; None means ask first."""
        key = normalize(app)
        admin = self.admin(app)
        if admin is not None:
            return admin  # the person's "Allow" cannot widen what the admin set
        with self._lock:
            if key in self._session:
                return self._session[key]
        return self.standing(app)

    def denied(self, app: str) -> bool:
        with self._lock:
            return normalize(app) in self._denied

    def grant(self, app: str, tier: str = FULL) -> None:
        with self._lock:
            self._session[normalize(app)] = tier
            self._denied.discard(normalize(app))

    def deny(self, app: str) -> None:
        with self._lock:
            self._denied.add(normalize(app))
            self._session.pop(normalize(app), None)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            self._load()
            return {
                "enabled": self.enabled,
                "allowed": dict(self._session),
                "denied": sorted(self._denied),
                "rules": dict(self._rules),
                "default": self._default,
                "file": str(self.path),
            }

    # ------------------------------------------------------------ check
    def check(self, app: str | None, action: str, ask: Callable[[str, str], bool]) -> None:
        """Raise :class:`AppBlocked` unless Dobby may do ``action`` in ``app``.

        ``ask(app, prompt_detail)`` puts the question to the person and
        returns their answer. An unknown app (no name) is not checked:
        Dobby cannot ask about something it cannot name.
        """
        if not app or not str(app).strip():
            return
        if not self.enabled and self.admin(app) is None:
            return
        tier = self.tier(app)
        if tier is not None:
            if allows(tier, action):
                return
            raise AppBlocked(app, action, tier, asked=False)
        if self.denied(app):
            raise AppBlocked(app, action, None, asked=True)
        if ask(app, access_detail(app)):
            self.grant(app, FULL)
            return
        self.deny(app)
        raise AppBlocked(app, action, None, asked=True)


def access_detail(app: str) -> str:
    """The second line of the "Let Dobby use …?" prompt."""
    base = (
        f"Dobby can click and type in {app} until this session ends. Stop it any time from the bar."
    )
    if _TYPING_RUNS.search(normalize(app)):
        return f"Typing in {app} runs commands on this computer. " + base
    return base
