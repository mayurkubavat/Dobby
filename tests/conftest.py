"""Pytest fixtures and mock classes for Vision Control MCP tests."""

import os
import sys

import pytest
from typing import Tuple, List, Optional
from PIL import Image

from dobby.hal.base import IDesktopHAL
from dobby.types import WindowInfo, BoundingBox


class MockDesktopHAL(IDesktopHAL):
    """Deterministic mock HAL for automated unit and headless testing."""

    def __init__(self, width: int = 1920, height: int = 1080) -> None:
        self.width = width
        self.height = height
        self.cursor_x = 100
        self.cursor_y = 100
        self.typed_text: List[str] = []
        self.clicks: List[Tuple[int, int, str, int]] = []
        self.hotkeys: List[List[str]] = []
        self.scrolls: List[Tuple[int, int]] = []

    def get_screen_size(self) -> Tuple[int, int]:
        return self.width, self.height

    def capture_screen(self, region: Optional[Tuple[int, int, int, int]] = None) -> Image.Image:
        w = region[2] if region else self.width
        h = region[3] if region else self.height
        return Image.new("RGB", (w, h), color=(40, 44, 52))

    def get_cursor_position(self) -> Tuple[int, int]:
        return self.cursor_x, self.cursor_y

    def mouse_move(self, x: int, y: int) -> None:
        self.cursor_x = x
        self.cursor_y = y

    def mouse_click(self, x: int, y: int, button: str = "left", click_count: int = 1) -> None:
        self.mouse_move(x, y)
        self.clicks.append((x, y, button, click_count))

    def mouse_drag(self, start_x: int, start_y: int, end_x: int, end_y: int, duration_sec: float = 0.3) -> None:
        self.mouse_move(end_x, end_y)

    def mouse_scroll(self, dx: int, dy: int) -> None:
        self.scrolls.append((dx, dy))

    def keyboard_type(self, text: str, delay_ms: float = 12.0) -> None:
        self.typed_text.append(text)

    def keyboard_press_key(self, key_name: str) -> None:
        self.typed_text.append(f"<{key_name}>")

    def keyboard_hotkey(self, keys: List[str]) -> None:
        self.hotkeys.append(keys)

    def list_windows(self) -> List[WindowInfo]:
        return [
            WindowInfo(
                window_id="0x1000",
                title="Mock Terminal",
                bbox=BoundingBox(x=50, y=50, width=800, height=600),
                is_active=True,
                app_name="gnome-terminal",
            ),
            WindowInfo(
                window_id="0x2000",
                title="Mock Web Browser",
                bbox=BoundingBox(x=900, y=50, width=950, height=1000),
                is_active=False,
                app_name="firefox",
            ),
        ]

    def get_active_window(self) -> Optional[WindowInfo]:
        for win in self.list_windows():
            if win.is_active:
                return win
        return None

    def focus_window(self, window_id: str) -> bool:
        return window_id in ("0x1000", "0x2000")


@pytest.fixture
def mock_hal() -> MockDesktopHAL:
    return MockDesktopHAL()


# ------------------------------------------------- real-desktop input guard
# A test that reaches the real mouse or keyboard types into whatever app is in
# front (it once typed into Chrome). Unless the desktop is disposable, such a
# test is skipped at the point it would post input.
_TRUE = {"1", "true", "yes", "on"}


def _display_is_virtual(display: str, proc_root: str = "/proc", tmp: str = "/tmp") -> bool:
    """True when DISPLAY is served by Xvfb, Xvnc or Xephyr (a throwaway screen)."""
    num = display.rsplit(":", 1)[-1].split(".")[0] if ":" in display else ""
    if not num.isdigit():
        return False
    try:
        with open(os.path.join(tmp, f".X{num}-lock")) as fh:
            pid = int(fh.read().strip())
        with open(os.path.join(proc_root, str(pid), "comm")) as fh:
            name = fh.read().strip()
    except (OSError, ValueError):
        return False
    return name in ("Xvfb", "Xvnc", "Xephyr", "Xvnc4")


def live_desktop_allowed(env=None, platform: str = sys.platform, **kw) -> bool:
    env = os.environ if env is None else env
    flag = env.get("DOBBY_LIVE_DESKTOP_TESTS", "").strip().lower()
    if flag in _TRUE:
        return True
    if flag in ("0", "false", "no", "off"):
        return False  # force the guard on, e.g. to check what would be skipped
    if env.get("DOBBY_REPLAY", "").strip().lower() in _TRUE or env.get("DESKTOP_HAL", "").lower() == "replay":
        return True
    if platform.startswith("linux"):
        return _display_is_virtual(env.get("DISPLAY", ""), **kw)
    return False


def _refuse(*_a, **_k):
    pytest.skip("would send real mouse/keyboard input to this desktop "
                "(set DOBBY_LIVE_DESKTOP_TESTS=1, or run under Xvfb)")


@pytest.fixture(autouse=True, scope="session")
def _guard_real_desktop_input():
    if live_desktop_allowed():
        yield
        return
    mp = pytest.MonkeyPatch()
    if sys.platform == "darwin":
        try:
            import Quartz  # noqa: F401

            for name in ("CGEventPost", "CGEventPostToPid", "CGEventPostToPSN"):
                if hasattr(Quartz, name):
                    mp.setattr(Quartz, name, _refuse)
        except Exception:  # noqa: BLE001 - no Quartz, nothing to guard
            pass
    try:
        from pynput import keyboard, mouse

        for cls in (mouse.Controller, keyboard.Controller):
            for name in ("press", "release", "click", "scroll", "move", "type", "tap"):
                if hasattr(cls, name):
                    mp.setattr(cls, name, _refuse)
        mp.setattr(mouse.Controller, "position",
                   property(lambda self: (0, 0), lambda self, value: _refuse()))
    except Exception:  # noqa: BLE001 - pynput missing or no display
        pass
    try:
        yield
    finally:
        mp.undo()


# ------------------------------------------------- local model role isolation
# Model roles resolve env > runtime > ~/.dobby/models.json > CONFIG. A user's
# real models.json (or DOBBY_MODEL_* in the shell) would point the fake Ollama
# tests at a model the fake does not serve, so every test starts from defaults.
@pytest.fixture(autouse=True)
def _isolate_model_roles(monkeypatch, tmp_path):
    from dobby import local_models as lm

    for role in lm.ROLES:
        monkeypatch.delenv(f"DOBBY_MODEL_{role.upper()}", raising=False)
        lm.set_role(role, None)
    monkeypatch.setattr(lm, "MODELS_FILE", tmp_path / "models.json")
    yield
    for role in lm.ROLES:
        lm.set_role(role, None)


@pytest.fixture(autouse=True)
def _isolate_admin_policy(monkeypatch, tmp_path):
    """Tests never read the machine's real admin policy or the person's shell opt-in."""
    from dobby import policy

    monkeypatch.setattr(policy, "system_path", lambda *a, **k: tmp_path / "no-system-policy.json")
    monkeypatch.delenv("DOBBY_POLICY_FILE", raising=False)
    monkeypatch.delenv("DOBBY_ALLOW_SHELL", raising=False)
    policy.reset_cache()
    yield
    policy.reset_cache()


@pytest.fixture
def shell_on(monkeypatch):
    """The person opted in to terminal commands (DOBBY_ALLOW_SHELL=1)."""
    monkeypatch.setenv("DOBBY_ALLOW_SHELL", "1")


# ------------------------------------------------- real-screen reads
@pytest.fixture
def live_screen():
    """For tests that read the real screen through the server (never input).

    Skips with the reason when this machine has no screen Dobby can capture:
    CI or a cloud container without DISPLAY, Wayland without XWayland, or
    macOS without Screen Recording permission. Elsewhere the test runs.
    """
    from subprocess import CalledProcessError

    from dobby.hal.errors import CapabilityUnavailable

    server = pytest.importorskip("dobby.server")
    try:
        server.hal.capture_screen()
    except (CapabilityUnavailable, CalledProcessError, OSError) as exc:
        pytest.skip(f"no screen to capture on this machine: {str(exc)[:120]}")
    return server


# ------------------------------------------------- menu list tidying
# Stop / Clear finished remember hidden rows in ~/.dobby/overlay/menu_hidden.json;
# keep tests away from the real file.
@pytest.fixture(autouse=True)
def _isolate_menu_hiding(monkeypatch, tmp_path):
    from dobby.overlay import topbar

    monkeypatch.setattr(topbar, "HIDDEN_PATH", str(tmp_path / "menu_hidden.json"))
    topbar._last_active.clear()
    yield
