"""Native Wayland backend.

Selected on Linux whenever DISPLAY is not set: native Wayland without
XWayland, or no display at all (SSH, CI).
Operations fail closed. XWayland sessions keep using the X11 driver.
"""

from __future__ import annotations

import os

from PIL import Image

from dobby.hal.base import IDesktopHAL
from dobby.hal.errors import CapabilityUnavailable
from dobby.types import WindowInfo

_MSG = (
    "No X11 display: native Wayland capture and input are not supported yet, "
    "and DISPLAY is not set. Run inside an X11 or XWayland session (or Xvfb) "
    "with DISPLAY set, or set DESKTOP_HAL=linux."
)


class WaylandHAL(IDesktopHAL):
    """Fail-closed Wayland driver. It does not silently click through X11."""

    display_server = "Wayland" if os.environ.get("WAYLAND_DISPLAY") else "none"

    def _unavailable(self) -> None:
        raise CapabilityUnavailable(_MSG)

    def get_screen_size(self) -> tuple[int, int]:
        self._unavailable()
        return (0, 0)

    def capture_screen(self, region: tuple[int, int, int, int] | None = None) -> Image.Image:
        self._unavailable()
        return Image.new("RGB", (1, 1))

    def get_cursor_position(self) -> tuple[int, int]:
        self._unavailable()
        return (0, 0)

    def mouse_move(self, x: int, y: int) -> None:
        self._unavailable()

    def mouse_click(self, x: int, y: int, button: str = "left", click_count: int = 1) -> None:
        self._unavailable()

    def mouse_drag(
        self, start_x: int, start_y: int, end_x: int, end_y: int, duration_sec: float = 0.3
    ) -> None:
        self._unavailable()

    def mouse_scroll(self, dx: int, dy: int) -> None:
        self._unavailable()

    def keyboard_type(self, text: str, delay_ms: float = 12.0) -> None:
        self._unavailable()

    def keyboard_press_key(self, key_name: str) -> None:
        self._unavailable()

    def keyboard_hotkey(self, keys: list[str]) -> None:
        self._unavailable()

    def list_windows(self) -> list[WindowInfo]:
        self._unavailable()
        return []

    def get_active_window(self) -> WindowInfo | None:
        self._unavailable()
        return None

    def focus_window(self, window_id: str) -> bool:
        self._unavailable()
        return False


def build_hal() -> WaylandHAL:
    return WaylandHAL()
