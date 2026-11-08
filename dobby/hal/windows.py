"""Windows Desktop Hardware Abstraction Layer stub / implementation."""

import sys

from PIL import Image

from dobby.hal.base import IDesktopHAL
from dobby.hal.errors import CapabilityUnavailable
from dobby.types import DisplayMetrics, DisplayTopology, WindowInfo


class WindowsHAL(IDesktopHAL):
    """Windows 10/11 Desktop HAL implementation.
    Architected for dxcam (DXGI GPU screen capture), Win32 SendInput (PerMonitorV2 DPI aware),
    and pywinauto / UIA3 window querying.
    """

    def __init__(self) -> None:
        if sys.platform != "win32":
            # Allowed for test environments or cross-compilation mocks
            pass
        else:
            self._init_dpi_awareness()

    def _init_dpi_awareness(self) -> None:
        """Initializes DPI awareness context to avoid coordinate drift on scaled screens."""
        try:
            import ctypes

            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
            ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
        except Exception:
            pass

    def _user32(self):
        import ctypes

        return ctypes.windll.user32

    def display_topology(self) -> DisplayTopology:
        """Per-monitor logical size. scale is dpi/96 under Per-Monitor V2."""
        if sys.platform != "win32":
            return DisplayTopology(
                displays=[
                    DisplayMetrics(
                        display_id="primary", origin=(0, 0), size=(1920, 1080), scale=1.0
                    )
                ],
                primary_id="primary",
            )
        user32 = self._user32()
        width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        dpi = 96
        try:
            dpi = int(user32.GetDpiForSystem())
        except Exception:
            pass
        return DisplayTopology(
            displays=[
                DisplayMetrics(
                    display_id="primary",
                    origin=(int(user32.GetSystemMetrics(76)), int(user32.GetSystemMetrics(77))),
                    size=(int(width), int(height)),
                    scale=dpi / 96.0,
                )
            ],
            primary_id="primary",
        )

    def get_screen_size(self) -> tuple[int, int]:
        if sys.platform != "win32":
            return (1920, 1080)
        user32 = self._user32()
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)

    def capture_screen(self, region: tuple[int, int, int, int] | None = None) -> Image.Image:
        """Captures screen using DXGI / dxcam on Windows."""
        try:
            import dxcam

            camera = dxcam.create(output_color="RGB")
            frame = camera.grab(region=region) if region else camera.grab()
            return Image.fromarray(frame)
        except Exception:
            # Fallback to Pillow ImageGrab
            from PIL import ImageGrab

            bbox = (
                (region[0], region[1], region[0] + region[2], region[1] + region[3])
                if region
                else None
            )
            return ImageGrab.grab(bbox=bbox)

    def get_cursor_position(self) -> tuple[int, int]:
        if sys.platform != "win32":
            return (0, 0)
        import ctypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def _send_mouse(self, flags: int, data: int = 0, dx: int = 0, dy: int = 0) -> None:
        """SendInput absolute or button event. MOUSEEVENTF_VIRTUALDESK covers other monitors."""
        import ctypes

        user32 = self._user32()
        extra = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", ctypes.c_long),
                ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", extra),
            ]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", ctypes.c_ulong), ("mi", MOUSEINPUT)]

        inp = INPUT(type=0, mi=MOUSEINPUT(dx, dy, data, flags, 0, 0))
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def mouse_move(self, x: int, y: int) -> None:
        if sys.platform != "win32":
            return
        user32 = self._user32()
        vx = int(user32.GetSystemMetrics(76))
        vy = int(user32.GetSystemMetrics(77))
        vw = max(int(user32.GetSystemMetrics(78)) - 1, 1)
        vh = max(int(user32.GetSystemMetrics(79)) - 1, 1)
        ax = int((x - vx) * 65535 / vw)
        ay = int((y - vy) * 65535 / vh)
        # MOVE | ABSOLUTE | VIRTUALDESK
        self._send_mouse(0x0001 | 0x8000 | 0x4000, dx=ax, dy=ay)

    def mouse_click(self, x: int, y: int, button: str = "left", click_count: int = 1) -> None:
        if sys.platform != "win32":
            return
        self.mouse_move(x, y)
        import time

        flags = {
            "left": (0x0002, 0x0004),
            "right": (0x0008, 0x0010),
            "middle": (0x0020, 0x0040),
        }.get(button, (0x0002, 0x0004))
        for _ in range(click_count):
            self._send_mouse(flags[0])
            time.sleep(0.01)
            self._send_mouse(flags[1])
            time.sleep(0.01)

    def mouse_drag(
        self, start_x: int, start_y: int, end_x: int, end_y: int, duration_sec: float = 0.3
    ) -> None:
        if sys.platform != "win32":
            return
        self.mouse_move(start_x, start_y)
        import time

        self._send_mouse(0x0002)
        try:
            steps = 30
            for i in range(1, steps + 1):
                cur_x = int(start_x + (end_x - start_x) * (i / steps))
                cur_y = int(start_y + (end_y - start_y) * (i / steps))
                self.mouse_move(cur_x, cur_y)
                time.sleep(duration_sec / steps)
        finally:
            self._send_mouse(0x0004)

    def mouse_scroll(self, dx: int, dy: int) -> None:
        if sys.platform != "win32":
            return
        import ctypes

        # MOUSEEVENTF_WHEEL = 0x0800, wheel delta 120 per click
        ctypes.windll.user32.mouse_event(0x0800, 0, 0, int(dy * 120), 0)

    def keyboard_type(self, text: str, delay_ms: float = 12.0) -> None:
        from pynput.keyboard import Controller

        kb = Controller()
        import time

        for char in text:
            kb.type(char)
            time.sleep(delay_ms / 1000.0)

    def keyboard_press_key(self, key_name: str) -> None:
        from pynput.keyboard import Controller

        kb = Controller()
        kb.press(key_name)
        kb.release(key_name)

    def keyboard_hotkey(self, keys: list[str]) -> None:
        from pynput.keyboard import Controller

        kb = Controller()
        for k in keys:
            kb.press(k)
        for k in reversed(keys):
            kb.release(k)

    def list_windows(self) -> list[WindowInfo]:
        """UIA window enumeration is not implemented. Fail closed."""
        raise CapabilityUnavailable("Windows UIA window enumeration is not implemented")

    def get_active_window(self) -> WindowInfo | None:
        """Returns the currently active window on Windows."""
        raise CapabilityUnavailable("Windows UIA active-window query is not implemented")

    def focus_window(self, window_id: str) -> bool:
        raise CapabilityUnavailable("Windows SetForegroundWindow is not implemented")


def build_hal() -> WindowsHAL:
    return WindowsHAL()
