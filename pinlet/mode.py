"""How much Pinlet asks before it acts: turbo (default), careful or secure.

A ladder of three levels, from least to most asking:

* ``turbo`` (Full, the default): no permission prompts. Pinlet does not ask
  before using an app, before risky shortcuts or before clicking Send / Delete
  / Pay, and it keeps going when the person touches the mouse. Stop and Pause
  still work.
* ``careful`` (Ask for risky): Allow/Deny for risky shortcuts and
  consequential buttons, no typing into password fields, and Pinlet pauses
  when the person takes over. No "Let Pinlet use …?" question per app.
* ``secure`` (Ask everything): all of careful, plus "Let Pinlet use …?" once
  per app (``pinlet.overlay.grants``, ``~/.pinlet/apps.json``).

Where a client's level comes from, the strictest wins:

* the client's own config: ``pinlet --careful`` / ``--secure`` / ``--turbo``,
  or ``PINLET_MODE`` (``PINLET_SECURE=1`` works too);
* the person's per-client choice in ``~/.pinlet/modes.json``
  (``pinlet mode cursor careful``; see :func:`client_modes`);
* an admin policy's ``"mode"`` (see ``pinlet.policy``), which no client can go
  below.

The level is fixed for the life of a session, so an AI cannot lower it mid-task.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

from pinlet.envflags import is_true

TURBO, CAREFUL, SECURE = "turbo", "careful", "secure"
LEVELS = (TURBO, CAREFUL, SECURE)  # least to most asking
LABELS = {TURBO: "Full", CAREFUL: "Ask for risky", SECURE: "Ask everything"}


def level(mode: str | None) -> int:
    """0 for turbo, 1 for careful, 2 for secure; unknown values count as turbo."""
    return LEVELS.index(mode) if mode in LEVELS else 0


def strictest(*modes: str | None) -> str:
    """The most asking of ``modes`` (turbo when none is a known level)."""
    return LEVELS[max([level(m) for m in modes] or [0])]


# In the shared Pinlet (``pinlet serve``) each connected client has its own
# mode, set for the duration of its session (see pinlet.hub).
_SESSION_MODE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "pinlet_mode", default=None
)


@contextlib.contextmanager
def session_mode(mode: str | None) -> Iterator[None]:
    """Run the block (and the tool calls it starts) in ``mode``."""
    token = _SESSION_MODE.set(mode if mode in LEVELS else None)
    try:
        yield
    finally:
        _SESSION_MODE.reset(token)


def _requested(env: Mapping[str, str], argv: Sequence[str]) -> str:
    if "--secure" in argv:
        return SECURE
    if "--careful" in argv:
        return CAREFUL
    if "--turbo" in argv:
        return TURBO
    mode = env.get("PINLET_MODE", "").strip().lower()
    if mode in LEVELS:
        return mode
    if is_true(env, "PINLET_SECURE"):
        return SECURE
    return TURBO


def security_mode(env: Mapping[str, str] | None = None, argv: Sequence[str] | None = None) -> str:
    """Returns this call's level: ``"turbo"``, ``"careful"`` or ``"secure"``.

    The admin policy's floor wins over anything lower; then the session's mode
    in a shared Pinlet; then ``--secure`` / ``--careful`` / ``--turbo`` in
    ``argv``; then ``PINLET_MODE`` or ``PINLET_SECURE`` in ``env``.
    """
    from pinlet.policy import current as _admin_policy

    floor = _admin_policy().floor
    if env is None and argv is None:
        current = _SESSION_MODE.get()
        if current is not None:
            return strictest(current, floor)
    env = os.environ if env is None else env
    argv = sys.argv if argv is None else argv
    return strictest(_requested(env, argv), floor)


def is_secure(env: Mapping[str, str] | None = None, argv: Sequence[str] | None = None) -> bool:
    """True when :func:`security_mode` is ``"secure"`` (asks before using each app)."""
    return security_mode(env, argv) == SECURE


def asks_risky(env: Mapping[str, str] | None = None, argv: Sequence[str] | None = None) -> bool:
    """True for careful and secure: Allow/Deny before risky steps, pause on take-over."""
    return security_mode(env, argv) != TURBO


# ------------------------------------------------------- per-client choice
def modes_path() -> Path:
    """``~/.pinlet/modes.json``: the person's level per AI client (``PINLET_MODES_FILE``)."""
    return Path(os.path.expanduser(os.environ.get("PINLET_MODES_FILE") or "~/.pinlet/modes.json"))


def client_modes(path: Path | None = None) -> dict[str, str]:
    """``{client key: level}``, client keys as in :mod:`pinlet.clients` ("cursor", "codex")."""
    path = modes_path() if path is None else path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if v in LEVELS}


def set_client_mode(key: str, mode: str | None, path: Path | None = None) -> dict[str, str]:
    """Saves (or with ``None`` forgets) the level for one client; returns all choices.

    Raises:
        ValueError: ``mode`` is not a level.
    """
    if mode is not None and mode not in LEVELS:
        raise ValueError(f"unknown level {mode!r}; use one of {', '.join(LEVELS)}")
    path = modes_path() if path is None else path
    modes = client_modes(path)
    if mode is None:
        modes.pop(key, None)
    else:
        modes[key] = mode
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(modes, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return modes


def mode_for_client(requested: str | None, client_name: str | None) -> str:
    """The level a newly connected client gets.

    The strictest of its request, the person's choice for it and the admin
    policy's floor.
    """
    from pinlet.clients import profile_for
    from pinlet.policy import current as _admin_policy

    chosen = client_modes().get(profile_for(client_name).key)
    return strictest(requested, chosen, _admin_policy().floor)


def mode_key(setup_key: str) -> str:
    """The client key a ``pinlet setup`` tool's level is saved under ("antigravity-cli" -> "antigravity")."""
    return {"antigravity-cli": "antigravity"}.get(setup_key, setup_key)


def overview() -> dict[str, object]:
    """Every client's saved level and the admin floor, for ``pinlet mode --json``."""
    from pinlet.policy import current as _admin_policy

    floor = _admin_policy().floor
    return {"modes": client_modes(), "floor": floor, "levels": list(LEVELS), "labels": LABELS}


def main(argv: list[str] | None = None) -> int:
    """``pinlet mode [<client> <turbo|careful|secure|default>] [--json]``: show or set levels."""
    from pinlet.clients import _TABLE
    from pinlet.policy import current as _admin_policy

    argv = list(argv or [])
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    keys = [p.key for _, p in _TABLE]
    if len(argv) == 2:
        key, value = argv[0].lower(), argv[1].lower()
        if key not in keys:
            why = f"unknown client {key!r}; one of {', '.join(keys)}"
            print(json.dumps({"ok": False, "error": why}) if as_json else f"pinlet mode: {why}")
            return 2
        try:
            set_client_mode(key, None if value == "default" else value)
        except ValueError as exc:
            print(
                json.dumps({"ok": False, "error": str(exc)}) if as_json else f"pinlet mode: {exc}"
            )
            return 2
        if not as_json:
            print("Saved. It applies the next time that client connects to Pinlet.")
    elif argv:
        print("usage: pinlet mode [<client> <turbo|careful|secure|default>] [--json]")
        return 2
    if as_json:
        print(json.dumps({"ok": True, **overview()}))
        return 0
    modes = client_modes()
    floor = _admin_policy().floor
    for key in keys:
        mine = modes.get(key)
        shown = strictest(mine, floor)
        note = " (admin policy)" if floor and level(floor) > level(mine) else ""
        print(f"{key:16} {LABELS[shown]} ({shown}){note}" if mine or note else f"{key:16} default")
    return 0
