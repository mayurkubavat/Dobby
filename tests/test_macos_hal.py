"""
Tests for the macOS HAL and AX inspector using fake Quartz / AppKit / AX modules,
so they run on Linux CI. Real-device behaviour is covered by a manual smoke run.
"""

from types import SimpleNamespace

import pytest
from PIL import Image

from dobby.hal.macos import KEYCODES, MacHAL
from dobby.perception.ax import AXInspector


class FakeQuartz:
    def __init__(self, windows=None, screen=(1512, 982)):
        self.posted = []
        self.flags = {}
        self.fields = {}
        self.unicode = {}
        self.windows = windows or []
        self.screen = screen

    # events are plain dicts so tests can inspect them
    def CGEventCreateMouseEvent(self, src, etype, pos, btn):
        return {"kind": "mouse", "type": etype, "pos": tuple(pos), "btn": btn}

    def CGEventCreateKeyboardEvent(self, src, code, down):
        return {"kind": "key", "code": code, "down": down}

    def CGEventCreateScrollWheelEvent(self, src, unit, count, dy, dx):
        return {"kind": "scroll", "dy": dy, "dx": dx}

    def CGEventSetIntegerValueField(self, ev, field, value):
        ev[f"field_{field}"] = value

    def CGEventSetFlags(self, ev, flags):
        ev["flags"] = flags

    def CGEventKeyboardSetUnicodeString(self, ev, n, s):
        ev["unicode"] = s

    def CGEventPost(self, tap, ev):
        self.posted.append(ev)

    def CGEventCreate(self, src):
        return None

    def CGEventGetLocation(self, ev):
        return SimpleNamespace(x=10.6, y=20.2)

    def CGMainDisplayID(self):
        return 1

    def CGDisplayBounds(self, did):
        return SimpleNamespace(size=SimpleNamespace(width=self.screen[0], height=self.screen[1]),
                               origin=SimpleNamespace(x=0, y=0))

    def CGWindowListCopyWindowInfo(self, opts, wid):
        return self.windows


def _win(num, owner, title, pid, x, y, w, h, layer=0):
    return {"kCGWindowNumber": num, "kCGWindowOwnerName": owner, "kCGWindowName": title,
            "kCGWindowOwnerPID": pid, "kCGWindowLayer": layer,
            "kCGWindowBounds": {"X": x, "Y": y, "Width": w, "Height": h}}


class FakeAppKit:
    NSApplicationActivateIgnoringOtherApps = 2

    def __init__(self, front_pid=42, refuse=False):
        self.activated = []
        self.front_pid = front_pid
        outer = self

        class App:
            def __init__(self, pid):
                self.pid = pid

            def processIdentifier(self):
                return self.pid

            def activateWithOptions_(self, opts):
                outer.activated.append(self.pid)
                if not refuse:
                    outer.front_pid = self.pid
                return True  # macOS says yes even when it refuses

        class WS:
            def frontmostApplication(self_inner):
                return App(outer.front_pid)

        self.NSWorkspace = SimpleNamespace(sharedWorkspace=lambda: WS())
        self.NSRunningApplication = SimpleNamespace(runningApplicationWithProcessIdentifier_=lambda pid: App(pid))


@pytest.fixture(autouse=True)
def _no_cooldown(monkeypatch):
    from dobby.config import CONFIG
    monkeypatch.setattr(CONFIG, "action_cooldown_ms", 0)
    monkeypatch.setattr("dobby.hal.macos.time.sleep", lambda s: None)


def _hal(**kw):
    q = FakeQuartz(**kw)
    return MacHAL(quartz=q, appkit=FakeAppKit(), capture_fn=lambda region: Image.new("RGB", (
        (region[2] * 2, region[3] * 2) if region else (3024, 1964)), "white")), q


def test_capture_is_resized_from_retina_to_points():
    hal, _ = _hal()
    assert hal.capture_screen().size == (1512, 982)
    assert hal.capture_screen(region=(100, 50, 400, 300)).size == (400, 300)


def test_click_posts_down_up_with_click_state():
    hal, q = _hal()
    hal.mouse_click(300, 200, click_count=2)
    clicks = [e for e in q.posted if e["kind"] == "mouse" and e["type"] in (1, 2)]
    assert [e["type"] for e in clicks] == [1, 2, 1, 2]
    assert [e["field_1"] for e in clicks] == [1, 1, 2, 2]
    assert all(e["pos"] == (300, 200) for e in clicks)


def test_right_click_and_failsafe():
    hal, q = _hal()
    hal.mouse_click(5, 5, button="right")
    assert {e["type"] for e in q.posted if e["kind"] == "mouse"} >= {3, 4}
    with pytest.raises(RuntimeError):
        hal.mouse_click(0, 0)


def test_hotkey_presses_and_releases_modifiers():
    hal, q = _hal()
    hal.keyboard_hotkey(["cmd", "shift", "s"])
    keys = [(e["code"], e["down"], e["flags"]) for e in q.posted if e["kind"] == "key"]
    cmd, shift, s = 0x100000, 0x20000, KEYCODES["s"]
    assert keys == [
        (55, True, cmd), (56, True, cmd | shift),
        (s, True, cmd | shift), (s, False, cmd | shift),
        (56, False, cmd), (55, False, 0),
    ]


def test_hotkey_releases_modifiers_on_error():
    hal, q = _hal()
    real = q.CGEventPost
    calls = []

    def flaky(tap, ev):
        calls.append(ev)
        if len(calls) == 2:  # the 's' key-down fails
            raise RuntimeError("post failed")
        real(tap, ev)

    q.CGEventPost = flaky
    with pytest.raises(RuntimeError):
        hal.keyboard_hotkey(["cmd", "s"])
    last = q.posted[-1]
    assert (last["code"], last["down"], last["flags"]) == (55, False, 0)


def test_every_event_is_tagged_as_dobby():
    from dobby.hal.macos import DOBBY_EVENT_TAG
    hal, q = _hal()
    hal.keyboard_type("a")
    hal.keyboard_hotkey(["cmd", "c"])
    hal.mouse_click(300, 200)
    assert q.posted and all(e.get("field_42") == DOBBY_EVENT_TAG for e in q.posted)


def test_press_key_and_unknown_key():
    hal, q = _hal()
    hal.keyboard_press_key("Escape")
    assert [(e["code"], e["flags"]) for e in q.posted] == [(53, 0), (53, 0)]
    with pytest.raises(ValueError):
        hal.keyboard_press_key("hyper")


def test_type_text_uses_unicode_events_with_no_modifiers():
    hal, q = _hal()
    hal.keyboard_type("aé\n")
    uni = [e["unicode"] for e in q.posted if "unicode" in e]
    assert uni == ["a", "a", "é", "é"]
    assert all(e["flags"] == 0 for e in q.posted if e["kind"] == "key")
    assert [e["code"] for e in q.posted if e["kind"] == "key" and "unicode" not in e] == [36, 36]


def test_release_modifiers_and_private_source():
    hal, q = _hal()
    hal.release_modifiers()
    assert sorted(e["code"] for e in q.posted) == [55, 56, 58, 59]
    assert all(not e["down"] and e["flags"] == 0 for e in q.posted)
    q2 = FakeQuartz()
    q2.CGEventSourceCreate = lambda state: ("src", state)
    assert MacHAL(quartz=q2)._src == ("src", -1)


def test_list_windows_and_focus():
    wins = [
        _win(1, "Dock", "", 10, 0, 0, 1512, 80, layer=20),     # not a normal window
        _win(7, "Vivado", "project_1", 42, 50, 40, 1200, 800),
        _win(8, "Vivado", "Settings", 42, 100, 100, 600, 400),
        _win(9, "Terminal", "zsh", 77, 0, 0, 800, 600),
        _win(10, "Tiny", "x", 78, 0, 0, 10, 10),               # too small
    ]
    hal, _ = _hal(windows=wins)
    out = hal.list_windows()
    assert [w.window_id for w in out] == ["7", "8", "9"]
    active = hal.get_active_window()
    assert active.window_id == "7" and active.title == "project_1 - Vivado" and active.app_name == "Vivado"
    assert sum(w.is_active for w in out) == 1
    assert hal.focus_window("9") is True
    assert hal._appkit.activated == [77]
    assert hal.focus_window("999") is False


def _focus_wins():
    return [_win(7, "Vivado", "project_1", 42, 50, 40, 1200, 800),
            _win(9, "Terminal", "zsh", 77, 0, 0, 800, 600)]


class RaiseAX:
    """Just enough AX for focus_window's fallback raise."""

    kAXValueCGPointType, kAXValueCGSizeType = 1, 2

    def __init__(self, appkit=None, works=True):
        self.appkit, self.works, self.calls = appkit, works, []

    def AXUIElementCreateApplication(self, pid):
        return {"pid": pid}

    def AXUIElementSetAttributeValue(self, el, name, value):
        self.calls.append((name, el["pid"]))
        if self.works and name == "AXFrontmost":
            self.appkit.front_pid = el["pid"]
        return 0

    def AXUIElementCopyAttributeValue(self, el, name, _):
        if name == "AXWindows":
            return 0, [{"AXPosition": SimpleNamespace(x=0, y=0), "AXSize": SimpleNamespace(width=800, height=600)}]
        return 0, el.get(name)

    def AXValueGetValue(self, value, kind, _):
        return True, value

    def AXUIElementPerformAction(self, el, action):
        self.calls.append((action, None))
        return 0


def test_focus_refused_by_macos_reports_failure_with_reason():
    appkit = FakeAppKit(front_pid=42, refuse=True)
    hal = MacHAL(quartz=FakeQuartz(windows=_focus_wins()), appkit=appkit, ax=RaiseAX(appkit, works=False),
                 capture_fn=lambda r: None)
    assert hal.focus_window("9") is False
    assert "did not bring window 9 to the front" in hal.last_focus_error
    assert "project_1 - Vivado (7)" in hal.last_focus_error
    assert hal.get_active_window().window_id == "7"


def test_focus_falls_back_to_ax_raise_when_activation_is_refused():
    appkit = FakeAppKit(front_pid=42, refuse=True)
    ax = RaiseAX(appkit)
    hal = MacHAL(quartz=FakeQuartz(windows=_focus_wins()), appkit=appkit, ax=ax, capture_fn=lambda r: None)
    assert hal.focus_window("9") is True
    assert hal.last_focus_error is None
    assert ("AXFrontmost", 77) in ax.calls and ("AXRaise", None) in ax.calls


def test_focus_missing_window_says_so():
    hal, _ = _hal(windows=_focus_wins())
    assert hal.focus_window("999") is False
    assert hal.last_focus_error == "window 999 is not on screen"


def test_frontmost_is_read_fresh_not_from_nsworkspace_cache():
    # NSWorkspace (the fake) still says pid 42; the fresh probe knows Terminal is in front.
    hal = MacHAL(quartz=FakeQuartz(windows=_focus_wins()), appkit=FakeAppKit(front_pid=42),
                 front_pid_fn=lambda: 77, capture_fn=lambda r: None)
    assert hal.get_active_window().window_id == "9"
    broken = MacHAL(quartz=FakeQuartz(windows=_focus_wins()), appkit=FakeAppKit(front_pid=42),
                    front_pid_fn=lambda: (_ for _ in ()).throw(OSError("no lsappinfo")), capture_fn=lambda r: None)
    assert broken.get_active_window().window_id == "7"  # falls back to NSWorkspace


def test_lsappinfo_front_pid_parses_both_output_styles(monkeypatch):
    import dobby.hal.macos as macos

    for info in ('    pid = 85435 !cgsConnection', '"pid"=85435'):
        def run(args, **kw):
            out = "ASN:0x0-0xad9ad9:\n" if args[1] == "front" else info
            return SimpleNamespace(stdout=out)
        monkeypatch.setattr(macos.subprocess, "run", run)
        assert macos._lsappinfo_front_pid() == 85435


def test_cursor_position_and_screen_size():
    hal, _ = _hal()
    assert hal.get_cursor_position() == (10, 20)
    assert hal.get_screen_size() == (1512, 982)


# ------------------------------------------------------------------- AX


class FakeAX:
    kAXValueCGPointType = 1
    kAXValueCGSizeType = 2

    def __init__(self, tree, trusted=True):
        self.tree = tree  # pid -> root element dict
        self.trusted = trusted
        self.set_calls = []

    def AXIsProcessTrusted(self):
        return self.trusted

    def AXUIElementCreateApplication(self, pid):
        return self.tree[pid]

    def AXUIElementCopyAttributeValue(self, el, name, _):
        if name in el:
            return 0, el[name]
        return -25212, None

    def AXValueGetValue(self, value, kind, _):
        return True, value

    def AXUIElementSetAttributeValue(self, el, name, value):
        self.set_calls.append((name, value))
        if el.get("electron") and name == "AXManualAccessibility":
            el["AXWindows"] = el.pop("_hidden_windows", [])  # Chromium exposes web content now
            return 0
        return -25205  # kAXErrorAttributeUnsupported


def _el(role, frame=None, children=(), **attrs):
    d = {"AXRole": role, "AXChildren": list(children), **attrs}
    if frame:
        x, y, w, h = frame
        d["AXPosition"] = SimpleNamespace(x=x, y=y)
        d["AXSize"] = SimpleNamespace(width=w, height=h)
    return d


def _ax_tree():
    save = _el("AXButton", (100, 100, 80, 24), AXTitle="Save", AXEnabled=True, AXFocused=True)
    tiny = _el("AXButton", (0, 0, 2, 2), AXTitle="dot")
    offscreen = _el("AXButton", (5000, 5000, 80, 24), AXTitle="Hidden")
    field = _el("AXTextField", (100, 140, 200, 22), AXPlaceholderValue="Search", AXEnabled=True)
    label = _el("AXStaticText", (100, 60, 200, 20), AXValue="Discard changes?")
    tab = _el("AXRadioButton", (10, 10, 60, 20), AXTitle="Sources")
    tabs = _el("AXTabGroup", (0, 0, 300, 40), children=[tab])
    dialog = _el("AXWindow", (50, 50, 400, 200), children=[label, save, field, tiny, offscreen, tabs],
                 AXSubrole="AXDialog", AXTitle="Confirm")
    return {42: {"AXWindows": [dialog]}}


def _inspector(**kw):
    return AXInspector(ax=FakeAX(_ax_tree(), **kw), appkit=FakeAppKit(front_pid=42), screen_bounds=(0, 0, 1512, 982))


def test_ax_inspector_maps_roles_and_prunes(monkeypatch):
    insp = _inspector()
    els = insp.inspect_desktop()
    names = {e.name: e for e in els}
    assert set(names) == {"Confirm", "Save", "Search", "Sources"}  # dialog kept, label filtered, tiny/offscreen pruned
    assert names["Confirm"].role == "dialog"
    assert names["Save"].role == "push button" and "focused" in names["Save"].states
    assert names["Search"].role == "entry"
    assert names["Sources"].role == "page tab"

    everything = insp.inspect()
    assert any(e.role == "label" and e.name == "Discard changes?" for e in everything)


def test_ax_inspector_requires_permission():
    insp = _inspector(trusted=False)
    assert insp.is_available is False
    assert "Accessibility" in insp.error
    assert insp.inspect_desktop() == []


def test_ax_inspector_node_budget():
    insp = AXInspector(ax=FakeAX(_ax_tree()), appkit=FakeAppKit(front_pid=42), max_nodes=2)
    assert len(insp.inspect()) <= 2


def test_factory_picks_mac_hal(monkeypatch):
    import dobby.hal.factory as factory
    import dobby.hal.macos as macos

    monkeypatch.setattr(factory, "_HAL_INSTANCE", None)
    monkeypatch.setattr(factory.sys, "platform", "darwin")
    monkeypatch.setattr(macos, "_load_quartz", lambda: FakeQuartz())
    hal = factory.get_desktop_hal()
    backend = getattr(hal, "backend", hal)
    assert isinstance(backend, MacHAL)
    monkeypatch.setattr(factory, "_HAL_INSTANCE", None)


def test_ax_inspector_enables_electron_web_content(monkeypatch):
    from dobby.config import CONFIG

    monkeypatch.setattr(CONFIG, "ax_web_content_settle_seconds", 0)
    monkeypatch.setattr(CONFIG, "ax_web_content_retry_seconds", 0)
    send = _el("AXButton", (200, 300, 60, 24), AXTitle="Send", AXEnabled=True)
    web = _el("AXWebArea", (0, 30, 1512, 900), children=[send])
    win = _el("AXWindow", (0, 0, 1512, 982), children=[web], AXTitle="Claude")
    fake = FakeAX({42: {"electron": True, "_hidden_windows": [win]}})
    insp = AXInspector(ax=fake, appkit=FakeAppKit(front_pid=42), screen_bounds=(0, 0, 1512, 982))
    assert [e.name for e in insp.inspect_desktop()] == ["Send"]
    assert insp.web_content_enabled == {42: True}
    insp.inspect_desktop()
    assert fake.set_calls == [("AXManualAccessibility", True)]  # once per pid


def test_ax_inspector_rereads_while_chromium_builds_its_tree(monkeypatch):
    from dobby.config import CONFIG

    monkeypatch.setattr(CONFIG, "ax_web_content_settle_seconds", 0)
    monkeypatch.setattr(CONFIG, "ax_web_content_retry_seconds", 5)
    buttons = [_el("AXButton", (100 + 70 * i, 300, 60, 24), AXTitle=f"b{i}", AXEnabled=True) for i in range(6)]
    web = _el("AXWebArea", (0, 30, 1512, 900), children=[])
    win = _el("AXWindow", (0, 0, 1512, 982), children=[web], AXTitle="Antigravity")
    reads = {"n": 0}

    class LazyChromium(FakeAX):
        def AXUIElementCopyAttributeValue(self, el, name, _):
            if el is web and name == "AXChildren":
                reads["n"] += 1
                return 0, buttons if reads["n"] >= 3 else []  # tree appears on the 3rd read
            return super().AXUIElementCopyAttributeValue(el, name, _)

    fake = LazyChromium({42: {"electron": True, "_hidden_windows": [win]}})
    insp = AXInspector(ax=fake, appkit=FakeAppKit(front_pid=42), screen_bounds=(0, 0, 1512, 982))
    assert len(insp.inspect_desktop()) == 6
    assert reads["n"] == 3


def test_ax_inspector_front_app_is_read_fresh():
    other = _el("AXWindow", (0, 0, 400, 300), children=[_el("AXButton", (10, 10, 60, 24), AXTitle="Go")])
    tree = {**_ax_tree(), 77: {"AXWindows": [other]}}
    insp = AXInspector(ax=FakeAX(tree), appkit=FakeAppKit(front_pid=42), screen_bounds=(0, 0, 1512, 982),
                       front_pid_fn=lambda: 77)
    assert [e.name for e in insp.inspect_desktop()] == ["Go"]


def test_ax_inspector_native_app_rejects_flag_harmlessly():
    insp = _inspector()
    assert {e.name for e in insp.inspect_desktop()} >= {"Save"}
    assert insp.web_content_enabled == {42: False}
    insp.inspect_desktop()
    assert insp._ax.set_calls == [("AXManualAccessibility", True)]


def test_ax_inspector_web_content_can_be_disabled(monkeypatch):
    from dobby.config import CONFIG

    monkeypatch.setattr(CONFIG, "ax_enable_web_content", False)
    insp = _inspector()
    insp.inspect_desktop()
    assert insp._ax.set_calls == []


def test_ax_inspector_wrapper_groups_do_not_use_depth():
    leaf = _el("AXButton", (10, 10, 40, 20), AXTitle="Deep")
    node = leaf
    for _ in range(30):
        node = _el("AXGroup", (0, 0, 500, 500), children=[node])
    win = _el("AXWindow", (0, 0, 800, 600), children=[node], AXTitle="W")
    insp = AXInspector(ax=FakeAX({42: {"AXWindows": [win]}}), appkit=FakeAppKit(front_pid=42),
                       screen_bounds=(0, 0, 1512, 982))
    assert [e.name for e in insp.inspect_desktop(max_depth=4)] == ["Deep"]


def test_ax_inspector_includes_top_level_menu_bar(monkeypatch):
    from dobby.config import CONFIG

    item_in_menu = _el("AXMenuItem", (60, 30, 120, 20), AXTitle="New Note")
    menu = _el("AXMenu", (60, 25, 200, 300), children=[item_in_menu])
    file_item = _el("AXMenuBarItem", (60, 0, 40, 24), children=[menu], AXTitle="File", AXEnabled=True)
    bar = _el("AXMenuBar", (0, 0, 1512, 24), children=[file_item])
    win = _el("AXWindow", (0, 30, 800, 600), children=[_el("AXButton", (10, 60, 40, 20), AXTitle="OK")], AXTitle="W")
    tree = {42: {"AXWindows": [win], "AXMenuBar": bar}}
    insp = AXInspector(ax=FakeAX(tree), appkit=FakeAppKit(front_pid=42), screen_bounds=(0, 0, 1512, 982))
    els = insp.inspect_desktop()
    assert [(e.name, e.role) for e in els] == [("File", "menu item"), ("OK", "push button")]  # closed menu not walked
    monkeypatch.setattr(CONFIG, "ax_include_menu_bar", False)
    assert [e.name for e in insp.inspect_desktop()] == ["OK"]


def test_ax_inspector_hit_test_maps_role_and_frame():
    sheet = _el("AXSheet", (0, 0, 400, 200), AXTitle="Save changes?")
    ax = FakeAX({42: {"AXWindows": []}})
    ax.AXUIElementCreateSystemWide = lambda: "system"
    ax.AXUIElementCopyElementAtPosition = lambda el, x, y, _: (0, sheet)
    insp = AXInspector(ax=ax, appkit=FakeAppKit(front_pid=42), screen_bounds=(0, 0, 1512, 982))
    hit = insp.hit_test(10, 10)
    assert hit == {"role": "dialog", "name": "Save changes?", "bbox": {"x": 0, "y": 0, "width": 400, "height": 200},
                   "secure": False}
    ax.AXUIElementCopyElementAtPosition = lambda el, x, y, _: (-25204, None)
    assert insp.hit_test(10, 10) is None


def test_background_click_presses_in_the_windows_app_without_posting_events():
    from tests.test_input_lock import FakeAX as TreeAX

    ax = TreeAX({"b": {"AXRole": "AXButton", "actions": ["AXPress"]}}, "b")
    hal = MacHAL(quartz=FakeQuartz(windows=_focus_wins()), appkit=FakeAppKit(), ax=ax, capture_fn=lambda r: None)
    assert hal.background_click(100, 100, "7") is True
    assert ax.app_pid == 42 and ax.pressed == [("b", "AXPress")]
    assert hal.Q.posted == []  # the real mouse never moved
    assert hal.background_click(100, 100, "404") is False  # unknown window: click normally
