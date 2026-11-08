"""Live cassette recording through DesktopSession, and the MCP tools."""

import json
from pathlib import Path

import pytest
from PIL import Image

from dobby.hal.cassette_recorder import LiveCassetteRecorder, pixel_delta, sanitize_name
from dobby.hal.cassette_replay import replay_cassette
from dobby.hal.mock_replay import MockReplayHAL, ScreenFrameState
from dobby.hal.session import DesktopSession
from dobby.types import DisplayMetrics, DisplayTopology


def _session():
    backend = MockReplayHAL(states=[ScreenFrameState(frame_id="live")])
    topo = DisplayTopology(
        displays=[DisplayMetrics(display_id="m", origin=(0, 0), size=(64, 48), scale=1.0)],
        primary_id="m",
    )
    return DesktopSession(backend, topo, quiescence=backend), backend


class _FakeScreen:
    """Snapshot source whose OCR text changes with each action."""

    def __init__(self):
        self.texts = ["Run"]
        self.shade = 0

    def __call__(self):
        img = Image.new("RGB", (64, 48), (self.shade, self.shade, self.shade))
        return {"screen_size": [64, 48], "ocr_boxes": [{"text": t} for t in self.texts]}, img


def test_recorded_session_replays(tmp_path: Path):
    session, _ = _session()
    screen = _FakeScreen()
    rec = LiveCassetteRecorder("My Demo!", tmp_path, screen, capture_images=True, settle_ms=0)
    assert rec.name == "my_demo"
    rec.start()
    session.recorder = rec

    session.mouse_click(5, 6)
    screen.texts, screen.shade = ["Running"], 200
    session.keyboard_type("hello")
    screen.texts = ["Done"]
    session.keyboard_hotkey(["ctrl", "s"])
    session.mouse_drag(1, 2, 3, 4)
    session.focus_window("0x1")

    summary = rec.stop()
    assert summary["frames"] == 6 and summary["transitions"] == 5 and summary["images"] == 6
    index = json.loads((tmp_path / "my_demo" / "index.json").read_text())
    assert [t["action"] for t in index["transitions"]] == [
        "mouse_click",
        "keyboard_type",
        "keyboard_hotkey",
        "mouse_drag",
        "focus_window",
    ]
    assert index["transitions"][1]["pre_frame_id"] == index["transitions"][0]["post_frame_id"]
    assert index["frames"][2]["pixel_delta_from_prev"] > 0.5
    assert (tmp_path / "my_demo" / "frames" / "f000.jpg").is_file()

    (tmp_path / "my_demo" / "expect.json").write_text(json.dumps({"text_present": ["Done"]}))
    report = replay_cassette(tmp_path / "my_demo")
    assert report["ok"], report["failures"]
    assert report["steps"] == 5


def test_replay_catches_drift(tmp_path: Path):
    from dobby.hal.errors import CassetteMismatch
    from dobby.hal.mock_replay import load_cassette

    session, _ = _session()
    rec = LiveCassetteRecorder("drift", tmp_path, _FakeScreen(), settle_ms=0)
    rec.start()
    session.recorder = rec
    session.mouse_click(5, 6)
    rec.stop()

    # An agent or macro that clicks somewhere else no longer matches the recording.
    hal = load_cassette(tmp_path / "drift")
    with pytest.raises(CassetteMismatch):
        hal.mouse_click(999, 6)

    # A wrong expectation fails the runner.
    (tmp_path / "drift" / "expect.json").write_text(json.dumps({"text_present": ["Done"]}))
    report = replay_cassette(tmp_path / "drift")
    assert not report["ok"] and "Done" in report["failures"][0]

    # A broken frame chain is reported, not raised.
    index_path = tmp_path / "drift" / "index.json"
    data = json.loads(index_path.read_text())
    data["transitions"][0]["pre_frame_id"] = "f001"
    index_path.write_text(json.dumps(data))
    (tmp_path / "drift" / "expect.json").unlink()
    report = replay_cassette(tmp_path / "drift")
    assert not report["ok"] and "mouse_click" in report["failures"][0]


def test_failing_snapshot_does_not_break_actions(tmp_path: Path):
    session, backend = _session()

    def broken():
        raise RuntimeError("no screen")

    rec = LiveCassetteRecorder("broken", tmp_path, broken, settle_ms=0)
    rec.start()
    session.recorder = rec
    session.keyboard_press_key("Return")
    assert backend.actions_dispatched[-1][0] == "keyboard_press_key"
    summary = rec.stop()
    assert summary["transitions"] == 1 and len(summary["errors"]) == 2


def test_inactive_recorder_and_existing_name(tmp_path: Path):
    session, _ = _session()
    rec = LiveCassetteRecorder("x", tmp_path, _FakeScreen(), settle_ms=0)
    rec.start()
    session.recorder = rec
    rec.stop()
    session.mouse_move(1, 1)  # after stop: not recorded
    assert len(rec.transitions) == 0
    with pytest.raises(FileExistsError):
        LiveCassetteRecorder("x", tmp_path, _FakeScreen()).start()


def test_helpers():
    assert sanitize_name("../../etc") == "etc"
    assert sanitize_name("") == "cassette"
    a = Image.new("RGB", (16, 16), (0, 0, 0))
    assert pixel_delta(a, a) == 0.0
    assert pixel_delta(a, Image.new("RGB", (16, 16), (255, 255, 255))) == 1.0


def test_mcp_tools_record_and_write(tmp_path: Path, monkeypatch):
    import dobby.server as server

    session, _ = _session()

    class _Ctl:
        def cassette_snapshot(self, capture_ocr=True):
            return {
                "screen_size": [64, 48],
                "ocr_boxes": [{"text": "hi"}] if capture_ocr else [],
            }, None

    monkeypatch.setattr(server, "hal", session)
    monkeypatch.setattr(server, "controller", _Ctl())
    monkeypatch.setattr(server.CONFIG, "cassette_dir", str(tmp_path))

    assert server.stop_cassette_recording()["success"] is False
    res = server.start_cassette_recording("tool run", settle_ms=0)
    assert res["success"] and res["name"] == "tool_run"
    assert server.start_cassette_recording("again")["success"] is False
    session.mouse_click(3, 4)
    out = server.stop_cassette_recording()
    assert out["success"] and out["transitions"] == 1
    assert session.recorder is None
    assert replay_cassette(tmp_path / "tool_run")["ok"]
    assert server.start_cassette_recording("tool run")["success"] is False  # name taken
    assert server._TOOL_DOMAINS["start_cassette_recording"][0] == "skills"


def test_controller_snapshot_shape():
    from dobby.agent_loop.controller import FeedbackLoopController

    session, _ = _session()
    frame, image = FeedbackLoopController(session).cassette_snapshot(capture_ocr=False)
    assert frame["screen_size"] == [1920, 1080]
    assert "windows" in frame and "ax_tree" in frame
    assert image is not None
    json.dumps(frame, default=str)
