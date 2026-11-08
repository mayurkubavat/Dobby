"""Hardware Abstraction Layer (HAL) for Desktop OS Control."""

from pinlet.hal.base import IDesktopHAL
from pinlet.hal.errors import CassetteMismatch
from pinlet.hal.factory import get_desktop_hal
from pinlet.hal.mock_replay import MockReplayHAL, ScreenFrameState, Transition, load_cassette
from pinlet.hal.protocols import (
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
