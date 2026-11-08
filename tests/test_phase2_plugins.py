"""Unit and integration tests for Phase 2: UI Plugin Framework & Chrome Browser Plugin."""

from unittest.mock import MagicMock

from PIL import Image

from dobby.agent_loop.controller import FeedbackLoopController
from dobby.plugins.chrome import ChromeBrowserPlugin
from dobby.plugins.registry import PluginRegistry
from dobby.server import mcp_server
from dobby.types import BoundingBox, SubPaneROI, WindowInfo


def test_sub_pane_roi_computation():
    """Validates that SubPaneROI computes exact physical bounding boxes relative to window bounds."""
    win_bbox = BoundingBox(x=100, y=200, width=1000, height=800)

    # 1. Fixed height pane (e.g. Tab strip)
    tab_strip = SubPaneROI(
        name="tab_strip",
        relative_x=0.0,
        relative_y=0.0,
        relative_width=1.0,
        fixed_height=42,
    )
    pane_box = tab_strip.compute_bounds(win_bbox)
    assert pane_box.x == 100
    assert pane_box.y == 200
    assert pane_box.width == 1000
    assert pane_box.height == 42

    # 2. Offset pane with fixed height (e.g. Address bar)
    address_bar = SubPaneROI(
        name="address_bar",
        relative_x=0.0,
        relative_y=0.0,
        offset_y=42,
        relative_width=1.0,
        fixed_height=44,
    )
    pane_box = address_bar.compute_bounds(win_bbox)
    assert pane_box.x == 100
    assert pane_box.y == 242
    assert pane_box.width == 1000
    assert pane_box.height == 44

    # 3. Flexible content area covering remainder
    content_area = SubPaneROI(
        name="content_area",
        relative_x=0.0,
        relative_y=0.0,
        offset_y=86,
        relative_width=1.0,
        relative_height=1.0,
    )
    pane_box = content_area.compute_bounds(win_bbox)
    assert pane_box.x == 100
    assert pane_box.y == 286
    assert pane_box.width == 1000
    assert pane_box.height == 800 - 86  # 714


def test_chrome_plugin_detection():
    """Validates window detection logic for Chrome and Chromium titles."""
    plugin = ChromeBrowserPlugin()

    # Matches
    assert plugin.detect("Google Chrome") is True
    assert plugin.detect("Google Gemini - Google Chrome") is True
    assert plugin.detect("New Tab - Chromium") is True
    assert plugin.detect("random title", app_name="google-chrome") is True

    # Non-matches
    assert plugin.detect("mk@linux: ~/DV") is False
    assert plugin.detect("Antigravity IDE") is False
    assert plugin.detect("Verdi - nWave") is False


def test_plugin_registry_lifecycle():
    """Tests registration, query, and detection in PluginRegistry."""
    registry = PluginRegistry()

    # Chrome is auto-registered
    chrome = registry.get("chrome")
    assert chrome is not None
    assert chrome.app_id == "chrome"

    # Detection by title
    detected = registry.detect("Architecture of Vision - Google Chrome")
    assert detected is not None
    assert detected.app_id == "chrome"

    # Listing summaries
    summaries = registry.list_all(active_window_title="Google Chrome")
    assert len(summaries) >= 1
    chrome_sum = next(s for s in summaries if s.app_id == "chrome")
    assert chrome_sum.is_active is True
    assert "address_bar" in chrome_sum.supported_panes
    assert chrome_sum.features_count >= 10


def test_chrome_feature_catalog():
    """Verifies all essential declarative browser capabilities exist in ChromeBrowserPlugin."""
    plugin = ChromeBrowserPlugin()
    features = plugin.list_features()
    feature_ids = [f.feature_id for f in features]

    expected = [
        "browser.navigate",
        "browser.new_tab",
        "browser.close_tab",
        "browser.switch_tab",
        "browser.reload",
        "browser.zoom_in",
        "browser.zoom_out",
        "browser.zoom_reset",
        "browser.find_in_page",
        "browser.devtools",
        "browser.scroll_content",
        "browser.dismiss_dialog",
        "browser.gemini_query",
    ]
    for exp in expected:
        assert exp in feature_ids, f"Expected feature {exp} in Chrome feature catalog"


def test_chrome_feature_execution_with_mock_hal(mock_hal):
    """Tests executing declarative features through ChromeBrowserPlugin."""
    plugin = ChromeBrowserPlugin()

    # Setup controller with mock hal
    controller = FeedbackLoopController(mock_hal)
    controller.pipeline.capture = MagicMock(
        return_value=(Image.new("RGB", (100, 100)), 1.0, (100, 100), (0, 0))
    )
    controller.pipeline.compute_image_delta = MagicMock(return_value=0.05)

    # 1. Execute new_tab
    res = plugin.execute_feature("browser.new_tab", controller)
    assert res["success"] is True
    assert ["ctrl", "t"] in mock_hal.hotkeys

    # 2. Execute close_tab
    res = plugin.execute_feature("browser.close_tab", controller)
    assert res["success"] is True
    assert ["ctrl", "w"] in mock_hal.hotkeys

    # 3. Execute reload
    res = plugin.execute_feature("browser.reload", controller)
    assert res["success"] is True
    assert ["ctrl", "r"] in mock_hal.hotkeys

    # 4. Execute navigate
    res = plugin.execute_feature(
        "browser.navigate",
        controller,
        args={"url": "https://gemini.google.com", "new_tab": False},
    )
    assert res["success"] is True
    assert ["ctrl", "l"] in mock_hal.hotkeys
    assert "https://gemini.google.com" in mock_hal.typed_text
    assert "<enter>" in mock_hal.typed_text

    # 5. Execute scroll_content
    res = plugin.execute_feature(
        "browser.scroll_content",
        controller,
        args={"direction": "down", "amount": 12},
    )
    assert res["success"] is True
    assert (0, -12) in mock_hal.scrolls


def test_controller_capture_pane(mock_hal):
    """Tests capture_pane sub-pane perception in FeedbackLoopController."""
    controller = FeedbackLoopController(mock_hal)
    mock_hal.get_active_window = MagicMock(
        return_value=WindowInfo(
            window_id="0x6400003",
            title="Google Gemini - Google Chrome",
            bbox=BoundingBox(x=0, y=34, width=3024, height=1830),
            is_active=True,
        )
    )

    # Mock capture pipeline
    dummy_img = Image.new("RGB", (500, 44), color=(255, 255, 255))
    controller.pipeline.capture = MagicMock(return_value=(dummy_img, 1.0, (3024, 44), (100, 50)))

    res = controller.capture_pane(pane_name="address_bar")
    assert res is not None
    assert res.image_base64 is not None
    controller.pipeline.capture.assert_called_once()
    call_kwargs = controller.pipeline.capture.call_args[1]
    # Region passed to capture should be bounded to address bar (height ~ 44)
    region = call_kwargs.get("region")
    assert region is not None
    assert region[3] == 44  # height


def test_phase2_mcp_server_tools_registered():
    """Verifies that the new Phase 2 tools are exposed on the MCP server."""
    registered = [t.name for t in mcp_server._tool_manager.list_tools()]

    expected_phase2_tools = [
        "list_plugins",
        "list_plugin_features",
        "invoke_plugin_feature",
        "capture_pane",
    ]
    for tool_name in expected_phase2_tools:
        assert tool_name in registered, (
            f"Expected Phase 2 tool {tool_name} to be registered on mcp_server"
        )
