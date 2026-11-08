"""Hardware Abstraction Layer (HAL) Protocol Definitions.

Zero-dependency, runtime-checkable contracts. This module imports only the
stdlib and pinlet.types so Linux CI can load it without Quartz, pywin32,
or Xlib.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pinlet.types import DisplayTopology, WindowInfo


@runtime_checkable
class IScreenCapture(Protocol):
    """Display capture and resolution metrics."""

    def get_screen_size(self) -> tuple[int, int]:
        """Primary screen size as (width, height) in logical points."""
        ...

    def capture_screen(self, region: tuple[int, int, int, int] | None = None) -> Any:
        """Captures the display or a sub-region. Region is (x, y, width, height)."""
        ...


@runtime_checkable
class IInputDriver(Protocol):
    """Low-level mouse and keyboard input in logical global points."""

    def get_cursor_position(self) -> tuple[int, int]:
        """Current pointer in logical global points."""
        ...

    def mouse_move(self, x: int, y: int) -> None:
        """Moves the pointer to logical global coordinates."""
        ...

    def mouse_click(self, x: int, y: int, button: str = "left", click_count: int = 1) -> None:
        """Clicks at logical global coordinates."""
        ...

    def mouse_drag(
        self, start_x: int, start_y: int, end_x: int, end_y: int, duration_sec: float = 0.3
    ) -> None:
        """Drags the pointer from start to end in logical points."""
        ...

    def mouse_scroll(self, dx: int, dy: int) -> None:
        """Scrolls the wheel. Positive dy is up."""
        ...

    def keyboard_type(self, text: str, delay_ms: float = 12.0) -> None:
        """Types text."""
        ...

    def keyboard_press_key(self, key_name: str) -> None:
        """Presses and releases one key."""
        ...

    def keyboard_hotkey(self, keys: list[str]) -> None:
        """Presses a chord and releases it in reverse order."""
        ...


@runtime_checkable
class IWindowManager(Protocol):
    """Window enumeration and focus."""

    def list_windows(self) -> list[WindowInfo]:
        """Top-level windows."""
        ...

    def get_active_window(self) -> WindowInfo | None:
        """Focused top-level window, if any."""
        ...

    def focus_window(self, window_id: str) -> bool:
        """Raises the window identified by window_id."""
        ...


@runtime_checkable
class IAXTreeDriver(Protocol):
    """Accessibility tree extraction and diffing."""

    def get_accessibility_tree(
        self,
        app_filter: str | None = None,
        max_depth: int | None = None,
        diff_only: bool = False,
    ) -> dict[str, Any]:
        """Returns the accessibility tree, or a diff when diff_only is set."""
        ...


@runtime_checkable
class IQuiescenceMonitor(Protocol):
    """Event-driven UI settle signal. Sleep is reported as SLEEP_FALLBACK."""

    is_available: bool

    def arm(self) -> None:
        """Clears damage accumulated before the action."""
        ...

    def wait_for_quiescence(
        self,
        quiescence_window_ms: int = 180,
        initial_timeout_ms: int = 1500,
        max_total_timeout_ms: int = 15000,
        poll_interval_sec: float = 0.025,
    ) -> tuple[Any, float, int]:
        """Returns (phase, elapsed_ms, dirty_area)."""
        ...


@runtime_checkable
class IDesktopHALProtocol(IScreenCapture, IInputDriver, IWindowManager, Protocol):
    """Capture, input, and windows. AX and quiescence stay separate."""

    ...


@runtime_checkable
class IDesktopSession(Protocol):
    """Facade over the segregated drivers. Not a god class."""

    capture: IScreenCapture
    input: IInputDriver
    windows: IWindowManager
    ax: IAXTreeDriver
    quiescence: IQuiescenceMonitor
    topology: DisplayTopology
