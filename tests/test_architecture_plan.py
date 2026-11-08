"""Contracts added by the pinlet architecture plan."""

import json
from pathlib import Path

import pytest
from PIL import Image

import pinlet.server as server
from pinlet.agent_loop.bicameral import BicameralStepController, VerificationTier
from pinlet.agent_loop.cache import ActionCache
from pinlet.crystallization.trace_compiler import TraceCompiler, replay_macro
from pinlet.hal.errors import CapabilityUnavailable, CassetteMismatch
from pinlet.hal.factory import get_desktop_hal, reset_desktop_hal
from pinlet.hal.linux_wayland import WaylandHAL
from pinlet.hal.mock_replay import MockReplayHAL, ScreenFrameState, Transition, load_cassette
from pinlet.hal.protocols import IDesktopHALProtocol, IQuiescenceMonitor
from pinlet.perception.grounding.engine import GroundingEngine
from pinlet.perception.grounding.magnitude import match_magnitude
from pinlet.perception.window_clipper import WindowClipper
from pinlet.toolset import OutputMode, ToolSetManager
from pinlet.types import (
    BoundingBox,
    CorrelatedStep,
    DisplayMetrics,
    DisplayTopology,
    RelationalAnchor,
    logical_to_backing,
)


def test_logical_points_survive_retina_scale():
    display = DisplayMetrics(display_id="main", origin=(-1920, 0), size=(1512, 982), scale=2.0)
    assert logical_to_backing((0, 10), display) == (3840, 20)


def test_window_clip_keeps_display_left_of_origin():
    class _Hal:
        topology = DisplayTopology(
            displays=[
                DisplayMetrics(display_id="left", origin=(-1920, 0), size=(1920, 1080), scale=1.0)
            ],
            primary_id="left",
        )

        def list_windows(self):
            return []

        def get_active_window(self):
            return None

    clipper = WindowClipper(_Hal())
    box = BoundingBox(x=-1800, y=40, width=200, height=100)
    assert clipper.clip_region_to_screen(box, (1920, 1080)) == (-1800, 40, 200, 100)


def test_cassette_transition_must_match(tmp_path: Path):
    frames = [
        ScreenFrameState(frame_id="pre", ocr_boxes=[{"text": "Run"}]),
        ScreenFrameState(frame_id="post", ocr_boxes=[{"text": "Done"}]),
    ]
    good = Transition(
        "pre", "mouse_click", {"x": 10, "y": 12, "button": "left", "click_count": 1}, "post"
    )
    hal = MockReplayHAL(states=frames, transitions=[good])
    hal.mouse_click(10, 12)
    assert hal.current_state.frame_id == "post"
    with pytest.raises(CassetteMismatch):
        hal.mouse_click(10, 12)

    index = {
        "frames": [
            {"frame_id": "pre", "ocr_boxes": [{"text": "Run"}]},
            {"frame_id": "post", "ocr_boxes": [{"text": "Done"}]},
        ],
        "transitions": [
            {
                "pre_frame_id": "pre",
                "action": "mouse_click",
                "args": {"x": 3, "y": 4, "button": "left", "click_count": 1},
                "post_frame_id": "post",
            }
        ],
    }
    path = tmp_path / "index.json"
    path.write_text(json.dumps(index), encoding="utf-8")
    loaded = load_cassette(path)
    assert isinstance(loaded, IDesktopHALProtocol)
    loaded.mouse_click(3, 4)
    assert loaded.current_state.frame_id == "post"


def test_factory_boots_replay_cassette(tmp_path: Path, monkeypatch):
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps({"frames": [{"frame_id": "only", "screen_size": [800, 600]}]}),
        encoding="utf-8",
    )
    reset_desktop_hal()
    session = get_desktop_hal(kind="replay", cassette=index)
    assert session.get_screen_size() == (800, 600)
    assert isinstance(session.quiescence, IQuiescenceMonitor)
    phase, _elapsed, _dirty = session.wait_for_quiescence()
    assert phase.value in ("QUIESCENT", "SLEEP_FALLBACK")
    reset_desktop_hal()


def test_wayland_driver_fails_closed():
    hal = WaylandHAL()
    with pytest.raises(CapabilityUnavailable):
        hal.mouse_click(1, 1)


def test_resource_mode_omits_filesystem_path(tmp_path: Path):
    mgr = ToolSetManager(output_mode=OutputMode.RESOURCE, max_inline_tokens=10, state_dir=tmp_path)
    payload = {"active_window": "Vivado", "nodes": [{"name": "Run Synthesis"} for _ in range(40)]}
    result = mgr.format_output("capture_vdom", payload)
    assert result["offloaded"] is True
    assert "file_path" not in result
    assert result["uri"].startswith("mcp://pinlet/state/")
    name = result["uri"].rsplit("/", 1)[-1]
    text = mgr.resolve_state_file(name).read_text(encoding="utf-8")
    assert "Run Synthesis" in text
    with pytest.raises(ValueError):
        mgr.resolve_state_file("../secret.json")


def test_tools_list_follows_active_profile():
    import asyncio

    listed = asyncio.run(server.mcp_server.list_tools())
    names = {tool.name for tool in listed}
    assert "click" in names
    assert "manage_toolsets" in names
    assert "capture_vdom" not in names
    before = len(server._TOOL_LIST_NOTIFICATIONS)
    server.manage_toolsets(action="enable", domains=["vdom"])
    assert len(server._TOOL_LIST_NOTIFICATIONS) == before + 1
    assert server._TOOL_LIST_NOTIFICATIONS[-1] == "notifications/tools/list_changed"
    listed = asyncio.run(server.mcp_server.list_tools())
    assert "capture_vdom" in {tool.name for tool in listed}
    server.manage_toolsets(action="disable", domains=["vdom"])


def test_default_screenshot_is_marks_not_pixels():
    payload = {
        "width": 20,
        "height": 10,
        "scale_factor": 1.0,
        "mime_type": "image/jpeg",
        "image_base64": "abc",
        "marks": [
            {
                "id": 1,
                "role": "push_button",
                "name": "Run",
                "bbox": {"x": 4, "y": 6, "width": 10, "height": 8},
            }
        ],
    }
    res = server._screenshot_summary(payload, include_image=False)
    assert res["marks"] == [{"id": 1, "role": "push_button", "name": "Run", "center": [9, 10]}]
    assert res["image_offloaded"] is True
    assert "image_base64" not in res
    assert str(res["uri"]).startswith("mcp://pinlet/state/")
    inline = server._screenshot_summary(payload, include_image=True)
    assert inline["image_base64"] == "abc"


def test_destructive_modal_escalates_without_a_cloud_client():
    hal = MockReplayHAL(states=[ScreenFrameState(frame_id="m")])
    controller = BicameralStepController(hal=hal)
    verdict = controller.execute_step(
        "mouse_click",
        {"x": 1, "y": 1},
        {
            "observed_modal": "Delete project?",
            "modal_risk": "DESTRUCTIVE_OR_SYSTEM",
        },
    )
    assert verdict.escalated_to_cloud is True
    assert verdict.tier_used == VerificationTier.TIER2_CLOUD_VLM
    assert "unexpected modal" in verdict.reason
    assert verdict.success is False


def test_magnitude_match_is_theme_invariant():
    def icon(foreground, background):
        img = Image.new("RGB", (24, 24), background)
        for i in range(6, 18):
            img.putpixel((i, 12), foreground)
            img.putpixel((12, i), foreground)
        return img

    canvas = Image.new("RGB", (80, 40), (240, 240, 240))
    dark = icon((20, 20, 20), (240, 240, 240))
    canvas.paste(dark, (30, 8))
    inverted = icon((240, 240, 240), (20, 20, 20))
    assert match_magnitude(canvas, inverted) is not None


def test_ground_icon_writes_relational_anchor(tmp_path: Path):
    def icon(foreground, background):
        img = Image.new("RGB", (16, 16), background)
        for i in range(4, 12):
            img.putpixel((i, 8), foreground)
            img.putpixel((8, i), foreground)
        return img

    canvas = Image.new("RGB", (120, 40), (250, 250, 250))
    canvas.paste(icon((10, 10, 10), (250, 250, 250)), (20, 10))
    anchor = RelationalAnchor(label="Run", dx=-20, dy=0, box=(48, 28))
    cache = ActionCache(cache_file_path=str(tmp_path / "loc.json"))
    element = GroundingEngine().ground_icon(
        canvas,
        icon((250, 250, 250), (10, 10, 10)),
        anchor=anchor,
        ocr_lines=[{"text": "Run", "bbox": [40, 10, 30, 16]}],
        cache=cache,
    )
    assert element is not None
    assert "desktop::run" in cache.relational_anchors


def test_macro_replay_is_required_before_registration(tmp_path: Path):
    from pinlet.crystallization.registry import DynamicSkillRegistry

    steps = [
        CorrelatedStep(
            action_id="a",
            gui_intent="click run",
            ui_target_text="Run Synthesis",
        )
    ]
    macro = TraceCompiler().compile_steps(steps, name="run_synth", app_id="demo")
    assert macro.steps[0].action == "find_and_click"
    hal = MockReplayHAL(
        states=[ScreenFrameState(frame_id="f", ocr_boxes=[{"text": "Other"}])],
        transitions=[],
    )
    with pytest.raises(CassetteMismatch):
        replay_macro(macro, hal)
    registry = DynamicSkillRegistry(storage_path=str(tmp_path / "skills.json"))
    with pytest.raises(CassetteMismatch):
        registry.register_macro(macro, hal=hal)
    assert registry.get_macro("run_synth") is None
    ok = MockReplayHAL(
        states=[ScreenFrameState(frame_id="f", ocr_boxes=[{"text": "Run Synthesis"}])],
    )
    assert registry.register_macro(macro, hal=ok) is True
