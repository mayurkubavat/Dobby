"""The server log records every tool call and keeps tracebacks, never on stdout."""

import asyncio
import logging

import pytest

import dobby.server as srv


@pytest.fixture
def server_log(tmp_path):
    path = tmp_path / "logs" / "server.log"
    handler = srv.setup_server_log(str(path), max_bytes=10_000, backups=1)
    yield path
    logging.getLogger("dobby").removeHandler(handler)
    handler.close()


def _flush():
    for h in logging.getLogger("dobby").handlers:
        h.flush()


def test_setup_is_idempotent_and_rotates_small(server_log):
    again = srv.setup_server_log(str(server_log), max_bytes=10_000, backups=1)
    handlers = [
        h
        for h in logging.getLogger("dobby").handlers
        if getattr(h, "baseFilename", "") == str(server_log)
    ]
    assert handlers == [again] and again.maxBytes == 10_000


def test_tool_calls_are_logged_with_duration_and_traceback(server_log, monkeypatch, capsys):
    monkeypatch.setattr(srv.toolset_manager, "is_tool_active", lambda name: True)
    monkeypatch.setattr(srv, "get_controller", lambda create=False: None)

    @srv.mcp_server.tool()
    def _test_boom() -> dict:
        raise ValueError("kaput")

    asyncio.run(srv.mcp_server.call_tool("manage_toolsets", {"action": "list_domains"}))
    try:
        asyncio.run(srv.mcp_server.call_tool("_test_boom", {}))
    except Exception:  # noqa: BLE001 - the SDK may raise or return an error result
        pass
    finally:
        srv.mcp_server.remove_tool("_test_boom")
    _flush()
    text = server_log.read_text()
    assert "dobby.tools: manage_toolsets ok " in text
    assert "_test_boom raised ValueError: kaput" in text and "Traceback" in text
    assert "_test_boom" in text.split("raised ValueError")[1]  # the call line follows the traceback
    assert capsys.readouterr().out == ""
