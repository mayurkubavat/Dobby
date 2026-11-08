"""
Unit and integration tests for Phase 1: Event-Driven Quiescence & Semantic Verification Engine.
Tests X11QuiescenceMonitor, PostConditionContract, and FeedbackLoopController quiescence loop.
"""

import os

import pytest

from dobby.agent_loop.controller import FeedbackLoopController
from dobby.hal.base import IDesktopHAL
from dobby.hal.quiescence import X11QuiescenceMonitor
from dobby.types import (
    BoundingBox,
    ExecutionPhase,
    PostConditionAssertion,
    PostConditionContract,
    StateAssertionType,
    WindowInfo,
)


class MockHAL(IDesktopHAL):
    """Mock Desktop HAL for deterministic unit testing."""

    def get_screen_size(self):
        return (1920, 1080)

    def capture_screen(self, region=None):
        from PIL import Image

        return Image.new("RGB", (100, 100), color="white")

    def get_cursor_position(self):
        return (500, 500)

    def mouse_move(self, x, y):
        pass

    def mouse_click(self, x, y, button="left", click_count=1):
        pass

    def mouse_drag(self, start_x, start_y, end_x, end_y, duration_sec=0.3):
        pass

    def mouse_scroll(self, dx, dy):
        pass

    def keyboard_type(self, text, delay_ms=12.0):
        pass

    def keyboard_press_key(self, key_name: str) -> None:
        pass

    def keyboard_hotkey(self, keys):
        pass

    def list_windows(self):
        return [
            WindowInfo(
                window_id="0x1000",
                title="Vivado 2025.1 - fifo_sim",
                bbox=BoundingBox(x=0, y=0, width=1920, height=1080),
                is_active=True,
            ),
            WindowInfo(
                window_id="0x2000",
                title="Synthesis Completed Successfully",
                bbox=BoundingBox(x=500, y=400, width=400, height=200),
                is_active=False,
            ),
        ]

    def get_active_window(self):
        return self.list_windows()[0]

    def focus_window(self, window_id):
        return True


def test_x11_quiescence_monitor_live():
    """Tests X11QuiescenceMonitor on current active display if available."""
    display = os.environ.get("DISPLAY", ":20")
    monitor = X11QuiescenceMonitor(display_name=display, min_damage_area=64)

    if monitor.is_available:
        cursor_name, serial = monitor.get_cursor_state()
        assert isinstance(cursor_name, str)
        assert isinstance(serial, int)
        assert isinstance(monitor.is_cursor_busy(), bool)
        assert isinstance(monitor.consume_damage_area(), int)

        phase, elapsed, damage = monitor.wait_for_quiescence(
            quiescence_window_ms=50,
            initial_timeout_ms=200,
            max_total_timeout_ms=1000,
        )
        assert phase in (ExecutionPhase.QUIESCENT, ExecutionPhase.SETTLING)
        assert elapsed >= 0.0
        assert damage >= 0
        monitor.close()
        assert monitor.is_available is False
    else:
        pytest.skip("X11 Display not available in test runner environment")


def test_quiescence_monitor_fallback_on_invalid_display():
    """Verifies that X11QuiescenceMonitor handles display connection errors gracefully."""
    monitor = X11QuiescenceMonitor(display_name=":99999", min_damage_area=64)
    assert monitor.is_available is False
    assert monitor.get_cursor_state() == ("unknown", 0)
    assert monitor.is_cursor_busy() is False
    assert monitor.consume_damage_area() == 0

    phase, elapsed, damage = monitor.wait_for_quiescence(
        quiescence_window_ms=50,
        initial_timeout_ms=100,
        max_total_timeout_ms=500,
    )
    assert phase == ExecutionPhase.SLEEP_FALLBACK
    assert elapsed >= 40.0
    assert damage == 0


def test_post_condition_contract_schema():
    """Verifies PostConditionContract and assertion schema serialization."""
    assertion1 = PostConditionAssertion(
        assertion_type=StateAssertionType.WINDOW_SPAWNED,
        target_name_regex=r".*Synthesis Completed.*",
    )
    assertion2 = PostConditionAssertion(
        assertion_type=StateAssertionType.AT_SPI_STATE_PRESENT,
        target_role="push_button",
        target_name_regex="OK",
        expected_state="FOCUSED",
    )

    contract = PostConditionContract(
        assertions=[assertion1, assertion2],
        require_all=True,
        quiescence_window_ms=150,
        initial_reaction_timeout_ms=1200,
        max_total_timeout_ms=8000,
    )

    data = contract.model_dump()
    assert len(data["assertions"]) == 2
    assert data["assertions"][0]["assertion_type"] == "window_spawned"
    assert data["assertions"][1]["expected_state"] == "FOCUSED"
    assert data["quiescence_window_ms"] == 150


def test_controller_contract_verification_window_spawned():
    """Verifies FeedbackLoopController evaluating WINDOW_SPAWNED contract."""
    mock_hal = MockHAL()
    controller = FeedbackLoopController(mock_hal)

    # 1. Matching window should succeed
    contract_success = PostConditionContract(
        assertions=[
            PostConditionAssertion(
                assertion_type=StateAssertionType.WINDOW_SPAWNED,
                target_name_regex=r".*Synthesis Completed.*",
            )
        ]
    )
    passed, details = controller.verify_post_conditions(contract_success)
    assert passed is True
    assert "found 1 windows" in details[0]

    # 2. Non-existent window should fail
    contract_fail = PostConditionContract(
        assertions=[
            PostConditionAssertion(
                assertion_type=StateAssertionType.WINDOW_SPAWNED,
                target_name_regex=r"NonExistentWindow_12345",
            )
        ]
    )
    passed, details = controller.verify_post_conditions(contract_fail)
    assert passed is False
    assert "found 0 windows" in details[0]


def test_controller_contract_verification_window_closed():
    """Verifies FeedbackLoopController evaluating WINDOW_CLOSED contract."""
    mock_hal = MockHAL()
    controller = FeedbackLoopController(mock_hal)

    contract = PostConditionContract(
        assertions=[
            PostConditionAssertion(
                assertion_type=StateAssertionType.WINDOW_CLOSED,
                target_name_regex=r"NonExistentWindow_12345",
            )
        ]
    )
    passed, details = controller.verify_post_conditions(contract)
    assert passed is True
    assert "0 windows remaining" in details[0]


def test_controller_execute_with_verification_and_contract():
    """Verifies full execute_with_verification loop returning ActionVerificationResult."""
    mock_hal = MockHAL()
    controller = FeedbackLoopController(mock_hal)

    contract = PostConditionContract(
        assertions=[
            PostConditionAssertion(
                assertion_type=StateAssertionType.WINDOW_SPAWNED,
                target_name_regex=r".*fifo_sim.*",
            )
        ],
        quiescence_window_ms=50,
    )

    action_executed = False

    def dummy_action():
        nonlocal action_executed
        action_executed = True

    res = controller.execute_with_verification(
        action_name="test_click_with_quiescence",
        action_func=dummy_action,
        contract=contract,
    )

    assert action_executed is True
    assert res.success is True
    assert res.execution_phase in (ExecutionPhase.CONTRACT_VERIFIED, ExecutionPhase.QUIESCENT)
    assert res.post_conditions_passed is True
    assert res.quiescence_elapsed_ms >= 0.0
    assert "Action verified" in res.message
