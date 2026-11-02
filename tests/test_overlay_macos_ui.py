"""Native checks of the overlay helper's controls on macOS (skipped elsewhere).

Builds a real OverlayController (windows are created but no event loop runs)
and drives the control bar, its ▾ menu button, the optional clickable name
tag and Turbo directly.
"""

from __future__ import annotations

import io
import json
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="needs macOS AppKit")


@pytest.fixture
def make(tmp_path):
    AppKit = pytest.importorskip("AppKit")
    AppKit.NSApplication.sharedApplication()
    from dobby.overlay import mac_helper

    made = []

    def _make(**cfg):
        out = io.StringIO()
        # The older tests below check the classic bar; modern tests ask for it.
        base = {"bar_lock": str(tmp_path / "bar.lock"), "style": "classic"}
        base.update(cfg)
        c = mac_helper.OverlayController(base, out=out, interactive=False)
        c._claim_bar()
        made.append(c)
        return c, out

    yield _make
    for c in made:
        for p in c.panels:
            p.close()
        if c.hotspot is not None:
            c.hotspot.close()
        if c.pill is not None:
            c.pill.close()
        if c.status_item is not None:
            AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(c.status_item)
        c.bar_owner.release()


def _lines(out):
    return [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]


def _events(out):
    return [m["event"] for m in _lines(out) if "event" in m]


# ------------------------------------------------------------ control bar
def test_bar_shows_ready_at_start_and_stays(make):
    c, _ = make()
    assert c.pill.mode == "ready" and c.pill.visible
    assert c.pill.window.isVisible()
    c.last_action = time.monotonic() - 3600  # long idle
    c.on_tick()
    assert c.pill.mode == "ready" and c.pill.window.isVisible()


def test_bar_hidden_from_captures_by_default(make):
    from AppKit import NSWindowSharingNone

    c, _ = make()
    assert c.pill.window.sharingType() == NSWindowSharingNone
    # Dobby's own capture handshake no longer blinks the bar
    c.capture_hidden = True
    c._apply_alpha()
    assert c.pill.window.alphaValue() == 1.0


def test_bar_hide_and_show_again(make):
    c, _ = make()
    assert c.pill.label.stringValue() == "Dobby is sleeping"
    c.set_pill_hidden(True)
    assert not c.pill.window.isVisible()
    c.set_pill_hidden(False)
    assert c.pill.window.isVisible()


def test_hidden_bar_comes_back_when_dobby_wakes_up(make):
    c, _ = make()
    c.last_action = time.monotonic() - 3600  # sleeping
    c.set_pill_hidden(True)
    c._mark_active()  # Dobby starts working
    assert c.pill.window.isVisible() and c.pill.mode == "working"


def test_bar_hidden_while_working_stays_hidden_until_next_wake(make):
    c, _ = make()
    c._mark_active()
    c.set_pill_hidden(True)
    c._mark_active()  # still the same burst of work
    assert not c.pill.window.isVisible()
    c.last_action = time.monotonic() - 3600
    c.on_tick()  # falls asleep, still hidden
    assert not c.pill.window.isVisible()
    c._mark_active()  # wakes up
    assert c.pill.window.isVisible()


def test_allow_deny_shows_even_when_bar_hidden(make):
    c, out = make()
    c.set_pill_hidden(True)
    c._start_confirm(7, "Allow Dobby to quit?", 5)
    assert c.pill.mode == "confirm" and c.pill.window.isVisible()
    assert c.pill.hide_btn.isHidden()  # can't hide a pending question
    c.on_pill("secondary")
    assert {"ack": 7, "ok": False} in _lines(out)
    assert not c.pill.window.isVisible()


def test_second_helper_does_not_stack_an_idle_bar(make):
    a, _ = make()
    b, _ = make()
    assert a.bar_owner.owned and not b.bar_owner.owned
    assert a.pill.window.isVisible() and not b.pill.window.isVisible()
    b.last_action = time.monotonic()
    b._refresh_pill()
    assert b.pill.window.isVisible()  # but it shows while working
    a.bar_owner.release()
    b._claim_bar()
    assert b.bar_owner.owned


def test_bar_fades_instead_of_vanishing_near_dobbys_pointer(make):
    c, _ = make()
    c.pill.duck(True)
    assert c.pill.window.isVisible()
    assert c.pill.window.ignoresMouseEvents()
    assert c.pill.view.alphaValue() < 0.5
    c.pill.duck(False)
    assert not c.pill.window.ignoresMouseEvents() and c.pill.view.alphaValue() == 1.0


def test_pause_resume_and_stop_from_bar(make):
    c, out = make()
    c.on_pill("primary")  # ready -> pause
    assert c.paused and c.pill.mode == "paused"
    c.on_pill("primary")  # resume
    assert not c.paused
    c.on_pill("secondary")  # stop
    assert c.pill.mode == "stopped"
    assert _events(out)[:3] == ["pause", "resume", "stop"]
    c.stopped_until = 0
    c.last_action = 0
    c.on_tick()
    assert c.pill.mode == "ready"


def test_turbo_auto_allows_and_labels_bar(make):
    c, out = make(turbo=True)
    c._start_confirm(3, "Allow Dobby to quit?", 5)
    assert {"ack": 3, "ok": True} in _lines(out)
    assert c.pill.mode != "confirm"
    assert "Turbo" in c.pill.label.stringValue()


def test_bar_off_and_auto_modes(make):
    off, _ = make(pill="off")
    assert off.pill is None
    auto, _ = make(pill="auto")
    assert not auto.pill.window.isVisible()


# ----------------------------------------------------- waking Dobby up
def _asleep(c):
    c.last_action = time.monotonic() - 3600
    c._refresh_pill()
    assert c.pill.mode == "ready"


def test_click_sleeping_bar_wakes_dobby(make):
    c, out = make()
    _asleep(c)
    assert c.pill.view.toolTip() == "Click to wake Dobby"
    w0 = c.pill.window.frame().size.width
    c.on_pill_label()
    assert c.pill.mode == "awake" and c.pill.label.stringValue().startswith("Dobby is awake")
    assert c.pill.view.toolTip() == "Click to put Dobby to sleep"
    assert c.pill.window.frame().size.width > w0
    assert _events(out) == ["wake"]
    assert c.pointer_visible and c.cursor is not None
    assert all(p.pointer.opacity() == 1.0 for p in c.panels)
    assert not c.pill.ducked  # the pointer lands clear of the bar


def test_wake_shows_a_hidden_bar(make):
    c, _ = make()
    _asleep(c)
    c.set_pill_hidden(True)
    c.wake()
    assert c.pill.window.isVisible() and c.pill.mode == "awake"


def test_wake_keeps_an_existing_pointer_where_it_is(make):
    c, _ = make()
    _asleep(c)
    c.cursor = (321.0, 456.0)
    c.wake()
    assert c.cursor == (321.0, 456.0) and c.pointer_visible


def test_awake_falls_back_asleep(make):
    c, out = make()
    _asleep(c)
    c.on_pill_label()
    c.awake_until = time.monotonic() - 1
    c.on_tick()
    assert c.pill.mode == "ready" and _events(out) == ["wake", "sleep"]
    assert not c.pointer_visible and c.cursor is None  # the wake pointer fades out


def test_click_awake_bar_puts_dobby_to_sleep(make):
    c, out = make()
    _asleep(c)
    c.on_pill_label()
    c.on_pill_label()
    assert c.pill.mode == "ready" and _events(out) == ["wake", "sleep"]


def test_work_ends_awake_and_then_dobby_sleeps(make):
    c, out = make()
    _asleep(c)
    c.wake()
    c._mark_active()
    assert c.pill.mode == "working" and c.awake_until == 0
    assert _events(out) == ["wake", "sleep"]
    c.last_action = time.monotonic() - 3600  # the work burst ends
    c.on_tick()
    assert c.pill.mode == "ready"


def test_stop_while_awake_goes_back_to_sleep(make):
    c, _ = make()
    _asleep(c)
    c.wake()
    c.request_stop()
    c.stopped_until = 0
    c.on_tick()
    assert c.pill.mode == "ready"


def test_menu_offers_wake_and_sleep(make):
    c, _ = make()
    _asleep(c)
    def titles():
        menu = c._build_menu("Idle")
        return [menu.itemAtIndex_(i) for i in range(menu.numberOfItems())]
    wake = next(i for i in titles() if i.title() == "Wake Dobby")
    assert wake.target() is c.target and wake.action() == "menuWake:"
    c.target.menuWake_(None)
    assert c.pill.mode == "awake"
    sleep = next(i for i in titles() if i.title() == "Put Dobby to sleep")
    assert sleep.action() == "menuSleep:"
    c.target.menuSleep_(None)
    assert c.pill.mode == "ready"


def test_non_owner_helper_shows_awake(make):
    a, _ = make()
    b, _ = make()
    assert not b.bar_owner.owned and not b.pill.window.isVisible()
    b.wake()
    assert b.pill.mode == "awake" and b.pill.window.isVisible()


# ------------------------------------------ an AI waits in wait_for_wake
def test_ai_waiting_bar_invites_a_click(make):
    c, _ = make()
    _asleep(c)
    c.handle({"op": "sleep.wait", "reason": "finished the report"})
    assert c.pill.mode == "ready" and c.pill.label.stringValue() == "Dobby is sleeping · click to wake"
    assert "finished the report" in c.pill.view.toolTip()
    c.handle({"op": "sleep.done"})
    assert c.pill.label.stringValue() == "Dobby is sleeping"
    assert c.pill.view.toolTip() == "Click to wake Dobby"


def test_label_click_without_waiting_ai_says_so_then_sleeps(make):
    c, out = make(wake_note_secs=0.6)
    _asleep(c)
    c.on_pill_label()
    assert c.pill.mode == "awake" and "no AI is waiting" in c.pill.label.stringValue()
    assert c.awake_until - time.monotonic() <= 0.6  # not the 120 s awake time
    assert _events(out) == ["wake"]
    time.sleep(0.7)
    c.on_tick()
    assert c.pill.mode == "ready" and _events(out) == ["wake", "sleep"]


def test_label_click_with_waiting_ai_hands_back(make):
    c, out = make(wake_note_secs=0.6)
    _asleep(c)
    c.handle({"op": "sleep.wait"})
    c.on_pill_label()
    assert _events(out) == ["wake"]
    assert c.pill.mode == "awake" and c.pill.label.stringValue() == "Dobby is awake · handing back to the AI"
    assert c.awake_until - time.monotonic() > 60  # stays until the AI acts
    c.handle({"op": "sleep.done"})  # wait_for_wake returned
    assert c.pill.label.stringValue() == "Dobby is awake · handing back to the AI"
    c._mark_active()  # the AI's next action
    assert c.pill.mode == "working" and not c.handoff


def test_waiting_again_ends_the_handoff(make):
    c, _ = make()
    _asleep(c)
    c.handle({"op": "sleep.wait"})
    c.on_pill_label()
    c.handle({"op": "sleep.done"})
    c.handle({"op": "sleep.wait"})  # the AI only answered in chat, then waits again
    assert c.pill.mode == "ready" and "click to wake" in c.pill.label.stringValue()


def test_wake_from_menu_while_paused_resumes_for_a_waiting_ai(make):
    c, out = make()
    _asleep(c)
    c.handle({"op": "sleep.wait"})
    c.set_paused(True)
    menu = c._build_menu("Paused")
    wake = next(menu.itemAtIndex_(i) for i in range(menu.numberOfItems())
                if menu.itemAtIndex_(i).title() == "Wake Dobby")
    c.target.menuWake_(wake)
    assert not c.paused and _events(out) == ["pause", "resume", "wake"]
    assert c.pill.mode == "awake"


def test_non_owner_helper_shows_its_waiting_ai(make):
    a, _ = make()
    b, out = make()
    assert not b.bar_owner.owned and not b.pill.window.isVisible()
    b.last_action = time.monotonic() - 3600
    b.handle({"op": "sleep.wait"})
    assert b.pill.window.isVisible() and b.pill.mode == "ready"
    b.on_pill_label()
    assert _events(out) == ["wake"]
    b.handle({"op": "sleep.done"})
    b.awake_until = time.monotonic() - 1
    b.on_tick()
    assert not b.pill.window.isVisible()  # back to leaving the idle bar to the owner


def test_owner_hides_its_idle_bar_while_another_ai_waits(make):
    import os

    from dobby.overlay.barlock import mark_waiting

    a, _ = make()
    _asleep(a)
    assert a.pill.window.isVisible()
    mark_waiting(a.wait_dir, os.getppid(), True)  # another (live) helper's AI waits
    try:
        a.others_checked = 0
        a.on_tick()
        assert not a.pill.window.isVisible()
    finally:
        mark_waiting(a.wait_dir, os.getppid(), False)
    a.others_checked = 0
    a.on_tick()
    assert a.pill.window.isVisible() and a.pill.mode == "ready"


# ------------------------------------------------- menu on the bar's ▾
def test_no_menu_bar_icon_by_default(make):
    c, _ = make()
    assert c.bar_owner.owned and c.status_item is None


def test_menu_bar_icon_when_enabled(make):
    c, _ = make(menu_bar=True)
    assert c.status_item is not None
    assert c.status_item.menu().itemAtIndex_(0).title().startswith("Dobby: ")


def _bar_in(c, mode):
    if mode == "ready":
        _asleep(c)
    elif mode == "awake":
        _asleep(c)
        c.wake()
    elif mode == "working":
        c._mark_active()
    else:
        c._start_confirm(3, "Allow Dobby to quit?", 5)
    assert c.pill.mode == mode


@pytest.mark.parametrize("mode", ["ready", "awake", "working"])
def test_bar_has_menu_button(make, mode):
    c, _ = make()
    _bar_in(c, mode)
    b = c.pill.menu_btn
    assert not b.isHidden() and b.superview() is c.pill.view
    assert b.toolTip() == "Dobby menu"
    f, hide = b.frame(), c.pill.hide_btn.frame()
    # right end of the bar, after "–", and inside the (centred) bar
    assert f.origin.x >= hide.origin.x + hide.size.width
    assert f.origin.x + f.size.width <= c.pill.window.frame().size.width - 8


def test_bar_menu_button_hidden_while_confirming(make):
    c, _ = make()
    _asleep(c)
    w_ready = c.pill.window.frame().size.width
    _bar_in(c, "confirm")
    assert c.pill.menu_btn.isHidden() and c.pill.hide_btn.isHidden()
    c.on_pill("secondary")
    _asleep(c)
    assert not c.pill.menu_btn.isHidden()
    assert c.pill.window.frame().size.width == pytest.approx(w_ready)


def test_bar_is_centred_with_menu_button(make):
    from AppKit import NSScreen

    c, _ = make()
    _bar_in(c, "working")
    vf, f = NSScreen.screens()[0].visibleFrame(), c.pill.window.frame()
    assert f.origin.x + f.size.width / 2 == pytest.approx(vf.origin.x + vf.size.width / 2, abs=1.0)


def test_bar_menu_button_opens_dobbys_menu(make):
    c, _ = make()
    opened = []
    c._pop_up = lambda menu, view, loc: opened.append((menu, view, loc))  # no modal tracking
    c.pill.menu_btn.performClick_(None)
    assert len(opened) == 1
    menu, view, loc = opened[0]
    assert view is c.pill.view
    titles = [menu.itemAtIndex_(i).title() for i in range(menu.numberOfItems())]
    for want in ("Dobby: Idle", "Wake Dobby", "Recent steps", "Pause", "Stop", "Show overlay",
                 "Show control bar (⌃⌥H)"):
        assert want in titles
    assert any(t.startswith("Turbo mode") for t in titles)
    assert any("trail" in t.lower() for t in titles)
    f = c.pill.menu_btn.frame()
    assert loc[0] == pytest.approx(f.origin.x) and loc[1] < f.origin.y  # just under the ▾


def test_tag_has_no_chevron_by_default(make):
    c, _ = make()
    assert c.hotspot is None
    _show_pointer(c)
    tag = c.panels[0].tag
    assert tag.chev.opacity() == 0.0
    idle_w = tag.width
    c.intent_text, c.intent_until = "Click 'Run'", time.monotonic() + 5
    c._refresh_tag()
    assert tag.chev.opacity() == 1.0  # the › breadcrumb before the detail
    assert tag.width > idle_w + 8


# -------------------------------- clickable tag (the ▾, cfg "chip_menu")
def _show_pointer(c, x=400.0, y=300.0):
    c.cursor = (x, y)
    c._set_pointer_visible(True)
    c._refresh_tag()


def test_tag_is_clickable_where_it_is_drawn(make):
    c, _ = make(chip_menu=True)
    assert c.panels[0].tag.chev.opacity() == 1.0  # the ▾ hint is back
    _show_pointer(c)
    hs = c.hotspot
    assert hs.shown and hs.window.isVisible()
    assert not hs.window.ignoresMouseEvents()
    x, y, w, h = hs.rect
    assert w == pytest.approx(max(p.tag.width for p in c.panels), abs=1.0) and w > 30  # snapped to points
    f = hs.window.frame()
    assert (f.origin.x, f.size.width) == pytest.approx((x, w))
    assert f.origin.y == pytest.approx(c.primary_h - y - h)
    # never over the tip, where Dobby clicks
    from dobby.overlay import geometry

    assert not geometry.contains(hs.rect, c.cursor)


def test_tag_click_area_grows_with_step_text(make):
    c, _ = make(chip_menu=True)
    _show_pointer(c)
    w0 = c.hotspot.rect[2]
    c.intent_text, c.intent_until = "Click 'Run Synthesis'", time.monotonic() + 5
    c._refresh_tag()
    assert c.hotspot.rect[2] > w0 + 40


def test_tag_click_area_hidden_with_pointer_and_during_capture(make):
    c, _ = make(chip_menu=True)
    _show_pointer(c)
    c.capture_hidden = True
    c._apply_alpha()
    assert not c.hotspot.shown
    c.capture_hidden = False
    c._apply_alpha()
    assert c.hotspot.shown
    c._set_pointer_visible(False)
    assert not c.hotspot.shown


def test_tag_menu_matches_menu_bar_menu(make):
    c, _ = make()
    menu = c._build_menu("Idle")
    titles = [menu.itemAtIndex_(i).title() for i in range(menu.numberOfItems())]
    assert titles[0] == "Dobby: Idle"
    for want in ("Pause", "Stop", "Show control bar (⌃⌥H)", "Show overlay"):
        assert want in titles
    stop = menu.itemAtIndex_(titles.index("Stop"))
    assert stop.target() is c.target and stop.action() == "menuStop:"


# ---------------------------------------------------------- Dobby picker
def test_picker_lists_dobbys_and_pauses_the_other(make, tmp_path, monkeypatch):
    import os

    from dobby.control import commands
    from dobby.control.registry import register

    monkeypatch.setenv("DOBBY_CONTROL_DIR", str(tmp_path / "control"))
    root = tmp_path / "control"
    c, _ = make()
    c.server_pid = os.getpid()
    register(root, "a", "Claude", os.getpid())
    register(root, "b", "Agy", os.getppid())
    menu = c._build_menu("Idle")
    titles = [menu.itemAtIndex_(i).title() for i in range(menu.numberOfItems())]
    assert "Dobbys" in titles and "All Dobbys" in titles
    agy = menu.itemAtIndex_(titles.index("Agy"))
    pause = agy.submenu().itemAtIndex_(0)
    assert pause.title() == "Pause" and pause.target() is c.target
    c.target.menuAgentPauseToggle_(pause)
    assert commands.read(root, os.getppid())["paused"] is True
    own = menu.itemAtIndex_(titles.index("Claude  · this bar")).submenu().itemAtIndex_(0)
    c.target.menuAgentPauseToggle_(own)
    assert c.paused
    c.target.menuAllResume_(None)
    assert not c.paused and commands.read(root, os.getppid())["paused"] is False


# ------------------------------------------------------------ modern look
def _modern(make, **cfg):
    c, out = make(style="modern", **cfg)
    _asleep(c)
    return c, out


def test_modern_sleeping_bar_is_compact_and_grows_on_hover(make):
    c, _ = _modern(make)
    assert c.pill.mode == "ready" and c.pill.spec.compact
    assert c.pill.label.stringValue() == "Dobby is sleeping"
    assert c.pill.primary.isHidden() and c.pill.menu_btn.isHidden()
    w0 = c.pill.window.frame().size.width
    assert w0 < 180
    c.on_pill_hover(True)
    assert not c.pill.spec.compact and "click to wake" in c.pill.label.stringValue()
    assert not c.pill.menu_btn.isHidden() and c.pill.primary.isHidden()
    assert c.pill.window.frame().size.width > w0
    c.on_pill_hover(False)
    c._hover_settle()
    assert c.pill.spec.compact


def test_modern_bar_has_glass(make):
    c, _ = _modern(make)
    assert c.pill.backdrop is not None and c.pill.backdrop.superview() is c.pill.view


def test_modern_working_bar_shows_step_and_timer_with_icons(make):
    c, _ = _modern(make)
    c.handle({"op": "intent", "text": "Click ‘Send’"})
    text = c.pill.label.stringValue()
    assert c.pill.mode == "working" and text.startswith("Dobby is working · Click ‘Send’ · step 1 · 0:0")
    assert c.pill.primary.image() is not None and c.pill.secondary.image() is not None
    assert c.pill.secondary.toolTip().startswith("Stop")
    assert not c.pill.menu_btn.isHidden()


def test_modern_agent_colour_name_and_dots(make):
    c, _ = _modern(make)
    c.server_pid = 222
    c._apply_agents([{"pid": 111, "name": "antigravity", "started": 1, "color": "#7C5CFF", "waiting": True},
                     {"pid": 222, "name": "claude-code", "started": 2, "color": "#14B8A6", "holding": True}])
    assert c.ok_hex == "#14B8A6" and c.agent_label == "Dobby · claude-code"
    assert not c.pill.dots_btn.isHidden() and c.pill.dots_btn.image() is not None


def test_modern_idle_dobbys_add_no_dots(make):
    agents = [{"pid": 111, "name": "antigravity", "started": 1, "color": "#7C5CFF"},
              {"pid": 222, "name": "claude-code", "started": 2, "color": "#14B8A6", "holding": True},
              {"pid": 333, "name": "cursor", "started": 3, "color": "#EC4899"}]
    c, _ = _modern(make)
    c.server_pid = 222
    c._apply_agents(agents)
    assert c.pill.dots_btn.isHidden() and c.agent_dots == []
    quiet, _ = _modern(make, narrate=False)
    quiet.server_pid = 222
    quiet._apply_agents(agents)
    assert not quiet.pill.dots_btn.isHidden() and len(quiet.agent_dots) == 3


def test_modern_tag_keeps_the_action_until_idle(make):
    c, _ = _modern(make)
    c.handle({"op": "intent", "text": "Click ‘Send’"})
    assert c.intent_until == float("inf") and c._tag_parts()[1] == "Click ‘Send’"
    c.cursor_state = "thinking"
    assert c._tag_parts()[1] == "Thinking…"
    c.cursor_state = "idle"
    c.last_action = time.monotonic() - float(c.cfg["active_secs"]) - 1
    c.on_tick()
    assert c.intent_text == "" and c._tag_parts()[1] == ""


def test_modern_fixed_colour_ignores_registry(make):
    c, _ = _modern(make, color="#00AA00", color_fixed=True)
    c.server_pid = 222
    c._apply_agents([{"pid": 222, "name": "x", "started": 1, "color": "#14B8A6"}])
    assert c.ok_hex == "#00AA00"


def test_modern_tag_flips_at_right_edge(make):
    from AppKit import NSScreen

    c, _ = _modern(make)
    f = NSScreen.screens()[0].frame()
    c.cursor = (f.size.width - 10, 200.0)
    c._place_pointer(c.cursor, 0)
    assert c.panels[0].flip == (True, False)
    c.cursor = (300.0, 200.0)
    c._place_pointer(c.cursor, 0)
    assert c.panels[0].flip == (False, False)


def test_modern_glow_follows_state(make):
    c, _ = _modern(make)
    c._place_glow((100.0, 100.0, 600.0, 400.0), 0)
    c.glow_rect = (100.0, 100.0, 600.0, 400.0)
    c._glow_sig = None
    c._refresh_glow()
    assert c.panels[0].glow.opacity() == 0.0  # idle: no glow
    c._mark_active()
    c._refresh_pill()
    assert c.panels[0].glow.opacity() == 1.0
    c.cursor_state = "thinking"
    c._refresh_pill()
    assert c.panels[0].sweep.opacity() > 0 or c.reduce_motion
    c.set_paused(True)
    assert c._glow_sig[0] == "halted"


def test_modern_trail_keeps_three_dots(make):
    c, _ = _modern(make)
    c.cursor = (300.0, 300.0)
    for _ in range(5):
        c.handle({"op": "cursor.click", "count": 1})
    marks = c.panels[0].trail.sublayers() or []
    assert len(marks) == 3 and all(hasattr(m, "string") for m in marks)  # numbered badges


def test_modern_paused_turns_pointer_and_tag_amber(make):
    import Quartz
    from dobby.overlay import mac_helper

    c, _ = _modern(make)
    c._mark_active()
    c._refresh_pill()
    c.set_paused(True)
    assert Quartz.CGColorEqualToColor(c.panels[0].arrow.fillColor(), mac_helper._cg(c.colors["caution"]))


def test_modern_one_step_per_action(make):
    c, _ = _modern(make)
    c.handle({"op": "intent", "text": "Click ‘Send’"})
    c.handle({"op": "cursor.move", "x": 300.0, "y": 300.0, "ms": 0})
    assert c.task_steps == 1
    assert "step 1" in c.pill.label.stringValue()


def test_modern_dot_sits_above_glass(make):
    c, _ = _modern(make)
    assert c.pill.dot.superlayer() is c.pill.tint.layer()
