"""
Unit and Integration Tests for Local Macro & Trajectory Dispatcher (Option 4).
"""

import pytest
from unittest.mock import MagicMock
from PIL import Image

from dobby.types import (
    TrajectoryStep,
    TrajectorySpec,
    StepExecutionResult,
    TrajectoryExecutionResult,
    BoundingBox,
    WindowInfo,
    VirtualDOMSnapshot,
    VDOMNode,
    GroundingSource,
    UIRole,
)
from dobby.agent_loop.trajectory import TrajectoryEngine
from dobby.agent_loop.controller import FeedbackLoopController


class MockHAL:
    def __init__(self):
        self.windows = [
            WindowInfo(
                window_id="0x401",
                title="Vivado 2025.1 - fifo_sim",
                bbox=BoundingBox(x=0, y=34, width=1920, height=1040),
                is_active=False,
                app_name="vivado",
            ),
            WindowInfo(
                window_id="0x402",
                title="Terminal",
                bbox=BoundingBox(x=100, y=100, width=800, height=600),
                is_active=True,
                app_name="gnome-terminal",
            ),
        ]
        self.focused_window_id = "0x402"
        self.clicks = []
        self.typed_text = []
        self.hotkeys = []
        self.pressed_keys = []
        self.scrolls = []

    def get_screen_size(self):
        return (1920, 1080)

    def capture_screen(self, region=None):
        return Image.new("RGB", (1920, 1080), (240, 240, 240))

    def get_active_window(self):
        for w in self.windows:
            if w.window_id == self.focused_window_id:
                w.is_active = True
                return w
        return self.windows[0]

    def list_windows(self):
        return self.windows

    def focus_window(self, window_id: str):
        self.focused_window_id = window_id
        for w in self.windows:
            w.is_active = (w.window_id == window_id)
        return True

    def mouse_click(self, x, y, button="left", click_count=1):
        self.clicks.append((x, y, button, click_count))

    def mouse_move(self, x, y):
        pass

    def mouse_down(self, button="left"):
        pass

    def mouse_up(self, button="left"):
        pass

    def mouse_scroll(self, dx, dy):
        self.scrolls.append((dx, dy))

    def keyboard_type(self, text, delay_ms=10.0):
        self.typed_text.append(text)

    def keyboard_press_key(self, key):
        self.pressed_keys.append(key)

    def keyboard_hotkey(self, keys):
        self.hotkeys.append(keys)

    def get_cursor_position(self):
        return (100, 100)


def test_trajectory_templating_and_variables():
    """Validates Jinja2 variable interpolation across parameters."""
    hal = MockHAL()
    controller = FeedbackLoopController(hal)
    engine = TrajectoryEngine(controller)

    spec = TrajectorySpec(
        trajectory_name="test_templating",
        variables={"sim_time": "500ns", "target_app": "Vivado"},
        steps=[
            TrajectoryStep(
                step_id="step_focus",
                action="focus_window",
                parameters={"title": "{{target_app}}"},
            ),
            TrajectoryStep(
                step_id="step_type",
                action="type_text",
                parameters={"text": "run {{sim_time}}", "press_enter": True},
            ),
        ],
    )

    res = engine.execute_trajectory(spec)
    assert res.success is True
    assert res.executed_steps == 2
    assert hal.focused_window_id == "0x401"
    assert "run 500ns" in hal.typed_text
    assert "enter" in hal.pressed_keys


def test_trajectory_multi_step_execution(shell_on):
    """Validates execution of multiple distinct action primitives."""
    hal = MockHAL()
    controller = FeedbackLoopController(hal)
    engine = TrajectoryEngine(controller)

    spec = TrajectorySpec(
        trajectory_name="full_verification_pipeline",
        steps=[
            TrajectoryStep(
                step_id="focus",
                action="focus_window",
                parameters={"title": "Vivado"},
            ),
            TrajectoryStep(
                step_id="click_coords",
                action="click",
                parameters={"x": 500, "y": 400, "button": "left"},
                verify_delta=False,
            ),
            TrajectoryStep(
                step_id="send_keys",
                action="hotkey",
                parameters={"keys": ["ctrl", "f5"]},
            ),
            TrajectoryStep(
                step_id="scroll_down",
                action="scroll",
                parameters={"dx": 0, "dy": -3},
            ),
            TrajectoryStep(
                step_id="pause",
                action="wait",
                parameters={"duration_sec": 0.05},
            ),
            TrajectoryStep(
                step_id="audit_vdom",
                action="capture_vdom",
                parameters={},
            ),
            TrajectoryStep(
                step_id="term_step",
                action="run_terminal_command",
                parameters={"command": "echo 'TRAJECTORY_STAGE_OK'"},
            ),
        ],
    )

    res = engine.execute_trajectory(spec)
    assert res.success is True
    assert res.executed_steps == 7
    assert res.successful_steps == 7
    assert res.failed_steps == 0
    assert (500, 400, "left", 1) in hal.clicks
    assert ["ctrl", "f5"] in hal.hotkeys
    assert (0, -3) in hal.scrolls
    assert res.estimated_tokens_saved == 7 * 1600


def test_trajectory_assertions_pass_and_fail(shell_on):
    """Validates step assertions (assert_exit_code, assert_text_present)."""
    hal = MockHAL()
    controller = FeedbackLoopController(hal)
    engine = TrajectoryEngine(controller)

    # 1. Passing assertions
    pass_spec = TrajectorySpec(
        trajectory_name="pass_assertions",
        steps=[
            TrajectoryStep(
                step_id="echo_step",
                action="run_terminal_command",
                parameters={"command": "echo 'SIMULATION_CONVERGED'"},
                assertions=[
                    {"assert_exit_code": 0},
                    {"assert_text_present": "SIMULATION_CONVERGED"},
                ],
            )
        ],
    )
    pass_res = engine.execute_trajectory(pass_spec)
    assert pass_res.success is True
    assert pass_res.step_results[0].success is True

    # 2. Failing assertion
    fail_spec = TrajectorySpec(
        trajectory_name="fail_assertions",
        stop_on_failure=True,
        steps=[
            TrajectoryStep(
                step_id="bad_echo",
                action="run_terminal_command",
                parameters={"command": "echo 'UNEXPECTED_ERROR'"},
                assertions=[
                    {"assert_text_present": "NON_EXISTENT_TOKEN"},
                ],
            ),
            TrajectoryStep(
                step_id="should_not_run",
                action="press_key",
                parameters={"key": "escape"},
            ),
        ],
    )
    fail_res = engine.execute_trajectory(fail_spec)
    assert fail_res.success is False
    assert fail_res.executed_steps == 1  # Aborted at step 1
    assert fail_res.failed_steps == 1
    assert "Assertion failed" in fail_res.step_results[0].error


def test_trajectory_continue_on_failure(shell_on):
    """Validates continue_on_failure flag allowing non-critical steps to fail gracefully."""
    hal = MockHAL()
    controller = FeedbackLoopController(hal)
    engine = TrajectoryEngine(controller)

    spec = TrajectorySpec(
        trajectory_name="resilient_workflow",
        stop_on_failure=True,
        steps=[
            TrajectoryStep(
                step_id="optional_step",
                action="focus_window",
                parameters={"title": "NonExistentApp"},
                continue_on_failure=True,  # Allow this step to fail without halting
            ),
            TrajectoryStep(
                step_id="mandatory_step",
                action="run_terminal_command",
                parameters={"command": "echo 'CRITICAL_STEP_RAN'"},
            ),
        ],
    )

    res = engine.execute_trajectory(spec)
    assert res.executed_steps == 2
    assert res.step_results[0].success is False
    assert res.step_results[1].success is True
    assert "CRITICAL_STEP_RAN" in res.step_results[1].output_data["stdout"]


def test_server_execute_trajectory_tool(shell_on):
    """Validates server execute_trajectory FastMCP tool."""
    from dobby.server import execute_trajectory

    spec_dict = {
        "trajectory_name": "server_mcp_batch",
        "description": "Tests FastMCP endpoint dispatching",
        "steps": [
            {
                "step_id": "test_wait",
                "action": "wait",
                "parameters": {"duration_sec": 0.02},
            },
            {
                "step_id": "test_cmd",
                "action": "run_terminal_command",
                "parameters": {"command": "echo 'MCP_TRAJECTORY_SUCCESS'"},
                "assertions": [{"assert_exit_code": 0}],
            },
        ],
    }

    res = execute_trajectory(trajectory_spec=spec_dict)
    assert res["success"] is True
    assert res["total_steps"] == 2
    assert res["executed_steps"] == 2
    assert res["estimated_tokens_saved"] == 3200


def test_trajectory_invoke_plugin_feature():
    """Validates dispatch of registered application plugin features from trajectory steps."""
    hal = MockHAL()
    hal.focused_window_id = "0x401"  # Vivado window
    controller = FeedbackLoopController(hal)
    engine = TrajectoryEngine(controller)

    spec = TrajectorySpec(
        trajectory_name="test_plugin_feature",
        steps=[
            TrajectoryStep(
                step_id="step_zoom",
                action="invoke_plugin_feature",
                parameters={"feature_id": "vivado.zoom_fit"},
            )
        ],
    )
    res = engine.execute_trajectory(spec)
    assert res.success is True
    assert res.executed_steps == 1
    assert res.step_results[0].action == "invoke_plugin_feature"
