"""``dobby``: connect an AI client's stdio to the shared Dobby.

Standard library only, and no Dobby engine imports: this runs once per AI
client, so it must start in milliseconds.

Wire format on the socket: one hello line each way, then MCP JSON-RPC
messages exactly as on stdio (one JSON object per line)::

    connector -> {"dobby_hello": 1, "pid": 4242, "parent_pid": 4100, "mode": "turbo",
                  "name": "Antigravity", "cwd": "/Users/me/proj"}
    server    -> {"dobby_ready": 1, "server_pid": 3900, "session": 7}
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
    env = os.environ if env is None else env
    if not hasattr(socket, "AF_UNIX"):
        return False
    return env.get("DOBBY_SHARED", "").strip().lower() not in _FALSE


def _mode(argv: Sequence[str]) -> str:
    from dobby.mode import security_mode  # stdlib-only module

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
    return {
        "dobby_hello": HELLO_VERSION,
        "pid": os.getpid(),
        "parent_pid": os.getppid(),
        "mode": _mode(argv),
        "name": os.environ.get("DOBBY_AGENT_NAME") or _parent_name() or "AI client",
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


# Settings that belong to one client, not to the shared Dobby: each client
# sends them in its hello instead.
PER_CLIENT_ENV = ("DOBBY_AGENT_NAME", "DOBBY_MODE", "DOBBY_SECURE", "DOBBY_OVERLAY_LABEL")


def daemon_env(env: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in env.items() if k not in PER_CLIENT_ENV}


# --------------------------------------------------------------- Dobby.app
APP_ID = "dev.dobby.app"
# What the Dobby app needs to start ``dobby serve`` itself: this Python, and
# the settings that shape the engine (never secrets the client happens to have).
_APP_ENV_KEYS = ("PATH", "PYTHONPATH", "VIRTUAL_ENV", "OLLAMA_HOST", "HF_HOME", "LANG")


def find_app(env: dict[str, str] | None = None, platform: str = sys.platform) -> Path | None:
    """Dobby.app on macOS: ``DOBBY_APP``, ~/Applications, /Applications, or the checkout's own build.

    ``DOBBY_APP=0`` never uses it.
    """
    env = os.environ if env is None else env
    if platform != "darwin":
        return None
    choice = env.get("DOBBY_APP", "").strip()
    if choice.lower() in _FALSE:
        return None
    root = Path(__file__).resolve().parents[2]
    candidates = (
        [Path(os.path.expanduser(choice))]
        if choice
        else [
            Path.home() / "Applications" / "Dobby.app",
            Path("/Applications/Dobby.app"),
            root / "apps" / "macos" / "build" / "Dobby.app",
        ]
    )
    return next((c for c in candidates if (c / "Contents" / "MacOS").is_dir()), None)


def app_config(env: dict[str, str] | None = None) -> dict[str, object]:
    env = os.environ if env is None else env
    keep = {
        k: v
        for k, v in daemon_env(dict(env)).items()
        if k.startswith("DOBBY_") or k in _APP_ENV_KEYS
    }
    keep.pop("DOBBY_APP", None)
    return {
        "python": sys.executable,
        "cwd": str(Path(__file__).resolve().parents[2]),
        "env": keep,
        "written_by": os.getpid(),
    }


def write_app_config(env: dict[str, str] | None = None) -> Path:
    path = paths.app_config_path()
    paths.ensure_private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(app_config(env), indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def start_app(app: Path) -> bool:
    """Opens Dobby.app in the background.

    The app starts ``dobby serve`` as its own child, so macOS asks for Screen Recording
    and Accessibility permission for Dobby rather than for the terminal.
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
    """Starts the shared Dobby.

    Goes through Dobby.app on macOS when it is installed, else runs ``dobby serve``
    detached from this client, so it outlives the client.
    """
    app = find_app(env)
    if app is not None and start_app(app):
        return None
    log = paths.daemon_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    out = open(log, "ab", buffering=0)
    return subprocess.Popen(
        [sys.executable, "-m", "dobby", "serve"],
        stdin=subprocess.DEVNULL,
        stdout=out,
        stderr=out,
        env=daemon_env(os.environ if env is None else env),
        start_new_session=True,
        close_fds=True,
    )


def connect_or_start(timeout_s: float = CONNECT_TIMEOUT_S) -> socket.socket | None:
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
    sock.sendall((json.dumps(hello(argv)) + "\n").encode())
    line = _read_line(sock, timeout_s)
    reply = json.loads(line.decode() or "{}")
    if not reply.get("dobby_ready"):
        raise ConnectionError(
            f"shared Dobby refused the connection: {reply.get('error') or line!r}"
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

    threading.Thread(target=up, name="dobby-up", daemon=True).start()
    threading.Thread(target=down, name="dobby-down", daemon=True).start()
    done.wait()


def run_in_process() -> None:
    from dobby.server import main as serve_stdio

    serve_stdio()


def main(argv: Sequence[str] | None = None) -> None:
    argv = list(sys.argv if argv is None else argv)
    if not shared_enabled():
        run_in_process()
        return
    sock = connect_or_start()
    if sock is None:
        sys.stderr.write(
            "dobby: the shared Dobby did not start (see ~/.dobby/logs/daemon.log); "
            "running a private one for this client\n"
        )
        run_in_process()
        return
    try:
        handshake(sock, argv)
    except (OSError, ValueError, ConnectionError) as exc:
        sys.stderr.write(f"dobby: {exc}; running a private one for this client\n")
        sock.close()
        run_in_process()
        return
    pump(sock)
