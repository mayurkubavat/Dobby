"""Live Linux X11 backend tests.

They run only on Linux with an X11 DISPLAY and the test apps installed
(zenity, gedit); everywhere else they skip. The headless rig used for these
is Xvfb + openbox + a session D-Bus + at-spi-bus-launcher.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import uuid

import pytest
from conftest import live_desktop_allowed

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or not os.environ.get("DISPLAY"),
    reason="needs Linux with an X11 DISPLAY",
)


def _need(app: str) -> None:
    if shutil.which(app) is None:
        pytest.skip(f"{app} not installed")


@pytest.fixture(scope="module")
def hal():
    from pinlet.hal.linux import LinuxHAL

    h = LinuxHAL()
    if h.display_server == "none":
        pytest.skip("cannot open the X display")
    return h


def _wait_for(pred, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = pred()
        if got:
            return got
        time.sleep(0.1)
    return None


@pytest.fixture
def spawn(hal):
    """Starts a zenity dialog with a unique title; returns its WindowInfo."""
    procs = []

    def _spawn(kind: str = "--info", extra=(), capture: bool = False):
        _need("zenity")
        title = f"pinlet-{uuid.uuid4().hex[:8]}"
        p = subprocess.Popen(
            ["zenity", kind, f"--title={title}", "--text=Pinlet test", *extra],
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        procs.append(p)
        win = _wait_for(lambda: next((w for w in hal.list_windows() if w.title == title), None))
        assert win is not None, f"window {title} never appeared"
        return win, p

    yield _spawn
    for p in procs:
        p.kill()
        p.wait(timeout=5)


# ---------------------------------------------------------------- windows


def test_display_server_reported(hal):
    assert hal.display_server in ("X11", "XWayland")


def test_list_windows_front_to_back_and_focus(hal, spawn):
    a, _ = spawn()
    b, _ = spawn()
    assert hal.focus_window(a.window_id)
    order = [w.window_id for w in hal.list_windows()]
    assert order.index(a.window_id) < order.index(b.window_id)
    assert hal.get_active_window().window_id == a.window_id

    assert hal.focus_window(b.window_id)
    order = [w.window_id for w in hal.list_windows()]
    assert order.index(b.window_id) < order.index(a.window_id)
    active = hal.get_active_window()
    assert active.window_id == b.window_id and active.is_active


def test_window_pid_and_app_name(hal, spawn):
    win, proc = spawn()
    assert hal.window_pid(win.window_id) == proc.pid
    assert (win.app_name or "").lower() == "zenity"
    assert win.bbox.width >= 20 and win.bbox.height >= 20


def test_window_disappears_after_close(hal, spawn):
    win, proc = spawn()
    proc.kill()
    proc.wait(timeout=5)
    gone = _wait_for(lambda: all(w.window_id != win.window_id for w in hal.list_windows()))
    assert gone
    assert hal.window_pid(win.window_id) is None


def test_focus_unknown_window_fails(hal):
    assert hal.focus_window("0x7ffffff0") is False


# ---------------------------------------------------------------- AT-SPI


@pytest.fixture(scope="module")
def inspector(hal):
    from pinlet.perception.atspi import ATSPIInspector

    insp = ATSPIInspector(hal=hal)
    if not insp.is_available:
        pytest.skip(f"AT-SPI unavailable: {insp.error}")
    return insp


def test_atspi_places_gtk4_widgets_on_screen(hal, inspector, spawn):
    """GTK 4 reports SCREEN extents as 0,0; boxes must still land inside the window."""
    win, _ = spawn("--entry")
    hal.focus_window(win.window_id)
    els = _wait_for(lambda: [e for e in inspector.inspect_desktop() if e.name in ("OK", "Cancel")])
    assert els, "no buttons found"
    slack = 8  # GTK draws button boxes a few pixels into the window edge
    for e in els:
        b = e.bbox
        assert win.bbox.x <= b.x and b.x + b.width <= win.bbox.x + win.bbox.width + slack
        assert win.bbox.y <= b.y and b.y + b.height <= win.bbox.y + win.bbox.height + slack


def test_atspi_defaults_to_active_app(hal, inspector, spawn):
    _a, _ = spawn("--entry")
    b, _ = spawn("--info")
    hal.focus_window(b.window_id)
    time.sleep(0.3)
    roles = {e.role for e in inspector.inspect_desktop()}
    assert "text" not in roles  # the --entry dialog belongs to another (inactive) process


def test_atspi_hit_test_finds_button(hal, inspector, spawn):
    win, _ = spawn("--entry")
    hal.focus_window(win.window_id)
    ok = _wait_for(lambda: next((e for e in inspector.inspect_desktop() if e.name == "OK"), None))
    assert ok is not None
    hit = inspector.hit_test(ok.bbox.x + ok.bbox.width // 2, ok.bbox.y + ok.bbox.height // 2)
    assert hit is not None and hit["name"] == "OK"


def test_atspi_hit_test_reports_covering_window(hal, inspector, spawn):
    """The topmost window at a point answers, even when it is not the app being inspected."""
    _below, _ = spawn("--entry")
    above, _ = spawn("--info")
    hal.focus_window(above.window_id)
    time.sleep(0.3)
    ab = above.bbox
    # A point inside the front window: the hit must come from inside its box.
    hit = inspector.hit_test(ab.x + ab.width // 2, ab.y + ab.height // 2)
    assert hit is not None
    hb = hit["bbox"]
    assert ab.x <= hb["x"] and hb["x"] + hb["width"] <= ab.x + ab.width + 8


def test_atspi_without_bus_does_not_abort():
    """libatspi aborts the process when the a11y bus is missing; the probe must prevent that."""
    code = (
        "from pinlet.perception.atspi import ATSPIInspector\n"
        "i = ATSPIInspector(hal=object())\n"
        "print('available', i.is_available)\n"
        "print(len(i.inspect_desktop()), i.hit_test(10, 10))\n"
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("DBUS_SESSION_BUS_ADDRESS", "AT_SPI_BUS_ADDRESS")
    }
    env["XDG_RUNTIME_DIR"] = "/nonexistent"
    env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/nonexistent/bus"
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30
    )
    assert out.returncode == 0, out.stderr
    assert "available False" in out.stdout
    assert "0 None" in out.stdout


# ---------------------------------------------------------------- input


def _center(b):
    return b.x + b.width // 2, b.y + b.height // 2


def _entry_dialog(hal, inspector, spawn):
    win, proc = spawn("--entry", capture=True)
    hal.focus_window(win.window_id)
    entry = _wait_for(
        lambda: next((e for e in inspector.inspect_desktop() if e.role == "text"), None)
    )
    assert entry is not None
    return win, proc, entry


def test_type_click_and_submit(hal, inspector, spawn):
    _win, proc, entry = _entry_dialog(hal, inspector, spawn)
    hal.mouse_click(*_center(entry.bbox))
    hal.keyboard_type("hello pinlet")
    ok = next(e for e in inspector.inspect_desktop() if e.name == "OK")
    hal.mouse_click(*_center(ok.bbox))
    out, _ = proc.communicate(timeout=5)
    assert out.strip() == "hello pinlet"


def test_hotkey_select_all_replaces_and_enter_submits(hal, inspector, spawn):
    _win, proc, entry = _entry_dialog(hal, inspector, spawn)
    hal.mouse_click(*_center(entry.bbox))
    hal.keyboard_type("first")
    hal.keyboard_hotkey(["ctrl", "a"])
    hal.keyboard_type("second")
    hal.keyboard_press_key("enter")
    out, _ = proc.communicate(timeout=5)
    assert out.strip() == "second"


def test_unicode_typing(hal, inspector, spawn):
    _win, proc, entry = _entry_dialog(hal, inspector, spawn)
    hal.mouse_click(*_center(entry.bbox))
    hal.keyboard_type("café ü")
    hal.keyboard_press_key("Return")
    out, _ = proc.communicate(timeout=5)
    assert out.strip() == "café ü"


def test_escape_cancels(hal, inspector, spawn):
    _win, proc, entry = _entry_dialog(hal, inspector, spawn)
    hal.mouse_click(*_center(entry.bbox))
    hal.keyboard_press_key("esc")
    proc.communicate(timeout=5)
    assert proc.returncode == 1  # zenity: cancelled


def test_cursor_position_follows_move(hal):
    hal.mouse_move(321, 234)
    assert hal.get_cursor_position() == (321, 234)


def test_capture_matches_screen_size(hal):
    w, h = hal.get_screen_size()
    img = hal.capture_screen()
    assert img.size == (w, h)
    crop = hal.capture_screen(region=(10, 20, 100, 50))
    assert crop.size == (100, 50)


def test_capture_of_a_window_past_the_screen_edge(hal):
    """A window dragged partly off screen: X would refuse the grab (BadMatch)."""
    w, h = hal.get_screen_size()
    full = hal.capture_screen()
    img = hal.capture_screen(region=(w - 60, h - 40, 100, 80))
    assert img.size == (100, 80)  # still lines up with the region
    assert img.crop((0, 0, 60, 40)).tobytes() == full.crop((w - 60, h - 40, w, h)).tobytes()
    assert img.crop((60, 40, 100, 80)).getextrema() == ((0, 0), (0, 0), (0, 0))  # off screen: black
    assert hal.capture_screen(region=(-30, -10, 50, 20)).size == (50, 20)
    assert hal.capture_screen(region=(w + 10, 0, 20, 20)).size == (20, 20)


def test_stop_hotkey_moves_when_ctrl_alt_esc_is_taken(hal, monkeypatch):
    """GNOME/Metacity own Ctrl+Alt+Esc (cycle-panels); Stop must still get a key."""
    from Xlib import XK, X
    from Xlib import display as xdisplay

    pytest.importorskip("gi")
    from pinlet.overlay import x11_helper

    assert x11_helper._HOTKEYS["stop"] == ("Escape", "End")
    # Keys of their own, so a desktop shortcut or another Pinlet overlay cannot interfere.
    monkeypatch.setattr(
        x11_helper, "_HOTKEYS", {"stop": ("bracketleft", "bracketright"), "pause": ("semicolon",)}
    )
    monkeypatch.setattr(x11_helper, "HOTKEY_LABELS", {})
    squatter, helper = xdisplay.Display(), xdisplay.Display()
    taken = squatter.keysym_to_keycode(XK.string_to_keysym("bracketleft"))
    mods = X.ControlMask | X.Mod1Mask
    squatter.screen().root.grab_key(taken, mods, True, X.GrabModeAsync, X.GrabModeAsync)
    squatter.sync()
    try:
        keys = x11_helper.grab_hotkeys(helper, helper.screen().root)
        assert taken not in keys
        assert sorted(keys.values()) == ["pause", "stop"]
        assert x11_helper.HOTKEY_LABELS["stop"] == "Ctrl+Alt+Bracketright"
    finally:
        helper.close()
        squatter.close()


def test_topology_and_ui_scale(hal):
    topo = hal.display_topology()
    assert topo.displays and topo.displays[0].scale == 1.0
    assert hal.ui_scale > 0


def test_factory_without_display_fails_closed():
    code = (
        "from pinlet.hal.factory import get_desktop_hal\n"
        "from pinlet.hal.errors import CapabilityUnavailable\n"
        "h = get_desktop_hal()\n"
        "try:\n"
        "    h.capture_screen()\n"
        "except CapabilityUnavailable as e:\n"
        "    print('closed', 'DISPLAY' in str(e))\n"
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("DISPLAY", "WAYLAND_DISPLAY", "DESKTOP_HAL")
    }
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30
    )
    assert out.returncode == 0, out.stderr
    assert "closed True" in out.stdout


def test_screen_state_loop_end_to_end(hal, spawn):
    """observe -> type -> click, through the same screen-state code the MCP tools use."""
    from pinlet.agent_loop.controller import FeedbackLoopController
    from pinlet.screen_state import StateTracker
    from pinlet.screen_state.observe import act_on_element, observe

    win, proc = spawn("--entry", capture=True)
    hal.focus_window(win.window_id)
    ctl = FeedbackLoopController(hal)
    if not ctl.atspi_inspector.is_available:
        pytest.skip("AT-SPI unavailable")
    tracker = StateTracker()
    state = _wait_for(
        lambda: (lambda s: s if any(e.label == "OK" for e in s.elements) else None)(
            observe(ctl, tracker, use_ocr=False)
        )
    )
    assert state is not None
    assert state.app == "zenity" or (state.window_title or "").startswith("pinlet-")
    entry = next(e for e in state.elements if e.source == "ax" and e.role == "text")
    assert entry.focused, "the entry starts focused in a zenity --entry dialog"
    assert "type" in state.as_dict()["operations"]

    r = act_on_element(ctl, tracker, entry.eid, state.generation, action="type", text="Pinlet")
    assert r["ok"], r
    state2 = observe(ctl, tracker, use_ocr=False)
    ok = next(e for e in state2.elements if e.label == "OK")
    r = act_on_element(ctl, tracker, ok.eid, state2.generation, action="click")
    assert r["ok"], r
    out, _ = proc.communicate(timeout=5)
    assert out.strip() == "Pinlet"


# ---------------------------------------------------------------- overlay

ACCENT = (14, 165, 198)  # #0EA5C6, the default overlay color


def _near(px, rgb, tol=12):
    return all(abs(a - b) <= tol for a, b in zip(px[:3], rgb))


@pytest.fixture
def overlay(hal, tmp_path):
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk  # noqa: F401
    except Exception:
        pytest.skip("GTK 3 (python3-gi, gir1.2-gtk-3.0) not installed")
    from pinlet.overlay.client import OverlayClient

    # Plain wording: these check the bar, test_personality checks the voice.
    client = OverlayClient(
        config={"speed": 0, "bar_lock": str(tmp_path / "bar.lock"), "personality": "off"},
        log_path=None,
    )
    yield client
    client.close()


def test_overlay_pointer_glow_and_capture_hiding(hal, overlay, spawn):
    win, _ = spawn()
    assert overlay.move(700, 500, verb="Click")  # clear of the dialog and its glow
    assert overlay.track(window_id=win.window_id)
    time.sleep(0.6)  # glow follows on the next track tick
    shot = hal.capture_screen()
    b = win.bbox
    assert _near(shot.getpixel((704, 508)), ACCENT), "pointer not drawn"
    assert _near(shot.getpixel((b.x + b.width // 2, b.y - 2)), ACCENT), "glow not drawn"

    assert overlay.hide()
    hidden = hal.capture_screen()
    assert not _near(hidden.getpixel((704, 508)), ACCENT)
    assert not _near(hidden.getpixel((b.x + b.width // 2, b.y - 2)), ACCENT)
    assert overlay.show()


def test_overlay_hal_capture_never_shows_overlay(hal, overlay):
    from pinlet.overlay import OverlayHAL

    wrapped = OverlayHAL(hal, overlay)
    wrapped.mouse_move(500, 400)  # the agent pointer glides here first
    time.sleep(0.2)
    shot = wrapped.capture_screen()
    assert not _near(shot.getpixel((504, 408)), ACCENT)
    time.sleep(0.2)  # overlay maps back after the capture
    raw = hal.capture_screen()
    assert _near(raw.getpixel((504, 408)), ACCENT)


def test_overlay_panels_are_not_listed_as_windows(hal, overlay):
    from pinlet.overlay.protocol import OVERLAY_WINDOW_TITLE

    assert overlay.move(300, 300)
    time.sleep(0.2)
    assert all(not w.title.startswith(OVERLAY_WINDOW_TITLE) for w in hal.list_windows())


def test_overlay_confirm_allow_deny_and_timeout(hal, overlay):
    import threading

    from pinlet.perception.atspi import ATSPIInspector

    insp = ATSPIInspector(hal=hal)
    if not insp.is_available:
        pytest.skip("AT-SPI unavailable")
    assert overlay.confirm("Delete the file", secs=0.5) is False  # timeout = deny

    for button, expected in (("Allow", True), ("Deny", False)):
        result = {}
        t = threading.Thread(
            target=lambda: result.update(ok=overlay.confirm("Run synthesis", secs=10))
        )
        t.start()
        btn = _wait_for(
            lambda: next(
                (
                    e
                    for e in insp.inspect_desktop(app_name_filter="Pinlet Overlay")
                    if e.name == button
                ),
                None,
            )
        )
        assert btn is not None, f"{button} button not found"
        hal.mouse_click(btn.bbox.x + btn.bbox.width // 2, btn.bbox.y + btn.bbox.height // 2)
        t.join(timeout=5)
        assert result.get("ok") is expected


def _bar_items(insp):
    return insp.inspect_desktop(app_name_filter="Pinlet Overlay", filter_interactable=False)


def _bar_label(insp, words):
    found = _wait_for(
        lambda: next((e for e in _bar_items(insp) if e.role == "label" and words in e.name), None)
    )
    if found is None:
        return None
    time.sleep(0.3)  # the bar re-centres after its text changes
    return next((e for e in _bar_items(insp) if e.role == "label" and words in e.name), found)


def _click(hal, el):
    hal.mouse_click(el.bbox.x + min(8, el.bbox.width // 2), el.bbox.y + el.bbox.height // 2)


def test_control_bar_sleep_wake_pause_stop(hal, overlay):
    from pinlet.perception.atspi import ATSPIInspector

    insp = ATSPIInspector(hal=hal)
    if not insp.is_available:
        pytest.skip("AT-SPI unavailable")
    overlay.start_async()
    sleeping = _bar_label(insp, "Pinlet is sleeping")
    assert sleeping is not None, "idle control bar not shown"
    assert sleeping.bbox.y < 60  # top of the screen

    _click(hal, sleeping)  # click the bar: wake
    assert _wait_for(lambda: overlay.awake, timeout=3)
    awake = _bar_label(insp, "Pinlet is awake")
    assert awake is not None
    _click(hal, awake)  # click again: back to sleep
    assert _wait_for(lambda: not overlay.awake, timeout=3)
    # Nothing runs while Pinlet sleeps, so the bar has no Pause or Stop then.
    assert not any(e.name in ("Pause", "⏸", "Stop", "■") for e in _bar_items(insp))

    assert overlay.move(700, 500, verb="Click")  # working: pause (⏸) and stop (■)
    assert _bar_label(insp, "Click · step 1") is not None  # waits for the bar to re-centre
    pause = next(e for e in _bar_items(insp) if e.name == "⏸")
    _click(hal, pause)
    assert _wait_for(lambda: overlay._paused, timeout=3)
    assert _bar_label(insp, "Pinlet is paused") is not None
    resume = next(e for e in _bar_items(insp) if e.name == "▶")
    _click(hal, resume)
    assert _wait_for(lambda: not overlay._paused, timeout=3)

    stop = _wait_for(lambda: next((e for e in _bar_items(insp) if e.name == "■"), None))
    _click(hal, stop)
    assert _bar_label(insp, "Pinlet stopped") is not None
    assert _wait_for(lambda: overlay._stop_requested, timeout=3)


def test_control_bar_working_and_hidden_for_capture(hal, overlay):
    from pinlet.perception.atspi import ATSPIInspector

    insp = ATSPIInspector(hal=hal)
    if not insp.is_available:
        pytest.skip("AT-SPI unavailable")
    assert overlay.move(700, 500, verb="Click")
    working = _bar_label(insp, "Click · step 1 · 0:0")  # live step, step count and timer
    assert working is not None
    b = working.bbox
    before = hal.capture_screen().crop((b.x, b.y, b.x + b.width, b.y + b.height))
    assert overlay.hide()
    hidden = hal.capture_screen().crop((b.x, b.y, b.x + b.width, b.y + b.height))
    assert overlay.show()
    assert before.getextrema() != hidden.getextrema()
    if live_desktop_allowed():  # an empty test display: nothing but background behind the bar
        assert all(lo == hi for lo, hi in hidden.getextrema())
