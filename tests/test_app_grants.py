"""Per-app grants, consequential-click prompts and "what and to what" bar text."""

import json
from types import SimpleNamespace

import pytest

from dobby import handoff
from dobby.overlay import protocol as p
from dobby.overlay import style
from dobby.overlay.client import NullOverlay, OverlayBlocked, OverlayDenied
from dobby.overlay.grants import CLICK, FULL, READ, AppBlocked, AppGrants, access_detail, allows, normalize
from dobby.overlay.hal import OverlayHAL, consequential_label


# ------------------------------------------------------------------ grants
def test_tiers_allow_what_they_say():
    assert allows(FULL, "type") and allows(FULL, "click")
    assert allows(CLICK, "click") and allows(CLICK, "scroll")
    assert not allows(CLICK, "type") and not allows(CLICK, "keys") and not allows(CLICK, "drag")
    assert not allows(READ, "click")


def test_names_match_without_case_or_endings():
    assert normalize("Vivado.app") == normalize("vivado") == normalize("VIVADO-bin")
    assert normalize("gnome-terminal-server") == "gnome-terminal"


def test_asks_once_then_remembers(tmp_path):
    g = AppGrants(path=tmp_path / "apps.json", env={})
    asked = []
    g.check("Vivado", "click", lambda app, detail: asked.append(app) or True)
    g.check("Vivado", "type", lambda app, detail: asked.append(app) or True)
    assert asked == ["Vivado"]
    assert g.tier("vivado") == FULL


def test_deny_sticks_without_asking_again(tmp_path):
    g = AppGrants(path=tmp_path / "apps.json", env={})
    asked = []
    with pytest.raises(AppBlocked):
        g.check("Mail", "click", lambda app, detail: asked.append(app) or False)
    with pytest.raises(AppBlocked) as err:
        g.check("Mail", "click", lambda app, detail: asked.append(app) or True)
    assert asked == ["Mail"]
    assert "said no" in str(err.value)


def test_standing_rules_from_apps_json(tmp_path):
    path = tmp_path / "apps.json"
    path.write_text(json.dumps({"apps": {"Terminal": "click", "Vivado": "full"}, "default": "ask"}))
    g = AppGrants(path=path, env={})
    never = lambda app, detail: pytest.fail("must not ask")  # noqa: E731
    g.check("Vivado", "type", never)
    g.check("Terminal", "click", never)
    with pytest.raises(AppBlocked) as err:
        g.check("Terminal", "type", never)
    assert "only click and scroll" in str(err.value)


def test_default_full_means_never_ask(tmp_path):
    path = tmp_path / "apps.json"
    path.write_text(json.dumps({"default": "full"}))
    AppGrants(path=path, env={}).check("Anything", "type", lambda a, d: pytest.fail("asked"))


def test_password_managers_start_read_only(tmp_path):
    g = AppGrants(path=tmp_path / "apps.json", env={})
    with pytest.raises(AppBlocked) as err:
        g.check("1Password", "click", lambda a, d: True)
    assert err.value.tier == READ


def test_file_changes_are_picked_up(tmp_path):
    import os
    import time

    path = tmp_path / "apps.json"
    path.write_text(json.dumps({"apps": {"Slack": "read"}}))
    g = AppGrants(path=path, env={})
    assert g.tier("Slack") == READ
    path.write_text(json.dumps({"apps": {"Slack": "full"}}))
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert g.tier("Slack") == FULL


def test_off_switch_and_unknown_apps(tmp_path):
    AppGrants(path=tmp_path / "x", env={"DOBBY_APP_GRANTS": "off"}).check("Mail", "type", lambda a, d: False)
    AppGrants(path=tmp_path / "x", env={}).check(None, "type", lambda a, d: False)


def test_terminal_prompt_warns_that_typing_runs_commands():
    assert "runs commands" in access_detail("gnome-terminal-server")
    assert "runs commands" not in access_detail("Vivado")


# ------------------------------------------------------------------ labels
def test_action_text_names_the_element_and_app():
    assert style.action_text("Click", "Run Synthesis", "vivado") == "Click ‘Run Synthesis’ in Vivado"
    assert style.action_text("Click", None, None) == "Click"
    assert style.app_label("com.apple.mail") == "Mail"
    assert style.app_label("Google Chrome") == "Google Chrome"


@pytest.mark.parametrize("role,name,risky", [
    ("button", "Send", True), ("push button", "Delete file", True), ("menu item", "Move to Trash", True),
    ("button", "Pay now", True), ("link", "Sign out", True), ("button", "Sender", False),
    ("button", "Run Synthesis", False), ("text", "Delete", False), ("button", "", False),
])
def test_consequential_labels(role, name, risky):
    assert (consequential_label(role, name) is not None) is risky


# ------------------------------------------------------------------ protocol
def test_confirm_carries_detail_labels_and_kind():
    p.validate({"op": "confirm", "text": "Let Dobby use Vivado?", "detail": "…", "allow": "Allow",
                "deny": "Don’t Allow", "kind": "access"})
    with pytest.raises(p.ProtocolError):
        p.validate({"op": "confirm", "text": "x", "kind": "maybe"})
    p.validate({"op": "cursor.move", "x": 1, "y": 2, "verb": "Click", "probe": True, "app": "Vivado"})


def test_access_prompt_is_calm_not_red():
    spec = style.confirm_spec("Let Dobby use Vivado?", allow="Allow", deny="Don’t Allow", kind="access",
                              detail="why")
    assert spec.color == "ok" and spec.tip == "why"
    assert [b.title for b in spec.buttons] == ["Allow", "Don’t Allow"]
    assert style.confirm_spec("Quit?").color == "danger"


# ------------------------------------------------------------------ HAL
class Overlay(NullOverlay):
    active = True

    def __init__(self, answers=()):
        self.answers = list(answers)
        self.calls = []

    def confirm(self, text, secs=30.0, detail="", allow="Allow", deny="Deny", kind="danger"):
        self.calls.append(("confirm", text, kind))
        return self.answers.pop(0) if self.answers else True

    def move(self, x, y, ms=None, wait=True, verb=None, probe=False, app=None):
        self.calls.append(("move", verb, app))
        return True

    def intent(self, text, ms=None):
        self.calls.append(("intent", text))
        return True


class Desk:
    """Two windows: Vivado on the left, Mail on the right; Vivado is active."""

    def __init__(self):
        self.done = []

    def list_windows(self):
        box = lambda x: SimpleNamespace(x=x, y=0, width=100, height=100)  # noqa: E731
        return [SimpleNamespace(title="Vivado", app_name="vivado", bbox=box(0)),
                SimpleNamespace(title="Inbox", app_name="Mail", bbox=box(100))]

    def get_active_window(self):
        return SimpleNamespace(window_id="1", app_name="vivado")

    def mouse_click(self, x, y, button="left", click_count=1):
        self.done.append(("click", x, y))

    def keyboard_type(self, text, delay_ms=12.0):
        self.done.append(("type", text))

    def keyboard_hotkey(self, keys):
        self.done.append(("hotkey", tuple(keys)))


def _hal(tmp_path, answers=(), hit=None):
    ov = Overlay(answers)
    desk = Desk()
    grants = AppGrants(path=tmp_path / "apps.json", env={})
    return OverlayHAL(desk, ov, grants=grants, hit_test=hit, secure=True), ov, desk


def test_first_click_in_an_app_asks_then_acts(tmp_path):
    hal, ov, desk = _hal(tmp_path)
    hal.mouse_click(10, 10)
    hal.mouse_click(20, 20)
    asks = [c for c in ov.calls if c[0] == "confirm"]
    assert asks == [("confirm", "Let Dobby use Vivado?", "access")]
    assert desk.done == [("click", 10, 10), ("click", 20, 20)]
    assert ("move", "Click", "vivado") in ov.calls


def test_app_is_the_window_under_the_point(tmp_path):
    hal, ov, desk = _hal(tmp_path, answers=[False])
    with pytest.raises(OverlayBlocked) as err:
        hal.mouse_click(150, 10)
    assert "Mail" in str(err.value)
    assert desk.done == []
    result = handoff.classify(err.value)
    assert result["handoff"] == "blocked" and result["retry_ok"] is False


def test_click_only_app_refuses_typing_without_asking(tmp_path):
    (tmp_path / "apps.json").write_text(json.dumps({"apps": {"vivado": "click"}}))
    hal, ov, desk = _hal(tmp_path)
    hal.mouse_click(10, 10)
    with pytest.raises(OverlayBlocked):
        hal.keyboard_type("rm -rf /")
    assert not [c for c in ov.calls if c[0] == "confirm"]
    assert desk.done == [("click", 10, 10)]


def test_send_button_asks_allow_deny(tmp_path):
    (tmp_path / "apps.json").write_text(json.dumps({"default": "full"}))
    hit = lambda x, y: {"role": "button", "name": "Send", "secure": False}  # noqa: E731
    hal, ov, desk = _hal(tmp_path, answers=[False], hit=hit)
    with pytest.raises(OverlayDenied):
        hal.mouse_click(10, 10)
    assert ("confirm", "Allow Dobby to click ‘Send’ in Vivado?", "danger") in ov.calls
    assert desk.done == []


def test_ordinary_button_does_not_ask(tmp_path):
    (tmp_path / "apps.json").write_text(json.dumps({"default": "full"}))
    hit = lambda x, y: {"role": "button", "name": "Run Synthesis", "secure": False}  # noqa: E731
    hal, ov, desk = _hal(tmp_path, hit=hit)
    hal.mouse_click(10, 10)
    assert not [c for c in ov.calls if c[0] == "confirm"]


def test_password_field_is_handed_to_the_person(tmp_path):
    (tmp_path / "apps.json").write_text(json.dumps({"default": "full"}))
    hit = lambda x, y: {"role": "password text", "name": "Password", "secure": True}  # noqa: E731
    hal, ov, desk = _hal(tmp_path, hit=hit)
    hal.mouse_click(10, 10)
    with pytest.raises(OverlayBlocked) as err:
        hal.keyboard_type("hunter2")
    assert "password" in str(err.value).lower()
    assert ("type", "hunter2") not in desk.done
    assert ("intent", "Your turn: type the password") in ov.calls


def test_typing_names_the_field(tmp_path):
    (tmp_path / "apps.json").write_text(json.dumps({"default": "full"}))
    hit = lambda x, y: {"role": "text", "name": "Search", "secure": False}  # noqa: E731
    hal, ov, desk = _hal(tmp_path, hit=hit)
    hal.mouse_click(10, 10)
    hal.keyboard_type("clk_div")
    assert ("intent", "Type “clk_div” into ‘Search’") in ov.calls


def test_no_grants_means_no_prompts(tmp_path):
    ov, desk = Overlay(), Desk()
    OverlayHAL(desk, ov).mouse_click(150, 10)
    assert desk.done == [("click", 150, 10)]
    assert not [c for c in ov.calls if c[0] == "confirm"]


def test_server_sends_instructions_with_every_handoff_kind():
    from dobby.server import mcp_server

    text = mcp_server.instructions or ""
    for kind in ("stopped", "denied", "blocked", "interrupted", "paused", "wait_for_wake"):
        assert kind in text


# ------------------------------------------------------------------ mode
def test_turbo_is_the_default_and_secure_is_opt_in():
    from dobby.mode import security_mode

    assert security_mode(env={}, argv=["dobby"]) == "turbo"
    assert security_mode(env={}, argv=["dobby", "--secure"]) == "secure"
    assert security_mode(env={"DOBBY_MODE": "secure"}, argv=["dobby"]) == "secure"
    assert security_mode(env={"DOBBY_SECURE": "1"}, argv=["dobby"]) == "secure"


def test_overlay_config_follows_the_mode():
    from dobby.overlay import overlay_config

    assert overlay_config({}).get("turbo") is True
    assert "turbo" not in overlay_config({"DOBBY_MODE": "secure"})
    assert "turbo" not in overlay_config({"DOBBY_OVERLAY_TURBO": "0"})


def test_turbo_hal_never_prompts(tmp_path):
    hit = lambda x, y: {"role": "button", "name": "Send", "secure": False}  # noqa: E731
    ov, desk = Overlay(answers=[False]), Desk()
    OverlayHAL(desk, ov, hit_test=hit).mouse_click(150, 10)
    assert desk.done == [("click", 150, 10)]
    assert not [c for c in ov.calls if c[0] == "confirm"]


def test_wrap_hal_turns_prompts_on_only_in_secure(monkeypatch):
    import dobby.overlay as overlay_pkg

    class Live(Overlay):
        pass

    monkeypatch.setattr("sys.argv", ["dobby"])
    monkeypatch.delenv("DOBBY_MODE", raising=False)
    monkeypatch.delenv("DOBBY_SECURE", raising=False)
    hal = overlay_pkg.wrap_hal(Desk(), Live())
    assert not hal.secure and not hal._danger_prompts()
    monkeypatch.setenv("DOBBY_MODE", "secure")
    assert hal.secure and hal._danger_prompts()  # read per call, not fixed at start


def test_one_shared_dobby_serves_turbo_and_secure_clients(tmp_path):
    from dobby.mode import session_mode
    from dobby.mode import is_secure

    hit = lambda x, y: {"role": "button", "name": "Send", "secure": False}  # noqa: E731

    class Turbo(Overlay):
        def set_turbo(self, on):
            self.calls.append(("turbo", on))
            return True

    ov, desk = Turbo(answers=[False]), Desk()
    hal = OverlayHAL(desk, ov, hit_test=hit, grants=AppGrants(path=tmp_path / "a.json", env={}),
                     policy=is_secure)
    with session_mode("turbo"):
        hal.mouse_click(10, 10)  # no questions
    assert desk.done == [("click", 10, 10)]
    with session_mode("secure"):
        with pytest.raises(OverlayBlocked):  # "Let Dobby use Vivado?" -> Don't Allow
            hal.mouse_click(10, 10)
    assert [c for c in ov.calls if c[0] == "turbo"] == [("turbo", True), ("turbo", False)]
