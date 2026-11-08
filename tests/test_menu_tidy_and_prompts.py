"""Menu list tidying (Stop / Clear finished / long asleep) and app prompts."""

import contextvars
import threading
import time

import pytest

from dobby.hub import control, prompts, sessions
from dobby.overlay import topbar


def _agents(*pids, started=1000.0):
    return [
        {"pid": p, "name": f"D{p}", "paused": False, "waiting": False, "started": started}
        for p in pids
    ]


def test_without_hiding_every_row_shows():
    view = topbar.topbar_view(_agents(1, 2), {}, now=10_000)
    assert [r.pid for r in view.rows] == [1, 2] and view.hidden == 0
    assert not any(k in ("clear_finished", "show_hidden") for _, k in view.all_actions)


def test_long_asleep_rows_hide_and_come_back_when_working():
    hiding = topbar.Hiding(idle_secs=600)
    view = topbar.topbar_view(_agents(1, 2), {}, hiding=hiding, now=1000 + 700)
    assert view.rows == [] and view.hidden == 2
    assert view.subtitle.endswith("2 hidden")
    assert ("Show 2 hidden", "show_hidden") in view.all_actions
    view = topbar.topbar_view(
        _agents(1, 2), {1: {"mode": "working"}}, hiding=hiding, now=1000 + 800
    )
    assert [r.pid for r in view.rows] == [1] and view.hidden == 1


def test_clear_finished_hides_sleeping_but_not_busy_rows():
    hiding = topbar.Hiding(idle_secs=0)
    states = {2: {"mode": "working"}, 3: {"mode": "confirm"}}
    view = topbar.topbar_view(_agents(1, 2, 3), states, hiding=hiding, now=2000)
    assert ("Clear finished", "clear_finished") in view.all_actions
    assert topbar.menu_action("clear_finished", view=view, hiding=hiding)
    assert set(hiding.pids) == {1}
    view = topbar.topbar_view(_agents(1, 2, 3), states, hiding=hiding, now=2001)
    assert sorted(r.pid for r in view.rows) == [2, 3]
    # Show hidden brings it back, and nothing auto-hides before that moment.
    hiding.idle_secs = 600
    topbar.menu_action("show_hidden", hiding=hiding)
    view = topbar.topbar_view(_agents(1, 2, 3), states, hiding=hiding, now=time.time() + 1)
    assert sorted(r.pid for r in view.rows) == [1, 2, 3]


def test_a_stopped_shared_session_stays_hidden_while_working_lingers():
    now = 5000.0
    hiding = topbar.Hiding(idle_secs=0)
    hiding.hide([7], now=now)
    # Still inside the 15 s "working" window from a call made before Stop.
    lingering = {7: {"mode": "working", "last_call": now - 3}}
    assert topbar.topbar_view(_agents(7), lingering, hiding=hiding, now=now + 2).rows == []
    # A new tool call after Stop: back in the list.
    fresh = {7: {"mode": "working", "last_call": now + 5}}
    assert [
        r.pid for r in topbar.topbar_view(_agents(7), fresh, hiding=hiding, now=now + 6).rows
    ] == [7]


def test_hiding_is_saved_and_pruned(tmp_path):
    path = str(tmp_path / "h.json")
    h = topbar.Hiding.load(path, idle_secs=0)
    h.hide([1, 2], now=10)
    again = topbar.Hiding.load(path)
    assert again.pids == {1: 10.0, 2: 10.0}
    again.prune([2])
    assert topbar.Hiding.load(path).pids == {2: 10.0}


def test_control_stop_takes_the_dobby_out_of_the_menu(tmp_path, monkeypatch):
    monkeypatch.setenv("DOBBY_CONTROL_DIR", str(tmp_path))
    monkeypatch.setenv("DOBBY_MENU_HIDE_IDLE", "0")
    started = time.time() - 5
    monkeypatch.setattr(control, "agents", lambda: _agents(11, 12, started=started))
    assert len(control.handle({"id": 1, "op": "topbar"})["view"]["rows"]) == 2
    control.handle({"id": 2, "op": "action", "key": "stop", "pid": 11})
    view = control.handle({"id": 3, "op": "topbar"})["view"]
    assert [r["pid"] for r in view["rows"]] == [12] and view["hidden"] == 1
    control.handle({"id": 4, "op": "action", "key": "clear_finished"})
    assert control.handle({"id": 5, "op": "topbar"})["view"]["rows"] == []
    control.handle({"id": 6, "op": "action", "key": "show_hidden"})
    assert len(control.handle({"id": 7, "op": "topbar"})["view"]["rows"]) == 2


# ------------------------------------------------------------ prompts
@pytest.fixture
def app_owned(monkeypatch):
    monkeypatch.setenv("DOBBY_APP_OWNED", "1")
    prompts._reset_for_tests()
    yield
    prompts._reset_for_tests()


def test_prompts_need_a_polling_app(app_owned, monkeypatch):
    assert not prompts.app_listening()
    prompts.note_poll(now=100.0)
    assert prompts.app_listening(now=101.0) and not prompts.app_listening(now=104.0)
    monkeypatch.setenv("DOBBY_APP_PROMPTS", "0")
    assert not prompts.app_listening(now=101.0)
    monkeypatch.delenv("DOBBY_APP_OWNED")
    monkeypatch.delenv("DOBBY_APP_PROMPTS")
    assert not prompts.app_listening(now=101.0)


def _ask_in_thread(**kw):
    out = {}
    ctx = contextvars.copy_context()  # like anyio.to_thread: the session travels along
    t = threading.Thread(target=lambda: out.setdefault("ok", ctx.run(prompts.ask, **kw)))
    t.start()
    for _ in range(200):
        if prompts.pending():
            break
        time.sleep(0.01)
    return t, out


def test_the_app_answers_through_the_control_socket(app_owned, monkeypatch):
    monkeypatch.setattr(control, "agents", lambda: _agents(4242, started=time.time()))
    info = sessions.open_session("Tibble · Claude Code", 4242, "secure")
    token = sessions.CURRENT.set(info)
    try:
        t, out = _ask_in_thread(text="Allow Dobby to press Send?", secs=5, detail="In Mail")
    finally:
        sessions.CURRENT.reset(token)
        sessions.close_session(info)
    reply = control.handle({"id": 1, "op": "topbar"})
    assert prompts.app_listening()
    (p,) = reply["prompts"]
    assert (
        p["text"] == "Allow Dobby to press Send?" and p["detail"] == "In Mail" and p["pid"] == 4242
    )
    assert p["name"] == "Tibble · Claude Code" and 0 < p["secs_left"] <= 5
    assert reply["view"]["rows"][0]["state"] == "confirm"
    assert control.handle({"id": 2, "op": "action", "key": "allow", "prompt_id": p["id"]})[
        "answered"
    ]
    t.join(2)
    assert out["ok"] is True and prompts.pending() == []
    # A second answer to the same prompt does nothing.
    assert (
        control.handle({"id": 3, "op": "answer", "prompt_id": p["id"], "ok": False})["answered"]
        is False
    )


def test_timeout_and_stop_all_deny(app_owned, monkeypatch):
    assert prompts.ask("Delete?", secs=1.0) is False  # nobody answered
    monkeypatch.setattr(control, "agents", list)
    t, out = _ask_in_thread(text="Pay?", secs=5)
    control.handle({"id": 1, "op": "action", "key": "stop_all"})
    t.join(2)
    assert out["ok"] is False


def test_overlay_confirm_goes_to_the_app_when_it_listens(app_owned):
    from dobby.overlay.client import OverlayClient

    client = OverlayClient.__new__(OverlayClient)  # no helper process
    sent = []
    client.intent = lambda text, ms=None: sent.append(text) or True
    prompts.note_poll()
    t = threading.Thread(target=lambda: sent.append(client.confirm("Send it?", secs=5)))
    t.start()
    for _ in range(200):
        if prompts.pending():
            break
        time.sleep(0.01)
    (p,) = prompts.pending()
    prompts.answer(p["id"], False)
    t.join(2)
    assert sent == ["Waiting for you: Send it?", False]


def test_group_actions_only_offer_what_would_do_something():
    keys = lambda states: [k for _, k in topbar.topbar_view(_agents(1, 2), states).all_actions]
    assert keys({}) == ["pause_all", "stop_all"]
    assert keys({1: {"mode": "paused"}, 2: {"mode": "paused"}}) == ["resume_all", "stop_all"]
    assert keys({1: {"mode": "paused"}}) == ["pause_all", "resume_all", "stop_all"]
