"""Agents coordinate through Pinlet: messages between them, and named control handoffs."""

from __future__ import annotations

import os

import pytest

from pinlet.control import inbox, peers, registry
from pinlet.control.controller import Controller


def _agents(root):
    me, other = os.getpid(), os.getppid()  # two live pids
    registry.register(root, f"{me}-a", "Tibble · Gemini", me)
    registry.register(root, f"{other}-b", "Pip · Cursor", other)
    return me, other


def test_roster_and_find(tmp_path):
    me, other = _agents(tmp_path)
    rows = peers.roster(tmp_path, me_pid=me)
    assert {r["first_name"] for r in rows} == {"Tibble", "Pip"}
    assert next(r for r in rows if r["pid"] == me)["is_you"]
    assert peers.find(rows, "pip")["pid"] == other
    assert peers.find(rows, str(other))["name"] == "Pip · Cursor"
    assert peers.find(rows, "Cursor")["pid"] == other  # part of the name
    with pytest.raises(LookupError, match="Pip · Cursor"):
        peers.find(rows, "Mallory")
    with pytest.raises(LookupError):
        peers.find(rows, "Tibble")  # yourself is not a target
    assert all("_id" not in r for r in peers.public(rows))


def test_messages_carry_the_sender_and_wake_a_listener(tmp_path):
    me, other = _agents(tmp_path)
    target = peers.find(peers.roster(tmp_path, me_pid=me), "Pip")
    out = peers.deliver(tmp_path, target, "fill the form", {"name": "Tibble · Gemini", "pid": me})
    assert out["delivery"].startswith("with its next")
    inbox.set_listening(tmp_path, other, True)
    out = peers.deliver(tmp_path, target, "now please", {"name": "Tibble · Gemini", "pid": me})
    assert out["delivery"].startswith("now")
    assert inbox.read(tmp_path, other)["wake"] == 1
    notes = inbox.take_notes(tmp_path, other)
    assert [n["from"]["name"] for n in notes] == ["Tibble · Gemini"] * 2
    assert notes[0]["kind"] == "message"


def test_rate_limit():
    key = "rate-test"
    assert all(peers.allow_send(key, now=100.0 + i) for i in range(peers.RATE_PER_MIN))
    assert not peers.allow_send(key, now=110.0)
    assert peers.allow_send(key, now=200.0)  # a minute later


def _ctl(root, name, aid):
    return Controller(
        root, name=name, pid=os.getpid(), agent_id=aid, register=False, alive=lambda p: True
    )


def test_hand_to_keeps_control_for_the_named_agent(tmp_path):
    a, b, c = _ctl(tmp_path, "A", "a"), _ctl(tmp_path, "B", "b"), _ctl(tmp_path, "C", "c")
    a.acquire(max_wait=0)
    a._finish_without_world()
    assert a.hand_to("b", "B")
    from pinlet.control.controller import ControlBusy

    with pytest.raises(ControlBusy, match="handed to B"):
        c.acquire(max_wait=0)  # C was waiting too, but B goes first
    b.acquire(max_wait=0)
    assert b.status()["you_hold_control"]
    assert not a.hand_to("c")  # A no longer holds it


def test_the_reservation_ends(tmp_path):
    clock = [1000.0]
    a = Controller(
        tmp_path,
        name="A",
        pid=os.getpid(),
        agent_id="a",
        register=False,
        alive=lambda p: True,
        clock=lambda: clock[0],
    )
    c = Controller(
        tmp_path,
        name="C",
        pid=os.getpid(),
        agent_id="c",
        register=False,
        alive=lambda p: True,
        clock=lambda: clock[0],
    )
    a.acquire(max_wait=0)
    a._finish_without_world()
    a.hand_to("b", "B")
    clock[0] += 31
    c.acquire(max_wait=0)  # B never came: C may go
    assert c.status()["you_hold_control"]


def test_agent_messages_reach_the_ai_marked_as_not_the_person(tmp_path, monkeypatch):
    srv = pytest.importorskip("pinlet.server")
    me = os.getpid()

    class Ctl:
        root = tmp_path

        class agent:
            pid = me

    monkeypatch.setattr(srv, "get_controller", lambda create=True: Ctl)
    inbox.add(tmp_path, me, "use the March invoice")
    inbox.add(
        tmp_path, me, "done with Safari", sender={"name": "Pip · Cursor", "pid": 1}, kind="handoff"
    )
    out = srv._attach_person_notes({"ok": True})
    assert out["person_says"] == ["use the March invoice"]
    assert out["agent_messages"] == [
        {"from": "Pip · Cursor", "text": "done with Safari", "kind": "handoff"}
    ]
    assert "not from the person" in out["agent_note"]
    inbox.add(tmp_path, me, "hi", sender={"name": "Pip", "pid": 1})
    text = srv._attach_person_notes("result")
    assert "Messages from other AI agents" in text and "- Pip: hi" in text


def test_the_tools_send_and_hand_over(tmp_path, monkeypatch):
    srv = pytest.importorskip("pinlet.server")
    me, other = _agents(tmp_path)
    a = _ctl(tmp_path, "Tibble · Gemini", f"{me}-a")
    a.acquire(max_wait=0)
    a._finish_without_world()
    monkeypatch.setattr(srv, "get_controller", lambda create=True: a)
    monkeypatch.setattr(srv, "_control_or_none", lambda: a)
    listed = srv.list_agents()
    assert listed["you"] == "Tibble · Gemini" and len(listed["agents"]) == 2
    assert srv.send_to_agent("Pip", "please check the totals")["sent"]
    assert not srv.send_to_agent("Mallory", "x")["ok"]
    assert not srv.send_to_agent("Pip", "   ")["ok"]
    out = srv.hand_control_to("Pip", "Safari is open on the invoice; submit is left")
    assert out["handed_over"] and out["to"] == "Pip · Cursor"
    notes = inbox.take_notes(tmp_path, other)
    assert [n["kind"] for n in notes] == ["message", "handoff"]


def test_the_panel_sees_recent_agent_messages(tmp_path):
    me, _ = _agents(tmp_path)
    target = peers.find(peers.roster(tmp_path, me_pid=me), "Pip")
    for i in range(peers.RECENT_KEEP + 3):
        peers.deliver(tmp_path, target, f"note  {i}\n", {"name": "Tibble · Gemini", "pid": me})
    kept = peers.recent_messages(tmp_path)
    assert len(kept) == peers.RECENT_KEEP
    assert kept[-1]["text"] == f"note {peers.RECENT_KEEP + 2}"
    assert kept[-1]["from"] == "Tibble · Gemini" and "Pip" in kept[-1]["to"]
    assert len(peers.recent_messages(tmp_path, 2)) == 2
    assert oct((tmp_path / "agent_messages.json").stat().st_mode & 0o777) == "0o600"
    assert peers.recent_messages(tmp_path / "nowhere") == []
