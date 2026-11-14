"""A call cut off by a Pinlet restart is not done twice."""

from __future__ import annotations

import json
import os

from pinlet.hub import receipts

KEY = "ab" * 16


def _as_old_pinlet(root, records_hub=1):
    path = receipts._path(root, KEY)
    data = json.loads(path.read_text())
    for rec in data:
        rec["hub"] = records_hub  # pretend another (earlier) Pinlet wrote them
    path.write_text(json.dumps(data))


def test_a_repeat_of_an_interrupted_call_is_blocked_once(tmp_path):
    fp = receipts.fingerprint("type_text", {"text": "hello"})
    receipts.started(tmp_path, KEY, "type_text", fp)
    assert receipts.check(tmp_path, KEY, "type_text", fp) is None  # same Pinlet: runs
    _as_old_pinlet(tmp_path)
    rec = receipts.check(tmp_path, KEY, "type_text", fp)
    assert rec is not None and rec["state"] == "started"
    out = receipts.blocked_result(rec)
    assert out["repeat_blocked"] and "may or may not" in out["note"]
    assert receipts.check(tmp_path, KEY, "type_text", fp) is None  # the next one runs


def test_the_receipt_holds_no_typed_text(tmp_path):
    fp = receipts.fingerprint("type_text", {"text": "my secret"})
    receipts.started(tmp_path, KEY, "type_text", fp)
    assert "my secret" not in receipts._path(tmp_path, KEY).read_text()


def test_finished_calls_say_so(tmp_path):
    fp = receipts.fingerprint("click", {"x": 1, "y": 2})
    receipts.started(tmp_path, KEY, "click", fp)
    receipts.finished(tmp_path, KEY, "click", fp, ok=True)
    _as_old_pinlet(tmp_path)
    rec = receipts.check(tmp_path, KEY, "click", fp)
    assert rec["state"] == "done"
    assert "answer was lost" in receipts.blocked_result(rec)["note"]


def test_other_calls_and_old_receipts_run(tmp_path):
    fp = receipts.fingerprint("click", {"x": 1, "y": 2})
    receipts.started(tmp_path, KEY, "click", fp, now=1000.0)
    _as_old_pinlet(tmp_path)
    other = receipts.fingerprint("click", {"x": 5, "y": 2})
    assert receipts.check(tmp_path, KEY, "click", other, now=1001.0) is None
    assert receipts.check(tmp_path, KEY, "click", fp, now=1000.0 + receipts.WINDOW_S + 1) is None
    # read-only tools and clients without an agent key never get receipts
    receipts.started(tmp_path, KEY, "take_screenshot", "x")
    receipts.started(tmp_path, "", "click", fp)
    assert len(json.loads(receipts._path(tmp_path, KEY).read_text())) == 1


def test_receipts_stay_small(tmp_path):
    for i in range(receipts.KEEP + 10):
        receipts.started(tmp_path, KEY, "press_key", str(i))
    assert len(json.loads(receipts._path(tmp_path, KEY).read_text())) == receipts.KEEP
    assert oct(os.stat(receipts._dir(tmp_path)).st_mode & 0o777) == "0o700"


def test_the_server_answers_a_repeat_without_acting(tmp_path, monkeypatch):
    import asyncio

    import pytest

    srv = pytest.importorskip("pinlet.server")
    from pinlet.hub import sessions

    monkeypatch.setenv("PINLET_CONTROL_DIR", str(tmp_path))
    args = {"text": "hello"}
    fp = receipts.fingerprint("type_text", args)
    receipts.started(tmp_path, KEY, "type_text", fp)
    _as_old_pinlet(tmp_path)

    def boom(*a, **k):
        raise AssertionError("must not act")

    monkeypatch.setattr(srv.PinletServer, "_call_in_scope", boom)
    info = sessions.SessionInfo(id=7, name="Pip · codex", pid=None, mode="turbo", agent_key=KEY)
    token = sessions.CURRENT.set(info)
    try:
        result = asyncio.run(srv.mcp_server.call_tool("type_text", args))
    finally:
        sessions.CURRENT.reset(token)
    assert result.is_error
    assert json.loads(result.content[0].text)["repeat_blocked"]


def test_a_clean_quit_lets_an_answered_call_be_sent_again(tmp_path):
    done = receipts.fingerprint("press_key", {"key": "shift"})
    fresh = receipts.fingerprint("click", {"x": 1, "y": 2})
    running = receipts.fingerprint("type_text", {"text": "hi"})
    receipts.started(tmp_path, KEY, "press_key", done, now=1000.0)
    receipts.finished(tmp_path, KEY, "press_key", done, ok=True, now=1000.5)
    receipts.started(tmp_path, KEY, "click", fresh, now=1014.0)
    receipts.finished(tmp_path, KEY, "click", fresh, ok=True, now=1014.5)
    receipts.started(tmp_path, KEY, "type_text", running, now=1014.8)
    receipts.settle(tmp_path, now=1015.0)  # the old Pinlet quits in good order
    _as_old_pinlet(tmp_path)
    assert receipts.check(tmp_path, KEY, "press_key", done, now=1020.0) is None  # meant: runs
    assert receipts.check(tmp_path, KEY, "click", fresh, now=1020.0)["state"] == "done"
    assert receipts.check(tmp_path, KEY, "type_text", running, now=1020.0)["state"] == "started"
