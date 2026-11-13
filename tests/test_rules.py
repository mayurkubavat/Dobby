"""Saved "don't ask again" rules: scope, lifetime, signature, ceiling, revoke."""

from __future__ import annotations

import json
import threading
import time

import pytest

from pinlet.hub import control, prompts
from pinlet.overlay import rules
from pinlet.overlay.client import NullOverlay, OverlayDenied
from pinlet.overlay.hal import OverlayHAL

KEY = bytes(range(32))
NOW = 1_800_000_000.0


@pytest.fixture
def book(tmp_path):
    b = rules.reset_for_tests(path=tmp_path / "rules.json", env={"PINLET_APP_OWNED": "1"})
    b.set_key(KEY)
    yield b
    rules.reset_for_tests(path=tmp_path / "unused.json", env={})


@pytest.mark.parametrize(
    ("label", "kind"),
    [("Send", "send"), ("Reply All", "send"), ("Publish", "send"), ("Delete…", "delete"),
     ("Move to Trash", "delete"), ("Uninstall", "install"), ("Sign Out", "account"), ("Frobnicate", "other")],
)  # fmt: skip
def test_kind_of_a_button(label, kind):
    assert rules.kind_of(label) == kind


def test_kind_of_a_shortcut():
    assert rules.kind_of_hotkey("quit the app") == "quit"
    assert rules.kind_of_hotkey("close all windows") == "close"
    assert rules.kind_of_hotkey("empty the Trash") == "delete"
    assert rules.kind_of_hotkey("log out") == "account"
    assert rules.kind_of_hotkey("force quit") == "quit"


def test_a_rule_covers_only_its_step_app_and_ai(book):
    rule = book.add("send", "Mail", "claude", rules.HOUR, now=NOW)
    assert rule and rule.describe() == "Send in Mail"
    assert book.match("send", "mail.app", "claude", 1, now=NOW + 60)
    assert book.match("delete", "Mail", "claude", 1, now=NOW + 60) is None
    assert book.match("send", "Slack", "claude", 1, now=NOW + 60) is None
    assert book.match("send", "Mail", "cursor", 1, now=NOW + 60) is None
    assert book.match("send", "Mail", "claude", 1, now=NOW + 3601) is None  # the hour is up


def test_lifetimes(book):
    assert rules.expiry(rules.TASK, NOW) is None
    assert rules.expiry(rules.HOUR, NOW) == NOW + 3600
    day = rules.expiry(rules.DAY, NOW)
    assert NOW < day <= NOW + 86400 and time.localtime(day)[3:6] == (0, 0, 0)
    assert rules.expiry(rules.ALWAYS, NOW, {}) == NOW + 30 * 86400  # asks again in a month
    assert rules.expiry(rules.ALWAYS, NOW, {"PINLET_ALWAYS_DAYS": "7"}) == NOW + 7 * 86400
    always = book.add("delete", "Finder", "claude", rules.ALWAYS, now=NOW)
    assert book.match("delete", "Finder", "claude", 1, now=NOW + 29 * 86400)
    assert book.match("delete", "Finder", "claude", 1, now=NOW + 31 * 86400) is None
    assert always.id not in [r["id"] for r in book.listing(now=NOW + 31 * 86400)]


def test_task_rules_belong_to_one_session_and_end_with_stop(book):
    book.add("send", "Mail", "claude", rules.TASK, pid=41)
    assert book.match("send", "Mail", "claude", 41)
    assert book.match("send", "Mail", "claude", 42) is None  # another session of the same AI
    assert not book.path.exists()  # never written down
    assert book.end_task(41) == 1
    assert book.match("send", "Mail", "claude", 41) is None


def test_run_this_task_without_asking_covers_every_risky_step_of_that_ai(book):
    book.add(rules.ANY, rules.ANY, "claude", rules.TASK, pid=41)
    assert book.match("send", "Mail", "claude", 41)
    assert book.match("quit", "Xcode", "claude", 41)
    assert book.match("send", "Mail", "cursor", 41) is None
    assert book.match("send", "Mail", "claude", 7) is None


def test_the_narrowest_rule_is_used_first(book):
    wide = book.add(rules.ANY, rules.ANY, "claude", rules.TASK, pid=41)
    narrow = book.add("send", "Mail", "claude", rules.TASK, pid=41)
    assert book.match("send", "Mail", "claude", 41).id == narrow.id
    assert book.match("delete", "Mail", "claude", 41).id == wide.id


def test_an_edited_file_gives_nothing(book, tmp_path):
    rule = book.add("send", "Mail", "claude", rules.ALWAYS, now=NOW)
    data = json.loads(book.path.read_text())
    assert data["rules"][0]["sig"] == rule.sig and book.path.stat().st_mode & 0o777 == 0o600
    forged = dict(data["rules"][0], id="aaaaaaaaaaaa", kind="*", app="*", agent="*")
    widened = dict(data["rules"][0], app="*")
    longer = dict(data["rules"][0], expires=NOW + 9e8)
    for bad in (forged, widened, longer, dict(forged, sig="")):
        book.path.write_text(json.dumps({"rules": [bad]}))
        fresh = rules.RuleBook(path=book.path, env={"PINLET_APP_OWNED": "1"})
        fresh.set_key(KEY)
        assert fresh.match("send", "Mail", "claude", 1, now=NOW + 5) is None
        assert fresh.listing(now=NOW + 5) == []
    # The real rule, with another key (or none yet): not honoured either.
    book.path.write_text(json.dumps(data))
    other = rules.RuleBook(path=book.path, env={"PINLET_APP_OWNED": "1"})
    assert other.match("send", "Mail", "claude", 1, now=NOW + 5) is None and not other.can_save
    assert other.add("send", "Mail", "claude", rules.ALWAYS) is None  # nothing to sign with
    assert (
        other.add("send", "Mail", "claude", rules.TASK, pid=3) is not None
    )  # task rules need no key
    other.set_key(bytes(32))
    assert other.match("send", "Mail", "claude", 1, now=NOW + 5) is None
    other.set_key(KEY)
    assert other.match("send", "Mail", "claude", 1, now=NOW + 5)


def test_without_the_app_the_key_is_a_private_file(tmp_path):
    b = rules.RuleBook(path=tmp_path / "rules.json", env={})
    assert b.add("send", "Mail", "local", rules.DAY, now=NOW)
    key_file = tmp_path / "rules.key"
    assert (
        key_file.stat().st_mode & 0o777 == 0o600 and len(bytes.fromhex(key_file.read_text())) == 32
    )
    again = rules.RuleBook(path=tmp_path / "rules.json", env={})
    assert again.match("send", "Mail", "local", 1, now=NOW + 5)


def test_daily_ceiling(tmp_path):
    b = rules.RuleBook(path=tmp_path / "rules.json", env={"PINLET_RULE_CEILING": "3"})
    b.add("send", "Mail", "claude", rules.TASK, pid=1)
    assert [bool(b.match("send", "Mail", "claude", 1, now=NOW)) for _ in range(5)] == [True] * 3 + [
        False
    ] * 2
    assert b.listing(now=NOW)[0]["uses_today"] == 3
    assert b.match("send", "Mail", "claude", 1, now=NOW + 86400)  # a new day
    assert rules.ceiling({}) == 50 and rules.ceiling({"PINLET_RULE_CEILING": "x"}) == 50


def test_revoke(book):
    a = book.add("send", "Mail", "claude", rules.ALWAYS)
    b = book.add("delete", "Finder", "claude", rules.TASK, pid=4)
    assert [r["what"] for r in book.listing()] == ["Delete in Finder", "Send in Mail"]
    assert book.revoke(a.id) and not book.revoke(a.id)
    assert book.match("send", "Mail", "claude", 4) is None
    assert json.loads(book.path.read_text()) == {"rules": []}
    assert book.revoke(b.id) and book.listing() == []
    book.add("send", "Mail", "claude", rules.ALWAYS)
    book.add("send", "Mail", "claude", rules.TASK, pid=4)
    assert book.revoke_all() == 2 and book.listing() == []


# ----------------------------------------------------------- the whole path
class _Desk:
    def __init__(self):
        self.done = []

    def mouse_click(self, x, y, button="left", click_count=1):
        self.done.append(("click", x, y))

    def keyboard_hotkey(self, keys):
        self.done.append(("hotkey", tuple(keys)))

    def get_active_window(self):
        from types import SimpleNamespace

        return SimpleNamespace(window_id="1", app_name="Mail", is_active=True)


class _AppOverlay(NullOverlay):
    """Sends questions to the app's card, as OverlayClient does while Pinlet.app listens."""

    def __init__(self):
        self.intents = []

    def intent(self, text, ms=None):
        self.intents.append(text)
        return True

    def confirm(self, text, secs=30.0, detail="", allow="Allow", deny="Deny", kind="danger",
                app="", action="", image=""):  # fmt: skip
        return prompts.ask(
            text, secs=3, detail=detail, kind=kind, app=app, action=action, image=image
        )


def _answer_next(**answer):
    """Answer the next question as Pinlet.app would, from another thread."""
    seen = {}

    def run():
        for _ in range(200):
            waiting = prompts.pending()
            if waiting:
                seen.update(waiting[0])
                control.handle({"id": 1, "op": "answer", "prompt_id": waiting[0]["id"], **answer})
                return
            time.sleep(0.01)

    t = threading.Thread(target=run)
    t.start()
    return t, seen


def test_tick_dont_ask_again_then_it_does_not_ask(book, monkeypatch):
    monkeypatch.setenv("PINLET_PROMPT_HOLD", "0")
    hit = lambda x, y: {"role": "button", "name": "Send", "secure": False}
    desk, ov = _Desk(), _AppOverlay()
    hal = OverlayHAL(desk, overlay=ov, hit_test=hit, secure=True)
    t, seen = _answer_next(ok=True, remember="hour")
    hal.mouse_click(10, 10)
    t.join(3)
    assert seen["action"] == "send" and seen["action_label"] == "Send" and seen["app"] == "Mail"
    assert seen["can_save"] is True
    assert desk.done == [("click", 10, 10)]
    (rule,) = book.listing()
    assert (rule["what"], rule["scope"]) == ("Send in Mail", "hour")
    hal.mouse_click(10, 10)  # no question this time: nobody is there to answer one
    assert desk.done == [("click", 10, 10)] * 2
    assert "Allowed by your rule: Send in Mail" in ov.intents
    assert prompts.pending() == []
    # Revoked from the Permissions list: it asks again, and nobody answers.
    assert control.handle({"id": 2, "op": "revoke", "rule": rule["id"]})["revoked"] == 1
    with pytest.raises(OverlayDenied):
        hal.mouse_click(10, 10)


def test_run_this_task_without_asking(book, monkeypatch):
    monkeypatch.setenv("PINLET_PROMPT_HOLD", "0")
    hit = lambda x, y: {"role": "button", "name": "Delete", "secure": False}
    desk, ov = _Desk(), _AppOverlay()
    hal = OverlayHAL(desk, overlay=ov, hit_test=hit, secure=True)
    t, _seen = _answer_next(ok=True, wide=True, remember="always")  # wide is always "this task"
    hal.mouse_click(10, 10)
    t.join(3)
    (rule,) = book.listing()
    assert (rule["what"], rule["scope"], rule["kind"]) == ("Every risky step", "task", "*")
    hal.keyboard_hotkey(["cmd", "q"])  # another kind of risky step: covered
    assert desk.done[-1] == ("hotkey", ("cmd", "q"))
    # Stop ends it.
    book.end_task(rule["pid"])
    with pytest.raises(OverlayDenied):
        hal.keyboard_hotkey(["cmd", "q"])


def test_a_deny_or_a_plain_allow_saves_nothing(book, monkeypatch):
    monkeypatch.setenv("PINLET_PROMPT_HOLD", "0")
    hit = lambda x, y: {"role": "button", "name": "Send", "secure": False}
    hal = OverlayHAL(_Desk(), overlay=_AppOverlay(), hit_test=hit, secure=True)
    t, _ = _answer_next(ok=False, remember="always")
    with pytest.raises(OverlayDenied):
        hal.mouse_click(10, 10)
    t.join(3)
    t, _ = _answer_next(ok=True)
    hal.mouse_click(10, 10)
    t.join(3)
    t, _ = _answer_next(ok=True, remember="forever")  # not a lifetime Pinlet knows
    hal.mouse_click(10, 10)
    t.join(3)
    assert book.listing() == []


def test_only_the_app_hands_over_the_key_and_lists_are_open(book):
    refused = control.handle({"id": 1, "op": "rules_key", "key": "00" * 32}, trusted=False)
    assert refused["error"] == control.NOT_THE_APP
    assert control.handle({"id": 2, "op": "rules_key", "key": "ab" * 32})["can_save"] is True
    assert control.handle({"id": 3, "op": "rules_key", "key": "zz"})["can_save"] is False
    assert control.handle({"id": 4, "op": "rules"}, trusted=False)["rules"] == []


def test_always_allowed_apps_are_listed_and_revoked(tmp_path, monkeypatch, book):
    from pinlet.overlay import grants

    path = tmp_path / "apps.json"
    path.write_text(json.dumps({"apps": {"Terminal": "click"}}))
    monkeypatch.setattr(grants, "DEFAULT_PATH", path)
    grants.remember("Vivado")
    (row,) = control.handle({"id": 1, "op": "rules"})["apps"]
    assert row["app"] == "Vivado" and row["expires"] == pytest.approx(
        time.time() + 30 * 86400, abs=10
    )
    assert control.handle({"id": 2, "op": "revoke", "app": "vivado"})["revoked"] == 1
    assert json.loads(path.read_text()) == {"apps": {"Terminal": "click"}, "remembered": {}}
    assert (
        control.handle({"id": 3, "op": "revoke", "app": "Terminal"})["revoked"] == 0
    )  # by hand: kept
    grants.remember("Vivado")
    book.add("send", "Mail", "claude", rules.TASK, pid=1)
    assert control.handle({"id": 4, "op": "revoke", "all": True})["revoked"] == 2


def test_return_in_a_message_box_asks_like_send(book, monkeypatch):
    from pinlet.overlay.hal import return_sends

    assert return_sends("Return", {"name": "Message #general"}) == "Message #general"
    assert return_sends("enter", {"name": "Reply to Sam"})
    assert return_sends("return", {"name": "Search"}) is None
    assert return_sends("return", {"name": "File name"}) is None
    assert return_sends("tab", {"name": "Message"}) is None
    assert return_sends("return", None) is None

    monkeypatch.setenv("PINLET_PROMPT_HOLD", "0")
    hit = lambda x, y: {"role": "text field", "name": "Message Sam", "secure": False}
    desk, ov = _Desk(), _AppOverlay()
    desk.keyboard_press_key = lambda key: desk.done.append(("key", key))
    hal = OverlayHAL(desk, overlay=ov, hit_test=hit, secure=True)
    hal.mouse_click(10, 10)  # into the message box: no question
    hal.keyboard_press_key("tab")  # not Return: no question
    t, seen = _answer_next(ok=True, remember="task")
    hal.keyboard_press_key("Return")
    t.join(3)
    assert seen["action"] == "send" and "Return" in seen["text"]
    hal.keyboard_press_key("Return")  # covered by the tick now
    assert desk.done == [("click", 10, 10), ("key", "tab"), ("key", "Return"), ("key", "Return")]
    # Turbo asks nothing.
    desk2 = _Desk()
    desk2.keyboard_press_key = lambda key: desk2.done.append(("key", key))
    fast = OverlayHAL(desk2, overlay=_AppOverlay(), hit_test=hit, secure=False)
    fast.mouse_click(10, 10)
    fast.keyboard_press_key("Return")
    assert desk2.done[-1] == ("key", "Return")
