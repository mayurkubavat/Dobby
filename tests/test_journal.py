"""The activity journal says who did what where, and never what was typed."""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from pinlet import journal


def _ev(tool, name="Tibble · Cursor", app="Safari", outcome="ok", t="2026-11-08T20:00:00Z"):
    return {"t": t, "name": name, "app": app, "tool": tool, "outcome": outcome}


def test_lines_are_written_private_and_read_back(tmp_path):
    p = tmp_path / "logs" / "activity.ndjson"
    journal.record({"tool": "click", "outcome": "ok"}, where=p)
    journal.record({"tool": "type_text", "outcome": "denied"}, where=p)
    events = journal.read(where=p)
    assert [e["tool"] for e in events] == ["click", "type_text"]
    assert all("t" in e for e in events)
    assert oct(os.stat(p).st_mode & 0o777) == "0o600"


def test_the_journal_rotates(tmp_path, monkeypatch):
    p = tmp_path / "a.ndjson"
    monkeypatch.setattr(journal, "MAX_BYTES", 200)
    for _ in range(20):
        journal.record({"tool": "click", "outcome": "ok", "pad": "x" * 40}, where=p)
    assert p.with_name("a.ndjson.1").exists()
    assert p.stat().st_size <= 200
    assert not p.with_name(f"a.ndjson.{journal.BACKUPS + 1}").exists()


def test_off_means_off(monkeypatch):
    monkeypatch.setenv("PINLET_JOURNAL", "0")
    assert journal.path() is None
    journal.record({"tool": "click"})  # no error, nothing written


def test_summaries_read_like_sentences():
    events = [
        _ev("take_screenshot"),
        _ev("click"),
        _ev("click"),
        _ev("type_text"),
        _ev("click"),
        _ev("click"),
        _ev("hotkey", outcome="denied"),
        _ev("click", name="Pip · Antigravity", app="Finder"),
        _ev("control_status", name="Pip · Antigravity", app="Finder"),
    ]
    lines = journal.summarize(events)
    assert lines[0]["text"] == (
        "Tibble · Cursor typed once, clicked 4 times and 2 more steps in Safari (denied)"
    )
    assert lines[1]["text"] == "Pip · Antigravity clicked once in Finder"
    assert lines[1]["steps"] == 1  # status checks are not steps


def test_a_quiet_gap_starts_a_new_line():
    lines = journal.summarize(
        [_ev("click", t="2026-11-08T20:00:00Z"), _ev("click", t="2026-11-08T20:10:00Z")]
    )
    assert len(lines) == 2


def test_outcomes_from_tool_results():
    from mcp.types import CallToolResult, TextContent

    ok = CallToolResult(content=[], structured_content={"result": {"ok": True}})
    denied = CallToolResult(content=[], structured_content={"handoff": "denied"})
    blocked = CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"repeat_blocked": True}))],
        is_error=True,
    )
    plain = CallToolResult(content=[TextContent(type="text", text="[stopped] x")], is_error=True)
    assert journal.outcome_of(ok) == "ok"
    assert journal.outcome_of(denied) == "denied"
    assert journal.outcome_of(blocked) == "repeat_blocked"
    assert journal.outcome_of(plain) == "stopped"
    assert journal.outcome_of(None, failed=True) == "error"


def test_the_server_journals_calls_without_their_arguments(tmp_path, monkeypatch):
    srv = pytest.importorskip("pinlet.server")
    p = tmp_path / "activity.ndjson"
    monkeypatch.setenv("PINLET_JOURNAL", str(p))
    from mcp.types import CallToolResult, TextContent

    async def fake(self, name, arguments, context):
        return CallToolResult(content=[TextContent(type="text", text="{}")])

    monkeypatch.setattr(srv.PinletServer, "_call_in_scope", fake)
    asyncio.run(srv.mcp_server.call_tool("type_text", {"text": "my secret password"}))
    text = p.read_text()
    assert '"tool": "type_text"' in text and '"outcome": "ok"' in text
    assert "my secret" not in text


def test_cli_prints_lines(tmp_path, monkeypatch, capsys):
    p = tmp_path / "activity.ndjson"
    monkeypatch.setenv("PINLET_JOURNAL", str(p))
    assert journal.main([]) == 0
    assert "No activity yet" in capsys.readouterr().out
    journal.record(_ev("click"), where=p)
    assert journal.main([]) == 0
    assert "Tibble · Cursor clicked once in Safari" in capsys.readouterr().out
