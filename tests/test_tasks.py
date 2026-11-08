"""The shared task board: post, claim, finish, and the poster hears how it went."""

from __future__ import annotations

import os

import pytest

from pinlet.control import inbox, registry, tasks

TIBBLE = {"name": "Tibble · Gemini", "pid": 101}
PIP = {"name": "Pip · Cursor", "pid": 202}
alive = lambda pid: pid in (101, 202)


def test_post_claim_finish(tmp_path):
    t = tasks.post(tmp_path, TIBBLE, "  Fill the   expense form ", "March receipts")
    assert t["id"] == 1 and t["title"] == "Fill the expense form" and t["status"] == "open"
    with pytest.raises(ValueError):
        tasks.post(tmp_path, TIBBLE, "   ")
    got = tasks.claim(tmp_path, PIP, 1, alive=alive)
    assert got["status"] == "claimed" and got["owner"]["name"] == "Pip · Cursor"
    assert tasks.claim(tmp_path, PIP, 1, alive=alive)["status"] == "claimed"  # again: fine
    with pytest.raises(PermissionError, match="claimed by Pip"):
        tasks.claim(tmp_path, TIBBLE, 1, alive=alive)
    with pytest.raises(PermissionError):
        tasks.finish(tmp_path, TIBBLE, 1, "done", alive=alive)
    with pytest.raises(ValueError):
        tasks.finish(tmp_path, PIP, 1, "x", failure="tired", alive=alive)
    done = tasks.finish(tmp_path, PIP, 1, "Submitted, ref 77", alive=alive)
    assert done["status"] == "done" and done["result"] == "Submitted, ref 77"
    assert tasks.line(done) == "1 · Fill the expense form · done"
    with pytest.raises(LookupError):
        tasks.claim(tmp_path, PIP, 9, alive=alive)
    assert oct((tmp_path / "tasks.json").stat().st_mode & 0o777) == "0o600"


def test_failure_codes_and_giving_back(tmp_path):
    tasks.post(tmp_path, TIBBLE, "Log in to the bank")
    tasks.claim(tmp_path, PIP, 1, alive=alive)
    back = tasks.finish(tmp_path, PIP, 1, "no time", failure="gave_up", alive=alive)
    assert back["status"] == "open" and back["owner"] is None
    tasks.claim(tmp_path, TIBBLE, 1, alive=alive)
    failed = tasks.finish(
        tmp_path, TIBBLE, 1, "asks for a code", failure="needs_person", alive=alive
    )
    assert failed["status"] == "failed" and tasks.line(failed).endswith("failed (needs person)")


def test_tasks_for_one_agent_and_owners_who_leave(tmp_path):
    tasks.post(tmp_path, TIBBLE, "Check totals", for_agent=PIP)
    with pytest.raises(PermissionError, match="posted for Pip"):
        tasks.claim(tmp_path, TIBBLE, 1, alive=alive)
    tasks.claim(tmp_path, PIP, 1, alive=alive)
    # Pip's process goes away: the task opens again, and anyone may take it.
    board = tasks.board(tmp_path, alive=lambda pid: pid == 101)
    assert board[0]["status"] == "open" and board[0]["owner"] is None
    assert tasks.claim(tmp_path, TIBBLE, 1, alive=lambda pid: pid == 101)["status"] == "claimed"


def test_old_finished_tasks_go_and_the_board_stays_small(tmp_path):
    for i in range(tasks.KEEP + 5):
        tasks.post(tmp_path, TIBBLE, f"task {i}", now=1000.0)
    assert len(tasks.board(tmp_path, now=1000.0, alive=alive)) == tasks.KEEP
    first = tasks.board(tmp_path, now=1000.0, alive=alive)[0]["id"]
    tasks.claim(tmp_path, PIP, first, now=1000.0, alive=alive)
    tasks.finish(tmp_path, PIP, first, "ok", now=1000.0, alive=alive)
    later = tasks.board(tmp_path, now=1000.0 + tasks.DONE_KEEP_S + 1, alive=alive)
    assert first not in [t["id"] for t in later] and len(later) == tasks.KEEP - 1
    assert tasks.board(tmp_path, "done", now=1000.0, alive=alive) == []


def test_the_tools_post_claim_finish_and_tell_the_poster(tmp_path, monkeypatch):
    srv = pytest.importorskip("pinlet.server")
    me, other = os.getpid(), os.getppid()
    registry.register(tmp_path, f"{me}-a", "Tibble · Gemini", me)
    registry.register(tmp_path, f"{other}-b", "Pip · Cursor", other)
    who = {"pid": me, "name": "Tibble · Gemini"}
    monkeypatch.setattr(srv, "_me", lambda: (tmp_path, dict(who), str(who["pid"])))

    posted = srv.post_task("Fill the form", "left window", for_agent="Pip")
    assert posted["posted"] and posted["task"]["for"] == "Pip · Cursor"
    news = inbox.take_notes(tmp_path, other)
    assert news[0]["kind"] == "task" and "claim_task(1)" in news[0]["text"]
    assert not srv.post_task("x", for_agent="Mallory")["ok"]
    assert srv.list_tasks()["tasks"][0]["status"] == "open"
    assert not srv.list_tasks("weird")["ok"]

    who.update(pid=other, name="Pip · Cursor")
    claimed = srv.claim_task(1)
    assert claimed["claimed"] and "not from the person" in claimed["agent_note"]
    out = srv.finish_task(1, "Sent it", "")
    assert out["finished"] and out["told"] == "Tibble · Gemini"
    told = inbox.take_notes(tmp_path, me)
    assert told[0]["kind"] == "task" and "is done. Sent it" in told[0]["text"]
    assert not srv.finish_task(1, "again")["ok"]
    assert srv.list_tasks("done")["tasks"][0]["result"] == "Sent it"


def test_cli_and_control_op(tmp_path, monkeypatch, capsys):
    from pinlet.hub import control

    monkeypatch.setenv("PINLET_CONTROL_DIR", str(tmp_path))
    assert tasks.main([]) == 0 and "No tasks" in capsys.readouterr().out
    tasks.post(tmp_path, TIBBLE, "Rename the files")
    assert tasks.main([]) == 0 and "1 · Rename the files · open" in capsys.readouterr().out
    reply = control.handle({"id": 1, "op": "tasks"})
    assert reply["ok"] and reply["tasks"][0]["line"] == "1 · Rename the files · open"
    assert "tasks" in control.handle({"id": 2, "op": "activity"})
