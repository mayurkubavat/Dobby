"""Three levels of asking: turbo, careful, secure; the strictest source wins."""

from __future__ import annotations

import json

from pinlet import mode, policy
from pinlet.overlay.hal import OverlayHAL


def _policy(tmp_path, monkeypatch, data):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("PINLET_POLICY_FILE", str(path))
    policy.reset_cache()


def test_levels_and_strictest():
    assert mode.strictest("turbo", "careful") == "careful"
    assert mode.strictest("secure", None, "careful") == "secure"
    assert mode.strictest(None, "bogus") == "turbo"


def test_careful_from_argv_and_env(monkeypatch):
    policy.reset_cache()
    assert mode.security_mode(env={}, argv=["pinlet", "--careful"]) == "careful"
    assert mode.security_mode(env={"PINLET_MODE": "careful"}, argv=["pinlet"]) == "careful"
    assert mode.asks_risky(env={"PINLET_MODE": "careful"}, argv=["pinlet"])
    assert not mode.is_secure(env={"PINLET_MODE": "careful"}, argv=["pinlet"])
    with mode.session_mode("careful"):
        assert mode.security_mode() == "careful"


def test_a_careful_policy_is_a_floor(tmp_path, monkeypatch):
    _policy(tmp_path, monkeypatch, {"mode": "careful"})
    try:
        assert policy.current().floor == "careful"
        assert not policy.current().forces_secure
        assert mode.security_mode(env={}, argv=["pinlet", "--turbo"]) == "careful"
        assert mode.security_mode(env={}, argv=["pinlet", "--secure"]) == "secure"
        assert policy.current().summary()["mode"] == "careful"
    finally:
        monkeypatch.delenv("PINLET_POLICY_FILE")
        policy.reset_cache()


def test_per_client_choices(tmp_path, monkeypatch):
    monkeypatch.setenv("PINLET_MODES_FILE", str(tmp_path / "modes.json"))
    policy.reset_cache()
    assert mode.mode_for_client("turbo", "cursor-vscode") == "turbo"
    mode.set_client_mode("cursor", "careful")
    assert mode.mode_for_client("turbo", "cursor-vscode") == "careful"
    assert mode.mode_for_client("secure", "cursor-vscode") == "secure"  # never lowered
    assert mode.mode_for_client("turbo", "codex") == "turbo"
    mode.set_client_mode("cursor", None)
    assert mode.client_modes() == {}
    assert oct((tmp_path / "modes.json").stat().st_mode & 0o777) == "0o600"


def test_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PINLET_MODES_FILE", str(tmp_path / "modes.json"))
    policy.reset_cache()
    assert mode.main(["cursor", "careful"]) == 0
    out = capsys.readouterr().out
    assert "cursor" in out and "Ask for risky (careful)" in out
    assert mode.main(["nope", "careful"]) == 2
    assert mode.main(["cursor", "loud"]) == 2
    assert mode.main(["cursor", "default"]) == 0


def test_careful_asks_before_risky_steps_but_not_per_app():
    hal = OverlayHAL(object(), policy=lambda: True, access_policy=lambda: False)
    assert hal.secure and not hal.asks_app_access
    secure = OverlayHAL(object(), policy=lambda: True, access_policy=lambda: True)
    assert secure.asks_app_access
    legacy = OverlayHAL(object(), secure=True)  # no access policy: same as secure
    assert legacy.asks_app_access
