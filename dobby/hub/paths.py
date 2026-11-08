"""Where the shared Dobby listens."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path


def run_dir(env: Mapping[str, str] | None = None, platform: str = sys.platform) -> Path:
    env = os.environ if env is None else env
    if env.get("DOBBY_SOCKET"):
        return Path(os.path.expanduser(env["DOBBY_SOCKET"])).parent
    if platform == "darwin":
        return Path(os.path.expanduser("~/Library/Application Support/Dobby"))
    runtime = env.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isdir(runtime):
        return Path(runtime) / "dobby"
    return Path(os.path.expanduser("~/.dobby/run"))


def socket_path(env: Mapping[str, str] | None = None, platform: str = sys.platform) -> Path:
    env = os.environ if env is None else env
    if env.get("DOBBY_SOCKET"):
        return Path(os.path.expanduser(env["DOBBY_SOCKET"]))
    return run_dir(env, platform) / "dobby.sock"


def lock_path(env: Mapping[str, str] | None = None) -> Path:
    return socket_path(env).with_suffix(".lock")


def daemon_log(env: Mapping[str, str] | None = None) -> Path:
    return Path(os.path.expanduser("~/.dobby/logs/daemon.log"))


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def app_config_path() -> Path:
    """Written by the connector, read by Dobby.app: how to start ``dobby serve``."""
    return Path(os.path.expanduser("~/.dobby/app.json"))
