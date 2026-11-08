"""Where the shared Pinlet listens."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path


def run_dir(env: Mapping[str, str] | None = None, platform: str = sys.platform) -> Path:
    """Directory for the shared Pinlet's socket and lock."""
    env = os.environ if env is None else env
    if env.get("PINLET_SOCKET"):
        return Path(os.path.expanduser(env["PINLET_SOCKET"])).parent
    if platform == "darwin":
        return Path(os.path.expanduser("~/Library/Application Support/Pinlet"))
    runtime = env.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isdir(runtime):
        return Path(runtime) / "pinlet"
    return Path(os.path.expanduser("~/.pinlet/run"))


def socket_path(env: Mapping[str, str] | None = None, platform: str = sys.platform) -> Path:
    """Unix socket the shared Pinlet listens on (``PINLET_SOCKET`` overrides it)."""
    env = os.environ if env is None else env
    if env.get("PINLET_SOCKET"):
        return Path(os.path.expanduser(env["PINLET_SOCKET"]))
    return run_dir(env, platform) / "pinlet.sock"


def lock_path(env: Mapping[str, str] | None = None) -> Path:
    """Lock file that makes sure only one shared Pinlet runs."""
    return socket_path(env).with_suffix(".lock")


def daemon_log(env: Mapping[str, str] | None = None) -> Path:
    """Log file of the shared Pinlet."""
    return Path(os.path.expanduser("~/.pinlet/logs/daemon.log"))


def ensure_private_dir(path: Path) -> None:
    """Creates ``path`` if needed and makes it readable by this user only."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def app_config_path() -> Path:
    """Written by the connector, read by Pinlet.app: how to start ``pinlet serve``."""
    return Path(os.path.expanduser("~/.pinlet/app.json"))
