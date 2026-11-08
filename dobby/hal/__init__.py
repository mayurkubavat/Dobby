"""Hardware Abstraction Layer (HAL) for Desktop OS Control."""

from dobby.hal.base import IDesktopHAL
from dobby.hal.errors import CassetteMismatch
from dobby.hal.factory import get_desktop_hal
from dobby.hal.mock_replay import MockReplayHAL, ScreenFrameState, Transition, load_cassette
from dobby.hal.protocols import (
    IAXTreeDriver,
    IDesktopHALProtocol,
    IDesktopSession,
    IInputDriver,
    IQuiescenceMonitor,
    IScreenCapture,
    IWindowManager,
)

__all__ = [
    "CassetteMismatch",
    "IAXTreeDriver",
    "IDesktopHAL",
    "IDesktopHALProtocol",
    "IDesktopSession",
    "IInputDriver",
    "IQuiescenceMonitor",
    "IScreenCapture",
    "IWindowManager",
    "MockReplayHAL",
    "ScreenFrameState",
    "Transition",
    "get_desktop_hal",
    "load_cassette",
]
