"""Carries settings over from Pinlet's old name, Dobby.

Pinlet was called Dobby until November 2026. For one release it keeps old
setups working without any action from the person:

* every ``DOBBY_*`` environment variable is copied to its ``PINLET_*`` twin
  when the twin is not set, so old MCP entries and shell profiles still work;
* ``~/.dobby`` is moved to ``~/.pinlet`` once, and a ``~/.dobby`` symlink is
  left behind for anything that still has the old path.

The admin policy file is also read from its old location (see
:func:`pinlet.policy.system_path`).
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path

OLD_PREFIX = "DOBBY_"
NEW_PREFIX = "PINLET_"


def copy_env(env: MutableMapping[str, str] | None = None) -> list[str]:
    """Copies ``DOBBY_*`` variables to unset ``PINLET_*`` ones; returns the names copied."""
    env = os.environ if env is None else env
    copied = []
    for key in [k for k in env if k.startswith(OLD_PREFIX)]:
        new = NEW_PREFIX + key[len(OLD_PREFIX) :]
        if new not in env:
            env[new] = env[key]
            copied.append(new)
    return copied


def move_home(home: Path | None = None) -> bool:
    """Moves ``~/.dobby`` to ``~/.pinlet`` once; returns True when it moved."""
    home = Path.home() if home is None else home
    old, new = home / ".dobby", home / ".pinlet"
    try:
        if old.is_symlink() or not old.is_dir() or new.exists():
            return False
        old.rename(new)
        old.symlink_to(new.name, target_is_directory=True)
        return True
    except OSError:
        return False


def migrate() -> None:
    """Runs both steps; never raises, so importing Pinlet cannot fail because of it."""
    try:
        copy_env()
        move_home()
    except Exception:  # noqa: BLE001 - a migration must never stop Pinlet starting
        pass
