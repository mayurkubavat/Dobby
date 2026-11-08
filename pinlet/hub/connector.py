"""``pinlet``: connect an AI client's stdio to the shared Pinlet.

Standard library only, and no Pinlet engine imports: this runs once per AI
client, so it must start in milliseconds.

Wire format on the socket: one hello line each way, then MCP JSON-RPC
messages exactly as on stdio (one JSON object per line)::

    connector -> {"pinlet_hello": 1, "pid": 4242, "parent_pid": 4100, "mode": "turbo",
                  "name": "Antigravity", "cwd": "/Users/me/proj"}
    server    -> {"pinlet_ready": 1, "server_pid": 3900, "session": 7}
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from . import paths

HELLO_VERSION = 1
CONNECT_TIMEOUT_S = 90.0  # the first start loads the models
_FALSE = {"0", "false", "no", "off"}


def shared_enabled(env: dict[str, str] | None = None) -> bool:
    """True when clients should share one Pinlet (``PINLET_SHARED`` not 0, and AF_UNIX exists)."""
    env = os.environ if env is None else env
    if not hasattr(socket, "AF_UNIX"):
        return False
    return env.get("PINLET_SHARED", "").strip().lower() not in _FALSE


def _mode(argv: Sequence[str]) -> str:
    from pinlet.mode import security_mode  # stdlib-only module

    return security_mode(argv=list(argv), env=os.environ)


def _parent_name() -> str | None:
    """The client app's name, for the bar and status, when the OS tells us."""
    ppid = os.getppid()
    try:
        out = subprocess.run(
            ["ps", "-o", "comm=", "-p", str(ppid)], capture_output=True, text=True, timeout=1
        )
        name = os.path.basename(out.stdout.strip())
        return name or None
    except Exception:  # noqa: BLE001
        return None


def hello(argv: Sequence[str]) -> dict[str, object]:
    """The first line a client sends to the shared Pinlet: protocol version, pids, mode, name."""
    return {
        "pinlet_hello": HELLO_VERSION,
        "pid": os.getpid(),
        "parent_pid": os.getppid(),
        "mode": _mode(argv),
        "name": os.environ.get("PINLET_AGENT_NAME") or _parent_name() or "AI client",
        "cwd": os.getcwd(),
    }


def _connect(path: Path) -> socket.socket | None:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(str(path))
        return sock
    except OSError:
        sock.close()
        return None


# Settings that belong to one client, not to the shared Pinlet: each client
# sends them in its hello instead.
PER_CLIENT_ENV = ("PINLET_AGENT_NAME", "PINLET_MODE", "PINLET_SECURE", "PINLET_OVERLAY_LABEL")


def daemon_env(env: dict[str, str]) -> dict[str, str]:
    """The environment to start the shared Pinlet with, minus per-client variables."""
    return {k: v for k, v in env.items() if k not in PER_CLIENT_ENV}


# --------------------------------------------------------------- Pinlet.app
APP_ID = "dev.pinlet.app"
# What the Pinlet app needs to start ``pinlet serve`` itself: this Python, and
# the settings that shape the engine (never secrets the client happens to have).
_APP_ENV_KEYS = ("PATH", "PYTHONPATH", "VIRTUAL_ENV", "OLLAMA_HOST", "HF_HOME", "LANG")


def find_app(env: dict[str, str] | None = None, platform: str = sys.platform) -> Path | None:
    """Pinlet.app on macOS: ``PINLET_APP``, ~/Applications, /Applications, or the checkout's own build.

    ``PINLET_APP=0`` never uses it.
    """
    env = os.environ if env is None else env
    if platform != "darwin":
        return None
    choice = env.get("PINLET_APP", "").strip()
    if choice.lower() in _FALSE:
        return None
    root = Path(__file__).resolve().parents[2]
    candidates = (
        [Path(os.path.expanduser(choice))]
        if choice
        else [
            Path.home() / "Applications" / "Pinlet.app",
            Path("/Applications/Pinlet.app"),
            root / "apps" / "macos" / "build" / "Pinlet.app",
        ]
    )
    return next((c for c in candidates if (c / "Contents" / "MacOS").is_dir()), None)


def app_config(env: dict[str, str] | None = None) -> dict[str, object]:
    """Settings Pinlet.app passes to the ``pinlet serve`` it starts: PINLET_* and a few others."""
    env = os.environ if env is None else env
    keep = {
        k: v
        for k, v in daemon_env(dict(env)).items()
        if k.startswith("PINLET_") or k in _APP_ENV_KEYS
    }
    keep.pop("PINLET_APP", None)
    return {
        "python": sys.executable,
        "cwd": str(Path(__file__).resolve().parents[2]),
        "env": keep,
        "written_by": os.getpid(),
    }


def write_app_config(env: dict[str, str] | None = None) -> Path:
    """Writes :func:`app_config` where Pinlet.app reads it, privately and atomically.

    Returns:
        The path of the written config file.
    """
    path = paths.app_config_path()
    paths.ensure_private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(app_config(env), indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def start_app(app: Path) -> bool:
    """Opens Pinlet.app in the background.

    The app starts ``pinlet serve`` as its own child, so macOS asks for Screen Recording
    and Accessibility permission for Pinlet rather than for the terminal.
    """
    try:
        write_app_config()
        return (
            subprocess.run(
                ["open", "-g", "-a", str(app)], capture_output=True, timeout=10
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def start_daemon(env: dict[str, str] | None = None) -> subprocess.Popen | None:
    """Starts the shared Pinlet.

    Goes through Pinlet.app on macOS when it is installed, else runs ``pinlet serve``
    detached from this client, so it outlives the client.
    """
    app = find_app(env)
    if app is not None and start_app(app):
        return None
    log = paths.daemon_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    out = open(log, "ab", buffering=0)
    return subprocess.Popen(
        [sys.executable, "-m", "pinlet", "serve"],
        stdin=subprocess.DEVNULL,
        stdout=out,
        stderr=out,
        env=daemon_env(os.environ if env is None else env),
        start_new_session=True,
        close_fds=True,
    )


def connect_or_start(timeout_s: float = CONNECT_TIMEOUT_S) -> socket.socket | None:
    """Connects to the shared Pinlet, starting it if nobody is listening.

    Returns:
        A connected socket, or None if no Pinlet answered within ``timeout_s``.
    """
    path = paths.socket_path()
    sock = _connect(path)
    if sock is not None:
        return sock
    start_daemon()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        sock = _connect(path)
        if sock is not None:
            return sock
        # If ours exited, another connector's start won the race: keep
        # waiting for that one to finish loading.
        time.sleep(0.15)
    return None


def _read_line(sock: socket.socket, timeout_s: float) -> bytes:
    sock.settimeout(timeout_s)
    buf = bytearray()
    while not buf.endswith(b"\n"):
        chunk = sock.recv(1)
        if not chunk:
            break
        buf += chunk
    sock.settimeout(None)
    return bytes(buf)


def handshake(
    sock: socket.socket, argv: Sequence[str], timeout_s: float = CONNECT_TIMEOUT_S
) -> dict[str, object]:
    """Sends this client's hello and returns the shared Pinlet's reply."""
    sock.sendall((json.dumps(hello(argv)) + "\n").encode())
    line = _read_line(sock, timeout_s)
    reply = json.loads(line.decode() or "{}")
    if not reply.get("pinlet_ready"):
        raise ConnectionError(
            f"shared Pinlet refused the connection: {reply.get('error') or line!r}"
        )
    return reply


def pump(sock: socket.socket, stdin_fd: int = 0, stdout_fd: int = 1) -> None:
    """Copy bytes both ways until either side closes."""
    done = threading.Event()

    def up() -> None:
        try:
            while True:
                data = os.read(stdin_fd, 65536)
                if not data:
                    break
                sock.sendall(data)
        except OSError:
            pass
        finally:
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def down() -> None:
        try:
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                view = memoryview(data)
                while view:
                    n = os.write(stdout_fd, view)
                    view = view[n:]
        except OSError:
            pass
        finally:
            done.set()

    threading.Thread(target=up, name="pinlet-up", daemon=True).start()
    threading.Thread(target=down, name="pinlet-down", daemon=True).start()
    done.wait()


def run_in_process() -> None:
    """Runs a private Pinlet MCP server on this process's stdio."""
    from pinlet.server import main as serve_stdio

    serve_stdio()


def main(argv: Sequence[str] | None = None) -> None:
    """Connects this client's stdio to the shared Pinlet, or runs a private one."""
    argv = list(sys.argv if argv is None else argv)
    if not shared_enabled():
        run_in_process()
        return
    sock = connect_or_start()
    if sock is None:
        sys.stderr.write(
            "pinlet: the shared Pinlet did not start (see ~/.pinlet/logs/daemon.log); "
            "running a private one for this client\n"
        )
        run_in_process()
        return
    try:
        handshake(sock, argv)
    except (OSError, ValueError, ConnectionError) as exc:
        sys.stderr.write(f"pinlet: {exc}; running a private one for this client\n")
        sock.close()
        run_in_process()
        return
    pump(sock)
