"""``pinlet``: connect an AI client's stdio to the shared Pinlet.

Standard library only, and no Pinlet engine imports: this runs once per AI
client, so it must start in milliseconds.

Wire format on the socket: one hello line each way, then MCP JSON-RPC
messages exactly as on stdio (one JSON object per line)::

    connector -> {"pinlet_hello": 1, "pid": 4242, "parent_pid": 4100, "mode": "turbo",
                  "name": "Antigravity", "cwd": "/Users/me/proj",
                  "agent_key": "9f2c...", "first_name": "Pip"}
    server    -> {"pinlet_ready": 1, "server_pid": 3900, "session": 7, "first_name": "Pip"}

``agent_key`` is made once per connector process, so it is the same on every
reconnect; ``first_name`` is the name the last Pinlet gave this client (only
sent on a reconnect). Together they keep the client's name and lease place
across a restart of the shared Pinlet.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

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
        **(
            {"tool_timeout_s": os.environ["PINLET_TOOL_TIMEOUT_S"]}
            if os.environ.get("PINLET_TOOL_TIMEOUT_S", "").strip()
            else {}
        ),
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
PER_CLIENT_ENV = (
    "PINLET_AGENT_NAME",
    "PINLET_MODE",
    "PINLET_SECURE",
    "PINLET_OVERLAY_LABEL",
    "PINLET_TOOL_TIMEOUT_S",
)


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


def connect_or_start(
    timeout_s: float = CONNECT_TIMEOUT_S, stop: Any = None
) -> socket.socket | None:
    """Connects to the shared Pinlet, starting it if nobody is listening.

    ``stop()`` returning True ends the wait early (the client went away).

    Returns:
        A connected socket, or None if no Pinlet answered within ``timeout_s``.
    """
    path = paths.socket_path()
    sock = _connect(path)
    if sock is not None:
        return sock
    start_daemon()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and not (stop is not None and stop()):
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
    sock: socket.socket,
    argv: Sequence[str],
    timeout_s: float = CONNECT_TIMEOUT_S,
    name: str | None = None,
    agent_key: str | None = None,
    first_name: str | None = None,
) -> dict[str, object]:
    """Sends this client's hello and returns the shared Pinlet's reply.

    ``name`` (the client's MCP ``clientInfo.name``) beats the parent process
    name, unless ``PINLET_AGENT_NAME`` is set. ``agent_key`` and
    ``first_name`` identify a reconnecting client (see the module docstring).
    """
    greeting = hello(argv)
    if name and not os.environ.get("PINLET_AGENT_NAME"):
        greeting["name"] = name
    if agent_key:
        greeting["agent_key"] = agent_key
    if first_name:
        greeting["first_name"] = first_name
    sock.sendall((json.dumps(greeting) + "\n").encode())
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


RESTARTED = (
    "Pinlet restarted while this call ran (the Pinlet app was updated, relaunched or quit), "
    "so it may or may not have acted. Look at the screen to see whether it happened before "
    "you call the tool again."
)
UNREACHABLE = (
    "Pinlet is not running and did not come back in time. Open Pinlet.app (or run "
    "`pinlet doctor`), then call the tool again."
)


def _message_id(line: bytes) -> tuple[str | None, str | None]:
    """(method, json-encoded id) of one JSON-RPC line, without trusting it to parse."""
    try:
        msg = json.loads(line)
    except ValueError:
        return None, None
    if not isinstance(msg, dict):
        return None, None
    rid = json.dumps(msg["id"]) if "id" in msg and msg["id"] is not None else None
    method = msg.get("method")
    return (method if isinstance(method, str) else None), rid


def _shut(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


class Bridge:
    """Pipes MCP lines between the client's stdio and the shared Pinlet, across restarts.

    When the shared Pinlet goes away (Pinlet.app updated, relaunched or quit)
    while the client is still running, the bridge starts or waits for a new
    one, replays the client's ``initialize`` to it, and tells the client its
    tool list changed. A call that was running when the old one went away is
    answered with an error (:data:`RESTARTED`), never sent again: it may have
    clicked already. Calls made while reconnecting wait for the new Pinlet.
    """

    def __init__(
        self,
        argv: Sequence[str],
        stdin_fd: int = 0,
        stdout_fd: int = 1,
        connect: Any = None,
        reconnect_s: float = CONNECT_TIMEOUT_S,
    ) -> None:
        self.argv = list(argv)
        self.stdin_fd = stdin_fd
        self.stdout_fd = stdout_fd
        self.connect = connect or (lambda: connect_or_start(stop=self._client_gone))
        self.reconnect_s = reconnect_s
        self._cond = threading.Condition()
        self._sock: socket.socket | None = None
        self._down: Any = None
        self._gone = False  # gave up reconnecting for now
        self._stdin_closed = False
        self._pending: dict[str, None] = {}  # request ids sent to this Pinlet, not answered
        self._init: bytes | None = None
        self._initialized: bytes | None = None
        self._out = threading.Lock()
        self.reconnects = 0
        self._client_name: str | None = None
        self._first: bytes | None = None
        self.agent_key = uuid.uuid4().hex  # same for every Pinlet this client talks to
        self.first_name: str | None = None  # what the last Pinlet called this client

    def _client_gone(self) -> bool:
        with self._cond:
            return self._stdin_closed

    def _read_first_line(self, limit: int = 1 << 20) -> None:
        """The client speaks first in MCP (``initialize``): read it to learn the client's name.

        Without this the menu shows the launcher (``disclaimer`` for Claude
        Desktop, ``node``) until the agent's first tool call.
        """
        buf = bytearray()
        try:
            while not buf.endswith(b"\n") and len(buf) < limit:
                ch = os.read(self.stdin_fd, 1)
                if not ch:
                    break
                buf += ch
        except OSError:
            pass
        self._first = bytes(buf) or None
        try:
            msg = json.loads(buf) if buf else None
        except ValueError:
            return
        if isinstance(msg, dict) and msg.get("method") == "initialize":
            info = (msg.get("params") or {}).get("clientInfo") or {}
            name = str(info.get("name") or "").strip()
            self._client_name = name[:40] or None

    # ----------------------------------------------------------- the client side
    def _to_client(self, data: bytes) -> None:
        with self._out:
            view = memoryview(data)
            while view:
                n = os.write(self.stdout_fd, view)
                view = view[n:]

    def _error_to_client(self, rid: str, message: str) -> None:
        reply = {
            "jsonrpc": "2.0",
            "id": json.loads(rid),
            "error": {"code": -32000, "message": message},
        }
        self._to_client((json.dumps(reply) + "\n").encode())

    def _wait_for_sock(self) -> socket.socket | None:
        deadline = time.monotonic() + self.reconnect_s
        with self._cond:
            while self._sock is None and not self._gone:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self._cond.wait(min(left, 0.5))
            return self._sock

    def _up(self) -> None:
        reader = os.fdopen(os.dup(self.stdin_fd), "rb", buffering=0)
        buf = b""
        try:
            while True:
                data = reader.read(65536)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._send_up(line + b"\n")
        except OSError:
            pass
        finally:
            with self._cond:
                self._stdin_closed = True
                sock = self._sock
                self._cond.notify_all()
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                # The client is gone: do not linger if Pinlet never closes its side.
                closer = threading.Timer(5.0, lambda: _shut(sock))
                closer.daemon = True
                closer.start()

    def _send_up(self, line: bytes) -> None:
        if not line.strip():
            return
        method, rid = _message_id(line)
        if method == "initialize":
            self._init = line
        elif method == "notifications/initialized":
            self._initialized = line
        while True:
            sock = self._wait_for_sock()
            if sock is None:
                if rid is not None and method is not None:
                    self._error_to_client(rid, UNREACHABLE)
                return
            with self._cond:
                if rid is not None and method is not None:
                    self._pending[rid] = None
            try:
                sock.sendall(line)
                return
            except OSError:
                with self._cond:
                    self._pending.pop(rid, None) if rid else None
                    if self._sock is sock:
                        self._sock = None  # the reader thread reconnects
                # loop: wait for the new Pinlet and send it there (it never reached the old one)

    # ------------------------------------------------------------ the Pinlet side
    def _attach(self, sock: socket.socket) -> bool:
        """Hello, then the client's initialize again when this is a reconnect."""
        try:
            reply = handshake(
                sock,
                self.argv,
                name=self._client_name,
                agent_key=self.agent_key,
                first_name=self.first_name,
            )
            got = reply.get("first_name")
            if isinstance(got, str) and got:
                self.first_name = got
            down = sock.makefile("rb")
            if self._init is not None:
                replay_id = f"pinlet-reconnect-{self.reconnects}"
                msg = json.loads(self._init)
                msg["id"] = replay_id
                sock.sendall((json.dumps(msg) + "\n").encode())
                while True:
                    line = down.readline()
                    if not line:
                        return False
                    if _message_id(line)[1] == json.dumps(replay_id):
                        break
                if self._initialized is not None:
                    sock.sendall(self._initialized)
        except (OSError, ValueError, ConnectionError):
            return False
        with self._cond:
            self._sock, self._down, self._gone = sock, down, False
            self._cond.notify_all()
        return True

    def _drop(self) -> None:
        """The Pinlet went away: fail the calls it was running."""
        with self._cond:
            sock, self._sock = self._sock, None
            failed, self._pending = list(self._pending), {}
        for rid in failed:
            self._error_to_client(rid, RESTARTED)
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _reconnect(self, announce: bool = True) -> bool:
        deadline = time.monotonic() + self.reconnect_s
        with self._cond:
            self._gone = False  # calls made now wait for this attempt
        while time.monotonic() < deadline:
            with self._cond:
                if self._stdin_closed:
                    return False
            sock = self.connect()
            if sock is not None and self._attach(sock):
                if announce:
                    self.reconnects += 1
                    self._to_client(
                        b'{"jsonrpc":"2.0","method":"notifications/tools/list_changed"}\n'
                    )
                return True
            if sock is not None:
                sock.close()
            time.sleep(0.5)
        with self._cond:
            self._gone = True  # waiting calls get UNREACHABLE; the next call tries again
            self._cond.notify_all()
        return False

    def run(self, sock: socket.socket) -> None:
        """Serve until the client closes stdin. ``sock`` is connected but not yet greeted."""
        self._read_first_line()
        # A Pinlet that is quitting (Pinlet.app restarting its engine) accepts and
        # then drops us. Wait for the next one rather than going private: a
        # private Pinlet is a second engine fighting the shared one for the screen.
        if not self._attach(sock):
            sock.close()
            if not self._reconnect(announce=False):
                raise ConnectionError("the shared Pinlet did not answer the hello")
        if self._first is not None:
            self._send_up(self._first)  # before the up thread, so it stays first
        threading.Thread(target=self._up, name="pinlet-up", daemon=True).start()
        while True:
            with self._cond:
                down = self._down
            try:
                line = down.readline()
            except OSError:
                line = b""
            if line:
                method, rid = _message_id(line)
                if rid is not None and method is None:
                    with self._cond:
                        self._pending.pop(rid, None)
                self._to_client(line)
                continue
            self._drop()
            with self._cond:
                if self._stdin_closed:
                    return
            while not self._reconnect():
                with self._cond:
                    if self._stdin_closed:
                        return
                    # Nobody answered: wait for the client's next call before trying again.
                    self._cond.wait(5.0)


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
    bridge = Bridge(argv)
    try:
        bridge.run(sock)
    except ConnectionError as exc:
        sys.stderr.write(f"pinlet: {exc}; running a private one for this client\n")
        sock.close()
        if bridge._first:
            _prepend_stdin(bridge._first)  # the private server must see initialize too
        run_in_process()


def _prepend_stdin(data: bytes) -> None:
    """Make fd 0 yield ``data`` first, then the rest of the real stdin."""
    real = os.dup(0)
    r, w = os.pipe()
    os.write(w, data)

    def copy() -> None:
        try:
            while chunk := os.read(real, 65536):
                os.write(w, chunk)
        except OSError:
            pass
        finally:
            os.close(w)

    threading.Thread(target=copy, name="pinlet-stdin", daemon=True).start()
    os.dup2(r, 0)
    os.close(r)
