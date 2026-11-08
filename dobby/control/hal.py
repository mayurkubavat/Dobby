"""HAL wrapper that takes the shared lease around every input action."""

from __future__ import annotations

import functools
from typing import Any

from .controller import Controller

# Methods that move the pointer, type, or change focus: they need the lease.
ACTING = (
    "mouse_move",
    "mouse_click",
    "mouse_drag",
    "mouse_scroll",
    "keyboard_type",
    "keyboard_press_key",
    "keyboard_hotkey",
    "focus_window",
)
# Methods that read the screen: free, but they mark what this agent has seen.
OBSERVING = ("capture_screen", "get_accessibility_tree", "read_screen_text")


class ControlHAL:
    """Wraps any HAL (or OverlayHAL). Unknown attributes pass through, so
    ``hasattr`` checks on the inner HAL keep working.
    """

    def __init__(self, inner: Any, controller: Controller) -> None:
        self._inner = inner
        self._controller = controller

    @property
    def controller(self) -> Controller:
        """This session's controller in a shared Dobby, else the one given."""
        from . import _SESSION_CONTROLLER

        return _SESSION_CONTROLLER.get() or self._controller

    @controller.setter
    def controller(self, value: Controller) -> None:
        self._controller = value

    @property
    def inner(self) -> Any:
        return self._inner

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr
        if name in ACTING:

            @functools.wraps(attr)
            def acting(*args: Any, **kwargs: Any) -> Any:
                with self.controller.action():
                    return attr(*args, **kwargs)

            return acting
        if name in OBSERVING:

            @functools.wraps(attr)
            def observing(*args: Any, **kwargs: Any) -> Any:
                result = attr(*args, **kwargs)
                self.controller.note_observation()
                return result

            return observing
        return attr


def unwrap_control(hal: Any) -> Any:
    while isinstance(hal, ControlHAL):
        hal = hal.inner
    return hal


def find_control_hal(hal: Any) -> ControlHAL | None:
    seen = 0
    while hal is not None and seen < 5:
        if isinstance(hal, ControlHAL):
            return hal
        hal = getattr(hal, "__dict__", {}).get("backend")
        seen += 1
    return None
