"""Two AI clients through the real shared Pinlet, with only the desktop replaced.

Each client is a real ``pinlet`` connector process (so the hub sees two pids),
talking JSON-RPC over stdio like Claude Code or Cursor would. The shared Pinlet
is a real ``pinlet serve`` with ``DESKTOP_HAL=replay`` (no mouse, keyboard or
screen) and the shared lease turned on for the replay desktop. This is the
"replace only the provider transport" idea from T3 Code's integration tests.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = [
    pytest.mark.replay,
    pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="needs Unix sockets"),
]


class Client:
    """One AI client: a ``pinlet`` connector process driven over stdio."""

    def __init__(self, env: dict[str, str], name: str, err: Path) -> None:
        self.name = name
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "pinlet"],
            cwd=ROOT,
            env={**env, "PINLET_AGENT_NAME": name},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=err.open("w"),
        )
        self._id = 0
        init = self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": name, "version": "1"},
            },
        )
        assert "result" in init, init
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, msg: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()

    def request(self, method: str, params: dict) -> dict:
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        assert self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline()
            assert line, f"{self.name}'s connector closed"
            reply = json.loads(line)
            if reply.get("id") == self._id:
                return reply

    def call(self, tool: str, **arguments) -> dict:
        reply = self.request("tools/call", {"name": tool, "arguments": arguments})
        assert "result" in reply, reply
        text = reply["result"]["content"][0]["text"]
        try:
            return json.loads(text)
        except ValueError:
            return {"text": text, "is_error": reply["result"].get("isError")}

    def close(self) -> None:
        self.proc.kill()
        self.proc.wait(timeout=10)


def _control(sock_path: Path, op: str, **fields) -> dict:
    """One request on the app's control connection."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10)
    s.connect(str(sock_path))
    try:
        f = s.makefile("rwb")
        f.write(b'{"pinlet_control": 1}\n')
        f.flush()
        assert json.loads(f.readline())["pinlet_ready"] == 1
        f.write((json.dumps({"id": 1, "op": op, **fields}) + "\n").encode())
        f.flush()
        return json.loads(f.readline())
    finally:
        s.close()


@pytest.fixture
def hub():
    """A real shared Pinlet on a replay desktop, with a short socket path and its own HOME."""
    home = Path(tempfile.mkdtemp(prefix="pnl", dir="/tmp" if os.path.isdir("/tmp") else None))
    sock_path = home / "p.sock"
    env = {
        **os.environ,
        "DESKTOP_HAL": "replay",
        "PINLET_REPLAY_LEASE": "1",
        "PINLET_SOCKET": str(sock_path),
        "PINLET_OVERLAY": "0",
        "PINLET_IDLE_EXIT": "0",
        "PINLET_APP": "0",
        "PINLET_CONTROL_MAX_WAIT": "1",  # a busy desktop answers in 1 s, not 45
        "PINLET_JOURNAL": str(home / "activity.ndjson"),
        "HOME": str(home),
    }
    # Shared control and the overlay switch themselves off under pytest; this
    # hub is meant to behave like a real one.
    for key in ("PINLET_CONTROL_DIR", "PYTEST_CURRENT_TEST", "PINLET_REPLAY"):
        env.pop(key, None)
    log = home / "daemon.err"
    daemon = subprocess.Popen(
        [sys.executable, "-m", "pinlet", "serve"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=log.open("w"),
    )
    for _ in range(600):
        if sock_path.exists() or daemon.poll() is not None:
            break
        time.sleep(0.1)
    if not sock_path.exists():
        daemon.kill()
        pytest.skip(f"the shared Pinlet did not start here: {log.read_text()[-400:]}")
    clients: list[Client] = []

    def connect(name: str) -> Client:
        c = Client(env, name, home / f"{name}.err")
        clients.append(c)
        return c

    try:
        yield connect, sock_path, home
    finally:
        for c in clients:
            c.close()
        daemon.terminate()
        try:
            daemon.wait(timeout=10)
        except subprocess.TimeoutExpired:
            daemon.kill()
        shutil.rmtree(home, ignore_errors=True)


def test_two_agents_take_turns_and_hand_over(hub):
    connect, sock_path, _home = hub
    tibble = connect("claude-code")
    pip = connect("cursor-vscode")

    # They see each other, and each knows which one it is.
    seen = tibble.call("list_agents")
    names = {a["first_name"]: a["is_you"] for a in seen["agents"]}
    assert names == {"Tibble": True, "Pip": False}

    # Tibble takes the desktop; Pip may not click meanwhile.
    assert tibble.call("request_control")["you_hold_control"] is True
    assert tibble.call("click", x=10, y=10)["success"] is True
    busy = pip.call("click", x=1, y=1)
    assert busy.get("handoff") == "control_busy", busy
    assert "Tibble" in busy["detail"]

    # A message reaches Pip with its next tool result, marked as from an AI.
    sent = tibble.call("send_to_agent", to="Pip", text="the form is in the left window")
    assert sent["sent"] is True
    status = pip.call("control_status")
    assert status["agent_messages"] == [
        {
            "from": "Tibble · claude-code",
            "text": "the form is in the left window",
            "kind": "message",
        }
    ]
    assert "person_says" not in status

    # Tibble hands over with a note; the desktop is kept for Pip.
    handed = tibble.call("hand_control_to", to="Pip", note="form is open")
    assert handed["handed_over"] is True
    after = tibble.call("click", x=2, y=2)
    assert after.get("handoff") == "control_busy", after
    got = pip.call("request_control", max_wait_s=5)
    assert got["you_hold_control"] is True
    assert got["agent_messages"][0]["kind"] == "handoff"
    assert pip.call("click", x=5, y=5)["success"] is True

    # The app sees both: the journal says who did what, and the message log.
    feed = _control(sock_path, "activity")
    assert feed["ok"] is True
    text = " ".join(line["text"] for line in feed["lines"])
    assert "Tibble" in text and "Pip" in text
    assert [m["kind"] for m in feed["messages"]] == ["message", "handoff"]
    assert feed["messages"][0]["from"] == "Tibble · claude-code"


def test_a_task_goes_from_one_agent_to_another_and_back(hub):
    connect, sock_path, _home = hub
    tibble = connect("claude-code")
    pip = connect("cursor-vscode")
    assert tibble.call("list_agents")["agents"]

    posted = tibble.call("post_task", title="Rename the March files", for_agent="Pip")
    assert posted["posted"] is True
    task_id = posted["task"]["id"]

    # Pip hears about it with its next call, claims it and finishes it.
    news = pip.call("list_tasks")
    assert news["agent_messages"][0]["kind"] == "task"
    assert news["tasks"][0]["status"] == "open"
    assert tibble.call("claim_task", task_id=task_id)["claimed"] is False  # it is Pip's
    assert pip.call("claim_task", task_id=task_id)["claimed"] is True
    board = _control(sock_path, "tasks")["tasks"]
    assert board[0]["line"].endswith("Pip is on it")
    assert pip.call("finish_task", task_id=task_id, result="12 files renamed")["finished"] is True

    # Tibble hears how it went.
    back = tibble.call("list_tasks", status="done")
    assert back["tasks"][0]["result"] == "12 files renamed"
    assert "12 files renamed" in back["agent_messages"][0]["text"]
