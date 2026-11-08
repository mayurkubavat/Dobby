"""Screenshots reach the client as image blocks, with a mapping from image pixels to click coordinates."""

import asyncio
import json

from mcp.types import CallToolResult, ImageContent, TextContent

from dobby import server
from dobby.agent_loop.controller import FeedbackLoopController
from dobby.types import BoundingBox, WindowInfo


def test_inlined_image_becomes_an_image_block():
    raw = CallToolResult(
        content=[TextContent(type="text", text="{}")],
        structured_content={
            "result": {"ok": True, "width": 4, "mime_type": "image/png", "image_base64": "QUJD"}
        },
    )
    res = server._image_as_content(raw)
    kinds = [block.type for block in res.content]
    assert kinds == ["text", "image"]
    image = res.content[1]
    assert isinstance(image, ImageContent)
    assert image.data == "QUJD"
    assert image.mime_type == "image/png"
    header = json.loads(res.content[0].text)
    assert "image_base64" not in header
    assert header["width"] == 4
    assert "image_base64" not in res.structured_content["result"]


def test_results_without_an_image_pass_through():
    plain = CallToolResult(
        content=[TextContent(type="text", text="ok")], structured_content={"result": {"ok": True}}
    )
    assert server._image_as_content(plain) is plain
    failed = CallToolResult(content=[], structured_content={"image_base64": "x"}, is_error=True)
    assert server._image_as_content(failed) is failed


def test_take_screenshot_over_mcp_sends_an_image_block(monkeypatch):
    class Shot:
        def model_dump(self):
            return {
                "width": 2,
                "height": 2,
                "scale_factor": 1.0,
                "mime_type": "image/jpeg",
                "image_base64": "QUJD",
                "marks": [],
                "origin_x": 10,
                "origin_y": 20,
                "area_width": 4,
                "area_height": 4,
            }

    monkeypatch.setattr(server.controller, "capture_state", lambda **_: Shot())
    res = asyncio.run(
        server.mcp_server.call_tool("take_screenshot", {"mode": "raw", "include_image": True})
    )
    assert [block.type for block in res.content] == ["text", "image"]
    assert "QUJD" not in res.content[0].text


def test_summary_maps_image_pixels_to_screen():
    payload = {
        "width": 750,
        "height": 500,
        "scale_factor": 1.0,
        "image_base64": "abc",
        "marks": [],
        "origin_x": 100,
        "origin_y": 33,
        "area_width": 1500,
        "area_height": 1000,
    }
    res = server._screenshot_summary(payload, include_image=True)
    assert res["origin"] == [100, 33]
    assert res["screen_per_pixel"] == 2.0


def test_clipped_capture_reports_its_screen_origin(mock_hal):
    ctl = FeedbackLoopController(mock_hal)
    shot = ctl.capture_state(mode="raw", clip_to_active_window=True)
    # The mock's active window sits at (50, 50) and is 800x600.
    assert (shot.origin_x, shot.origin_y) == (50, 50)
    assert (shot.area_width, shot.area_height) == (800, 600)


def test_full_screen_capture_reports_the_whole_screen(mock_hal):
    shot = FeedbackLoopController(mock_hal).capture_state(mode="raw")
    assert (shot.origin_x, shot.origin_y) == (0, 0)
    assert (shot.area_width, shot.area_height) == (1920, 1080)


def test_a_sliver_active_window_is_not_used_for_clipping(mock_hal, monkeypatch):
    strip = WindowInfo(
        window_id="0x9",
        title="bar",
        bbox=BoundingBox(x=14, y=900, width=1484, height=21),
        is_active=True,
        app_name="Dobby",
    )
    monkeypatch.setattr(mock_hal, "get_active_window", lambda: strip)
    shot = FeedbackLoopController(mock_hal).capture_state(mode="raw", clip_to_active_window=True)
    assert (shot.area_width, shot.area_height) == (1920, 1080)
