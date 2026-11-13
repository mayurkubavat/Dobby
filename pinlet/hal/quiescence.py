"""Event-Driven X11 Quiescence Monitor using libXdamage and libXfixes via ctypes.

Provides sub-millisecond visibility into X11 screen damage regions and mouse cursor
busy/hourglass states without incurring screenshot overhead or relying on static sleep delays.
"""

import ctypes
import ctypes.util
import logging
import os
import time
from ctypes import POINTER, Structure, c_char_p, c_int, c_short, c_ulong, c_ushort

from pinlet.types import ExecutionPhase, QuiescenceReport

logger = logging.getLogger(__name__)


class XRectangle(Structure):
    """X11 Rectangle structure."""

    _fields_ = [
        ("x", c_short),
        ("y", c_short),
        ("width", c_ushort),
        ("height", c_ushort),
    ]


class XFixesCursorImage(Structure):
    """XFixes Cursor Image structure from Xfixes.h."""

    _fields_ = [
        ("x", c_short),
        ("y", c_short),
        ("width", c_ushort),
        ("height", c_ushort),
        ("xhot", c_ushort),
        ("yhot", c_ushort),
        ("cursor_serial", c_ulong),
        ("pixels", POINTER(c_ulong)),
        ("atom", c_ulong),
        ("name", c_char_p),
    ]


class X11QuiescenceMonitor:
    """Sub-millisecond X11 screen buffer damage and cursor state monitor.

    Uses XDamage (DamageReportRawRectangles) to track dirty buffer regions and XFixes to monitor
    cursor pointer transformations (watch, wait, spinning).
    """

    BUSY_CURSOR_NAMES: set[str] = {
        "watch",
        "wait",
        "progress",
        "left_ptr_watch",
        "clock",
        "spinning",
        "busy",
        "half-busy",
        "sandclock",
    }

    def __init__(
        self,
        display_name: str | None = None,
        target_window_id: int | None = None,
        min_damage_area: int = 64,
    ) -> None:
        """Initializes X11 ctypes connection and registers damage monitors.

        Args:
            display_name: X11 display string (e.g. ':20' or None for default).
            target_window_id: Window ID to monitor (defaults to DefaultRootWindow).
            min_damage_area: Pixel area threshold to filter out tiny caret blinks (default 64px).
        """
        self.min_damage_area = min_damage_area
        self.is_available = False
        self.dpy = None
        self._target_win = 0
        self._damage_handle = 0
        self._region_handle = 0

        # Try to resolve display name
        if display_name is None:
            display_name = os.environ.get("DISPLAY", ":0")

        self.display_bytes = (
            display_name.encode("utf-8") if isinstance(display_name, str) else display_name
        )

        try:
            lib_x11 = ctypes.util.find_library("X11")
            lib_xdamage = ctypes.util.find_library("Xdamage")
            lib_xfixes = ctypes.util.find_library("Xfixes")

            if not (lib_x11 and lib_xdamage and lib_xfixes):
                logger.warning(
                    "X11 libraries missing (X11, Xdamage, or Xfixes). Quiescence monitor unavailable."
                )
                return

            self.x11 = ctypes.cdll.LoadLibrary(lib_x11)
            self.xdamage = ctypes.cdll.LoadLibrary(lib_xdamage)
            self.xfixes = ctypes.cdll.LoadLibrary(lib_xfixes)

            # Define function signatures
            self.x11.XOpenDisplay.restype = ctypes.c_void_p
            self.x11.XOpenDisplay.argtypes = [c_char_p]

            self.x11.XCloseDisplay.restype = c_int
            self.x11.XCloseDisplay.argtypes = [ctypes.c_void_p]

            self.x11.XDefaultRootWindow.restype = c_ulong
            self.x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]

            self.x11.XSync.restype = c_int
            self.x11.XSync.argtypes = [ctypes.c_void_p, c_int]

            self.x11.XFree.restype = c_int
            self.x11.XFree.argtypes = [ctypes.c_void_p]

            self.xfixes.XFixesGetCursorImage.restype = POINTER(XFixesCursorImage)
            self.xfixes.XFixesGetCursorImage.argtypes = [ctypes.c_void_p]

            self.xfixes.XFixesCreateRegion.restype = c_ulong
            self.xfixes.XFixesCreateRegion.argtypes = [ctypes.c_void_p, POINTER(XRectangle), c_int]

            self.xfixes.XFixesDestroyRegion.restype = None
            self.xfixes.XFixesDestroyRegion.argtypes = [ctypes.c_void_p, c_ulong]

            self.xfixes.XFixesFetchRegion.restype = POINTER(XRectangle)
            self.xfixes.XFixesFetchRegion.argtypes = [ctypes.c_void_p, c_ulong, POINTER(c_int)]

            self.xdamage.XDamageCreate.restype = c_ulong
            self.xdamage.XDamageCreate.argtypes = [ctypes.c_void_p, c_ulong, c_int]

            self.xdamage.XDamageDestroy.restype = None
            self.xdamage.XDamageDestroy.argtypes = [ctypes.c_void_p, c_ulong]

            self.xdamage.XDamageSubtract.restype = None
            self.xdamage.XDamageSubtract.argtypes = [ctypes.c_void_p, c_ulong, c_ulong, c_ulong]

            # Connect to X11 Display
            self.dpy = self.x11.XOpenDisplay(self.display_bytes)
            if not self.dpy:
                logger.warning(f"Could not open X11 display: {display_name}")
                return

            self._target_win = (
                target_window_id
                if target_window_id is not None
                else self.x11.XDefaultRootWindow(self.dpy)
            )

            # XDamageReportRawRectangles = 0
            self._damage_handle = self.xdamage.XDamageCreate(
                self.dpy, c_ulong(self._target_win), c_int(0)
            )
            self._region_handle = self.xfixes.XFixesCreateRegion(self.dpy, None, c_int(0))

            if self._damage_handle and self._region_handle:
                self.is_available = True
                # Clear any pre-existing damage
                self.consume_damage_area()
                logger.info(
                    f"X11QuiescenceMonitor initialized on {display_name} (root window: {self._target_win})"
                )

        except Exception as e:
            logger.warning(f"Failed to initialize X11QuiescenceMonitor: {e}")
            self.close()

    def get_cursor_state(self) -> tuple[str, int]:
        """Returns (cursor_name, cursor_serial_hash)."""
        if not self.is_available or not self.dpy:
            return ("unknown", 0)

        cursor_ptr = None
        try:
            cursor_ptr = self.xfixes.XFixesGetCursorImage(self.dpy)
            if not cursor_ptr:
                return ("unknown", 0)

            img = cursor_ptr.contents
            name = img.name.decode("utf-8", errors="ignore").lower() if img.name else ""
            serial = int(img.cursor_serial)
            return (name, serial)
        except Exception as e:
            logger.debug(f"Error reading cursor state: {e}")
            return ("unknown", 0)
        finally:
            if cursor_ptr:
                self.x11.XFree(cursor_ptr)

    def is_cursor_busy(self) -> bool:
        """Returns True if the current X11 mouse cursor is in a busy/wait/hourglass state."""
        name, _ = self.get_cursor_state()
        if not name:
            return False
        return any(busy in name for busy in self.BUSY_CURSOR_NAMES)

    def consume_damage_area(self) -> int:
        """Atomically copies and clears accumulated XDamage regions.

        Returns total number of modified pixels, filtering out blinks <= min_damage_area.
        """
        if not self.is_available or not self.dpy:
            return 0

        try:
            self.x11.XSync(self.dpy, c_int(0))
            self.xdamage.XDamageSubtract(
                self.dpy,
                c_ulong(self._damage_handle),
                c_ulong(0),
                c_ulong(self._region_handle),
            )

            nrects = c_int(0)
            rects_ptr = self.xfixes.XFixesFetchRegion(
                self.dpy, c_ulong(self._region_handle), ctypes.byref(nrects)
            )
            if not rects_ptr or nrects.value == 0:
                return 0

            total_area = 0
            try:
                for i in range(nrects.value):
                    r = rects_ptr[i]
                    area = int(r.width) * int(r.height)
                    # Filter out tiny caret rectangles (e.g. 2x18 = 36 px)
                    if area >= self.min_damage_area:
                        total_area += area
                return total_area
            finally:
                self.x11.XFree(rects_ptr)
        except Exception as e:
            logger.debug(f"Error consuming damage area: {e}")
            return 0

    def wait_for_quiescence(
        self,
        quiescence_window_ms: int = 180,
        initial_timeout_ms: int = 1500,
        max_total_timeout_ms: int = 15000,
        poll_interval_sec: float = 0.025,
    ) -> tuple[ExecutionPhase, float, int]:
        """Executes the adaptive 6-state quiescence loop.

        Returns:
            Tuple of (final_phase, elapsed_ms, total_damage_observed).
        """
        t_start = time.monotonic()
        total_damage = 0

        if not self.is_available:
            time.sleep(quiescence_window_ms / 1000.0)
            elapsed_ms = (time.monotonic() - t_start) * 1000.0
            return (ExecutionPhase.SLEEP_FALLBACK, elapsed_ms, 0)

        # Clear pre-action residual damage
        self.consume_damage_area()

        phase = ExecutionPhase.DISPATCHED
        quiet_since: float | None = None

        while True:
            now = time.monotonic()
            elapsed_ms = (now - t_start) * 1000.0

            damaged_px = self.consume_damage_area()
            cursor_busy = self.is_cursor_busy()
            total_damage += damaged_px

            if phase == ExecutionPhase.DISPATCHED:
                if damaged_px > 0 or cursor_busy:
                    phase = ExecutionPhase.DAMAGE_ACCUMULATION
                elif elapsed_ms >= initial_timeout_ms:
                    # No reaction occurred within initial threshold
                    return (ExecutionPhase.SETTLING, elapsed_ms, total_damage)

            elif phase == ExecutionPhase.DAMAGE_ACCUMULATION:
                if damaged_px == 0 and not cursor_busy:
                    phase = ExecutionPhase.SETTLING
                    quiet_since = now
                elif elapsed_ms >= max_total_timeout_ms:
                    if cursor_busy:
                        return (ExecutionPhase.ASYNC_PROMOTED, elapsed_ms, total_damage)
                    return (ExecutionPhase.CONTRACT_FAILED, elapsed_ms, total_damage)

            elif phase == ExecutionPhase.SETTLING:
                if elapsed_ms >= max_total_timeout_ms:
                    if cursor_busy:
                        return (ExecutionPhase.ASYNC_PROMOTED, elapsed_ms, total_damage)
                    return (ExecutionPhase.CONTRACT_FAILED, elapsed_ms, total_damage)

                if damaged_px > 0 or cursor_busy:
                    # Damage spiked back up or cursor became busy again
                    phase = ExecutionPhase.DAMAGE_ACCUMULATION
                    quiet_since = None
                else:
                    if quiet_since is None:
                        quiet_since = now
                    elif (now - quiet_since) * 1000.0 >= quiescence_window_ms:
                        # UI has remained completely quiet for the required window
                        return (ExecutionPhase.QUIESCENT, elapsed_ms, total_damage)

            time.sleep(poll_interval_sec)

    def arm(self) -> None:
        """Clears damage that accumulated before the action under test."""
        self.consume_damage_area()

    def wait(
        self,
        quiescence_window_ms: int = 180,
        initial_timeout_ms: int = 1500,
        max_total_timeout_ms: int = 15000,
    ) -> QuiescenceReport:
        """Waits for the screen to settle and reports how it went."""
        phase, elapsed, dirty = self.wait_for_quiescence(
            quiescence_window_ms=quiescence_window_ms,
            initial_timeout_ms=initial_timeout_ms,
            max_total_timeout_ms=max_total_timeout_ms,
        )
        cursor, _ = self.get_cursor_state()
        return QuiescenceReport(phase=phase, elapsed_ms=elapsed, dirty_area=dirty, cursor=cursor)

    def close(self) -> None:
        """Cleans up X11 and damage resources."""
        try:
            if self.dpy:
                if self._region_handle:
                    self.xfixes.XFixesDestroyRegion(self.dpy, c_ulong(self._region_handle))
                    self._region_handle = 0
                if self._damage_handle:
                    self.xdamage.XDamageDestroy(self.dpy, c_ulong(self._damage_handle))
                    self._damage_handle = 0
                self.x11.XCloseDisplay(self.dpy)
                self.dpy = None
        except Exception:
            pass
        self.is_available = False

    def __del__(self) -> None:
        self.close()


class SleepQuiescenceMonitor:
    """Platforms without a damage stream. A wait is SLEEP_FALLBACK, not QUIESCENT."""

    def __init__(self, platform: str = "") -> None:
        self.platform = platform
        self.is_available = False

    def arm(self) -> None:
        """No-op: this monitor cannot see screen damage."""
        return None

    def consume_damage_area(self) -> int:
        """Always 0: this monitor cannot see screen damage."""
        return 0

    def get_cursor_state(self) -> tuple[str, int]:
        """Always ``("unknown", 0)``: this monitor cannot read the cursor."""
        return ("unknown", 0)

    def is_cursor_busy(self) -> bool:
        """Always False: this monitor cannot read the cursor."""
        return False

    def wait_for_quiescence(
        self,
        quiescence_window_ms: int = 180,
        initial_timeout_ms: int = 1500,
        max_total_timeout_ms: int = 15000,
        poll_interval_sec: float = 0.025,
    ) -> tuple[ExecutionPhase, float, int]:
        """Sleeps for ``quiescence_window_ms`` instead of watching the screen."""
        time.sleep(quiescence_window_ms / 1000.0)
        return (ExecutionPhase.SLEEP_FALLBACK, float(quiescence_window_ms), 0)

    def wait(
        self,
        quiescence_window_ms: int = 180,
        initial_timeout_ms: int = 1500,
        max_total_timeout_ms: int = 15000,
    ) -> QuiescenceReport:
        """Sleeps for the quiescence window and reports it as a fallback."""
        phase, elapsed, dirty = self.wait_for_quiescence(
            quiescence_window_ms=quiescence_window_ms,
            initial_timeout_ms=initial_timeout_ms,
            max_total_timeout_ms=max_total_timeout_ms,
        )
        return QuiescenceReport(phase=phase, elapsed_ms=elapsed, dirty_area=dirty, cursor="unknown")
