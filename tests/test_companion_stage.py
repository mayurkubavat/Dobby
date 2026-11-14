"""Rime standing in for the bar: heartbeat, stage feed, step aside, One step, why."""

from __future__ import annotations

import asyncio

import pytest
from test_control import Clock, act, make

from pinlet.control import commands
from pinlet.hub import control
from pinlet.overlay import companion, topbar
from pinlet.overlay.text import clean_thought


# ------------------------------------------------------------ heartbeat
def test_fresh_heartbeat_stands_in_for_the_bar(tmp_path):
    path = str(tmp_path / "c.json")
    companion.beat(visible=True, frame=[10, 20, 72, 72], bar=True, now=100.0, path=path)
    rec = companion.read(now=101.0, path=path)
    assert rec["fresh"] and companion.stands_in_for_bar(rec)


@pytest.mark.parametrize(
    ("visible", "bar", "age"),
    [(False, True, 0.5), (True, False, 0.5), (True, True, companion.STALE_S + 0.1)],
)
def test_the_bar_comes_back(tmp_path, visible, bar, age):
    path = str(tmp_path / "c.json")
    companion.beat(visible=visible, frame=[10, 20, 72, 72], bar=bar, now=100.0, path=path)
    assert not companion.stands_in_for_bar(companion.read(now=100.0 + age, path=path))


def test_no_heartbeat_means_the_bar(tmp_path):
    assert companion.read(path=str(tmp_path / "none.json")) == {}
    assert not companion.stands_in_for_bar({})


def test_a_bad_frame_never_hides_the_bar(tmp_path):
    path = str(tmp_path / "c.json")
    companion.beat(visible=True, frame=[0, 0, 0, 0], bar=True, now=100.0, path=path)
    assert not companion.stands_in_for_bar(companion.read(now=100.0, path=path))


# ------------------------------------------------------------ step aside
def test_covers_includes_a_margin():
    rec = {"fresh": True, "visible": True, "frame": [100.0, 100.0, 72.0, 72.0]}
    assert companion.covers(rec, (130, 130))
    assert companion.covers(rec, (100 - companion.MARGIN + 1, 130))
    assert not companion.covers(rec, (100 - companion.MARGIN - 1, 130))
    assert not companion.covers({**rec, "fresh": False}, (130, 130))


def test_avoid_request_expires(tmp_path):
    path = str(tmp_path / "a.json")
    companion.ask_avoid((5, 6), now=10.0, path=path)
    assert companion.avoid_request(now=10.5, path=path) == [5.0, 6.0]
    assert companion.avoid_request(now=10.0 + companion.AVOID_S + 0.1, path=path) is None
    companion.clear_avoid(path)
    assert companion.avoid_request(now=10.5, path=path) is None


# ------------------------------------------------------------ what it saw
def test_seen_is_trimmed_and_expires(tmp_path):
    path = str(tmp_path / "s.json")
    items = [{"label": "Send‮", "role": "button", "rect": [1, 2, 3, 4]}] * 50
    items.append({"label": "bad", "role": "x", "rect": [1, 2, 0, 4]})
    companion.note_seen(items, now=0.0, path=path)
    got = companion.read_seen(now=1.0, path=path)
    assert len(got) == companion.SEEN_MAX
    assert got[0] == {"label": "Send", "role": "button", "rect": [1.0, 2.0, 3.0, 4.0]}
    assert companion.read_seen(now=companion.SEEN_S + 1, path=path) == []


# ------------------------------------------------------------ stage feed
def test_stage_record_is_plain_json():
    rec = topbar.stage_record(goal="g" * 300, thought="why", kind="typing", pointer=(1.23, 4.56))
    assert len(rec["goal"]) == 160 and rec["thought"] == "why"
    assert rec["pointer"] == [1.2, 4.6] and rec["bar_hidden"] is False


def test_stage_of_prefers_what_needs_the_person():
    states = {
        1: {"mode": "working", "at": 5, "step": "Click Send", "stage": {"goal": "mail"}},
        2: {"mode": "confirm", "at": 4, "stage": {"goal": "delete"}},
        3: {"mode": "ready", "at": 9},
    }
    stage = topbar.stage_of(states)
    assert stage["server_pid"] == 2 and stage["mode"] == "confirm" and stage["goal"] == "delete"
    assert topbar.stage_of({3: {"mode": "ready"}}) == {}


def test_publish_carries_the_stage(tmp_path):
    topbar.publish(
        4242, mode="working", step="Click", stage={"goal": "send notes"}, directory=str(tmp_path)
    )
    states = topbar.read_states(str(tmp_path))
    assert states[4242]["stage"] == {"goal": "send notes"}


# ------------------------------------------------------------ control ops
def test_only_the_app_may_send_the_heartbeat():
    reply = control.handle(
        {"id": 1, "op": "companion", "visible": True, "bar": True}, trusted=False
    )
    assert reply == {"id": 1, "ok": False, "error": control.NOT_THE_APP}


def test_heartbeat_reply(monkeypatch, tmp_path):
    monkeypatch.setattr(companion, "HEART_PATH", str(tmp_path / "c.json"))
    monkeypatch.setattr(companion, "AVOID_PATH", str(tmp_path / "a.json"))
    calls = []
    monkeypatch.setattr(companion, "beat", lambda **kw: calls.append(kw))
    monkeypatch.setattr(companion, "avoid_request", lambda: [3.0, 4.0])
    monkeypatch.setattr(control, "stage", lambda: {"mode": "working"})
    reply = control.handle(
        {"id": 2, "op": "companion", "visible": True, "frame": [1, 2, 3, 4], "bar": True}
    )
    assert reply["ok"] and reply["avoid"] == [3.0, 4.0] and reply["stage"] == {"mode": "working"}
    assert calls == [{"visible": True, "frame": [1, 2, 3, 4], "bar": True}]


def test_one_step_is_a_person_action():
    assert control._needs_person({"op": "action", "key": "step", "pid": 1})


# ------------------------------------------------------------ One step
def test_one_step_lets_exactly_one_action_through(tmp_path):
    c = Clock()
    a = make(tmp_path, c, "Claude", 1)
    commands.set_paused(tmp_path, 1, True)
    commands.step_once(tmp_path, 1)
    assert commands.read(tmp_path, 1) == {"paused": True, "stop": 0, "step": 1}
    act(a)  # runs at once
    seen = []
    a.on_paused = seen.append
    real_sleep = c.sleep

    def sleep(s):
        real_sleep(s)
        if c.t > 1003:
            commands.step_once(tmp_path, 1)  # the person presses One step again

    a._sleep = sleep
    act(a)  # waited for the second press
    assert seen == [True, False] and c.t > 1003
    assert commands.read(tmp_path, 1)["paused"] is True


def test_one_step_unpaused_is_harmless(tmp_path):
    c = Clock()
    a = make(tmp_path, c, "Claude", 1)
    act(a)
    commands.step_once(tmp_path, 1)
    act(a)  # paused by step_once, but the press itself lets this action through


# ------------------------------------------------------------ why
def test_clean_thought_drops_tricks():
    assert clean_thought("to‮ attach\nthe\tnotes") == "to attach the notes"
    assert len(clean_thought("x" * 500)) == 90


def test_action_tools_take_why():
    from pinlet import server

    async def schemas():
        return {t.name: t.input_schema for t in await server.mcp_server.list_tools()}

    tools = asyncio.run(schemas())
    for name in ("click", "press_key", "type_text", "scroll"):
        if name in tools:
            assert tools[name]["properties"]["why"]["type"] == "string"
            assert "why" not in (tools[name].get("required") or [])
    assert "why" not in tools["take_screenshot"]["properties"]


def test_why_goes_to_the_companion_not_the_tool(monkeypatch):
    from pinlet import server

    shown = []
    monkeypatch.setattr(server, "_show_thought", shown.append)

    def tool(key: str, verify_delta: bool = True) -> dict:
        """Presses a key."""
        return {"key": key, "verify": verify_delta}

    wrapped = server._with_why(tool)
    assert wrapped("a", why="to save") == {"key": "a", "verify": True}
    assert wrapped("b") == {"key": "b", "verify": True}
    assert shown == ["to save", ""]
    assert "why" in str(wrapped.__signature__) and "why:" in wrapped.__doc__
