"""The person's instructions and wake-ups for a running Pinlet (pinlet.control.inbox)."""

from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

import pytest

from pinlet.control import inbox


def test_notes_queue_and_are_taken_once(tmp_path):
    assert inbox.take(tmp_path, 7) == []
    assert inbox.add(tmp_path, 7, "  use the March invoice ") == 1
    assert inbox.add(tmp_path, 7, "then email Sam") == 2
    assert inbox.read(tmp_path, 7)["notes"][0]["text"] == "use the March invoice"
    assert inbox.take(tmp_path, 7) == ["use the March invoice", "then email Sam"]
    assert inbox.take(tmp_path, 7) == []
    with pytest.raises(ValueError):
        inbox.add(tmp_path, 7, "   ")


def test_queue_is_capped_and_text_trimmed(tmp_path):
    for i in range(inbox.MAX_QUEUED + 3):
        inbox.add(tmp_path, 1, f"note {i}")
    notes = inbox.take(tmp_path, 1)
    assert len(notes) == inbox.MAX_QUEUED and notes[-1] == f"note {inbox.MAX_QUEUED + 2}"
    inbox.add(tmp_path, 1, "x" * (inbox.MAX_CHARS + 50))
    assert len(inbox.take(tmp_path, 1)[0]) == inbox.MAX_CHARS


def test_wake_and_listening(tmp_path):
    assert inbox.read(tmp_path, 3) == {"notes": [], "wake": 0, "listening": False}
    assert inbox.request_wake(tmp_path, 3) == 1
    inbox.set_listening(tmp_path, 3, True)
    assert inbox.read(tmp_path, 3)["listening"] is True
    inbox.clear(tmp_path, 3)
    assert inbox.read(tmp_path, 3)["wake"] == 0


def test_overview_and_menu_show_listening_and_queued(tmp_path):
    from pinlet.control import commands, registry
    from pinlet.overlay import topbar

    pid = os.getpid()
    registry.register(tmp_path, pid=pid, name="Tibble · Claude", agent_id="a")
    inbox.set_listening(tmp_path, pid, True)
    inbox.add(tmp_path, pid, "next do the slides")
    agents = commands.overview(tmp_path)
    assert agents[0]["listening"] is True and agents[0]["queued"] == 1
    view = topbar.topbar_view(agents, {})
    row = view.rows[0]
    assert row.state == "listening" and row.queued == 1
    assert ("Wake", "wake") in row.actions
    assert "1 note waiting" in row.detail
    assert "waiting for you" in view.subtitle


def test_control_tell_and_wake(tmp_path, monkeypatch):
    from pinlet.hub import control

    monkeypatch.setenv("PINLET_CONTROL_DIR", str(tmp_path))
    out = control.handle({"id": 1, "op": "tell", "pid": 9, "text": "stop after the first file"})
    assert out == {"id": 1, "ok": True, "pid": 9, "queued": 1, "woke": False}
    inbox.set_listening(tmp_path, 9, True)
    assert control.handle({"id": 2, "op": "tell", "pid": 9, "text": "go"})["woke"] is True
    assert inbox.read(tmp_path, 9)["wake"] == 1
    assert control.handle({"id": 3, "op": "action", "key": "wake", "pid": 9})["wake"] == 2
    assert control.handle({"id": 4, "op": "tell", "text": "x"})["ok"] is False
    assert control.handle({"id": 5, "op": "tell", "pid": 9, "text": " "})["ok"] is False


@pytest.fixture
def person_ctl(tmp_path, monkeypatch):
    server = pytest.importorskip("pinlet.server")
    ctl = SimpleNamespace(root=tmp_path, agent=SimpleNamespace(pid=555))
    monkeypatch.setattr(server, "get_controller", lambda create=True: ctl)
    monkeypatch.setattr(server, "_control_or_none", lambda: None)
    return server, ctl


def test_notes_ride_on_the_next_tool_result(person_ctl, tmp_path):
    server, _ = person_ctl
    assert server._attach_person_notes({"ok": True}) == {"ok": True}
    inbox.add(tmp_path, 555, "use the blue theme")
    got = server._attach_person_notes({"ok": True})
    assert got["person_says"] == ["use the blue theme"] and "person" in got["person_note"]
    assert server._attach_person_notes({"ok": True}) == {"ok": True}  # handed over once
    inbox.add(tmp_path, 555, "and bigger text")
    text = server._attach_person_notes("clicked")
    assert text.startswith("clicked") and "- and bigger text" in text
    inbox.add(tmp_path, 555, "later")
    image = object()
    assert server._attach_person_notes(image) is image  # waits for a dict or text result
    assert inbox.take(tmp_path, 555) == ["later"]


class _SlowOverlay:
    def wait_for_wake(self, secs, reason, cancel=None):
        deadline = time.monotonic() + secs
        while time.monotonic() < deadline:
            if cancel is not None and cancel.wait(0.05):
                return {"cancelled": True, "woken": False, "stopped": False, "timed_out": False}
        return {"woken": False, "stopped": False, "timed_out": True}


def test_wait_returns_when_woken_from_the_menu(person_ctl, tmp_path, monkeypatch):
    server, ctl = person_ctl
    monkeypatch.setattr(server, "INBOX_POLL_S", 0.05)
    threading.Timer(0.2, lambda: inbox.request_wake(tmp_path, 555)).start()
    out = server._wait_bar_or_inbox(_SlowOverlay(), ctl, 5.0, "", None)
    assert out["woken"] is True
    assert inbox.read(tmp_path, 555)["listening"] is False


def test_wait_returns_at_once_for_a_waiting_note(person_ctl, tmp_path):
    server, ctl = person_ctl
    inbox.add(tmp_path, 555, "do the next one")
    assert server._wait_bar_or_inbox(_SlowOverlay(), ctl, 5.0, "", None)["woken"] is True


def test_wait_still_times_out(person_ctl, monkeypatch):
    server, ctl = person_ctl
    monkeypatch.setattr(server, "INBOX_POLL_S", 0.05)
    assert server._wait_bar_or_inbox(_SlowOverlay(), ctl, 0.3, "", None)["timed_out"] is True
