"""Input lock (DOBBY_INPUT_LOCK): the shared rules, the server side, and the X11 shield."""

import os
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from dobby.overlay import NullOverlay, OverlayClient, OverlayHAL, overlay_config
from dobby.overlay import inputlock as il
from dobby.overlay import protocol as p
from dobby.overlay.menu import bar_click, bar_view
from dobby.overlay.style import bar_spec


# ------------------------------------------------------------------ rules
def test_lock_mode_is_opt_in():
    assert il.lock_mode({}) == "off"
    assert il.lock_mode({"DOBBY_INPUT_LOCK": "off"}) == "off"
    assert il.lock_mode({"DOBBY_INPUT_LOCK": "nonsense"}) == "off"
    for on in ("window", "on", "1", "TRUE"):
        assert il.lock_mode({"DOBBY_INPUT_LOCK": on}) == "window"


def test_overlay_config_carries_the_lock():
    assert "input_lock" not in overlay_config({})
    assert overlay_config({"DOBBY_INPUT_LOCK": "window"})["input_lock"] == "window"


def _armed(**kw):
    base = dict(mode="window", working=True, paused=False, takeover=False, confirming=False, has_target=True)
    base.update(kw)
    return il.armed(**base)


def test_armed_only_while_working_on_a_window():
    assert _armed()
    assert not _armed(mode="off")
    assert not _armed(working=False)  # lapses by itself after active_secs
    assert not _armed(has_target=False)


@pytest.mark.parametrize("why", ["paused", "takeover", "confirming", "screen_reader"])
def test_never_armed_while_the_person_is_in_control_or_asked(why):
    assert not _armed(**{why: True})


TARGET = (100, 100, 400, 300)


def test_blocks_clicks_only_inside_dobbys_window():
    assert il.blocks("click", (200, 200), target=TARGET)
    assert il.blocks("scroll", (200, 200), target=TARGET)
    assert il.blocks("drag", (499, 399), target=TARGET)
    assert not il.blocks("click", (50, 50), target=TARGET)  # other apps stay usable
    assert not il.blocks("click", (500, 200), target=TARGET)  # right edge is outside
    assert not il.blocks("click", (200, 200), target=None)
    assert not il.blocks("click", None, target=TARGET)


def test_moves_always_pass():
    assert not il.blocks("move", (200, 200), target=TARGET, target_front=True)


def test_windows_on_top_and_the_bar_stay_clickable():
    above = [(150, 150, 50, 50)]
    bar = [(300, 100, 120, 34)]
    assert not il.blocks("click", (160, 160), target=TARGET, above=above)
    assert il.blocks("click", (260, 260), target=TARGET, above=above)
    assert not il.blocks("click", (310, 110), target=TARGET, own=bar)


def test_keys_blocked_only_while_dobbys_window_is_in_front():
    assert il.blocks("key", None, target=TARGET, target_front=True)
    assert not il.blocks("key", None, target=TARGET, target_front=False)


def test_insist_three_clicks_in_two_seconds():
    ins = il.Insist()
    assert not ins.press(0.0)
    assert not ins.press(0.5)
    assert ins.press(1.0)
    assert not ins.press(1.1)  # counting starts over
    slow = il.Insist()
    assert not slow.press(0.0) and not slow.press(1.5) and not slow.press(3.0)


def test_insist_long_press():
    ins = il.Insist()
    ins.press(0.0)
    assert not ins.held(1.0)
    assert ins.held(1.6)
    ins.press(10.0)
    assert ins.release(11.6)
    ins.press(20.0)
    assert not ins.release(20.2)


def test_frontmost_owner_skips_overlay_and_other_layers():
    windows = [
        {"kCGWindowLayer": 25, "kCGWindowOwnerPID": 1},   # menu bar
        {"kCGWindowLayer": 0, "kCGWindowOwnerPID": 99},   # our own overlay
        {"kCGWindowLayer": 0, "kCGWindowOwnerPID": 7, "kCGWindowAlpha": 0},
        {"kCGWindowLayer": 0, "kCGWindowOwnerPID": 42},
    ]
    assert il.frontmost_owner(windows, skip_pid=99) == 42
    assert il.frontmost_owner([]) is None


# ------------------------------------------------------------------- bar
def _working(locked):
    return bar_view(paused=False, takeover=False, takeover_resume="auto", working=True, turbo=False,
                    current_mode="ready", bar_mode="always", owner=True, locked=locked)


def test_bar_offers_take_over_when_locked():
    assert _working(False)[2] == "Pause"
    view = _working(True)
    assert view[2] == "Take over"
    assert bar_click("working", "primary") == "pause"  # Take over is the existing Pause
    spec = bar_spec(view)
    primary = next(b for b in spec.buttons if b.key == "primary")
    assert primary.title == "Take over" and primary.icon == "hand.raised.fill"


# -------------------------------------------------------------- protocol
def test_input_pass_op():
    assert p.validate({"op": "input.pass", "on": True})
    with pytest.raises(p.ProtocolError):
        p.validate({"op": "input.pass"})


def test_client_sends_input_pass_only_when_the_x11_lock_is_on(monkeypatch):
    sent = []
    client = OverlayClient(config={})
    monkeypatch.setattr(client, "_send", lambda op, **kw: sent.append((op, kw)) or True)
    monkeypatch.setattr(OverlayClient, "active", property(lambda self: True))
    assert not client.lock_on
    client.input_pass(True)
    assert sent == []
    client.config["input_lock"] = "window"
    assert client.lock_on
    monkeypatch.setattr(sys, "platform", "linux")
    client.input_pass(True)
    client.input_pass(False)
    assert [(op, kw["on"], kw["wait"]) for op, kw in sent] == [("input.pass", True, True),
                                                               ("input.pass", False, False)]
    sent.clear()
    monkeypatch.setattr(sys, "platform", "darwin")  # the tap knows Dobby's events
    client.input_pass(True)
    assert sent == []


# ------------------------------------------------------------ OverlayHAL
class LockOverlay(NullOverlay):
    active = True

    def __init__(self, lock_on=True):
        self.lock_on = lock_on
        self.calls = []

    def input_pass(self, on):
        self.calls.append(("pass", on))
        return True

    def track(self, window_id=None, pid=None):
        self.calls.append(("track", window_id))
        return True


class LockHAL:
    def __init__(self, calls, active="w1"):
        self.calls = calls
        self.active = active

    def get_active_window(self):
        return SimpleNamespace(window_id=self.active)

    def focus_window(self, wid):
        self.calls.append(("hal.focus", wid))
        self.active = wid
        return True

    def mouse_click(self, x, y, button="left", click_count=1):
        self.calls.append(("hal.click", x, y))

    def keyboard_type(self, text):
        self.calls.append(("hal.type", text))

    def keyboard_hotkey(self, keys):
        self.calls.append(("hal.hotkey", tuple(keys)))
        self.active = "w2"  # e.g. Cmd+N opened a new window


def _hal(lock_on=True, active="w1"):
    ov = LockOverlay(lock_on)
    hal = OverlayHAL(LockHAL(ov.calls, active), ov)
    return hal, ov.calls


def test_actions_are_wrapped_in_input_pass():
    hal, calls = _hal()
    hal.mouse_click(5, 6)
    i = calls.index(("hal.click", 5, 6))
    assert calls[i - 1] == ("pass", True) and calls[i + 1] == ("pass", False)
    calls.clear()
    hal.keyboard_type("hi")
    i = calls.index(("hal.type", "hi"))
    assert calls[i - 1] == ("pass", True) and calls[i + 1] == ("pass", False)


def test_dobby_takes_its_window_back_before_acting():
    hal, calls = _hal(active="w1")
    hal.focus_window("w1")
    hal.inner.active = "person-app"  # the person clicked into another app meanwhile
    calls.clear()
    hal.keyboard_type("hi")
    assert calls.index(("hal.focus", "w1")) < calls.index(("hal.type", "hi"))


def test_no_refocus_without_the_lock():
    hal, calls = _hal(lock_on=False)
    hal.focus_window("w1")
    hal.inner.active = "person-app"
    calls.clear()
    hal.keyboard_type("hi")
    assert ("hal.focus", "w1") not in calls


def test_keys_that_switch_windows_are_followed():
    hal, calls = _hal()
    hal.focus_window("w1")
    hal.keyboard_hotkey(["cmd", "n"])
    assert ("track", "w2") in calls
    calls.clear()
    hal.keyboard_type("x")
    assert ("hal.focus", "w1") not in calls  # w2 is Dobby's window now


# ------------------------------------------------------- X11 shield (Xvfb)
def _xvfb():
    if not shutil.which("Xvfb"):
        pytest.skip("needs Xvfb")
    pytest.importorskip("Xlib")
    disp = ":%d" % (90 + os.getpid() % 50)
    proc = subprocess.Popen(["Xvfb", disp, "-screen", "0", "800x600x24"], stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    if proc.poll() is not None:
        pytest.skip("Xvfb did not start")
    return proc, disp


def test_x11_shield_blocks_person_passes_dobby_and_dies_with_the_helper():
    from Xlib import X, XK, Xatom, display
    from Xlib.ext import xtest

    from dobby.overlay.x11_lock import X11InputLock

    xvfb, disp = _xvfb()
    try:
        app = display.Display(disp)
        root = app.screen().root
        tgt = root.create_window(100, 100, 300, 200, 0, app.screen().root_depth, X.InputOutput,
                                 event_mask=X.ButtonPressMask | X.KeyPressMask)
        tgt.map()
        tgt.set_input_focus(X.RevertToParent, X.CurrentTime)
        root.change_property(app.intern_atom("_NET_ACTIVE_WINDOW"), Xatom.WINDOW, 32, [tgt.id])
        app.sync()
        seen = []
        lock = X11InputLock(lambda k: seen.append(k), lambda: seen.append("insist"),
                            lambda w: seen.append("hotkey:" + w), display_name=disp)
        drv = display.Display(disp)

        def click(x=200, y=200):
            drv.screen().root.warp_pointer(x, y)
            drv.sync()
            xtest.fake_input(drv, X.ButtonPress, 1)
            xtest.fake_input(drv, X.ButtonRelease, 1)
            drv.sync()
            time.sleep(0.15)
            lock.pump()

        def key(name):
            code = drv.keysym_to_keycode(XK.string_to_keysym(name))
            xtest.fake_input(drv, X.KeyPress, code)
            xtest.fake_input(drv, X.KeyRelease, code)
            drv.sync()
            time.sleep(0.15)
            lock.pump()

        def app_types():
            types = []
            while app.pending_events():
                types.append(app.next_event().type)
            return types

        def app_got(kind):
            return kind in app_types()

        lock.update((100, 100, 300, 200), tgt.id, True)
        assert lock.mapped and lock.keys_grabbed
        click()
        key("a")
        assert not app_got(X.ButtonPress) and seen[-2:] == ["click", "key"]

        lock.set_pass(True)  # Dobby's own input
        assert not lock.mapped and not lock.keys_grabbed
        click()
        key("a")
        got = app_types()
        assert X.ButtonPress in got and X.KeyPress in got
        lock.set_pass(False)
        assert lock.mapped

        lock.insist.reset()
        click()
        click()
        click()  # the third one is a take-over, and is replayed
        assert "insist" in seen and not lock.mapped
        time.sleep(0.1)
        assert app_got(X.ButtonPress)

        lock.update((100, 100, 300, 200), tgt.id, True)
        assert lock.keys_grabbed
        lock.d.close()  # the helper dies with the lock on
        time.sleep(0.2)
        app_got(X.ButtonPress)
        drv.screen().root.warp_pointer(200, 200)
        xtest.fake_input(drv, X.ButtonPress, 1)
        xtest.fake_input(drv, X.ButtonRelease, 1)
        drv.sync()
        time.sleep(0.15)
        assert app_got(X.ButtonPress)  # nothing is left blocking
    finally:
        xvfb.terminate()
