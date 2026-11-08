"""
Tests for macOS Automation, Token Efficiency, and Robustness Enhancements.
Validates:
1. Fail-fast error propagation with is_error flag in trajectories.
2. Local OCR assertions (assert_screen_text, assert_screen_text_not_present).
3. Diff-only accessibility tree streaming (AXUIElement / AT-SPI).
4. Coarse-to-fine zoom cropping in take_screenshot.
"""

from unittest.mock import MagicMock

from PIL import Image

from dobby.agent_loop.controller import FeedbackLoopController
from dobby.agent_loop.trajectory import TrajectoryEngine
from dobby.types import (
    BoundingBox,
    TrajectorySpec,
    TrajectoryStep,
    UIElement,
    WindowInfo,
)


class MockHAL:
    def __init__(self):
        self.windows = [
            WindowInfo(
                window_id="0x101",
                title="Gemini — macOS AI Orchestration and Automation",
                bbox=BoundingBox(x=0, y=33, width=1512, height=949),
                is_active=True,
                app_name="Gemini",
            ),
        ]
        self.focused_window_id = "0x101"
        self.clicks = []
        self.typed_text = []

    def get_screen_size(self):
        return (1512, 949)

    def capture_screen(self, region=None):
        w = region[2] if region else 1512
        h = region[3] if region else 949
        return Image.new("RGB", (w, h), (255, 255, 255))

    def get_active_window(self):
        return self.windows[0]

    def list_windows(self):
        return self.windows

    def focus_window(self, window_id: str):
        self.focused_window_id = window_id
        return True

    def mouse_click(self, x, y, button="left", click_count=1):
        self.clicks.append((x, y, button, click_count))

    def mouse_move(self, x, y):
        pass

    def mouse_scroll(self, dx, dy):
        pass

    def keyboard_type(self, text, delay_ms=10.0):
        self.typed_text.append(text)

    def keyboard_press_key(self, key):
        pass

    def keyboard_hotkey(self, keys):
        pass

    def get_cursor_position(self):
        return (500, 400)


def test_trajectory_is_error_flag(shell_on):
    """Validates that trajectory execution results include the fail-fast is_error flag."""
    hal = MockHAL()
    controller = FeedbackLoopController(hal)
    engine = TrajectoryEngine(controller)

    # Successful trajectory -> is_error is False
    pass_spec = TrajectorySpec(
        trajectory_name="test_success_spec",
        steps=[
            TrajectoryStep(
                step_id="step1",
                action="run_terminal_command",
                parameters={"command": "echo 'SUCCESS'"},
            )
        ],
    )
    pass_res = engine.execute_trajectory(pass_spec)
    assert pass_res.success is True
    assert pass_res.is_error is False

    # Failing trajectory -> is_error is True
    fail_spec = TrajectorySpec(
        trajectory_name="test_fail_spec",
        stop_on_failure=True,
        steps=[
            TrajectoryStep(
                step_id="step_fail",
                action="run_terminal_command",
                parameters={"command": "exit 1"},
                assertions=[{"assert_exit_code": 0}],
            )
        ],
    )
    fail_res = engine.execute_trajectory(fail_spec)
    assert fail_res.success is False
    assert fail_res.is_error is True


def test_trajectory_screen_ocr_assertions():
    """Validates assert_screen_text and assert_screen_text_not_present in trajectories."""
    hal = MockHAL()
    controller = FeedbackLoopController(hal)
    engine = TrajectoryEngine(controller)

    # Mock controller.read_screen_text
    def mock_read_screen_text(region=None, contains=None, regex=False):
        if contains and "Target Ready" in contains:
            return {"available": True, "count": 1, "lines": [{"text": "Target Ready"}]}
        return {"available": True, "count": 0, "lines": []}

    controller.read_screen_text = mock_read_screen_text

    # Passing OCR assertion
    spec_pass = TrajectorySpec(
        trajectory_name="test_ocr_pass",
        steps=[
            TrajectoryStep(
                step_id="wait_and_check",
                action="wait",
                parameters={"duration_sec": 0.01},
                assertions=[
                    {"assert_screen_text": "Target Ready"},
                    {"assert_screen_text_not_present": "Spinner Loading"},
                ],
            )
        ],
    )
    res_pass = engine.execute_trajectory(spec_pass)
    assert res_pass.success is True
    assert res_pass.is_error is False

    # Failing OCR assertion
    spec_fail = TrajectorySpec(
        trajectory_name="test_ocr_fail",
        steps=[
            TrajectoryStep(
                step_id="wait_and_check_fail",
                action="wait",
                parameters={"duration_sec": 0.01},
                assertions=[
                    {"assert_screen_text": "NonExistentDialog"},
                ],
            )
        ],
    )
    res_fail = engine.execute_trajectory(spec_fail)
    assert res_fail.success is False
    assert res_fail.is_error is True
    assert "assert_screen_text 'NonExistentDialog': False" in res_fail.step_results[0].error


def test_accessibility_tree_diff_only():
    """Validates diff-only streaming for accessibility trees to prevent multi-turn token explosion."""
    hal = MockHAL()
    controller = FeedbackLoopController(hal)

    elem1 = UIElement(
        id=1,
        role="push button",
        name="Submit",
        bbox=BoundingBox(x=10, y=20, width=80, height=30),
        interactable=True,
        states=["enabled"],
    )
    elem2 = UIElement(
        id=2,
        role="entry",
        name="Search",
        bbox=BoundingBox(x=100, y=20, width=200, height=30),
        interactable=True,
        states=["enabled"],
    )

    mock_inspector = MagicMock()
    controller.atspi_inspector = mock_inspector

    # Step 1: Initial inspection returns full tree under diff_mode
    mock_inspector.inspect_desktop.return_value = [elem1, elem2]
    res1 = controller.get_accessibility_tree(diff_only=True)
    assert res1["diff_mode"] is True
    assert res1["full_tree"] is True
    assert len(res1["added"]) == 2
    assert res1["removed"] == []
    assert res1["modified"] == []
    assert res1["total_count"] == 2

    # Step 2: Unchanged UI returns empty diffs (saving 90%+ tokens)
    mock_inspector.inspect_desktop.return_value = [elem1, elem2]
    res2 = controller.get_accessibility_tree(diff_only=True)
    assert res2["diff_mode"] is True
    assert res2["full_tree"] is False
    assert res2["added"] == []
    assert res2["removed"] == []
    assert res2["modified"] == []
    assert res2["total_count"] == 2

    # Step 3: Modifying button position and adding a new dialog
    elem1_moved = UIElement(
        id=1,
        role="push button",
        name="Submit",
        bbox=BoundingBox(x=15, y=25, width=80, height=30),  # Moved
        interactable=True,
        states=["enabled"],
    )
    elem3_new = UIElement(
        id=3,
        role="dialog",
        name="Confirm Action",
        bbox=BoundingBox(x=300, y=300, width=400, height=200),
        interactable=True,
        states=["enabled"],
    )
    mock_inspector.inspect_desktop.return_value = [
        elem1_moved,
        elem3_new,
    ]  # elem2 removed, elem1 modified, elem3 added
    res3 = controller.get_accessibility_tree(diff_only=True)
    assert res3["full_tree"] is False
    assert len(res3["added"]) == 1
    assert res3["added"][0]["name"] == "Confirm Action"
    assert len(res3["removed"]) == 1
    assert res3["removed"][0]["name"] == "Search"
    assert len(res3["modified"]) == 1
    assert res3["modified"][0]["name"] == "Submit"

    # Step 4: Backward compatibility (diff_only=False returns List[Dict])
    mock_inspector.inspect_desktop.return_value = [elem1]
    res_legacy = controller.get_accessibility_tree(diff_only=False)
    assert isinstance(res_legacy, list)
    assert len(res_legacy) == 1
    assert res_legacy[0]["name"] == "Submit"


def test_take_screenshot_coarse_to_fine_zoom():
    """Validates zoom_factor magnification in screenshot capture."""
    hal = MockHAL()
    controller = FeedbackLoopController(hal)

    # Capture a 100x100 region with 2.0x zoom
    res = controller.capture_state(
        mode="raw",
        region=(10, 10, 100, 100),
        zoom_factor=2.0,
    )
    assert res.zoom_factor == 2.0
    assert res.width == 200
    assert res.height == 200
    assert res.image_base64 is not None
