"""Tests for the macOS overlay helper that need AppKit and a window server."""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="needs AppKit")


def test_panel_sits_above_menus():
    AppKit = pytest.importorskip("AppKit")
    from dobby.overlay import mac_helper

    screen = AppKit.NSScreen.mainScreen()
    if screen is None:
        pytest.skip("no window server connection")
    panel = mac_helper._Panel(screen, mac_helper.DEFAULT_CONFIG, (0.5, 0.4, 1.0))
    try:
        assert panel.window.level() == AppKit.NSScreenSaverWindowLevel
    finally:
        panel.close()


def test_liquid_glass_capsule_where_the_os_has_it():
    objc = pytest.importorskip("objc")
    from dobby.overlay import mac_helper

    try:
        objc.lookUpClass("NSGlassEffectView")
    except objc.nosuchclass_error:
        assert (
            mac_helper._liquid_glass({"liquid_glass": "auto"}, 34.0) is None
        )  # older macOS: frosted bar
        return
    glass = mac_helper._liquid_glass({"liquid_glass": "clear"}, 34.0)
    assert glass is not None
    assert glass.cornerRadius() == mac_helper._BAR_RADIUS  # rounded rectangle, not a capsule
    assert glass.style() == 1  # clear


def test_liquid_glass_can_be_turned_off():
    from dobby.overlay import mac_helper

    assert mac_helper._liquid_glass({"liquid_glass": "off"}, 34.0) is None
