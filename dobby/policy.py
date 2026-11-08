"""Admin policy: rules an organisation sets for every Dobby on a computer.

A person's own settings (``DOBBY_*`` variables, ``~/.dobby/apps.json``,
``--turbo``) cannot loosen what this file says. It lives at a system path
that only an administrator can write, so MDM tools (Jamf, Intune, Ansible…)
can deploy it:

* macOS:   ``/Library/Application Support/Dobby/policy.json``
* Linux:   ``/etc/dobby/policy.json``
* Windows: ``%ProgramData%\\Dobby\\policy.json``

When no system file exists, ``DOBBY_POLICY_FILE`` may point at one (for
trying a policy out, and for tests). With a system file it is ignored, so a
person cannot swap the admin's policy for their own.

Every key is optional::

    {
      "mode": "secure",
      "tools": {"deny": ["run_terminal_command"], "allow": []},
      "shell": {"enabled": true, "allow": ["git status", "git log *", "open -a *"]},
      "apps": {"Slack": "read", "Terminal": "click", "Vivado": "full"},
      "app_default": "ask"
    }

* ``mode``: ``"secure"`` turns permission prompts on for every client;
  ``--turbo`` and ``DOBBY_MODE=turbo`` are then ignored.
* ``tools.deny``: tools no client may call. ``tools.allow``, when not empty,
  is the only tools a client may call (``manage_toolsets`` and the handoff
  tools stay callable so the AI can still find out what is allowed).
* ``shell``: ``run_terminal_command``. Off unless ``enabled`` is true. With
  an ``allow`` list, a command runs only when it matches one of its
  patterns (``*`` matches anything), and it runs without a shell, so ``;``,
  ``|``, ``&&``, ``$`` variables and redirects are refused.
* ``apps``: app tiers (``full``, ``click``, ``read``, see
  ``dobby.overlay.grants``) that win over ``apps.json`` and over the
  person's "Allow" on the overlay. ``app_default`` is the tier, or ``ask``,
  for apps not listed.

A file that exists but cannot be read or parsed fails closed: secure mode,
no shell and no tools beyond the core ones, until it is fixed.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import shlex
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple

log = logging.getLogger("dobby.policy")

TIERS = ("full", "click", "read")

# Tools that stay callable under a ``tools.allow`` list, so the AI can learn
# what it may do and hand control back.
ALWAYS_CALLABLE = frozenset({"manage_toolsets", "wait_for_wake"})

# Shell syntax that only means something to a shell. Refused when commands
# run from an allow list (they run without a shell).
_SHELL_SYNTAX = (";", "|", "&", "`", "$", ">", "<", "\n")


def system_path(platform: Optional[str] = None, env: Optional[Mapping[str, str]] = None) -> Path:
    platform = sys.platform if platform is None else platform
    env = os.environ if env is None else env
    if platform == "darwin":
        return Path("/Library/Application Support/Dobby/policy.json")
    if platform.startswith("win"):
        return Path(env.get("ProgramData", r"C:\ProgramData")) / "Dobby" / "policy.json"
    return Path("/etc/dobby/policy.json")


@dataclass(frozen=True)
class Policy:
    """The rules in force. ``Policy()`` (no file) leaves everything to the person."""

    source: Optional[str] = None
    mode: Optional[str] = None
    tools_deny: FrozenSet[str] = frozenset()
    tools_allow: FrozenSet[str] = frozenset()
    shell_enabled: Optional[bool] = None
    shell_allow: Tuple[str, ...] = ()
    apps: Dict[str, str] = field(default_factory=dict)
    app_default: Optional[str] = None
    broken: Optional[str] = None  # why the file could not be used

    # ------------------------------------------------------------ queries
    @property
    def active(self) -> bool:
        return self.source is not None

    @property
    def forces_secure(self) -> bool:
        return self.mode == "secure" or self.broken is not None

    def tool_allowed(self, name: str) -> bool:
        if name in ALWAYS_CALLABLE:
            return True
        if self.broken is not None:
            return False
        if name in self.tools_deny:
            return False
        return not self.tools_allow or name in self.tools_allow

    def tool_refusal(self, name: str) -> str:
        if self.broken is not None:
            return (f"Tool '{name}' is blocked: the admin policy at {self.source} could not be read "
                    f"({self.broken}). Ask your administrator.")
        return f"Tool '{name}' is turned off by your organisation's Dobby policy ({self.source})."

    def app_tier(self, normalized_app: str) -> Optional[str]:
        """The admin's tier for an app (already normalized), or None when not set."""
        if self.broken is not None:
            return "read"
        tier = self.apps.get(normalized_app)
        if tier is None and self.app_default in TIERS:
            tier = self.app_default
        return tier

    def summary(self) -> Dict[str, Any]:
        if not self.active:
            return {"active": False}
        return {
            "active": True,
            "file": self.source,
            "broken": self.broken,
            "mode": "secure" if self.forces_secure else (self.mode or "person decides"),
            "tools_denied": sorted(self.tools_deny),
            "tools_allowed_only": sorted(self.tools_allow),
            "shell": shell_summary(self),
            "apps": dict(self.apps),
            "app_default": self.app_default or "ask",
        }


def shell_summary(policy: Policy) -> str:
    enabled, _ = shell_enabled(policy)
    if not enabled:
        return "off"
    return "allow list" if policy.shell_allow else "on"


def _strings(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    return []


def parse(data: Any, source: str) -> Policy:
    """Build a :class:`Policy` from the decoded JSON. Unknown keys are ignored."""
    from dobby.overlay.grants import normalize

    if not isinstance(data, dict):
        return Policy(source=source, broken="the file is not a JSON object")
    mode = str(data.get("mode", "")).strip().lower() or None
    if mode not in (None, "secure", "turbo"):
        return Policy(source=source, broken=f"unknown mode {mode!r}")
    tools = data.get("tools") if isinstance(data.get("tools"), dict) else {}
    shell = data.get("shell") if isinstance(data.get("shell"), dict) else {}
    apps_in = data.get("apps") if isinstance(data.get("apps"), dict) else {}
    apps: Dict[str, str] = {}
    for name, tier in apps_in.items():
        tier = str(tier).strip().lower()
        if tier not in TIERS:
            return Policy(source=source, broken=f"app {name!r} has unknown tier {tier!r}")
        apps[normalize(name)] = tier
    default = str(data.get("app_default", "ask")).strip().lower()
    if default not in TIERS + ("ask",):
        return Policy(source=source, broken=f"unknown app_default {default!r}")
    enabled = shell.get("enabled")
    return Policy(
        source=source,
        mode=mode,
        tools_deny=frozenset(_strings(tools.get("deny"))),
        tools_allow=frozenset(_strings(tools.get("allow"))),
        shell_enabled=bool(enabled) if enabled is not None else None,
        shell_allow=tuple(" ".join(p.split()) for p in _strings(shell.get("allow"))),
        apps=apps,
        app_default=None if default == "ask" else default,
    )


def read_policy(path: Path) -> Policy:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Policy()
    except OSError as exc:
        return Policy(source=str(path), broken=f"cannot read it: {exc.strerror or exc}")
    try:
        data = json.loads(text)
    except ValueError as exc:
        return Policy(source=str(path), broken=f"not valid JSON: {exc}")
    return parse(data, str(path))


class _Loader:
    """Caches the policy and re-reads it when the file changes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key: Optional[Tuple[str, Optional[float]]] = None
        self._policy = Policy()

    def path(self) -> Optional[Path]:
        system = system_path()
        try:
            if system.exists():
                return system
        except OSError:
            return system  # there but unreadable: read_policy fails closed
        override = os.environ.get("DOBBY_POLICY_FILE", "").strip()
        return Path(os.path.expanduser(override)) if override else None

    def get(self) -> Policy:
        path = self.path()
        if path is None:
            return Policy()
        try:
            mtime: Optional[float] = path.stat().st_mtime
        except OSError:
            mtime = None
        key = (str(path), mtime)
        with self._lock:
            if key != self._key:
                self._key = key
                self._policy = read_policy(path)
                if self._policy.broken:
                    log.error("admin policy %s is unusable (%s); failing closed", path, self._policy.broken)
                elif self._policy.active:
                    log.info("admin policy loaded from %s", path)
            return self._policy

    def reset(self) -> None:
        with self._lock:
            self._key, self._policy = None, Policy()


_LOADER = _Loader()


def current() -> Policy:
    """The admin policy in force right now (``Policy()`` when there is none)."""
    return _LOADER.get()


def reset_cache() -> None:
    _LOADER.reset()


# ---------------------------------------------------------------- shell
_TRUE = {"1", "true", "yes", "on"}


def shell_enabled(policy: Optional[Policy] = None, env: Optional[Mapping[str, str]] = None) -> Tuple[bool, str]:
    """Whether ``run_terminal_command`` may run, and why not when it may not.

    The admin policy decides when it says anything. Otherwise the person opts
    in with ``DOBBY_ALLOW_SHELL=1`` (or ``allow_terminal_commands`` in the
    config); it is off by default.
    """
    from dobby.config import CONFIG

    policy = current() if policy is None else policy
    env = os.environ if env is None else env
    if policy.broken is not None:
        return False, policy.tool_refusal("run_terminal_command")
    if policy.shell_enabled is not None:
        if policy.shell_enabled:
            return True, ""
        return False, f"Terminal commands are turned off by your organisation's Dobby policy ({policy.source})."
    if env.get("DOBBY_ALLOW_SHELL", "").strip().lower() in _TRUE or CONFIG.allow_terminal_commands:
        return True, ""
    return False, ("Terminal commands are off. The person can turn them on with DOBBY_ALLOW_SHELL=1 "
                   "in the MCP client's config; until then, open apps and files through the screen.")


def check_command(command: str, policy: Optional[Policy] = None,
                  env: Optional[Mapping[str, str]] = None) -> Tuple[Optional[List[str]], Optional[str]]:
    """Decide how ``command`` may run.

    Returns ``(argv, None)`` to run without a shell, ``(None, None)`` to run
    it through the shell as written (the person turned the shell on and no
    allow list applies), or ``(None, reason)`` when it is refused.
    """
    policy = current() if policy is None else policy
    ok, why = shell_enabled(policy, env)
    if not ok:
        return None, why
    if not command or not command.strip():
        return None, "Missing command."
    if not policy.shell_allow:
        return None, None
    if any(s in command for s in _SHELL_SYNTAX):
        return None, ("This command uses shell syntax (; | & ` $ > <), which the Dobby policy does not "
                      "allow. Run one plain command at a time.")
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return None, f"Could not parse the command: {exc}"
    if not argv:
        return None, "Missing command."
    flat = " ".join(argv)
    for pattern in policy.shell_allow:
        if fnmatch.fnmatchcase(flat, pattern):
            return argv, None
    return None, (f"'{flat}' is not on the list of commands your organisation's Dobby policy allows. "
                  f"Allowed: {', '.join(policy.shell_allow)}.")


def run_command(command: str, timeout: float, cwd: Optional[str] = None) -> Dict[str, Any]:
    """Run ``command`` if the policy allows it. Never raises."""
    import subprocess
    import time

    argv, refused = check_command(command)
    if refused is not None:
        log.warning("refused terminal command: %s", refused)
        return {"success": False, "exit_code": -1, "error": refused, "refused": True}
    start = time.perf_counter()
    try:
        if argv is not None:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        else:
            shell = "/bin/bash" if os.path.exists("/bin/bash") else None
            proc = subprocess.run(command, shell=True, executable=shell, capture_output=True, text=True,
                                  timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        return {"success": False, "exit_code": -1, "error": f"Command timed out after {timeout} seconds",
                "execution_time_sec": timeout}
    except (OSError, ValueError) as exc:
        return {"success": False, "exit_code": -1, "error": str(exc)}
    return {
        "success": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "execution_time_sec": round(time.perf_counter() - start, 3),
    }
