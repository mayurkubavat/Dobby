"""The menu carries what the app's banners need: failures in a row and what a waiting AI waits for."""

from __future__ import annotations

from pinlet.hub import control, sessions
from pinlet.overlay import topbar


def test_failures_count_in_a_row():
    info = sessions.SessionInfo(id=1, name="Tibble · Codex", pid=11, mode="turbo")
    for outcome in ("error", "error", "denied", "error"):
        sessions.note_outcome(outcome, info)
    assert info.failures == 3  # a Deny is the person's word, not a failure
    sessions.note_outcome("ok", info)
    assert info.failures == 0


def test_session_states_pass_failures_and_reason():
    snap = [{"pid": 11, "last_call": 1.0, "failures": 4, "waiting_for": "the report is ready"}]
    state = control.session_states(snap, now=100.0)[11]
    assert state["failures"] == 4 and state["reason"] == "the report is ready"


def test_rows_show_the_reason_only_while_listening():
    agents = [
        {"pid": 11, "name": "Tibble · Codex", "listening": True},
        {"pid": 12, "name": "Pip · Cursor"},
    ]
    states = {
        11: {"mode": "ready", "reason": "finished the report; ready for more", "failures": 0},
        12: {"mode": "working", "reason": "stale", "failures": 3, "last_call": 99.0},
    }
    view = topbar.topbar_view(agents, states, now=100.0)
    rows = {r.pid: r for r in view.rows}
    assert rows[11].state == "listening" and rows[11].reason.startswith("finished the report")
    assert rows[12].reason == "" and rows[12].failures == 3
    d = view.to_dict()["rows"]
    assert {r["pid"]: r["failures"] for r in d} == {11: 0, 12: 3}
