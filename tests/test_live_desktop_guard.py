"""The test suite never types or clicks on a real desktop by accident."""

from conftest import _display_is_virtual, live_desktop_allowed


def test_mac_and_windows_refuse_by_default():
    assert live_desktop_allowed({}, platform="darwin") is False
    assert live_desktop_allowed({}, platform="win32") is False


def test_opt_in_and_replay_allow():
    assert live_desktop_allowed({"DOBBY_LIVE_DESKTOP_TESTS": "1"}, platform="darwin")
    assert live_desktop_allowed({"DOBBY_REPLAY": "1"}, platform="darwin")
    assert live_desktop_allowed({"DESKTOP_HAL": "replay"}, platform="darwin")


def test_linux_allows_only_virtual_displays(tmp_path):
    proc, tmp = tmp_path / "proc", tmp_path / "tmp"
    (proc / "42").mkdir(parents=True)
    tmp.mkdir()
    (tmp / ".X99-lock").write_text("      42\n")
    (proc / "42" / "comm").write_text("Xvfb\n")
    kw = {"proc_root": str(proc), "tmp": str(tmp)}
    assert live_desktop_allowed({"DISPLAY": ":99"}, platform="linux", **kw)
    (proc / "42" / "comm").write_text("Xorg\n")
    assert not live_desktop_allowed({"DISPLAY": ":99"}, platform="linux", **kw)
    assert not live_desktop_allowed({"DISPLAY": ":0"}, platform="linux", **kw)
    assert not live_desktop_allowed({}, platform="linux", **kw)
    assert not _display_is_virtual("wayland-0", **kw)


def test_explicit_off_forces_guard():
    assert not live_desktop_allowed(
        {"DOBBY_LIVE_DESKTOP_TESTS": "0", "DOBBY_REPLAY": "1"}, platform="darwin"
    )
