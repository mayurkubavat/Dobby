"""Unit tests for VivadoPlugin in Vision MCP."""

from unittest.mock import MagicMock

from PIL import Image

from pinlet.agent_loop.controller import FeedbackLoopController
from pinlet.hal.base import IDesktopHAL
from pinlet.plugins.registry import PluginRegistry
from pinlet.plugins.vivado import VivadoPlugin
from pinlet.types import BoundingBox, WindowInfo


def test_vivado_plugin_detection():
    """Validates window detection logic for Vivado and xsim titles."""
    plugin = VivadoPlugin()

    assert plugin.detect("Vivado v2025.1 (64-bit)") is True
    assert plugin.detect("fifo_sim - [C:/vivado/fifo_sim.xpr] - Vivado 2025.1") is True
    assert plugin.detect("xsim: Untitled 1") is True
    assert plugin.detect("Unknown Window", app_name="vivado") is True
    assert plugin.detect("Unknown Window", app_name="librdi_vivado") is True

    # Non-matching
    assert plugin.detect("Google Chrome") is False
    assert plugin.detect("Visual Studio Code") is False


def test_vivado_sub_panes():
    """Validates that Vivado sub-panes compute valid physical screen ROIs."""
    plugin = VivadoPlugin()
    win_bbox = BoundingBox(x=0, y=34, width=3024, height=1830)

    # 1. Flow Navigator
    fn_box = plugin.get_sub_pane("flow_navigator", win_bbox)
    assert fn_box is not None
    assert fn_box.x == 0
    assert fn_box.y == 34 + 55
    assert fn_box.width == 250
    assert fn_box.height == int(1830 * 0.92) - 55

    # 2. Workspace
    ws_box = plugin.get_sub_pane("workspace", win_bbox)
    assert ws_box is not None
    assert ws_box.x == 250
    assert ws_box.width == 3024 - 250

    # 3. Waveform Canvas
    wc_box = plugin.get_sub_pane("waveform_canvas", win_bbox)
    assert wc_box is not None
    assert wc_box.width > 1000

    # 4. Tcl Console
    tcl_box = plugin.get_sub_pane("tcl_console", win_bbox)
    assert tcl_box is not None
    assert tcl_box.x == 250
    assert tcl_box.height > 200


def test_vivado_feature_catalog():
    """Validates that all expected features are present in the catalog."""
    plugin = VivadoPlugin()
    expected_features = [
        "vivado.launch_simulation",
        "vivado.zoom_fit",
        "vivado.zoom_in",
        "vivado.zoom_out",
        "vivado.run_all",
        "vivado.run_for",
        "vivado.restart",
        "vivado.add_wave",
        "vivado.set_radix",
        "vivado.tcl_exec",
        "vivado.toggle_flow_navigator",
    ]
    for feat_id in expected_features:
        feat = plugin.get_feature(feat_id)
        assert feat is not None
        assert feat.feature_id == feat_id


def test_vivado_plugin_execution_mocked():
    """Validates feature dispatching against a mocked controller and HAL."""
    plugin = VivadoPlugin()

    mock_hal = MagicMock(spec=IDesktopHAL)
    mock_hal.get_screen_size.return_value = (3024, 1898)
    dummy_img = Image.new("RGB", (3024, 1898), color="black")
    mock_hal.capture_screen.return_value = dummy_img

    mock_win = WindowInfo(
        window_id="0x123",
        title="Vivado 2025.1",
        bbox=BoundingBox(x=0, y=0, width=3024, height=1898),
        is_active=True,
    )
    mock_hal.get_active_window.return_value = mock_win

    controller = FeedbackLoopController(hal=mock_hal)

    # 1. Zoom fit
    res = plugin.execute_feature("vivado.zoom_fit", controller)
    assert res["success"] is True
    mock_hal.keyboard_press_key.assert_called_with("f")

    # 2. Zoom in / Zoom out
    res = plugin.execute_feature("vivado.zoom_in", controller)
    assert res["success"] is True
    res = plugin.execute_feature("vivado.zoom_out", controller)
    assert res["success"] is True

    # 3. Run all
    res = plugin.execute_feature("vivado.run_all", controller)
    assert res["success"] is True
    mock_hal.keyboard_press_key.assert_called_with("f5")

    # 4. Restart
    res = plugin.execute_feature("vivado.restart", controller)
    assert res["success"] is True

    # 5. Tcl Exec
    res = plugin.execute_feature("vivado.tcl_exec", controller, {"command": "puts HELLO"})
    assert res["success"] is True
    mock_hal.keyboard_type.assert_called_with("puts HELLO")

    # 6. Run For
    res = plugin.execute_feature("vivado.run_for", controller, {"time": "1000ns"})
    assert res["success"] is True
    mock_hal.keyboard_type.assert_called_with("run 1000ns")

    # 7. Add Wave
    res = plugin.execute_feature("vivado.add_wave", controller, {"signal": "/tb/dut/*"})
    assert res["success"] is True
    mock_hal.keyboard_type.assert_called_with("add_wave /tb/dut/*")

    # 8. Set Radix
    res = plugin.execute_feature("vivado.set_radix", controller, {"radix": "hex"})
    assert res["success"] is True
    mock_hal.keyboard_type.assert_called_with("set_property radix hex [get_waves *]")

    # 9. Toggle Flow Navigator
    res = plugin.execute_feature("vivado.toggle_flow_navigator", controller)
    assert res["success"] is True
    mock_hal.keyboard_hotkey.assert_called_with(["ctrl", "q"])


def test_registry_includes_vivado():
    """Validates that global PluginRegistry automatically recognizes Vivado windows."""
    registry = PluginRegistry()
    detected = registry.detect("Vivado 2025.1 - fifo_sim")
    assert detected is not None
    assert detected.app_id == "vivado"
