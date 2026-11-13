"""Saved answers to "Allow Pinlet to …?": the person's "don't ask again" rules.

A consequential step (Send, Delete, Quit…; see :func:`kind_of`) asks Allow or
Deny. On the card the person can tick "Don't ask again", and that saves a
rule with four parts:

* **what**: the kind of step (``send``, ``delete``…), or ``*`` for every
  consequential step ("Run this task without asking");
* **where**: the app, or ``*``;
* **who**: the AI client that asked (``claude``, ``cursor``…), or ``*``;
* **how long**: ``task`` (until that AI's session ends or the person presses
  Stop), ``hour``, ``day`` (until midnight) or ``always`` (a month, see
  ``PINLET_ALWAYS_DAYS``; then Pinlet asks again).

Rules never cover what :mod:`pinlet.overlay.offlimits` refuses (Pinlet's own
windows, macOS security prompts, passwords, payments): those are checked
first and no rule is consulted.

Task rules live in memory. The others are saved in ``~/.pinlet/rules.json``,
each signed (HMAC-SHA256) with a key that Pinlet.app keeps in the Keychain and
hands to the engine when it connects (control op ``rules_key``). A rule whose
signature does not check out is ignored, so editing the file by hand, or by an
AI, gives nothing. Without the app (Linux, a private engine) the key is a
file beside the rules, which only keeps other users out.

A rule also has a daily ceiling (``PINLET_RULE_CEILING``, default 50 uses a
day): a runaway loop reaches it and has to ask.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .grants import always_seconds, normalize

ANY = "*"
TASK, HOUR, DAY, ALWAYS = "task", "hour", "day", "always"
SCOPES = (TASK, HOUR, DAY, ALWAYS)
SCOPE_LABELS = {
    TASK: "for this task",
    HOUR: "for 1 hour",
    DAY: "today",
    ALWAYS: "always (asks again in a month)",
}
DEFAULT_CEILING = 50

# kind -> (what the card and the Permissions list call it, the button words that mean it)
_KINDS: tuple[tuple[str, str, str], ...] = (
    (
        "send",
        "Send",
        r"send|submit|post|publish|share|reply( all)?|forward|deploy|merge|force push",
    ),
    (
        "delete",
        "Delete",
        r"delete|remove|erase|discard|destroy|overwrite|replace( all)?|format|wipe|"
        r"empty (the )?trash|move to (the )?trash|reset",
    ),
    ("install", "Install or remove software", r"install|uninstall|update now|upgrade"),
    (
        "account",
        "Sign out or change an account",
        r"sign out|log out|revoke|unsubscribe|cancel (subscription|order|account)|close account",
    ),
)
_KIND_RE = tuple((k, re.compile(rf"^\W*({words})\b", re.I)) for k, _label, words in _KINDS)
KIND_LABELS: dict[str, str] = {k: label for k, label, _ in _KINDS} | {
    "quit": "Quit",
    "close": "Close",
    "run": "Run a command",
    "other": "Risky steps",
    ANY: "Every risky step",
}
# A dangerous shortcut's reason (pinlet.overlay.hal._DANGER) -> kind.
_HOTKEY_KINDS = (
    ("quit", "quit"),
    ("close", "close"),
    ("trash", "delete"),
    ("lock", "account"),
    ("log out", "account"),
)


def kind_of(label: str | None) -> str:
    """The kind of consequential step a button label stands for ("other" when unsure)."""
    text = " ".join(str(label or "").split())
    for kind, pattern in _KIND_RE:
        if pattern.match(text):
            return kind
    return "other"


def kind_of_hotkey(reason: str | None) -> str:
    """The kind for a dangerous shortcut, from its reason ("quit the app" -> "quit")."""
    low = str(reason or "").lower()
    for word, kind in _HOTKEY_KINDS:
        if word in low:
            return kind
    return "other"


def kind_label(kind: str) -> str:
    """How a kind is shown to the person ("send" -> "Send")."""
    return KIND_LABELS.get(kind, KIND_LABELS["other"])


def ceiling(env: Mapping[str, str] | None = None) -> int:
    """Uses a rule allows per day (``PINLET_RULE_CEILING``; 0 = no ceiling)."""
    env = os.environ if env is None else env
    try:
        return max(0, int(env.get("PINLET_RULE_CEILING", DEFAULT_CEILING)))
    except (TypeError, ValueError):
        return DEFAULT_CEILING


@dataclass
class Rule:
    """One saved "don't ask again"."""

    id: str
    kind: str  # "send", "delete"… or "*"
    app: str  # normalized app name, or "*"
    agent: str  # client key ("claude", "cursor"…), or "*"
    scope: str  # task | hour | day | always
    created: float
    expires: float | None = None  # None: a task rule, ends with the task
    pid: int | None = None  # task rules: the session it belongs to
    app_label: str = ""  # the app as the person saw it ("Mail")
    agent_label: str = ""  # "Claude Code"
    sig: str = ""

    def _signed_text(self) -> bytes:
        fields = (self.id, self.kind, self.app, self.agent, self.scope, int(self.created))
        return json.dumps([*fields, int(self.expires or 0)], separators=(",", ":")).encode()

    def sign(self, key: bytes) -> None:
        """Stamp the rule with ``key``."""
        self.sig = hmac.new(key, self._signed_text(), hashlib.sha256).hexdigest()

    def signed_by(self, key: bytes | None) -> bool:
        """True when the rule carries a signature made with ``key``."""
        if not key or not self.sig:
            return False
        want = hmac.new(key, self._signed_text(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(want, self.sig)

    def covers(self, kind: str, app: str, agent: str, pid: int | None) -> bool:
        """True when this rule is about that step, in that app, by that AI."""
        if self.scope == TASK and self.pid != pid:
            return False
        return self.kind in (ANY, kind) and self.app in (ANY, app) and self.agent in (ANY, agent)

    @property
    def width(self) -> int:
        """How many parts are "any": narrower rules are tried first."""
        return (self.kind == ANY) + (self.app == ANY) + (self.agent == ANY)

    def describe(self) -> str:
        """The rule in the person's words: "Send in Mail"."""
        what = kind_label(self.kind)
        where = f" in {self.app_label or self.app}" if self.app != ANY else ""
        return what + where


def _midnight(now: float) -> float:
    t = time.localtime(now)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday + 1, 0, 0, 0, 0, 0, -1))


def expiry(scope: str, now: float, env: Mapping[str, str] | None = None) -> float | None:
    """When a rule of ``scope`` made at ``now`` stops counting (None for a task rule)."""
    if scope == HOUR:
        return now + 3600.0
    if scope == DAY:
        return _midnight(now)
    if scope == ALWAYS:
        life = always_seconds(dict(env) if env is not None else None)
        return now + life if life > 0 else now + 100 * 365 * 86400.0
    return None


class RuleBook:
    """The rules in force. Thread safe; one per engine (see :func:`book`)."""

    def __init__(self, path: Path | None = None, env: Mapping[str, str] | None = None) -> None:
        self._env = os.environ if env is None else env
        self.path = Path(path) if path is not None else default_path(self._env)
        self._lock = threading.RLock()
        self._task: list[Rule] = []
        self._saved: list[Rule] = []
        self._mtime: float | None = None
        self._key: bytes | None = None
        self._uses: dict[str, tuple[str, int]] = {}  # rule id -> (day, uses that day)
        self._app_owned = self._env.get("PINLET_APP_OWNED") == "1"

    # -------------------------------------------------------------- key
    def set_key(self, key: bytes | None) -> None:
        """The signing key from Pinlet.app's Keychain (control op ``rules_key``)."""
        with self._lock:
            self._key = key if key and len(key) >= 16 else None
            self._mtime = None  # read the file again with the key

    def _signing_key(self) -> bytes | None:
        if self._key is not None or self._app_owned:
            return self._key  # with the app, only the app's key counts
        key_path = self.path.with_name("rules.key")
        try:
            return bytes.fromhex(key_path.read_text().strip())
        except (OSError, ValueError):
            pass
        key = secrets.token_bytes(32)
        try:
            key_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = key_path.with_name(f".{key_path.name}.{os.getpid()}")
            tmp.write_text(key.hex())
            os.chmod(tmp, 0o600)
            os.replace(tmp, key_path)
        except OSError:
            return None
        self._key = key
        return key

    @property
    def can_save(self) -> bool:
        """True when a rule that outlives the task can be saved (a signing key is known)."""
        with self._lock:
            return self._signing_key() is not None

    # ------------------------------------------------------------- file
    def _load(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self._saved, self._mtime = [], None
            return
        if mtime == self._mtime:
            return
        self._mtime = mtime
        key = self._signing_key()
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self._saved = []
            return
        rules = []
        known = set(Rule.__dataclass_fields__)
        for raw in data.get("rules", []) if isinstance(data, dict) else []:
            if not isinstance(raw, dict):
                continue
            try:
                rule = Rule(**{k: v for k, v in raw.items() if k in known})
            except TypeError:
                continue
            if rule.scope in (HOUR, DAY, ALWAYS) and rule.expires and rule.signed_by(key):
                rules.append(rule)
        self._saved = rules

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}")
        body = {"rules": [asdict(r) for r in self._saved]}
        tmp.write_text(json.dumps(body, indent=2) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
        try:
            self._mtime = self.path.stat().st_mtime
        except OSError:
            self._mtime = None

    def _live(self, now: float) -> list[Rule]:
        self._load()
        saved = [r for r in self._saved if r.expires is not None and r.expires > now]
        return self._task + saved

    # -------------------------------------------------------------- use
    def add(
        self,
        kind: str,
        app: str,
        agent: str,
        scope: str = TASK,
        pid: int | None = None,
        app_label: str = "",
        agent_label: str = "",
        now: float | None = None,
    ) -> Rule | None:
        """Save a rule. None when it cannot be saved (no signing key yet, or a bad scope)."""
        if scope not in SCOPES:
            return None
        now = time.time() if now is None else now
        rule = Rule(
            id=secrets.token_hex(6),
            kind=kind or "other",
            app=normalize(app) if app != ANY else ANY,
            agent=agent or ANY,
            scope=scope,
            created=now,
            expires=expiry(scope, now, self._env),
            pid=pid if scope == TASK else None,
            app_label=app_label or ("" if app == ANY else app),
            agent_label=agent_label,
        )
        with self._lock:
            if scope == TASK:
                self._task = [r for r in self._task if not _same(r, rule)] + [rule]
                return rule
            key = self._signing_key()
            if key is None:
                return None
            self._load()
            rule.sign(key)
            keep = [r for r in self._saved if (r.expires or 0) > now and not _same(r, rule)]
            self._saved = [*keep, rule]
            try:
                self._write()
            except OSError:
                return None
            return rule

    def match(
        self, kind: str, app: str | None, agent: str, pid: int | None, now: float | None = None
    ) -> Rule | None:
        """The rule that lets this step through without asking, or None to ask.

        Counts the use: past the daily ceiling the rule stops matching until tomorrow.
        """
        now = time.time() if now is None else now
        app = normalize(app)
        with self._lock:
            for rule in sorted(self._live(now), key=lambda r: r.width):
                if not rule.covers(kind, app, agent or ANY, pid):
                    continue
                if self._count(rule, now):
                    return rule
        return None

    def _count(self, rule: Rule, now: float) -> bool:
        limit = ceiling(self._env)
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        seen_day, uses = self._uses.get(rule.id, (day, 0))
        if seen_day != day:
            uses = 0
        if limit and uses >= limit:
            return False
        self._uses[rule.id] = (day, uses + 1)
        return True

    def end_task(self, pid: int | None) -> int:
        """Stop, or the AI's session ended: its "for this task" rules are over."""
        with self._lock:
            before = len(self._task)
            self._task = [r for r in self._task if r.pid != pid]
            return before - len(self._task)

    def revoke(self, rule_id: str) -> bool:
        """Take one rule back. False when there is no such rule."""
        with self._lock:
            self._load()
            task = [r for r in self._task if r.id != rule_id]
            saved = [r for r in self._saved if r.id != rule_id]
            found = len(task) != len(self._task) or len(saved) != len(self._saved)
            self._task = task
            if len(saved) != len(self._saved):
                self._saved = saved
                try:
                    self._write()
                except OSError:
                    return False
            return found

    def revoke_all(self) -> int:
        """Every rule is gone ("Ask about everything again")."""
        with self._lock:
            self._load()
            n = len(self._task) + len(self._saved)
            self._task, self._saved = [], []
            try:
                if self.path.exists():
                    self._write()
            except OSError:
                pass
            return n

    def listing(self, now: float | None = None) -> list[dict[str, Any]]:
        """The rules in force as JSON for the app's Permissions list, newest first."""
        now = time.time() if now is None else now
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        with self._lock:
            out = []
            for r in sorted(self._live(now), key=lambda r: -r.created):
                seen_day, uses = self._uses.get(r.id, (day, 0))
                out.append(
                    {
                        "id": r.id,
                        "what": r.describe(),
                        "kind": r.kind,
                        "app": r.app_label or r.app,
                        "agent": r.agent_label or ("Any AI" if r.agent == ANY else r.agent),
                        "scope": r.scope,
                        "until": SCOPE_LABELS[TASK] if r.expires is None else "",
                        "expires": r.expires,
                        "pid": r.pid,
                        "uses_today": uses if seen_day == day else 0,
                        "ceiling": ceiling(self._env),
                    }
                )
            return out


def _same(a: Rule, b: Rule) -> bool:
    return (a.kind, a.app, a.agent, a.scope, a.pid) == (b.kind, b.app, b.agent, b.scope, b.pid)


def default_path(env: Mapping[str, str] | None = None) -> Path:
    """``~/.pinlet/rules.json`` (``PINLET_RULES_FILE``)."""
    env = os.environ if env is None else env
    return Path(os.path.expanduser(env.get("PINLET_RULES_FILE") or "~/.pinlet/rules.json"))


_BOOK: RuleBook | None = None
_BOOK_LOCK = threading.Lock()


def book() -> RuleBook:
    """This engine's rule book."""
    global _BOOK
    with _BOOK_LOCK:
        if _BOOK is None:
            _BOOK = RuleBook()
        return _BOOK


def reset_for_tests(path: Path | None = None, env: Mapping[str, str] | None = None) -> RuleBook:
    """A fresh rule book (tests)."""
    global _BOOK
    with _BOOK_LOCK:
        _BOOK = RuleBook(path=path, env=env)
        return _BOOK


def asker() -> tuple[int | None, str, str]:
    """Who is asking right now: the session's process id, client key and name.

    The process id comes from the kernel (the socket's peer); the client key
    is looked up from the name the client gave when it connected.
    """
    try:
        from pinlet.clients import profile_for
        from pinlet.hub import sessions

        info = sessions.CURRENT.get()
    except Exception:  # noqa: BLE001
        return os.getpid(), "local", ""
    if info is None:
        return os.getpid(), "local", ""
    client = info.client or info.name
    return info.pid, profile_for(client).key or "unknown", client
