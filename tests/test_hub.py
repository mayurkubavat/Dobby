"""One shared Dobby per computer: connector, socket, sessions."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from dobby.hub import connector, paths, sessions
from dobby.mode import session_mode, security_mode


def test_socket_paths():
    assert paths.socket_path({"DOBBY_SOCKET": "/tmp/x/d.sock"}) == Path("/tmp/x/d.sock")
    mac = paths.socket_path({}, platform="darwin")
    assert str(mac).endswith("Library/Application Support/Dobby/dobby.sock")
    assert paths.socket_path({"XDG_RUNTIME_DIR": "/"}, platform="linux") == Path("/dobby/dobby.sock")


def test_per_client_settings_stay_out_of_the_shared_dobby():
    env = connector.daemon_env({"DOBBY_AGENT_NAME": "Cursor", "DOBBY_MODE": "secure", "DOBBY_OVERLAY_STYLE": "classic"})
    assert env == {"DOBBY_OVERLAY_STYLE": "classic"}


def test_hello_carries_mode_and_identity(monkeypatch):
    monkeypatch.setenv("DOBBY_AGENT_NAME", "Antigravity")
    h = connector.hello(["dobby", "--secure"])
    assert h["dobby_hello"] == 1 and h["mode"] == "secure" and h["name"] == "Antigravity"
    assert h["pid"] == os.getpid()


def test_shared_can_be_turned_off():
    assert not connector.shared_enabled({"DOBBY_SHARED": "0"})


def test_session_mode_is_per_context():
    with session_mode("secure"):
        assert security_mode() == "secure"
    assert security_mode(env={}, argv=["dobby"]) == "turbo"


def test_sessions_registry():
    info = sessions.open_session("Claude Code", 1234, "turbo")
    token = sessions.CURRENT.set(info)
    try:
        sessions.note_call("click")
        sessions.rename("claude-code")
    finally:
        sessions.CURRENT.reset(token)
    snap = [s for s in sessions.snapshot() if s["id"] == info.id][0]
    assert snap["name"] == "claude-code" and snap["calls"] == 1 and snap["turbo"] is True
    sessions.close_session(info)
    assert not [s for s in sessions.snapshot() if s["id"] == info.id]


# ------------------------------------------------------------ end to end
def _rpc(sock, buf, msg):
    sock.sendall((json.dumps(msg) + "\n").encode())
    if "id" not in msg:
        return None
    while True:
        while b"\n" not in buf[0]:
            chunk = sock.recv(65536)
            assert chunk, "server closed"
            buf[0] += chunk
        line, buf[0] = buf[0].split(b"\n", 1)
        reply = json.loads(line)
        if reply.get("id") == msg["id"]:
            return reply


def _client(path, name, mode):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    for _ in range(300):
        try:
            sock.connect(str(path))
            break
        except OSError:
            time.sleep(0.1)
    else:
        pytest.fail("shared Dobby did not listen")
    sock.sendall((json.dumps({"dobby_hello": 1, "pid": os.getpid(), "mode": mode, "name": name}) + "\n").encode())
    buf = [b""]
    while b"\n" not in buf[0]:
        buf[0] += sock.recv(4096)
    ready, buf[0] = buf[0].split(b"\n", 1)
    assert json.loads(ready)["dobby_ready"] == 1
    init = _rpc(sock, buf, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": name, "version": "1"}}})
    assert "result" in init, init
    _rpc(sock, buf, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    return sock, buf


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="needs Unix sockets")
def test_two_clients_share_one_dobby(tmp_path):
    sock_path = tmp_path / "run" / "dobby.sock"
    env = {**os.environ, "DOBBY_SOCKET": str(sock_path), "DOBBY_OVERLAY": "0", "DOBBY_IDLE_EXIT": "0",
           "HOME": str(tmp_path)}
    root = Path(__file__).resolve().parents[1]
    daemon = subprocess.Popen([sys.executable, "-m", "dobby", "serve"], cwd=root, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        a, abuf = _client(sock_path, "Claude Code", "turbo")
        b, bbuf = _client(sock_path, "Antigravity", "secure")
        assert oct(sock_path.stat().st_mode & 0o777) == "0o600"
        call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "control_status", "arguments": {}}}
        ra = json.loads(_rpc(a, abuf, call)["result"]["content"][0]["text"])
        rb = json.loads(_rpc(b, bbuf, call)["result"]["content"][0]["text"])
        assert ra["mode"] == "turbo" and rb["mode"] == "secure"
        assert ra["session"] != rb["session"]
        assert sorted(c["name"] for c in ra["clients"]) == ["Antigravity", "Claude Code"]
        # a second shared Dobby refuses to start while this one runs
        second = subprocess.run([sys.executable, "-m", "dobby", "serve"], cwd=root, env=env,
                                capture_output=True, text=True, timeout=60)
        assert "already running" in second.stderr
        a.close()
        b.close()
    finally:
        daemon.terminate()
        daemon.wait(timeout=10)
