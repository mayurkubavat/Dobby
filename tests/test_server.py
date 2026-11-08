"""Integration tests for FastMCP server tools."""

from subprocess import CalledProcessError

import pytest

import dobby.server as server


def test_mcp_tools_registered():
    """Verify all required OS vision tools are registered on the MCPServer."""
    tools = server.mcp_server._tool_manager.list_tools()
    tool_names = {t.name for t in tools}

    expected_tools = {
        "take_screenshot",
        "get_accessibility_tree",
        "click",
        "move_cursor",
        "drag_and_drop",
        "scroll",
        "type_text",
        "press_key",
        "hotkey",
        "list_windows",
        "focus_window",
        "run_terminal_command",
        "wait_for_ui_change",
    }

    assert expected_tools.issubset(tool_names)


def test_run_terminal_command(shell_on):
    """Verify terminal tool executes bash commands and captures stdout."""
    res = server.run_terminal_command("echo 'MCP_TEST_OUTPUT'")
    assert res["success"] is True
    assert res["exit_code"] == 0
    assert "MCP_TEST_OUTPUT" in res["stdout"]


def test_take_screenshot_tool(live_screen):
    """Verify take_screenshot tool executes and returns valid payload."""
    try:
        res = server.take_screenshot(mode="raw", include_image=True)
    except CalledProcessError:
        pytest.skip("screen capture is unavailable in this environment")
    assert "image_base64" in res
    assert res["mime_type"] == "image/jpeg"
    assert res["width"] > 0
    assert res["height"] > 0
    assert res["scale_factor"] > 0.0
