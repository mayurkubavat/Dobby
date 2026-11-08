"""The connector survives a restart of the shared Pinlet (Pinlet.app updated or relaunched)."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest

from pinlet.hub.connector import RESTARTED, Bridge

pytestmark = pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="needs Unix sockets")


class FakePinlet:
    """Accepts one connection: answers the hello, records every message, answers on request."""

    def __init__(self, path: Path, answer: bool = True) -> None:
        self.path = path
        self.answer = answer
        self.got: list[dict] = []
        self.conn: socket.socket | None = None
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(str(path))
        self.srv.listen(1)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        conn, _ = self.srv.accept()
        self.conn = conn
        f = conn.makefile("rb")
        assert json.loads(f.readline())["pinlet_hello"] == 1
        conn.sendall(b'{"pinlet_ready": 1}\n')
        for line in f:
            msg = json.loads(line)
            self.got.append(msg)
            if self.answer and "id" in msg and "method" in msg:
                conn.sendall(
                    (json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}) + "\n").encode()
                )

    def die(self) -> None:
        if self.conn is not None:
            with contextlib.suppress(OSError):
                self.conn.shutdown(socket.SHUT_RDWR)
            self.conn.close()
        self.srv.close()
        self.path.unlink(missing_ok=True)


def _connect(path: Path):
    def connect():
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(str(path))
            return s
        except OSError:
            s.close()
            return None

    return connect


def _lines(fd: int, out: list, stop: threading.Event) -> None:
    f = os.fdopen(fd, "rb", buffering=0)
    buf = b""
    while not stop.is_set():
        data = f.read(65536)
        if not data:
            return
        buf += data
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            out.append(json.loads(line))


def _until(cond, secs=5.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_bridge_reconnects_replays_initialize_and_fails_running_calls():
    short = Path(tempfile.mkdtemp(prefix="pbr", dir="/tmp"))
    path = short / "p.sock"
    try:
        first = FakePinlet(path)
        cin_r, cin_w = os.pipe()
        cout_r, cout_w = os.pipe()
        seen: list[dict] = []
        stop = threading.Event()
        threading.Thread(target=_lines, args=(cout_r, seen, stop), daemon=True).start()
        bridge = Bridge(
            ["pinlet"], stdin_fd=cin_r, stdout_fd=cout_w, connect=_connect(path), reconnect_s=10
        )
        runner = threading.Thread(target=bridge.run, args=(_connect(path)(),), daemon=True)
        runner.start()

        def send(msg):
            os.write(cin_w, (json.dumps(msg) + "\n").encode())

        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "t"}},
            }
        )
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert _until(lambda: any(m.get("id") == 1 for m in seen))
        first.answer = False  # the next call is still running when Pinlet goes away
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "click"}})
        assert _until(lambda: any(m.get("id") == 2 for m in first.got))
        first.die()
        second = FakePinlet(path)

        # the running call is answered with an error, never sent to the new Pinlet
        assert _until(lambda: any(m.get("id") == 2 and "error" in m for m in seen))
        err = next(m for m in seen if m.get("id") == 2)
        assert err["error"]["message"] == RESTARTED
        # the new Pinlet got the client's initialize again, then initialized
        assert _until(lambda: len(second.got) >= 2)
        assert second.got[0]["method"] == "initialize" and second.got[0]["id"] != 1
        assert second.got[1]["method"] == "notifications/initialized"
        assert not any(m.get("id") == 2 for m in second.got)
        # the client hears its tools may have changed, and never sees the replayed answer
        assert _until(
            lambda: any(m.get("method") == "notifications/tools/list_changed" for m in seen)
        )
        assert not any(str(m.get("id", "")).startswith("pinlet-reconnect") for m in seen)
        # and the next call works
        send({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        assert _until(lambda: any(m.get("id") == 3 and "result" in m for m in seen))
        assert bridge.reconnects == 1
        os.close(cin_w)
        runner.join(8)
        assert not runner.is_alive()  # the client left: the connector ends
        stop.set()
        second.die()
    finally:
        shutil.rmtree(short, ignore_errors=True)


def test_hello_carries_the_clients_own_name(monkeypatch):
    monkeypatch.delenv("PINLET_AGENT_NAME", raising=False)
    short = Path(tempfile.mkdtemp(prefix="pbn", dir="/tmp"))
    path = short / "p.sock"
    hellos: list[dict] = []
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        f = conn.makefile("rb")
        hellos.append(json.loads(f.readline()))
        conn.sendall(b'{"pinlet_ready": 1}\n')
        first = json.loads(f.readline())
        hellos.append(first)
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    cin_r, cin_w = os.pipe()
    _cout_r, cout_w = os.pipe()
    os.write(
        cin_w,
        b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"claude-ai"}}}\n',
    )
    bridge = Bridge(
        ["pinlet"], stdin_fd=cin_r, stdout_fd=cout_w, connect=_connect(path), reconnect_s=0.5
    )
    t = threading.Thread(target=bridge.run, args=(_connect(path)(),), daemon=True)
    t.start()
    try:
        assert _until(lambda: len(hellos) == 2)
        assert hellos[0]["name"] == "claude-ai"  # not "disclaimer", Claude Desktop's launcher
        assert hellos[1]["method"] == "initialize" and hellos[1]["id"] == 1
    finally:
        os.close(cin_w)
        srv.close()
        shutil.rmtree(short, ignore_errors=True)


def test_a_quitting_pinlet_is_waited_out_not_replaced_by_a_private_one():
    """Pinlet.app restarting its engine: the first socket drops the hello, the next one works."""
    short = Path(tempfile.mkdtemp(prefix="pbq", dir="/tmp"))
    path = short / "p.sock"
    try:
        dying = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        dying.bind(str(path))
        dying.listen(1)
        first = _connect(path)()

        def drop_then_replace():
            conn, _ = dying.accept()
            conn.close()  # quitting: accepts, then goes away
            dying.close()
            path.unlink()
            time.sleep(0.3)
            holder.append(FakePinlet(path))

        holder: list = []
        threading.Thread(target=drop_then_replace, daemon=True).start()
        cin_r, cin_w = os.pipe()
        _cout_r, cout_w = os.pipe()
        os.write(cin_w, b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
        bridge = Bridge(
            ["pinlet"], stdin_fd=cin_r, stdout_fd=cout_w, connect=_connect(path), reconnect_s=10
        )
        t = threading.Thread(target=bridge.run, args=(first,), daemon=True)
        t.start()
        assert _until(lambda: holder and holder[0].got)
        assert holder[0].got[0]["method"] == "initialize" and holder[0].got[0]["id"] == 1
        assert bridge.reconnects == 0  # a first connection, not announced as a restart
        os.close(cin_w)
        holder[0].die()
    finally:
        shutil.rmtree(short, ignore_errors=True)
