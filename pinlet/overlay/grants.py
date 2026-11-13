"""Which apps Pinlet may use, and how much.

Before Pinlet clicks or types in an app for the first time in a session, it
asks the person once on the overlay: "Let Pinlet use Vivado?". Allow lasts
until Pinlet's server exits; Deny lasts as long, so Pinlet does not ask again
and the AI is told plainly that the app is off limits.

Three levels (tiers):

* ``full``   click, type, press keys, drag and scroll.
* ``click``  click and scroll only: no typing, keys or drags. Good for
             terminals and chat apps, where typed text has effects.
* ``read``   Pinlet may look but never act. Password managers start here.

Standing rules live in ``~/.pinlet/apps.json`` (re-read when it changes)::

    {
      "apps": {"Vivado": "full", "Terminal": "click", "1Password": "read"},
      "default": "ask"
    }

An app saved with "Always allow in this app" also gets a line under
``"remembered"`` with the time it was saved. That rule lapses after 30 days
(``PINLET_ALWAYS_DAYS``; 0 keeps it for good), and Pinlet asks about the app
again. Rules written by hand have no such line and do not lapse.

``default`` is what happens for an app not listed: ``ask`` (the default) or
a tier, for example ``"full"`` to never be asked. ``PINLET_APP_GRANTS=off``
turns the whole check off. App names match without case and without
endings such as ``.app`` or ``-bin``.

An admin policy (``pinlet.policy``) can set tiers too. Those win over
``apps.json``, the built-in list and the person's "Allow", and they apply
even with ``PINLET_APP_GRANTS=off``.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
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

DEFAULT_PATH = Path(os.path.expanduser("~/.pinlet/apps.json"))
ALWAYS_DAYS = 30.0  # an "Always allow" lasts this long, then Pinlet asks again


def always_seconds(env: dict[str, str] | None = None) -> float:
    """How long a saved "Always allow" lasts, in seconds (``PINLET_ALWAYS_DAYS``; 0 = for good)."""
    env = os.environ if env is None else env
    try:
        days = float(env.get("PINLET_ALWAYS_DAYS", ALWAYS_DAYS))
    except (TypeError, ValueError):
        days = ALWAYS_DAYS
    return max(0.0, days) * 86400.0


def lapsed(saved_at: float | None, now: float | None = None, life: float | None = None) -> bool:
    """True when an "Always allow" saved at ``saved_at`` is too old to count."""
    if saved_at is None:
        return False  # written by hand: no age
    life = always_seconds() if life is None else life
    if life <= 0:
        return False
    now = time.time() if now is None else now
    return now - saved_at >= life or saved_at - now > 86400.0  # a date in the future is not trusted


def _stamps(data: object) -> dict[str, float]:
    """``{normalized app: when its "Always allow" was saved}`` from the decoded file."""
    raw = data.get("remembered") if isinstance(data, dict) else None
    out: dict[str, float] = {}
    if isinstance(raw, dict):
        for name, when in raw.items():
            if isinstance(when, (int, float)) and not isinstance(when, bool):
                out[normalize(name)] = float(when)
    return out


def normalize(app: str | None) -> str:
    """Canonical app name for grant lookups: lower case, single spaces, no ``.app``/``.exe``-style suffix."""
    name = " ".join(str(app or "").split()).lower()
    for suffix in (".app", ".exe", ".bin", "-bin", "-server", "-wrapped"):
        if name.endswith(suffix) and len(name) > len(suffix):
            name = name[: -len(suffix)]
    return name


def allows(tier: str, action: str) -> bool:
    """True when ``tier`` lets Pinlet do ``action`` (see :data:`NEEDS`)."""
    return _RANK.get(tier, -1) >= _RANK[NEEDS.get(action, FULL)]


class AppBlocked(Exception):
    """Pinlet may not do this in this app. ``tier`` is what the app allows."""

    def __init__(self, app: str, action: str, tier: str | None, asked: bool) -> None:
        self.app, self.action, self.tier, self.asked = app, action, tier, asked
        super().__init__(blocked_message(app, action, tier, asked))


def blocked_message(app: str, action: str, tier: str | None, asked: bool) -> str:
    """Explains to the AI why Pinlet may not act in ``app``, and what the person can change."""
    if tier is None:
        how = (
            "The person said no when Pinlet asked to use it"
            if asked
            else "The person has not allowed it"
        )
        return f"Pinlet may not use {app}. {how}."
    what = {READ: "only look at it", CLICK: "only click and scroll there"}.get(tier, "not do this")
    doing = {"type": "type", "keys": "press keys", "drag": "drag"}.get(action, action)
    return f"Pinlet may {what} in {app}, so it cannot {doing} there."


class AppGrants:
    """Session grants plus standing rules from ``apps.json``. Thread safe."""

    def __init__(self, path: Path | None = None, env: dict[str, str] | None = None) -> None:
        env = os.environ if env is None else env
        self.enabled = env.get("PINLET_APP_GRANTS", "").strip().lower() not in (
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
        self._saved_at: dict[str, float] = {}  # app -> when "Always allow" saved its rule
        self._life = always_seconds(env)
        self._default = ASK
        self._mtime: float | None = None

    # ------------------------------------------------------------ rules
    def _load(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self._rules, self._default, self._mtime = {}, ASK, None
            self._saved_at = {}
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
        self._saved_at = _stamps(data)
        default = str(data.get("default", ASK)).lower() if isinstance(data, dict) else ASK
        self._default = default if default in TIERS + (ASK,) else ASK

    @staticmethod
    def admin(app: str) -> str | None:
        """The tier the admin policy sets for ``app``, or None."""
        from pinlet.policy import current

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
            if rule is not None and lapsed(self._saved_at.get(key), life=self._life):
                rule = None  # the month is up: ask again
            if rule is None:
                rule = BUILTIN.get(key)
            if rule is None and self._default != ASK:
                rule = self._default
        return None if rule in (None, ASK) else rule

    def tier(self, app: str) -> str | None:
        """What Pinlet may do in ``app`` right now; None means ask first."""
        key = normalize(app)
        admin = self.admin(app)
        if admin is not None:
            return admin  # the person's "Allow" cannot widen what the admin set
        with self._lock:
            if key in self._session:
                return self._session[key]
        return self.standing(app)

    def denied(self, app: str) -> bool:
        """True when the person denied ``app`` this session."""
        with self._lock:
            return normalize(app) in self._denied

    def grant(self, app: str, tier: str = FULL) -> None:
        """Grants ``app`` the given tier for the rest of this session."""
        with self._lock:
            self._session[normalize(app)] = tier
            self._denied.discard(normalize(app))

    def deny(self, app: str) -> None:
        """Denies ``app`` for the rest of this session."""
        with self._lock:
            self._denied.add(normalize(app))
            self._session.pop(normalize(app), None)

    def snapshot(self) -> dict[str, object]:
        """The saved and session grants, as a dict."""
        with self._lock:
            self._load()
            return {
                "enabled": self.enabled,
                "allowed": dict(self._session),
                "denied": sorted(self._denied),
                "rules": dict(self._rules),
                "lapsed": sorted(
                    k for k in self._rules if lapsed(self._saved_at.get(k), life=self._life)
                ),
                "default": self._default,
                "file": str(self.path),
            }

    # ------------------------------------------------------------ check
    def check(self, app: str | None, action: str, ask: Callable[[str, str], bool]) -> None:
        """Raise :class:`AppBlocked` unless Pinlet may do ``action`` in ``app``.

        ``ask(app, prompt_detail)`` puts the question to the person and
        returns their answer. An unknown app (no name) is not checked:
        Pinlet cannot ask about something it cannot name.
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


def remember(app: str, tier: str = FULL, path: Path | None = None) -> Path:
    """Saves ``app`` at ``tier`` in ``apps.json`` ("Always allow in this app").

    The time is saved with it, so the rule lapses after :func:`always_seconds`
    and Pinlet asks again. Other rules and keys in the file are kept, rules
    that have already lapsed are removed, and the write is atomic and private.

    Returns:
        The path of the rules file.
    """
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}")
    path = Path(path) if path is not None else DEFAULT_PATH
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    apps = data.get("apps") if isinstance(data.get("apps"), dict) else {}
    saved = data.get("remembered") if isinstance(data.get("remembered"), dict) else {}
    now = time.time()
    stamps = _stamps(data)
    gone = {k for k, when in stamps.items() if lapsed(when, now)} | {normalize(app)}
    for key in [k for k in apps if normalize(k) in gone]:
        del apps[key]
    for key in [k for k in saved if normalize(k) in gone or normalize(k) not in stamps]:
        del saved[key]
    apps[app] = tier
    saved[app] = int(now)
    data["apps"] = apps
    data["remembered"] = saved
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def access_detail(app: str) -> str:
    """The second line of the "Let Pinlet use …?" prompt."""
    base = f"Pinlet can click and type in {app} until this session ends. Stop it any time from the bar."
    if _TYPING_RUNS.search(normalize(app)):
        return f"Typing in {app} runs commands on this computer. " + base
    return base
