"""Tests for Hardware Abstraction Layer implementations."""

import os
import sys

import pytest

from pinlet.hal.base import IDesktopHAL
from pinlet.hal.factory import get_desktop_hal
from pinlet.hal.linux import LinuxHAL

_needs_x11 = pytest.mark.skipif(
    sys.platform == "darwin" or sys.platform == "win32" or not os.environ.get("DISPLAY"),
    reason="Linux HAL screen-size check needs an X11 DISPLAY",
)


def test_hal_factory():
    """Verify HAL factory returns an IDesktopHAL instance."""
    hal = get_desktop_hal()
    assert isinstance(hal, IDesktopHAL)


def test_mock_hal_mouse_and_keyboard(mock_hal):
    """Test mock HAL input emulation."""
    mock_hal.mouse_click(250, 400, button="left", click_count=1)
    assert mock_hal.cursor_x == 250
    assert mock_hal.cursor_y == 400
    assert len(mock_hal.clicks) == 1
    assert mock_hal.clicks[0] == (250, 400, "left", 1)

    mock_hal.keyboard_type("ls -la")
    assert "ls -la" in mock_hal.typed_text

    mock_hal.keyboard_hotkey(["ctrl", "c"])
    assert ["ctrl", "c"] in mock_hal.hotkeys


def test_mock_hal_windows(mock_hal):
    """Test window listing in mock HAL."""
    windows = mock_hal.list_windows()
    assert len(windows) == 2
    assert windows[0].title == "Mock Terminal"
    assert windows[0].is_active is True
    assert mock_hal.focus_window("0x1000") is True
    assert mock_hal.focus_window("0x9999") is False


@_needs_x11
def test_linux_hal_screen_size():
    """Verify live Linux HAL reports non-zero screen size."""
    hal = LinuxHAL()
    w, h = hal.get_screen_size()
    assert w > 0
    assert h > 0


@_needs_x11
def test_linux_hal_failsafe_corner():
    """Verify failsafe corner (0, 0) triggers emergency abort."""
    hal = LinuxHAL()
    with pytest.raises(RuntimeError, match="Failsafe Triggered"):
        hal.mouse_move(0, 0)
