"""Tests for dobby.overlay that run anywhere (no AppKit needed)."""

import json
import sys
import textwrap
import time
from types import SimpleNamespace

import pytest

from dobby.overlay import (
    NullOverlay,
    OverlayClient,
    OverlayHAL,
    overlay_enabled,
    unwrap,
    wrap_hal,
)
from dobby.overlay import geometry as g
from dobby.overlay import protocol as p
from dobby.overlay import overlay_config


# --------------------------------------------------------------- protocol
def test_encode_decode_roundtrip():
    line = p.encode("cursor.move", 7, x=10, y=20.5, ms=300)
    assert line.endswith("\n")
    assert p.decode(line) == {"op": "cursor.move", "x": 10, "y": 20.5, "ms": 300, "id": 7}


def test_encode_drops_none_fields():
    assert json.loads(p.encode("cursor.move", None, x=1, y=2, ms=None)) == {"op": "cursor.move", "x": 1, "y": 2}


@pytest.mark.parametrize("msg", [
    {"op": "nope"},
    {"op": "cursor.move", "x": 1},
    {"op": "cursor.move", "x": "1", "y": 2},
    {"op": "cursor.state", "state": "dancing"},
    {"op": "glow.track"},
    {"op": "hide", "extra": 1},
    [],
])
def test_validate_rejects(msg):
    with pytest.raises(p.ProtocolError):
        p.validate(msg)


def test_decode_bad_json():
    with pytest.raises(p.ProtocolError):
        p.decode("{not json")


@pytest.mark.parametrize("raw,expected", [(42, 42), ("42", 42), ("0x2a", 42), ("abc", None), (None, None)])
def test_window_id_int(raw, expected):
    assert p.window_id_int(raw) == expected


# --------------------------------------------------------------- geometry
PRIMARY = (0.0, 0.0, 1440.0, 900.0)  # Cocoa frame of the primary display


def test_point_on_primary():
    # Global (top-left) 100,100 is 800 up from the bottom on a 900-high screen.
    assert g.global_point_to_local((100, 100), PRIMARY, 900) == (100, 800)


def test_rect_on_primary():
    assert g.global_rect_to_local((100, 100, 200, 50), PRIMARY, 900) == (100, 750, 200, 50)


def test_secondary_display_left_and_above():
    # Second display 1920x1080 to the left, its bottom 200 pt above primary's top.
    sec = (-1920.0, 1100.0, 1920.0, 1080.0)
    # Global y for that display's top-left: 900 - (1100 + 1080) = -1280.
    assert g.global_point_to_local((-1920, -1280), sec, 900) == (0, 1080)
    assert g.cocoa_rect_to_global(sec, 900) == (-1920, -1280, 1920, 1080)


def test_screen_index_for_point():
    frames = [(0, 0, 1440, 900), (1440, 0, 1920, 1080)]
    assert g.screen_index_for_point((10, 10), frames) == 0
    assert g.screen_index_for_point((1500, 1000), frames) == 1
    assert g.screen_index_for_point((-5, 0), frames) is None


def test_pixels_to_points():
    assert g.pixels_to_points((2880, 1800), 2.0) == (1440, 900)
    with pytest.raises(ValueError):
        g.pixels_to_points((1, 1), 0)


def test_glide_ms():
    assert g.glide_ms((0, 0), (0, 0)) == 0
    assert g.glide_ms((0, 0), (10, 0)) == pytest.approx(252, abs=2)
    assert g.glide_ms((0, 0), (5000, 0)) == 450
    assert g.glide_ms((0, 0), (500, 0), speed=0) == 0
    assert g.glide_ms((0, 0), (5000, 0), speed=2) == 225


def test_occluded():
    target = (0, 0, 100, 100)
    assert not g.occluded(target, [])
    assert not g.occluded(target, [(90, 90, 50, 50)])
    assert g.occluded(target, [(0, 0, 60, 60)])
    assert not g.occluded((0, 0, 0, 0), [(0, 0, 10, 10)])
    assert g.intersects((0, 0, 10, 10), (5, 5, 10, 10))
    assert not g.intersects((0, 0, 10, 10), (10, 0, 10, 10))


# --------------------------------------------------------------- enabling
def test_overlay_enabled_rules():
    assert not overlay_enabled({}, platform="linux")
    assert not overlay_enabled({"DOBBY_OVERLAY": "1"}, platform="linux")
    assert overlay_enabled({"DISPLAY": ":0"}, platform="linux")
    assert not overlay_enabled({"DISPLAY": ":0", "DOBBY_OVERLAY": "0"}, platform="linux")
    assert not overlay_enabled({"DISPLAY": ":0", "PYTEST_CURRENT_TEST": "x"}, platform="linux")
    assert not overlay_enabled({"WAYLAND_DISPLAY": "wayland-0"}, platform="linux")
    assert overlay_enabled({}, platform="darwin")
    assert not overlay_enabled({"DOBBY_OVERLAY": "0"}, platform="darwin")
    assert not overlay_enabled({"PYTEST_CURRENT_TEST": "x"}, platform="darwin")
    assert overlay_enabled({"PYTEST_CURRENT_TEST": "x", "DOBBY_OVERLAY": "1"}, platform="darwin")
    assert not overlay_enabled({"DOBBY_REPLAY": "1"}, platform="darwin")


def test_overlay_config():
    cfg = overlay_config({"DOBBY_OVERLAY_COLOR": "#00ff00", "DOBBY_OVERLAY_SPEED": "0", "DOBBY_OVERLAY_LABEL": "D"})
    assert cfg == {"color": "#00ff00", "color_fixed": True, "speed": 0.0, "label": "D"}
    assert overlay_config({"DOBBY_OVERLAY_SPEED": "fast"}) == {}
    assert overlay_config({"DOBBY_OVERLAY_CAPTURE_VISIBLE": "0"}) == {"capture_visible": False}
    assert overlay_config({"DOBBY_OVERLAY_TURBO": "1", "DOBBY_OVERLAY_RESUME": "manual",
                           "DOBBY_OVERLAY_IDLE_SECS": "0.1", "DOBBY_OVERLAY_TRAIL": "0"}) == {
        "turbo": True, "takeover_resume": "manual", "idle_secs": 0.5, "trail": False}


def test_overlay_config_narration_is_on_unless_turned_off():
    assert "narrate" not in overlay_config({})
    assert overlay_config({"DOBBY_OVERLAY_NARRATE": "0"}) == {"narrate": False}
    assert "narrate" not in overlay_config({"DOBBY_OVERLAY_NARRATE": "1"})


def test_overlay_config_menu_bar_icon_is_opt_in():
    assert "menu_bar" not in overlay_config({})
    assert overlay_config({"DOBBY_OVERLAY_MENU_BAR": "1"}) == {"menu_bar": True}
    assert "menu_bar" not in overlay_config({"DOBBY_OVERLAY_MENU_BAR": "0"})


def test_wrap_hal_is_noop_when_disabled():
    hal = object()
    assert wrap_hal(hal, overlay=NullOverlay()) is hal


# --------------------------------------------------------------- client
FAKE_HELPER = textwrap.dedent("""
    import json, sys
    log = open(sys.argv[1], "a")
    print(json.dumps({"ready": True, "version": %d}), flush=True)
    for line in sys.stdin:
        log.write(line); log.flush()
        msg = json.loads(line)
        if msg.get("op") == "cursor.move" and msg.get("x") == 666:
            continue  # simulate a lost ack
        if msg.get("op") == "intent" and msg.get("text", "").startswith("EMIT:"):
            for name in msg["text"][5:].split(","):
                print(json.dumps({"event": name}), flush=True)
        if msg.get("op") == "sleep.wait" and msg.get("reason", "").startswith("EMIT:"):
            for name in msg["reason"][5:].split(","):  # like a user click on the waiting bar
                print(json.dumps({"event": name}), flush=True)
        if msg.get("op") == "confirm":
            print(json.dumps({"ack": msg["id"], "ok": msg["text"] == "yes?"}), flush=True)
            continue
        if "id" in msg:
            print(json.dumps({"ack": msg["id"], "ok": True}), flush=True)
        if msg.get("op") == "bye":
            break
""") % p.PROTOCOL_VERSION


@pytest.fixture
def fake_client(tmp_path):
    script = tmp_path / "helper.py"
    script.write_text(FAKE_HELPER)
    log = tmp_path / "log.jsonl"
    client = OverlayClient(cmd=[sys.executable, str(script), str(log)], ack_timeout=0.5, startup_timeout=5)
    yield client, log
    client.close()


def _ops(log):
    return [json.loads(line)["op"] for line in log.read_text().splitlines()]


def test_client_roundtrip(fake_client):
    client, log = fake_client
    assert not client.active
    assert client.move(10, 20, ms=0) is True
    assert client.active and client.pid
    assert client.click() is True
    assert client.state("typing") is True
    assert client.track(window_id="0x10") is True
    assert client.track() is False  # needs an id or pid
    assert client.hide() is True
    assert client.show() is True
    client.close()
    assert _ops(log) == ["cursor.move", "cursor.click", "cursor.state", "glow.track", "hide", "show", "bye"]


def test_client_ack_timeout_does_not_block(fake_client):
    client, _ = fake_client
    t0 = time.monotonic()
    assert client.move(666, 1, ms=0) is False
    assert time.monotonic() - t0 < 2.0
    assert client.move(1, 1, ms=0) is True  # still usable afterwards


def test_client_disables_when_helper_missing(tmp_path):
    client = OverlayClient(cmd=[str(tmp_path / "does-not-exist")])
    assert client.move(1, 2) is False
    assert client.click() is False
    assert not client.active
    assert client.hide() is True  # inactive overlay never blocks capture


def test_client_disables_on_protocol_mismatch(tmp_path):
    script = tmp_path / "old.py"
    script.write_text("import json,sys\nprint(json.dumps({'ready': True, 'version': 999}), flush=True)\n"
                      "[None for _ in sys.stdin]\n")
    client = OverlayClient(cmd=[sys.executable, str(script)], ack_timeout=0.3)
    client.move(1, 1)
    time.sleep(0.2)
    assert not client.active
    client.close()


def test_client_disables_when_helper_dies(tmp_path):
    script = tmp_path / "dies.py"
    script.write_text("import sys\nsys.exit(3)\n")
    client = OverlayClient(cmd=[sys.executable, str(script)], ack_timeout=0.3, startup_timeout=1)
    client.move(1, 1)
    time.sleep(0.3)
    assert client.move(2, 2) is False
    assert not client.active


# --------------------------------------------------------------- HAL wrapper
class RecordingOverlay(NullOverlay):
    active = True

    def __init__(self):
        self.calls = []

    def move(self, x, y, ms=None, wait=True, verb=None, probe=False):
        self.calls.append(("move", x, y, ms, wait) + ((verb, probe) if (verb or probe) else ()))
        return True

    def intent(self, text, ms=None):
        self.calls.append(("intent", text))
        return True

    def risk(self, level):
        self.calls.append(("risk", level))
        return True

    def scan(self, rect=None):
        self.calls.append(("scan", rect))
        return True

    def clear_box(self):
        self.calls.append(("clear_box",))
        return True

    def checkpoint(self, max_wait=120.0):
        self.calls.append(("checkpoint",))

    answer = True

    def confirm(self, text, secs=30.0):
        self.calls.append(("confirm", text))
        return self.answer

    def click(self, button="left", count=1):
        self.calls.append(("click", button, count))
        return True

    def state(self, state):
        self.calls.append(("state", state))
        return True

    def track(self, window_id=None, pid=None):
        self.calls.append(("track", window_id))
        return True

    def hide(self):
        self.calls.append(("hide",))
        return True

    def show(self):
        self.calls.append(("show",))
        return True


class FakeHAL:
    def __init__(self, events):
        self.events = events

    def mouse_move(self, x, y):
        self.events.append(("hal.move", x, y))

    def mouse_click(self, x, y, button="left", click_count=1):
        self.events.append(("hal.click", x, y, button, click_count))
        return "clicked"

    def mouse_drag(self, sx, sy, ex, ey, duration_sec=0.3):
        self.events.append(("hal.drag", sx, sy, ex, ey))

    def keyboard_type(self, text, delay_ms=12.0):
        self.events.append(("hal.type", text))
        if text == "boom":
            raise RuntimeError("boom")

    def focus_window(self, window_id):
        self.events.append(("hal.focus", window_id))
        return window_id != "missing"

    def list_windows(self):
        return [SimpleNamespace(title="Vivado"), SimpleNamespace(title=p.OVERLAY_WINDOW_TITLE + " - Python")]

    def capture_screen(self, region=None):
        self.events.append(("hal.capture", region))
        return "image"

    def get_screen_size(self):
        return (1440, 900)


@pytest.fixture
def wrapped():
    ov = RecordingOverlay()
    events = ov.calls  # one shared timeline for overlay and HAL calls
    return OverlayHAL(FakeHAL(events), ov), events


def test_click_points_first_then_acts_then_ripples(wrapped):
    hal, ev = wrapped
    assert hal.mouse_click(5, 6, "right", 1) == "clicked"
    assert ev == [("checkpoint",), ("move", 5, 6, None, True, "Right-click", True),
                  ("hal.click", 5, 6, "right", 1), ("click", "right", 1)]


def test_coordinate_converter_applies_to_overlay_only():
    ov = RecordingOverlay()
    hal = OverlayHAL(FakeHAL(ov.calls), ov, to_points=lambda x, y: (x / 2, y / 2))
    hal.mouse_move(100, 50)
    assert ov.calls == [("checkpoint",), ("move", 50.0, 25.0, None, True, "Move", False), ("hal.move", 100, 50)]


def test_drag_follows_in_parallel(wrapped):
    hal, ev = wrapped
    hal.mouse_drag(0, 0, 10, 10, 0.5)
    assert ev == [("checkpoint",), ("move", 0, 0, None, True, "Drag", True), ("move", 10, 10, 500, False),
                  ("hal.drag", 0, 0, 10, 10), ("clear_box",)]


def test_typing_badge_reset_even_on_error(wrapped):
    hal, ev = wrapped
    with pytest.raises(RuntimeError):
        hal.keyboard_type("boom")
    assert ev == [("checkpoint",), ("intent", "Type \u201cboom\u201d"), ("state", "typing"),
                  ("hal.type", "boom"), ("state", "idle")]


def test_focus_tracks_only_on_success(wrapped):
    hal, ev = wrapped
    assert hal.focus_window("0x1") is True
    assert hal.focus_window("missing") is False
    assert ev == [("checkpoint",), ("hal.focus", "0x1"), ("track", "0x1"), ("checkpoint",), ("hal.focus", "missing")]


def test_capture_hides_overlay(wrapped):
    hal, ev = wrapped
    assert hal.capture_screen((0, 0, 10, 10)) == "image"
    assert ev == [("hide",), ("hal.capture", (0, 0, 10, 10)), ("show",), ("scan", (0, 0, 10, 10)),
                  ("state", "thinking")]


def test_capture_skips_handshake_when_inactive():
    ov = RecordingOverlay()
    ov.active = False
    hal = OverlayHAL(FakeHAL(ov.calls), ov)
    hal.capture_screen()
    assert ov.calls == [("hal.capture", None)]


def test_list_windows_filters_overlay(wrapped):
    hal, _ = wrapped
    assert [w.title for w in hal.list_windows()] == ["Vivado"]


def test_passthrough_and_unwrap(wrapped):
    hal, _ = wrapped
    assert hal.get_screen_size() == (1440, 900)
    assert isinstance(unwrap(hal), FakeHAL)
    assert wrap_hal(hal) is hal  # never double-wrapped


def test_click_moves_glow_to_active_window_once():
    ov = RecordingOverlay()
    inner = FakeHAL(ov.calls)
    inner.get_active_window = lambda: SimpleNamespace(window_id="77")
    hal = OverlayHAL(inner, ov)
    hal.mouse_click(1, 1)
    hal.mouse_click(2, 2)
    assert [c for c in ov.calls if c[0] == "track"] == [("track", "77")]


def test_active_window_errors_are_ignored():
    ov = RecordingOverlay()
    inner = FakeHAL(ov.calls)

    def broken():
        raise RuntimeError("no AX")

    inner.get_active_window = broken
    assert OverlayHAL(inner, ov).mouse_click(1, 1) == "clicked"


# --------------------------------------------------------------- phase 2
from dobby.overlay import OverlayInterrupted, OverlayStopped, find_overlay_hal, step  # noqa: E402
from dobby.overlay.hal import combo_label, danger_reason, typed_label  # noqa: E402


def test_protocol_v2_ops():
    assert p.decode(p.encode("cursor.move", 1, x=1, y=2, verb="Click", probe=True))["probe"] is True
    assert p.decode(p.encode("risk", None, level="danger"))["level"] == "danger"
    assert p.decode(p.encode("confirm", 3, text="ok?", secs=5))["secs"] == 5
    for bad in ({"op": "risk", "level": "spicy"}, {"op": "scan", "x": 1},
                {"op": "cursor.move", "x": 1, "y": 1, "probe": "yes"},
                {"op": "intent", "text": 5}, {"op": "target.box", "x": 1, "y": 1, "w": True, "h": 1}):
        with pytest.raises(p.ProtocolError):
            p.validate(bad)
    assert json.loads(p.event("stop")) == {"event": "stop"}
    with pytest.raises(p.ProtocolError):
        p.event("explode")


def test_labels():
    assert combo_label(["cmd", "shift", "s"]) == "\u21e7\u2318S"
    assert combo_label(["Return"]) == "Return"
    assert danger_reason(["command", "q"]) == "quit the app"
    assert danger_reason(["cmd", "s"]) is None
    assert typed_label("hello world") == "Type \u201chello world\u201d"
    assert typed_label("x" * 40).endswith("\u2026\u201d")
    assert "\u2022" in typed_label("Hunter2!secret")
    assert "\u2022" not in typed_label("just a sentence")


def test_client_stop_event_raises_at_checkpoint(fake_client):
    client, _ = fake_client
    client.intent("EMIT:stop")
    client.move(1, 1, ms=0)  # round trip so the event has been read
    with pytest.raises(OverlayStopped) as exc:
        client.checkpoint()
    assert not isinstance(exc.value, OverlayInterrupted)
    client.checkpoint()  # stop is one-shot


def test_client_pause_then_resume(fake_client):
    client, _ = fake_client
    client.intent("EMIT:pause")
    client.move(1, 1, ms=0)
    import threading as _t
    _t.Timer(0.3, lambda: client.intent("EMIT:resume")).start()
    t0 = time.monotonic()
    client.checkpoint(max_wait=5)
    assert 0.2 < time.monotonic() - t0 < 3


def test_client_wake_and_sleep_events(fake_client):
    client, _ = fake_client
    woke = []
    client.on_wake(lambda: woke.append(client.awake))
    client.on_wake(lambda: 1 / 0)  # a failing callback is logged, not fatal
    assert client.awake is False
    client.intent("EMIT:wake")
    client.move(1, 1, ms=0)  # round trip so the event has been read
    assert woke == [True] and client.awake is True
    client.intent("EMIT:sleep")
    client.move(1, 1, ms=0)
    assert client.awake is False and woke == [True]
    client.checkpoint()  # waking does not stop or pause


# ----------------------------------------------------------- wait_for_wake
def test_wait_for_wake_returns_when_woken(fake_client):
    client, log = fake_client
    r = client.wait_for_wake(timeout_s=5, reason="EMIT:wake")
    assert r["woken"] and not r["stopped"] and not r["timed_out"] and r["waited_s"] < 3
    client.move(1, 1, ms=0)  # round trip: sleep.done has been written
    assert _ops(log)[:2] == ["sleep.wait", "sleep.done"]
    assert json.loads(log.read_text().splitlines()[0])["reason"] == "EMIT:wake"
    client.checkpoint()  # waking does not stop


def test_wait_for_wake_reports_stop_once(fake_client):
    client, _ = fake_client
    r = client.wait_for_wake(timeout_s=5, reason="EMIT:stop")
    assert r["stopped"] and not r["woken"]
    client.checkpoint()  # the Stop was reported by the wait, not again at the next step


def test_wait_for_wake_times_out(fake_client):
    client, log = fake_client
    t0 = time.monotonic()
    r = client.wait_for_wake(timeout_s=0.3)
    assert r["timed_out"] and not r["woken"] and 0.25 < time.monotonic() - t0 < 2
    client.move(1, 1, ms=0)
    assert _ops(log)[:2] == ["sleep.wait", "sleep.done"]


def test_wait_for_wake_ignores_an_earlier_wake(fake_client):
    client, _ = fake_client
    client.intent("EMIT:wake")
    client.move(1, 1, ms=0)  # that wake has been read; nobody was waiting
    assert client.wait_for_wake(timeout_s=0.3)["timed_out"]


def test_wait_for_wake_reports_a_pending_stop_at_once(fake_client):
    client, log = fake_client
    client.intent("EMIT:stop")
    client.move(1, 1, ms=0)
    assert client.wait_for_wake(timeout_s=5)["stopped"]
    assert "sleep.wait" not in _ops(log)
    client.checkpoint()


def test_wait_for_wake_clears_pause_and_takeover_on_wake(fake_client):
    client, _ = fake_client
    client.intent("EMIT:pause,user_input")
    client.move(1, 1, ms=0)
    assert client.wait_for_wake(timeout_s=5, reason="EMIT:wake")["woken"]
    t0 = time.monotonic()
    client.checkpoint(max_wait=2)  # neither blocked by the old pause nor skipped as interrupted
    assert time.monotonic() - t0 < 0.5


def test_wait_for_wake_can_be_cancelled(fake_client):
    client, log = fake_client
    import threading as _t
    cancel = _t.Event()
    _t.Timer(0.2, cancel.set).start()
    r = client.wait_for_wake(timeout_s=10, cancel=cancel)
    assert r.get("cancelled") and not r["woken"] and r["waited_s"] < 2
    client.move(1, 1, ms=0)
    assert "sleep.done" in _ops(log)


def test_wait_for_wake_unavailable_without_helper(tmp_path):
    client = OverlayClient(cmd=[str(tmp_path / "does-not-exist")])
    assert client.wait_for_wake(timeout_s=5)["unavailable"] is True


def test_wait_for_wake_is_thread_safe(fake_client):
    client, _ = fake_client
    import threading as _t
    out = []
    waiters = [_t.Thread(target=lambda: out.append(client.wait_for_wake(timeout_s=5))) for _ in range(2)]
    for w in waiters:
        w.start()
    time.sleep(0.3)
    client.intent("EMIT:wake")
    for w in waiters:
        w.join(5)
    assert len(out) == 2 and all(r["woken"] for r in out)


def test_null_overlay_wait_for_wake_returns_at_once():
    r = NullOverlay().wait_for_wake(timeout_s=600)
    assert r["woken"] is False and r["unavailable"] is True


def test_sleep_wait_ops_validate():
    assert p.decode('{"op": "sleep.wait", "reason": "done"}')["reason"] == "done"
    assert p.decode('{"op": "sleep.done"}')
    with pytest.raises(p.ProtocolError):
        p.decode('{"op": "sleep.wait", "reason": 3}')


def test_null_overlay_is_never_awake():
    n = NullOverlay()
    assert n.awake is False
    n.on_wake(lambda: None)


def test_client_pause_timeout(fake_client):
    client, _ = fake_client
    client.intent("EMIT:pause")
    client.move(1, 1, ms=0)
    with pytest.raises(OverlayStopped):
        client.checkpoint(max_wait=0.3)


def test_client_takeover_skips_step_after_idle(fake_client):
    client, _ = fake_client
    client.intent("EMIT:user_input")
    client.move(1, 1, ms=0)
    import threading as _t
    _t.Timer(0.3, lambda: client.intent("EMIT:user_idle")).start()
    with pytest.raises(OverlayInterrupted):
        client.checkpoint(max_wait=5)
    client.checkpoint()  # next action goes ahead


def test_client_confirm(fake_client):
    client, log = fake_client
    assert client.confirm("yes?", secs=2) is True
    assert client.confirm("no?", secs=2) is False
    deadline = time.time() + 2  # confirm.cancel is fire-and-forget
    while "confirm.cancel" not in _ops(log) and time.time() < deadline:
        time.sleep(0.02)
    ops = _ops(log)
    assert ops.count("confirm") == 2 and "confirm.cancel" in ops


def test_client_writes_track(tmp_path):
    script = tmp_path / "helper.py"
    script.write_text(FAKE_HELPER)
    client = OverlayClient(cmd=[sys.executable, str(script), str(tmp_path / "log")], track_dir=tmp_path / "tracks")
    client.move(5, 6, ms=0, verb="Click", probe=True)
    client.hide()
    client.risk("caution")
    client.close()
    lines = [json.loads(x) for x in client.track_path.read_text().splitlines()]
    assert [m["op"] for m in lines] == ["cursor.move", "risk"]
    assert all("ts" in m and "id" not in m for m in lines)


def test_danger_hotkey_denied_raises():
    ov = RecordingOverlay()
    ov.answer = False
    events = ov.calls

    class K(FakeHAL):
        def keyboard_hotkey(self, keys):
            events.append(("hal.hotkey", tuple(keys)))

    hal = OverlayHAL(K(events), ov)
    with pytest.raises(OverlayStopped):
        hal.keyboard_hotkey(["cmd", "q"])
    assert ("hal.hotkey", ("cmd", "q")) not in events
    assert ("risk", "danger") in events and events[-2:] == [("risk", "ok"), ("intent", "")]


def test_danger_hotkey_allowed_runs_and_safe_hotkey_skips_confirm():
    ov = RecordingOverlay()
    events = ov.calls

    class K(FakeHAL):
        def keyboard_hotkey(self, keys):
            events.append(("hal.hotkey", tuple(keys)))

    hal = OverlayHAL(K(events), ov)
    hal.keyboard_hotkey(["cmd", "w"])
    assert ("hal.hotkey", ("cmd", "w")) in events
    events.clear()
    hal.keyboard_hotkey(["cmd", "s"])
    assert not any(e[0] == "confirm" for e in events)
    assert ("intent", "Press \u2318S") in events


def test_step_context_sets_intent_and_risk():
    ov = RecordingOverlay()
    hal = OverlayHAL(FakeHAL(ov.calls), ov)
    with step(hal, "Run synthesis", risk="caution"):
        hal.mouse_click(1, 2)
    calls = ov.calls
    assert ("intent", "Run synthesis") in calls and ("risk", "caution") in calls
    move = next(c for c in calls if c[0] == "move")
    assert move[5:] == (None, True)  # verb suppressed inside a step, box still probed
    assert calls[-2:] == [("risk", "ok"), ("intent", "")]


def test_step_denied():
    ov = RecordingOverlay()
    ov.answer = False
    hal = OverlayHAL(FakeHAL(ov.calls), ov)
    with pytest.raises(OverlayStopped):
        with hal.step("Delete the run", risk="danger"):
            hal.mouse_click(1, 2)
    assert not any(c[0] == "hal.click" for c in ov.calls)


def test_step_is_noop_without_overlay_and_finds_session_backend():
    plain = FakeHAL([])
    with step(plain, "anything"):
        pass
    wrapped = OverlayHAL(FakeHAL([]), RecordingOverlay())
    session = SimpleNamespace(backend=wrapped)
    assert find_overlay_hal(session) is wrapped


def test_scroll_and_key_intents(wrapped):
    hal, ev = wrapped
    hal._inner.mouse_scroll = lambda dx, dy: ev.append(("hal.scroll", dx, dy))
    hal._inner.keyboard_press_key = lambda k: ev.append(("hal.key", k))
    hal.mouse_scroll(0, -3)
    hal.keyboard_press_key("Return")
    assert ("intent", "Scroll down") in ev and ("intent", "Press Return") in ev


# --------------------------------------------------------------- annotate
from PIL import Image  # noqa: E402

from dobby.overlay import annotate as an  # noqa: E402


def _track():
    return [
        {"ts": 10.0, "op": "cursor.move", "x": 50, "y": 40, "verb": "Click", "probe": True},
        {"ts": 10.1, "op": "target.box", "x": 40, "y": 30, "w": 30, "h": 20},
        {"ts": 10.5, "op": "cursor.click"},
        {"ts": 11.0, "op": "risk", "level": "danger"},
        {"ts": 11.1, "op": "intent", "text": "Press \u2318Q", "ms": 1000},
        {"ts": 13.0, "op": "cursor.move", "x": 120, "y": 90, "verb": "Click"},
        {"ts": 13.2, "op": "cursor.click"},
        {"ts": 14.0, "op": "trail.clear"},
    ]


def test_state_at_reconstructs_overlay():
    ev = _track()
    s = an.state_at(ev, 10.2)
    assert s.cursor == (50, 40) and s.intent == "Click" and s.box == (40, 30, 30, 20)
    s = an.state_at(ev, 11.5)
    assert s.box is None and s.risk == "danger" and s.intent == "Press \u2318Q" and len(s.trail) == 1
    s = an.state_at(ev, 13.5)
    assert s.cursor == (120, 90) and [n for n, _ in s.trail] == [1, 3]
    assert an.state_at(ev, 14.5).trail == []
    assert an.state_at(ev, 9.0).cursor is None


def test_annotate_draws_pointer_and_box():
    base = Image.new("RGB", (200, 150), (255, 255, 255))
    out = an.annotate(base, an.state_at(_track(), 10.2), glow_rect=(5, 5, 190, 140))
    assert out.size == base.size
    assert out.getpixel((51, 45)) != (255, 255, 255)  # inside the arrow
    assert out.getpixel((150, 120)) == (255, 255, 255)  # untouched area
    scaled = an.annotate(Image.new("RGB", (400, 300), "white"), an.state_at(_track(), 10.2), scale=2)
    assert scaled.getpixel((102, 90)) != (255, 255, 255)


def test_render_gif_and_frames(tmp_path):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    for ts in (10.2, 11.5, 13500):  # last one in ms style -> 13.5 s
        name = f"{ts}.png" if ts < 1e4 else "shot_0000000013500.png"
        Image.new("RGB", (200, 150), "white").save(frames_dir / name)
    found = an.find_frames(frames_dir)
    assert len(found) == 3
    track = tmp_path / "t.jsonl"
    track.write_text("\n".join(json.dumps(e) for e in _track()) + "\nnot json\n")
    assert len(an.load_track(track)) == 8
    gif = tmp_path / "run.gif"
    assert an.main(["--track", str(track), "--frames", str(frames_dir), "--out", str(gif)]) == 0
    assert gif.exists() and Image.open(gif).n_frames >= 2
    pngs = an.render(found, an.load_track(track), tmp_path / "pngs")
    assert len(pngs) == 3


def test_annotate_chip_grows_with_detail_and_uses_risk_colour():
    from PIL import Image
    from dobby.overlay.annotate import TrackState, annotate

    bg = Image.new("RGB", (400, 80), (255, 255, 255))

    def right_edge(img):
        px = img.load()
        y = 22 + 11  # chip vertical centre for cursor at y=0, scale 1
        xs = [x for x in range(img.width) if px[x, y] != (255, 255, 255)]
        return max(xs)

    plain = annotate(bg, TrackState(cursor=(10, 0)))
    detailed = annotate(bg, TrackState(cursor=(10, 0), intent="Click “Run Synthesis”"))
    assert right_edge(detailed) > right_edge(plain) + 60
    red = annotate(bg, TrackState(cursor=(10, 0), risk="danger"))
    r, g, b = red.getpixel((30, 22 + 18))
    assert r > 200 and g < 150


def test_overlay_config_pill_options():
    from dobby.overlay import overlay_config

    assert "pill" not in overlay_config({})
    assert overlay_config({"DOBBY_OVERLAY_PILL": "auto"})["pill"] == "auto"
    assert overlay_config({"DOBBY_OVERLAY_PILL": "0"})["pill"] == "off"
    assert overlay_config({"DOBBY_OVERLAY_PILL_RECORD": "1"})["pill_in_recordings"] is True


def test_client_start_async_starts_one_helper(fake_client):
    client, log = fake_client
    client.start_async()
    client.start_async()
    deadline = time.time() + 5
    while not client.active and time.time() < deadline:
        time.sleep(0.02)
    assert client.active
    pid = client.pid
    assert client.move(1, 2, ms=0) is True
    assert client.pid == pid


def test_wrap_hal_starts_overlay_early_only_for_always_bar():
    from dobby.overlay import wrap_hal
    from dobby.overlay.client import OverlayClient

    class Probe(OverlayClient):
        def __init__(self, cfg):
            super().__init__(cmd=["true"], config=cfg)
            self.started = 0

        def start_async(self):
            self.started += 1

    always, auto = Probe({}), Probe({"pill": "auto"})
    wrap_hal(object(), always)
    wrap_hal(object(), auto)
    assert always.started == 1 and auto.started == 0


def test_bar_owner_lock_is_exclusive_and_handed_over(tmp_path):
    from dobby.overlay.barlock import BarOwner

    path = str(tmp_path / "bar.lock")
    a, b = BarOwner(path), BarOwner(path)
    assert a.try_acquire() and a.owned
    assert not b.try_acquire() and not b.owned
    a.release()
    assert b.try_acquire()
    b.release()


def test_overlay_config_awake_secs():
    from dobby.overlay import overlay_config

    assert "awake_secs" not in overlay_config({})
    assert overlay_config({"DOBBY_OVERLAY_AWAKE_SECS": "30"})["awake_secs"] == 30.0
    assert overlay_config({"DOBBY_OVERLAY_AWAKE_SECS": "1"})["awake_secs"] == 5.0
    assert "awake_secs" not in overlay_config({"DOBBY_OVERLAY_AWAKE_SECS": "soon"})
