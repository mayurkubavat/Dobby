"""Client quirks come from one table, not from name checks."""

from __future__ import annotations

from pinlet import clients
from pinlet.hub import sessions


def test_known_clients_get_their_profiles():
    assert clients.profile_for("claude-code").tool_timeout_s is None
    assert clients.profile_for("claude-ai").tool_timeout_s == 60.0
    assert clients.profile_for("codex-mcp-client").key == "codex"
    assert clients.profile_for("gemini-cli-mcp-client").tool_timeout_s == 600.0
    ag = clients.profile_for("Antigravity")
    assert ag.key == "antigravity" and not ag.sees_list_changed
    assert clients.profile_for("cursor-vscode").key == "cursor"
    assert clients.profile_for("something new") is clients.UNKNOWN
    assert clients.profile_for(None) is clients.UNKNOWN


def test_the_persons_override_wins():
    assert clients.profile_for("codex", 300.0).tool_timeout_s == 300.0
    assert clients.profile_for("codex", 0.0).tool_timeout_s is None  # 0 = no limit
    assert clients.parse_timeout("90") == 90.0
    assert clients.parse_timeout("") is None
    assert clients.parse_timeout("soon") is None
    assert clients.parse_timeout("-5") == 0.0


def test_blocking_budget_stays_under_the_clients_limit():
    sixty = clients.profile_for("codex")
    assert clients.blocking_budget(600, sixty) == 55.0
    assert clients.blocking_budget(20, sixty) == 20
    assert clients.blocking_budget(600, clients.UNKNOWN) == 600


def test_current_reads_the_shared_session():
    info = sessions.SessionInfo(id=99, name="Pip · codex", pid=None, mode="turbo")
    info.client, info.tool_timeout_s = "codex", None
    token = sessions.CURRENT.set(info)
    try:
        assert clients.current().key == "codex"
        info.tool_timeout_s = 120.0
        assert clients.current().tool_timeout_s == 120.0
    finally:
        sessions.CURRENT.reset(token)
