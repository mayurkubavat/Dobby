"""The ``wait_for_wake`` MCP tool and the helpers' "an AI waits" markers.

Fakes only: no overlay helper, no desktop input.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading

import pytest

from dobby.overlay import NullOverlay, OverlayHAL
from dobby.overlay.barlock import mark_waiting, others_waiting, waiting_dir


# ------------------------------------------------------- waiting markers
def test_waiting_dir_sits_next_to_the_bar_lock(tmp_path):
    assert waiting_dir(str(tmp_path / "bar.lock")) == str(tmp_path / "waiting")


def test_markers_show_other_helpers_waiting(tmp_path):
    d = str(tmp_path / "waiting")
    assert not others_waiting(d, os.getpid())  # no directory yet
    mark_waiting(d, os.getpid(), True)
    assert not others_waiting(d, os.getpid())  # our own marker does not count
    mark_waiting(d, os.getppid(), True)  # a live process
    assert others_waiting(d, os.getpid())
    mark_waiting(d, os.getppid(), False)
    mark_waiting(d, os.getppid(), False)  # removing twice is harmless
    assert not others_waiting(d, os.getpid())


def test_stale_markers_are_dropped(tmp_path):
    d = str(tmp_path / "waiting")
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    mark_waiting(d, proc.pid, True)
    assert not others_waiting(d, os.getpid())
    assert not os.path.exists(os.path.join(d, str(proc.pid)))


# ------------------------------------------------------------- MCP tool
class FakeOverlay(NullOverlay):
    """Records calls; returns ``result`` or blocks until cancelled."""

    def __init__(self, result=None, block=False):
        self.calls = []
        self.result = result or {
            "woken": True,
            "stopped": False,
            "timed_out": False,
            "waited_s": 1.0,
        }
        self.block = block
        self.cancel = None

    def wait_for_wake(self, timeout_s=600.0, reason="", cancel=None):
        self.calls.append((timeout_s, reason))
        self.cancel = cancel
        if self.block:
            assert cancel is not None and cancel.wait(10)
            return {
                "woken": False,
                "stopped": False,
                "timed_out": False,
                "waited_s": 0.1,
                "cancelled": True,
            }
        return dict(self.result)


class FakeControl:
    def __init__(self):
        self.released = 0

    def release(self):
        self.released += 1
        return True


@pytest.fixture
def server(monkeypatch):
    srv = pytest.importorskip("dobby.server")
    ctl = FakeControl()
    monkeypatch.setattr(srv, "_control_or_none", lambda: ctl)

    def install(overlay):
        monkeypatch.setattr(
            srv, "hal", OverlayHAL(object(), overlay) if overlay is not None else object()
        )

    srv.fake_ctl = ctl
    return srv, install


def test_tool_is_registered_in_core_and_outside_the_lease(server):
    srv, _ = server
    names = {t.name for t in srv.mcp_server._tool_manager.list_tools()}
    assert "wait_for_wake" in names
    assert srv._TOOL_DOMAINS["wait_for_wake"][0] == "core"
    assert "wait_for_wake" in srv._OUTSIDE_LEASE_TOOLS
    doc = srv.wait_for_wake.__doc__
    assert "finished the current task" in doc and "click" in doc


def test_tool_waits_on_the_overlay_and_releases_control(server):
    srv, install = server
    ov = FakeOverlay()
    install(ov)
    r = asyncio.run(srv.wait_for_wake(timeout_s=30, reason="report done"))
    assert r["ok"] and r["woken"] and "next instruction" in r["note"]
    assert ov.calls == [(30.0, "report done")]
    assert srv.fake_ctl.released == 1  # waiting never holds the desktop


def test_tool_caps_the_timeout(server):
    srv, install = server
    ov = FakeOverlay()
    install(ov)
    asyncio.run(srv.wait_for_wake(timeout_s=99999))
    asyncio.run(srv.wait_for_wake(timeout_s=-5))
    assert [c[0] for c in ov.calls] == [srv.WAIT_FOR_WAKE_CAP_S, 0.0]


@pytest.mark.parametrize(
    "result,word",
    [
        ({"woken": False, "stopped": True, "timed_out": False, "waited_s": 2.0}, "Stop"),
        ({"woken": False, "stopped": False, "timed_out": True, "waited_s": 600.0}, "again"),
        (
            {
                "woken": False,
                "stopped": False,
                "timed_out": False,
                "waited_s": 0.0,
                "unavailable": True,
            },
            "End your turn",
        ),
    ],
)
def test_tool_explains_each_outcome(server, result, word):
    srv, install = server
    install(FakeOverlay(result))
    r = asyncio.run(srv.wait_for_wake(timeout_s=5))
    assert word in r["note"]


def test_tool_without_overlay_is_unavailable(server):
    srv, install = server
    install(None)
    r = asyncio.run(srv.wait_for_wake(timeout_s=600))
    assert r["unavailable"] is True and r["woken"] is False
    assert srv.fake_ctl.released == 0


def test_tool_cancel_takes_the_wait_down(server):
    srv, install = server
    ov = FakeOverlay(block=True)
    install(ov)

    async def run():
        task = asyncio.ensure_future(srv.wait_for_wake(timeout_s=600))
        for _ in range(100):
            await asyncio.sleep(0.02)
            if ov.cancel is not None:
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert isinstance(ov.cancel, threading.Event) and ov.cancel.is_set()
