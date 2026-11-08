"""Factory for the desktop session. The only module that imports a backend."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any

from pinlet.hal.base import IDesktopHAL
from pinlet.hal.session import DesktopSession
from pinlet.types import DisplayMetrics, DisplayTopology

_HAL_INSTANCE: IDesktopHAL | None = None

_BACKENDS = {
    "darwin": "pinlet.hal.macos",
    "linux": "pinlet.hal.linux_x11",
    "windows": "pinlet.hal.windows",
    "win32": "pinlet.hal.windows",
    "wayland": "pinlet.hal.linux_wayland",
    "replay": "pinlet.hal.mock_replay",
}


def reset_desktop_hal() -> None:
    """Drops the process singleton so tests can boot another backend."""
    global _HAL_INSTANCE
    _HAL_INSTANCE = None


def _platform_kind() -> str:
    forced = os.environ.get("DESKTOP_HAL")
    if forced:
        return forced.strip().lower()
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    if not os.environ.get("DISPLAY"):
        # Native Wayland without XWayland, or no display at all (SSH, CI):
        # the fail-closed driver explains what is missing instead of crashing
        # in pynput/Xlib at import time.
        return "wayland"
    return "linux"


def _topology_for(backend: Any) -> DisplayTopology:
    probe = getattr(backend, "display_topology", None)
    if callable(probe):
        try:
            topo = probe()
            if isinstance(topo, DisplayTopology):
                return topo
        except Exception:
            pass
    try:
        width, height = backend.get_screen_size()
    except Exception:
        width, height = (0, 0)
    return DisplayTopology(
        displays=[
            DisplayMetrics(
                display_id="primary",
                origin=(0, 0),
                size=(int(width), int(height)),
                scale=1.0,
            )
        ],
        primary_id="primary",
    )


def _quiescence_for(kind: str, backend: Any) -> Any:
    if kind == "replay" or hasattr(backend, "transitions"):
        return backend
    if kind == "linux":
        from pinlet.config import CONFIG
        from pinlet.hal.quiescence import X11QuiescenceMonitor

        return X11QuiescenceMonitor(min_damage_area=CONFIG.min_damage_area)
    from pinlet.hal.quiescence import SleepQuiescenceMonitor

    return SleepQuiescenceMonitor(platform=kind)


def _build(kind: str, cassette: Path | str | None) -> DesktopSession:
    if kind == "replay":
        from pinlet.hal.mock_replay import MockReplayHAL, load_cassette

        path = cassette or os.environ.get("DESKTOP_CASSETTE")
        backend = load_cassette(path) if path else MockReplayHAL()
        return DesktopSession(
            backend=backend,
            topology=getattr(backend, "topology", None) or _topology_for(backend),
            quiescence=backend,
            ax=backend,
        )

    module_name = _BACKENDS.get(kind)
    if module_name is None:
        raise ValueError(f"Unknown desktop HAL kind: {kind}")
    module = importlib.import_module(module_name)
    backend = module.build_hal()
    if kind in ("darwin", "linux"):
        from pinlet.overlay import wrap_hal

        # Agent cursor, glow, Stop / Pause / Allow-Deny and wait_for_wake's bar
        # (mac_helper or x11_helper); a no-op when the overlay is disabled.
        backend = wrap_hal(backend)
    from pinlet.control import wrap_hal as wrap_control

    backend = wrap_control(backend)  # shared lease between agents; no-op when disabled
    return DesktopSession(
        backend=backend,
        topology=_topology_for(backend),
        quiescence=_quiescence_for(kind, backend),
    )


def get_desktop_hal(kind: str | None = None, cassette: Path | str | None = None) -> IDesktopHAL:
    """Returns the process session, or a new one when kind or cassette is passed."""
    global _HAL_INSTANCE
    if kind is None and cassette is None and _HAL_INSTANCE is not None:
        return _HAL_INSTANCE
    resolved = (kind or _platform_kind()).strip().lower()
    session = _build(resolved, cassette)
    if kind is None and cassette is None:
        _HAL_INSTANCE = session
    return session
