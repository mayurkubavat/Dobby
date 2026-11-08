"""Admin policy (dobby.policy): what an organisation's policy file can lock down."""

import json

import pytest

from dobby import mode, policy
from dobby.overlay import DenyingOverlay, wrap_hal
from dobby.overlay.grants import AppBlocked, AppGrants


def _write(tmp_path, monkeypatch, data, raw=None):
    path = tmp_path / "policy.json"
    path.write_text(raw if raw is not None else json.dumps(data))
    monkeypatch.setenv("DOBBY_POLICY_FILE", str(path))
    policy.reset_cache()
    return path


def _never_asked(app, detail):
    raise AssertionError("the person should not be asked")


# ------------------------------------------------------------ loading
def test_no_policy_leaves_everything_to_the_person():
    p = policy.current()
    assert not p.active and not p.forces_secure
    assert p.tool_allowed("run_terminal_command")
    assert p.summary() == {"active": False}


def test_system_file_wins_over_the_env_override(tmp_path, monkeypatch):
    system = tmp_path / "system.json"
    system.write_text(json.dumps({"mode": "secure"}))
    monkeypatch.setattr(policy, "system_path", lambda *a, **k: system)
    _write(tmp_path, monkeypatch, {"mode": "turbo"})
    assert policy.current().source == str(system)
    assert policy.current().forces_secure


def test_policy_is_reread_when_the_file_changes(tmp_path, monkeypatch):
    import os

    path = _write(tmp_path, monkeypatch, {"tools": {"deny": ["a"]}})
    assert not policy.current().tool_allowed("a")
    path.write_text(json.dumps({"tools": {"deny": ["b"]}}))
    os.utime(path, (1, 1))
    assert policy.current().tool_allowed("a") and not policy.current().tool_allowed("b")


@pytest.mark.parametrize(
    "raw", ["{not json", "[1, 2]", '{"mode": "fast"}', '{"apps": {"Slack": "write"}}']
)
def test_broken_policy_fails_closed(tmp_path, monkeypatch, raw):
    _write(tmp_path, monkeypatch, None, raw=raw)
    p = policy.current()
    assert p.broken
    assert p.forces_secure
    assert not p.tool_allowed("click")
    assert p.tool_allowed("manage_toolsets")  # the AI can still learn what is going on
    assert p.app_tier("anything") == "read"
    assert policy.shell_enabled(p, env={"DOBBY_ALLOW_SHELL": "1"})[0] is False


# ------------------------------------------------------------ mode
def test_secure_policy_overrides_turbo_everywhere(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"mode": "secure"})
    assert mode.security_mode(env={"DOBBY_MODE": "turbo"}, argv=["dobby", "--turbo"]) == mode.SECURE
    with mode.session_mode(mode.TURBO):
        assert mode.is_secure()


def test_secure_policy_ignores_overlay_turbo_and_overlay_off(tmp_path, monkeypatch):
    from dobby.overlay import overlay_config, overlay_enabled

    monkeypatch.setenv("DOBBY_OVERLAY_TURBO", "1")
    assert overlay_config().get("turbo") is True
    _write(tmp_path, monkeypatch, {"mode": "secure"})
    assert "turbo" not in overlay_config()
    assert overlay_enabled({"DOBBY_OVERLAY": "0"}, platform="darwin") is True


# ------------------------------------------------------------ tools
def test_denied_and_allow_listed_tools(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"tools": {"deny": ["run_terminal_command"]}})
    p = policy.current()
    assert not p.tool_allowed("run_terminal_command") and p.tool_allowed("click")
    assert "organisation" in p.tool_refusal("run_terminal_command")

    _write(tmp_path, monkeypatch, {"tools": {"allow": ["take_screenshot", "click"]}})
    p = policy.current()
    assert p.tool_allowed("click") and not p.tool_allowed("type_text")
    assert p.tool_allowed("wait_for_wake")


def test_server_refuses_and_hides_denied_tools(tmp_path, monkeypatch):
    import anyio

    import dobby.server as server

    _write(tmp_path, monkeypatch, {"tools": {"deny": ["list_windows"]}})
    names = {t.name for t in anyio.run(server.mcp_server.list_tools)}
    assert "list_windows" not in names and "manage_toolsets" in names
    with pytest.raises(Exception, match="turned off by your organisation"):
        anyio.run(server.mcp_server.call_tool, "list_windows", {})


# ------------------------------------------------------------ shell
def test_shell_is_off_by_default():
    ok, why = policy.shell_enabled(env={})
    assert not ok and "DOBBY_ALLOW_SHELL" in why
    res = policy.run_command("echo hi", timeout=5)
    assert res["refused"] and not res["success"]


def test_person_opt_in_runs_through_the_shell(shell_on):
    assert policy.check_command("echo a && echo b") == (None, None)
    res = policy.run_command("echo a && echo b", timeout=5)
    assert res["success"] and res["stdout"].split() == ["a", "b"]


def test_policy_can_turn_the_shell_off_despite_opt_in(tmp_path, monkeypatch, shell_on):
    _write(tmp_path, monkeypatch, {"shell": {"enabled": False}})
    ok, why = policy.shell_enabled()
    assert not ok and "policy" in why


def test_allow_list_runs_listed_commands_without_a_shell(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"shell": {"enabled": True, "allow": ["echo *", "git status"]}})
    argv, refused = policy.check_command("echo  'hello world'")
    assert refused is None and argv == ["echo", "hello world"]
    assert policy.run_command("echo $HOME", timeout=5)["refused"]  # shell syntax
    assert policy.run_command("echo a; rm -rf /tmp/x", timeout=5)["refused"]
    assert policy.run_command("git push", timeout=5)["refused"]
    res = policy.run_command("echo listed", timeout=5)
    assert res["success"] and res["stdout"].strip() == "listed"


def test_terminal_tool_follows_the_policy(tmp_path, monkeypatch):
    import dobby.server as server

    assert server.run_terminal_command("echo nope")["refused"]
    _write(tmp_path, monkeypatch, {"shell": {"enabled": True, "allow": ["echo *"]}})
    assert server.run_terminal_command("echo yes")["stdout"].strip() == "yes"


# ------------------------------------------------------------ apps
def test_admin_app_tiers_beat_apps_json_and_the_persons_allow(tmp_path, monkeypatch):
    rules = tmp_path / "apps.json"
    rules.write_text(json.dumps({"apps": {"Slack": "full"}}))
    grants = AppGrants(path=rules, env={})
    _write(tmp_path, monkeypatch, {"apps": {"Slack": "read"}})
    grants.grant("Slack", "full")
    assert grants.tier("slack") == "read"
    with pytest.raises(AppBlocked):
        grants.check("Slack", "click", _never_asked)


def test_admin_app_tiers_apply_with_grants_turned_off(tmp_path, monkeypatch):
    grants = AppGrants(path=tmp_path / "none.json", env={"DOBBY_APP_GRANTS": "off"})
    grants.check("Slack", "type", _never_asked)  # no policy: off means off
    _write(tmp_path, monkeypatch, {"apps": {"Slack": "click"}})
    grants.check("Slack", "click", _never_asked)
    with pytest.raises(AppBlocked):
        grants.check("Slack", "type", _never_asked)


def test_app_default_applies_to_unlisted_apps(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"apps": {"Vivado": "full"}, "app_default": "read"})
    grants = AppGrants(path=tmp_path / "none.json", env={})
    grants.check("Vivado", "type", _never_asked)
    with pytest.raises(AppBlocked):
        grants.check("Notes", "click", _never_asked)


def test_without_an_overlay_the_policy_still_holds_and_questions_are_denied(
    tmp_path, monkeypatch, mock_hal
):
    from dobby.overlay import NullOverlay, OverlayDenied

    assert wrap_hal(mock_hal, overlay=NullOverlay()) is mock_hal  # no policy: unchanged
    _write(tmp_path, monkeypatch, {"mode": "secure"})
    wrapped = wrap_hal(mock_hal, overlay=NullOverlay())
    assert isinstance(wrapped.overlay, DenyingOverlay)
    with pytest.raises((OverlayDenied, Exception)):
        wrapped.keyboard_hotkey(["cmd", "q"])
    assert ["cmd", "q"] not in mock_hal.hotkeys


# ------------------------------------------------------------ bundled model
def test_tampered_bundled_model_is_refused(tmp_path, monkeypatch):
    from dobby.vdom import fast_detector as fd

    assert fd.bundled_model_ok(fd.BUNDLED_MODEL_PATH)
    fake = tmp_path / "ui_detector_fast.onnx"
    fake.write_bytes(b"not the shipped model")
    assert fd.bundled_model_ok(str(fake))  # a model the person chose
    monkeypatch.setattr(fd, "BUNDLED_MODEL_PATH", str(fake))
    assert not fd.bundled_model_ok(str(fake))
