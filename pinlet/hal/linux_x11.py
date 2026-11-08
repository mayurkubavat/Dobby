"""X11 backend loader. Native libraries are imported by linux.py on first use."""

from __future__ import annotations


def build_hal():
    """Factory used by :func:`pinlet.hal.factory.get_desktop_hal`; returns the X11 ``LinuxHAL``."""
    from pinlet.hal.linux import LinuxHAL

    return LinuxHAL()
