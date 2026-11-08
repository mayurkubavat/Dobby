"""Shared Pinlet: what one agent saw or set never answers another agent's call."""

from __future__ import annotations

import contextlib

from pinlet.hub import per_session, sessions
from pinlet.hub.per_session import PerSession
from pinlet.toolset import ToolDefinition, ToolSetManager


@contextlib.contextmanager
def as_session(info):
    token = sessions.CURRENT.set(info)
    try:
        yield
    finally:
        sessions.CURRENT.reset(token)


class Holder:
    marks = PerSession(default={})
    meta = PerSession(default=None)


def test_each_session_sees_its_own_marks():
    a = sessions.open_session("A", 1, "turbo")
    b = sessions.open_session("B", 2, "turbo")
    h = Holder()
    try:
        with as_session(a):
            h.marks = {7: "Send button"}
            h.marks[8] = "Cancel"
        with as_session(b):
            assert h.marks == {}  # B never took a screenshot
            h.marks = {7: "Trash icon"}
        with as_session(a):
            assert h.marks == {7: "Send button", 8: "Cancel"}
        assert h.marks == {} and h.meta is None  # outside the hub: its own slot
    finally:
        sessions.close_session(a)
        sessions.close_session(b)


def test_closing_a_session_forgets_its_state():
    a = sessions.open_session("A", 1, "turbo")
    h = Holder()
    with as_session(a):
        h.meta = (100, 100, 1.0)
    sessions.close_session(a)
    store = h.__dict__["_per_session_store"]["meta"]
    assert a.id not in store
    per_session.forget(0)  # the private slot is never dropped


def test_feedback_controller_marks_are_per_session():
    from pinlet.agent_loop.controller import FeedbackLoopController

    assert isinstance(FeedbackLoopController.__dict__["active_marks"], PerSession)
    assert isinstance(FeedbackLoopController.__dict__["last_screenshot_meta"], PerSession)


def _manager(tmp_path):
    m = ToolSetManager(initial_profile="minimal_computer_use", state_dir=tmp_path)
    m.register_tool(ToolDefinition(name="click", description="", domain="core"))
    m.register_tool(ToolDefinition(name="list_windows", description="", domain="window_mgmt"))
    return m


def test_manage_toolsets_changes_only_the_calling_agents_tools(tmp_path):
    m = _manager(tmp_path)
    a = sessions.open_session("A", 1, "turbo")
    b = sessions.open_session("B", 2, "turbo")
    try:
        with as_session(a):
            m.manage_toolsets("enable", ["window_mgmt"])
            assert m.is_tool_active("list_windows")
        with as_session(b):
            assert not m.is_tool_active("list_windows")
            assert m.get_active_domains() == ["core"]
        with as_session(a):
            assert m.get_active_domains() == ["core", "window_mgmt"]
    finally:
        sessions.close_session(a)
        sessions.close_session(b)
    assert a.id not in m._domains_by_session
