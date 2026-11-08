"""Session facade. Callers keep using HAL methods; drivers stay segregated."""

from __future__ import annotations

from typing import Any

from PIL import Image
from typing_extensions import override

from dobby.hal.base import IDesktopHAL
from dobby.types import DisplayTopology, WindowInfo


class NullAXDriver:
    """AX is not on this backend. Callers get an explicit empty tree."""

    def get_accessibility_tree(
        self,
        app_filter: str | None = None,
        max_depth: int | None = None,
        diff_only: bool = False,
    ) -> dict[str, Any]:
        """Reports that no accessibility tree is available."""
        if diff_only:
            return {"available": False, "changed": False, "nodes": []}
        return {"available": False, "nodes": []}


class DesktopSession(IDesktopHAL):
    """IDesktopHAL plus the segregated drivers from IDesktopSession."""

    def __init__(
        self,
        backend: IDesktopHAL,
        topology: DisplayTopology,
        quiescence: Any,
        ax: Any | None = None,
    ) -> None:
        self.backend = backend
        self.capture = backend
        self.input = backend
        self.windows = backend
        self.ax = (
            ax
            if ax is not None
            else (backend if hasattr(backend, "get_accessibility_tree") else NullAXDriver())
        )
        self.quiescence = quiescence
        self.topology = topology
        # Optional LiveCassetteRecorder; input methods report to it after acting.
        self.recorder: Any = None

    def _record(self, action: str, args: dict[str, Any]) -> None:
        rec = self.recorder
        if rec is not None and getattr(rec, "active", False):
            rec.after_action(action, args)

    def __getattr__(self, name: str) -> Any:
        """Backend-specific attributes (display_server, backing_scale, ...) pass through."""
        if name in ("backend", "recorder"):
            raise AttributeError(name)
        return getattr(self.backend, name)

    @override
    def get_screen_size(self) -> tuple[int, int]:
        return self.backend.get_screen_size()

    @override
    def capture_screen(self, region: tuple[int, int, int, int] | None = None) -> Image.Image:
        return self.backend.capture_screen(region)

    @override
    def get_cursor_position(self) -> tuple[int, int]:
        return self.backend.get_cursor_position()

    @override
    def mouse_move(self, x: int, y: int) -> None:
        self.backend.mouse_move(x, y)
        self._record("mouse_move", {"x": x, "y": y})

    @override
    def mouse_click(self, x: int, y: int, button: str = "left", click_count: int = 1) -> None:
        self.backend.mouse_click(x, y, button=button, click_count=click_count)
        self._record("mouse_click", {"x": x, "y": y, "button": button, "click_count": click_count})

    @override
    def mouse_drag(
        self, start_x: int, start_y: int, end_x: int, end_y: int, duration_sec: float = 0.3
    ) -> None:
        self.backend.mouse_drag(start_x, start_y, end_x, end_y, duration_sec)
        self._record(
            "mouse_drag",
            {
                "start_x": start_x,
                "start_y": start_y,
                "end_x": end_x,
                "end_y": end_y,
                "duration_sec": duration_sec,
            },
        )

    @override
    def mouse_scroll(self, dx: int, dy: int) -> None:
        self.backend.mouse_scroll(dx, dy)
        self._record("mouse_scroll", {"dx": dx, "dy": dy})

    @override
    def keyboard_type(self, text: str, delay_ms: float = 12.0) -> None:
        self.backend.keyboard_type(text, delay_ms=delay_ms)
        self._record("keyboard_type", {"text": text, "delay_ms": delay_ms})

    @override
    def keyboard_press_key(self, key_name: str) -> None:
        self.backend.keyboard_press_key(key_name)
        self._record("keyboard_press_key", {"key_name": key_name})

    @override
    def keyboard_hotkey(self, keys: list[str]) -> None:
        self.backend.keyboard_hotkey(keys)
        self._record("keyboard_hotkey", {"keys": list(keys)})

    @override
    def list_windows(self) -> list[WindowInfo]:
        return self.backend.list_windows()

    @override
    def get_active_window(self) -> WindowInfo | None:
        return self.backend.get_active_window()

    @override
    def focus_window(self, window_id: str) -> bool:
        ok = self.backend.focus_window(window_id)
        self._record("focus_window", {"window_id": window_id})
        return ok

    def get_accessibility_tree(
        self,
        app_filter: str | None = None,
        max_depth: int | None = None,
        diff_only: bool = False,
    ) -> dict[str, Any]:
        """Returns the accessibility tree from the session's AX driver."""
        return self.ax.get_accessibility_tree(
            app_filter=app_filter, max_depth=max_depth, diff_only=diff_only
        )

    def wait_for_quiescence(self, **kwargs: Any) -> Any:
        """Delegates to the session's quiescence monitor."""
        return self.quiescence.wait_for_quiescence(**kwargs)
