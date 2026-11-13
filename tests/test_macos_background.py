"""Background mode on macOS: one window, no real pointer, with fake Quartz / AX."""

from types import SimpleNamespace

import pytest
from PIL import Image
from test_macos_hal import FakeAppKit, FakeAX, FakeQuartz, _el, _win

from pinlet.hal import target
from pinlet.hal.macos import MacHAL
from pinlet.hal.macos_background import BackgroundMacHAL, NeedsPointer, NoTarget
from pinlet.perception.ax import AXInspector

MINE, THEIRS = 77, 42  # the agent's app and the person's app (in front)


class Quartz(FakeQuartz):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.to_pid = []
        self.idle = 0.0
        self.warped = None

    def CGEventPostToPid(self, pid, ev):
        self.to_pid.append((pid, ev))

    def CGWindowListCopyWindowInfo(self, opts, wid):
        if opts & 1:  # on screen only
            return [w for w in self.windows if w.get("kCGWindowIsOnscreen")]
        return self.windows

    def CGEventSourceSecondsSinceLastEventType(self, state, kind):
        return self.idle

    def CGWarpMouseCursorPosition(self, pos):
        self.warped = tuple(pos)


class AX(FakeAX):
    """Adds hit testing, actions and settable attributes to the shared fake."""

    def __init__(self, tree, at=None):
        super().__init__(tree)
        self.at = at or {}  # (pid, x, y) -> element
        self.performed = []

    def AXUIElementCopyElementAtPosition(self, app, x, y, _):
        el = self.at.get((app["pid"], int(x), int(y)))
        return (0, el) if el is not None else (-25200, None)

    def AXUIElementCopyActionNames(self, el, _):
        return 0, el.get("actions", [])

    def AXUIElementPerformAction(self, el, action):
        self.performed.append((el.get("AXTitle") or el["AXRole"], action))
        return 0

    def AXUIElementSetAttributeValue(self, el, name, value):
        self.set_calls.append((el.get("AXTitle") or el["AXRole"], name, value))
        if name in el.get("settable", ()):
            el[name] = value
            return 0
        return -25205

    def AXUIElementSetMessagingTimeout(self, el, secs):
        return 0


def _windows():
    front = _win(1, "Notes", "Shopping", THEIRS, 0, 0, 800, 600)
    front["kCGWindowIsOnscreen"] = True
    mine = _win(2, "TextEdit", "Draft", MINE, 100, 50, 400, 300)  # on another Desktop
    helper = _win(3, "TextEdit", "", MINE, 0, 0, 500, 500)  # hidden helper: not listed
    return [front, mine, helper]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from pinlet.config import CONFIG

    monkeypatch.setattr(CONFIG, "action_cooldown_ms", 0)
    monkeypatch.setattr("pinlet.hal.macos.time.sleep", lambda s: None)
    monkeypatch.setattr("pinlet.hal.macos_background.time.sleep", lambda s: None)
    monkeypatch.delenv("PINLET_BACKGROUND_POINTER", raising=False)
    target.reset()
    yield
    target.reset()


def _hal(ax=None, windows=None):
    q = Quartz(windows=_windows() if windows is None else windows)
    captured = []

    def window_shot(wid):
        captured.append(wid)
        return Image.new("RGB", (800, 600), "white")  # Retina: twice the window's points

    hal = BackgroundMacHAL(
        quartz=q,
        appkit=FakeAppKit(front_pid=THEIRS),
        capture_fn=lambda region: Image.new("RGB", (3024, 1964), "red"),
        ax=ax or AX({MINE: {"pid": MINE, "AXWindows": []}}),
        window_capture_fn=window_shot,
    )
    return hal, q, captured


def test_setting_reads_on_words_only():
    assert target.enabled({"PINLET_BACKGROUND": " On "})
    assert not target.enabled({"PINLET_BACKGROUND": "0"})
    assert not target.enabled({})
    assert target.pointer_mode({}) == "refuse"
    assert target.pointer_mode({"PINLET_BACKGROUND_POINTER": "IDLE"}) == "idle"
    assert target.pointer_mode({"PINLET_BACKGROUND_POINTER": "whatever"}) == "refuse"


def test_build_hal_picks_the_background_hal_only_when_on(monkeypatch):
    import pinlet.hal.macos as macos

    monkeypatch.setattr(macos, "_load_quartz", lambda: Quartz())
    monkeypatch.delenv("PINLET_BACKGROUND", raising=False)
    assert type(macos.build_hal()) is MacHAL
    monkeypatch.setenv("PINLET_BACKGROUND", "1")
    assert isinstance(macos.build_hal(), BackgroundMacHAL)


def test_lists_windows_on_other_desktops_but_not_hidden_helpers():
    hal, _, _ = _hal()
    wins = hal.list_windows()
    assert [w.window_id for w in wins] == ["1", "2"]
    assert [w.is_active for w in wins] == [True, False]  # nothing picked: the front app's


def test_focus_picks_a_target_and_activates_nothing():
    hal, q, _ = _hal()
    assert hal.focus_window("2") is True
    assert hal._appkit.activated == []
    assert q.posted == [] and q.to_pid == []
    assert target.get() == target.Target("2", MINE, "TextEdit")
    assert hal.get_active_window().window_id == "2"
    assert [w.is_active for w in hal.list_windows()] == [False, True]


def test_focus_on_a_missing_window_says_so():
    hal, _, _ = _hal()
    assert hal.focus_window("999") is False
    assert "does not exist" in hal.last_focus_error
    assert target.get() is None


def test_capture_without_a_target_is_the_ordinary_screenshot():
    hal, _, captured = _hal()
    shot = hal.capture_screen()
    assert shot.size == (1512, 982) and shot.getpixel((5, 5)) == (255, 0, 0)
    assert captured == []


def test_capture_shows_only_the_target_window_where_it_sits():
    hal, _, captured = _hal()
    hal.focus_window("2")
    shot = hal.capture_screen()
    assert captured == ["2"]
    assert shot.size == (1512, 982)
    assert shot.getpixel((300, 200)) == (255, 255, 255)  # inside the window
    assert shot.getpixel((5, 5)) != (255, 0, 0)  # the person's screen is not in it
    assert shot.getpixel((99, 49)) == shot.getpixel((5, 5))  # just outside: backdrop
    region = hal.capture_screen(region=(100, 50, 400, 300))
    assert region.size == (400, 300) and region.getpixel((0, 0)) == (255, 255, 255)


def test_capture_places_a_menu_that_hangs_below_the_window():
    # macOS hands back the window and its open menu as one picture covering both.
    menu = _win(9, "TextEdit", "", MINE, 120, 340, 100, 100, layer=101)
    hal, _, captured = _hal(windows=[menu, *_windows()])
    both = Image.new("RGB", (800, 780), "black")  # 400 x 390 points at 2x
    both.paste(Image.new("RGB", (800, 600), "white"), (0, 0))
    both.paste(Image.new("RGB", (200, 200), "blue"), (40, 580))
    hal._capture_window = lambda wid: captured.append(wid) or both
    hal.focus_window("2")
    shot = hal.capture_screen()
    assert captured == ["2"]
    assert shot.getpixel((300, 200)) == (255, 255, 255)  # the window, not shrunk
    assert shot.getpixel((498, 348)) == (255, 255, 255)  # its bottom-right corner
    assert shot.getpixel((170, 400)) == (0, 0, 255)  # the menu, where it really is
    assert shot.getpixel((300, 400)) == shot.getpixel((5, 5))  # beside the menu: backdrop


def test_see_through_menu_edges_do_not_paint_black_over_the_window():
    menu = _win(9, "TextEdit", "", MINE, 150, 80, 120, 320, layer=101)  # hangs 50 pt below
    hal, _, _ = _hal(windows=[menu, *_windows()])
    both = Image.new("RGB", (800, 700), "black")
    both.paste(Image.new("RGB", (800, 600), "white"), (0, 0))
    both.paste(Image.new("RGB", (240, 20), "black"), (100, 60))  # the menu's clear top edge
    both.paste(Image.new("RGB", (240, 620), "blue"), (100, 80))
    hal._capture_window = lambda wid: both
    hal.focus_window("2")
    shot = hal.capture_screen()
    assert shot.getpixel((200, 200)) == (0, 0, 255)  # the menu
    assert shot.getpixel((200, 82)) != (0, 0, 0)  # its clear edge leaves no black strip


def test_capture_of_an_unexpected_size_is_shown_as_the_window():
    hal, _, _ = _hal()
    hal._capture_window = lambda wid: Image.new("RGB", (613, 377), "white")
    hal.focus_window("2")
    shot = hal.capture_screen()
    assert shot.getpixel((101, 51)) == (255, 255, 255)
    assert shot.getpixel((498, 348)) == (255, 255, 255)


def test_capture_after_the_window_closed_asks_for_a_new_target():
    hal, q, _ = _hal()
    hal.focus_window("2")
    q.windows = [w for w in q.windows if w["kCGWindowNumber"] != 2]
    with pytest.raises(NoTarget, match="gone"):
        hal.capture_screen()
    assert target.get() is None


def test_acting_before_picking_a_window_is_refused():
    hal, q, _ = _hal()
    for act in (
        lambda: hal.mouse_click(10, 10),
        lambda: hal.keyboard_type("hi"),
        lambda: hal.keyboard_press_key("enter"),
        lambda: hal.keyboard_hotkey(["cmd", "s"]),
        lambda: hal.mouse_scroll(0, -3),
    ):
        with pytest.raises(NoTarget, match="focus_window"):
            act()
    assert q.posted == [] and q.to_pid == []


def test_typing_and_keys_go_to_the_targets_app_only():
    hal, q, _ = _hal()
    hal.focus_window("2")
    hal.keyboard_type("hi")
    hal.keyboard_press_key("enter")
    hal.keyboard_hotkey(["shift", "tab"])
    assert q.posted == []  # nothing into the shared input stream
    assert {pid for pid, _ in q.to_pid} == {MINE}
    assert [e["unicode"] for _, e in q.to_pid if "unicode" in e] == ["h", "h", "i", "i"]
    assert hal._post_pid is None


def test_click_presses_a_button_through_accessibility():
    save = _el("AXButton", AXTitle="Save", AXEnabled=True, actions=["AXPress"])
    ax = AX({MINE: {"pid": MINE}}, at={(MINE, 120, 80): save})
    hal, q, _ = _hal(ax=ax)
    hal.focus_window("2")
    hal.mouse_click(120, 80)
    assert ax.performed == [("Save", "AXPress")]
    assert q.posted == [] and q.to_pid == []


def test_click_gives_a_text_field_the_keyboard():
    field = _el("AXTextField", AXTitle="Name", settable=("AXFocused",))
    ax = AX({MINE: {"pid": MINE}}, at={(MINE, 120, 80): field})
    hal, q, _ = _hal(ax=ax)
    hal.focus_window("2")
    hal.mouse_click(120, 80)
    assert ("Name", "AXFocused", True) in ax.set_calls
    assert q.posted == []


def test_click_selects_a_table_row_through_its_cell():
    row = _el("AXRow", AXTitle="Row 3", settable=("AXSelected",))
    cell = _el("AXCell", AXParent=row)
    ax = AX({MINE: {"pid": MINE}}, at={(MINE, 120, 80): cell})
    hal, _, _ = _hal(ax=ax)
    hal.focus_window("2")
    hal.mouse_click(120, 80)
    assert ("Row 3", "AXSelected", True) in ax.set_calls


def test_right_click_is_refused_because_its_menu_would_show_on_the_persons_screen():
    item = _el("AXStaticText", AXTitle="File.txt", actions=["AXShowMenu"])
    ax = AX({MINE: {"pid": MINE}}, at={(MINE, 120, 80): item})
    hal, _, _ = _hal(ax=ax)
    hal.focus_window("2")
    with pytest.raises(NeedsPointer, match="right-click"):
        hal.mouse_click(120, 80, button="right")
    assert ax.performed == []


def test_double_click_opens_through_accessibility():
    item = _el("AXStaticText", AXTitle="File.txt", actions=["AXOpen"])
    ax = AX({MINE: {"pid": MINE}}, at={(MINE, 120, 80): item})
    hal, _, _ = _hal(ax=ax)
    hal.focus_window("2")
    hal.mouse_click(120, 80, click_count=2)
    assert ax.performed == [("File.txt", "AXOpen")]


def test_a_click_that_needs_the_pointer_is_refused_by_default():
    canvas = _el("AXGroup", AXParent=_el("AXWindow"))
    ax = AX({MINE: {"pid": MINE}}, at={(MINE, 120, 80): canvas})
    hal, q, _ = _hal(ax=ax)
    hal.focus_window("2")
    with pytest.raises(NeedsPointer, match="real pointer"):
        hal.mouse_click(120, 80)
    with pytest.raises(NeedsPointer):
        hal.mouse_drag(1, 1, 50, 50)
    assert q.posted == [] and hal._appkit.activated == []


def test_idle_mode_borrows_the_pointer_and_puts_things_back(monkeypatch):
    monkeypatch.setenv("PINLET_BACKGROUND_POINTER", "idle")
    hal, q, _ = _hal()
    for w in q.windows:
        w["kCGWindowIsOnscreen"] = True  # activation "brings" it to this Desktop
    hal.focus_window("2")
    q.idle = 10.0
    hal.mouse_drag(110, 60, 200, 200)
    assert any(e["kind"] == "mouse" for e in q.posted)  # the real pointer did it
    assert hal._appkit.activated == [MINE, THEIRS]  # ours forward, then theirs back
    assert q.warped == (10, 20)  # the pointer returns to where the person left it
    assert hal._borrowing is False


def test_idle_mode_gives_up_while_the_person_keeps_working(monkeypatch):
    monkeypatch.setenv("PINLET_BACKGROUND_POINTER", "idle")
    hal, q, _ = _hal()
    hal.focus_window("2")
    q.idle = 0.2
    with pytest.raises(NeedsPointer, match="kept using"):
        hal.mouse_drag(110, 60, 200, 200)
    assert q.posted == [] and hal._appkit.activated == []


def test_scroll_moves_the_scroll_bar_through_accessibility():
    bar = _el("AXScrollBar", AXValue=0.5, settable=("AXValue",))
    doc = _el("AXTextArea", AXSize=SimpleNamespace(width=400, height=1300))
    area = _el(
        "AXScrollArea",
        AXVerticalScrollBar=bar,
        AXContents=[doc],
        AXSize=SimpleNamespace(width=400, height=300),
    )
    doc["AXParent"] = area
    ax = AX({MINE: {"pid": MINE}}, at={(MINE, 300, 200): doc})  # the window's middle
    hal, q, _ = _hal(ax=ax)
    hal.focus_window("2")
    hal.mouse_scroll(0, -5)  # down: 5 lines of 40 pt over 1000 hidden points
    assert bar["AXValue"] == pytest.approx(0.7)
    hal.mouse_scroll(0, 50)  # up, past the top: clamped
    assert bar["AXValue"] == 0.0
    assert q.posted == [] and q.to_pid == []


def test_scroll_without_a_scroll_bar_sends_the_wheel_to_the_app():
    hal, q, _ = _hal()
    hal.focus_window("2")
    hal.mouse_scroll(0, -3)
    assert [(pid, e["kind"]) for pid, e in q.to_pid] == [(MINE, "scroll")]
    assert q.posted == []


def test_each_agent_session_keeps_its_own_target():
    hal, _, _ = _hal()
    session = {"id": 1}
    target.set_key_fn(lambda: session["id"])
    hal.focus_window("2")
    session["id"] = 2
    assert target.get() is None
    assert hal.focus_window("1")
    session["id"] = 1
    assert target.get().window_id == "2"
    target.clear(1)
    assert target.get() is None


def test_accessibility_tree_reads_the_targets_app_not_the_front_app():
    mine = _el(
        "AXWindow",
        (100, 50, 400, 300),
        children=[_el("AXButton", (120, 80, 60, 24), AXTitle="Mine", AXEnabled=True)],
        AXTitle="Draft",
    )
    theirs = _el(
        "AXWindow",
        (0, 0, 800, 600),
        children=[_el("AXButton", (10, 10, 60, 24), AXTitle="Theirs", AXEnabled=True)],
        AXTitle="Shopping",
    )
    ax = FakeAX({MINE: {"AXWindows": [mine]}, THEIRS: {"AXWindows": [theirs]}})
    inspector = AXInspector(
        ax=ax, appkit=FakeAppKit(front_pid=THEIRS), screen_bounds=(0, 0, 1512, 982)
    )
    assert "Theirs" in {e.name for e in inspector.inspect_desktop()}
    target.choose("2", MINE, "TextEdit")
    names = {e.name for e in inspector.inspect_desktop()}
    assert "Mine" in names and "Theirs" not in names


def _menu_bar(enabled=True):
    select_all = _el(
        "AXMenuItem",
        AXTitle="Select All",
        AXMenuItemCmdChar="A",
        AXMenuItemCmdModifiers=0,
        AXEnabled=enabled,
        actions=["AXPress"],
    )
    save_as = _el(
        "AXMenuItem",
        AXTitle="Save As",
        AXMenuItemCmdChar="S",
        AXMenuItemCmdModifiers=1,  # shift
        actions=["AXPress"],
    )
    save = _el(
        "AXMenuItem",
        AXTitle="Save",
        AXMenuItemCmdChar="S",
        AXMenuItemCmdModifiers=0,
        actions=["AXPress"],
    )
    edit = _el("AXMenuBarItem", children=[_el("AXMenu", children=[select_all])], AXTitle="Edit")
    file = _el("AXMenuBarItem", children=[_el("AXMenu", children=[save, save_as])], AXTitle="File")
    return _el("AXMenuBar", children=[file, edit])


def test_cmd_shortcut_picks_the_menu_item_instead_of_sending_keys():
    ax = AX({MINE: {"pid": MINE, "AXMenuBar": _menu_bar()}})
    hal, q, _ = _hal(ax=ax)
    hal.focus_window("2")
    hal.keyboard_hotkey(["cmd", "a"])
    hal.keyboard_hotkey(["cmd", "shift", "s"])
    hal.keyboard_hotkey(["cmd", "s"])
    assert ax.performed == [("Select All", "AXPress"), ("Save As", "AXPress"), ("Save", "AXPress")]
    assert q.posted == [] and q.to_pid == []


def test_shortcut_without_an_enabled_menu_item_is_sent_as_keys_to_the_app():
    ax = AX({MINE: {"pid": MINE, "AXMenuBar": _menu_bar(enabled=False)}})
    hal, q, _ = _hal(ax=ax)
    hal.focus_window("2")
    hal.keyboard_hotkey(["shift", "tab"])  # not a Cmd shortcut: plain keys reach the app
    assert q.posted == [] and {pid for pid, _ in q.to_pid} == {MINE}
    for keys in (["cmd", "a"], ["cmd", "k"]):  # a disabled item, and no such item
        with pytest.raises(NeedsPointer, match="not in front"):
            hal.keyboard_hotkey(keys)
    assert ax.performed == []


def test_cmd_a_selects_the_focused_fields_text_when_the_menu_item_is_disabled():
    field = _el("AXTextArea", AXNumberOfCharacters=11, settable=("AXSelectedTextRange",))
    ax = AX(
        {MINE: {"pid": MINE, "AXMenuBar": _menu_bar(enabled=False), "AXFocusedUIElement": field}}
    )
    ax.kAXValueCFRangeType = 4
    ax.AXValueCreate = lambda kind, value: ("range", *value)
    hal, q, _ = _hal(ax=ax)
    hal.focus_window("2")
    hal.keyboard_hotkey(["cmd", "a"])
    assert field["AXSelectedTextRange"] == ("range", 0, 11)
    assert q.posted == [] and q.to_pid == []


def test_idle_mode_sends_a_cmd_shortcut_with_the_app_in_front(monkeypatch):
    monkeypatch.setenv("PINLET_BACKGROUND_POINTER", "idle")
    hal, q, _ = _hal()
    for w in q.windows:
        w["kCGWindowIsOnscreen"] = True
    hal.focus_window("2")
    q.idle = 10.0
    hal.keyboard_hotkey(["cmd", "k"])
    assert [e["code"] for e in q.posted if e["kind"] == "key" and e["down"]] == [55, 40]
    assert hal._appkit.activated == [MINE, THEIRS]


def _framed_window(frame, title):
    return _el("AXWindow", frame, AXTitle=title)


def test_a_click_the_app_would_give_to_its_other_window_is_refused():
    theirs = _framed_window((0, 0, 900, 700), "Their tab")  # same app, covers the point
    link = _el("AXLink", AXTitle="Buy", AXEnabled=True, actions=["AXPress"], AXWindow=theirs)
    ax = AX({MINE: {"pid": MINE}}, at={(MINE, 120, 80): link})
    hal, q, _ = _hal(ax=ax)
    hal.focus_window("2")
    with pytest.raises(NeedsPointer, match="pinlet browser"):
        hal.mouse_click(120, 80)
    assert ax.performed == [] and q.posted == []


def test_a_click_in_the_agents_own_window_or_its_sheet_goes_through():
    own = _framed_window((100, 50, 400, 300), "Draft")
    sheet = _framed_window((150, 50, 300, 120), "")
    ok = _el("AXButton", AXTitle="OK", AXEnabled=True, actions=["AXPress"], AXWindow=sheet)
    save = _el("AXButton", AXTitle="Save", AXEnabled=True, actions=["AXPress"], AXWindow=own)
    ax = AX({MINE: {"pid": MINE}}, at={(MINE, 120, 80): save, (MINE, 200, 100): ok})
    hal, _, _ = _hal(ax=ax)
    hal.focus_window("2")
    hal.mouse_click(120, 80)
    hal.mouse_click(200, 100)
    assert ax.performed == [("Save", "AXPress"), ("OK", "AXPress")]


def test_picking_a_window_asks_a_browser_for_its_page_elements():
    ax = AX(
        {MINE: {"pid": MINE, "AXRole": "AXApplication", "settable": ("AXManualAccessibility",)}}
    )
    hal, _, _ = _hal(ax=ax)
    hal.focus_window("2")
    assert ax.tree[MINE]["AXManualAccessibility"] is True


def test_accessibility_tree_reads_only_the_agents_window_of_a_shared_app():
    mine = _el(
        "AXWindow",
        (100, 50, 400, 300),
        children=[_el("AXButton", (120, 80, 60, 24), AXTitle="Mine", AXEnabled=True)],
    )
    other = _el(
        "AXWindow",
        (0, 0, 800, 600),
        children=[_el("AXButton", (10, 10, 60, 24), AXTitle="Other tab", AXEnabled=True)],
    )
    bar = _el(
        "AXMenuBar",
        (0, 0, 1512, 24),
        children=[_el("AXMenuBarItem", (10, 0, 40, 24), AXTitle="File")],
    )
    ax = FakeAX({MINE: {"AXWindows": [other, mine], "AXMenuBar": bar}})
    inspector = AXInspector(
        ax=ax, appkit=FakeAppKit(front_pid=THEIRS), screen_bounds=(0, 0, 1512, 982)
    )
    hal, _, _ = _hal()
    hal.focus_window("2")  # the window at (100, 50, 400, 300)
    names = {e.name for e in inspector.inspect_desktop()}
    assert names == {"Mine"}


def test_agent_browser_opens_a_separate_background_instance(tmp_path, monkeypatch, capsys):
    from pinlet import agent_browser

    (tmp_path / "Google Chrome.app").mkdir()
    assert agent_browser.find_browser(app_dirs=[str(tmp_path)]) == "Google Chrome"
    assert agent_browser.find_browser(app_dirs=[str(tmp_path / "none")]) is None
    monkeypatch.setenv("PINLET_HOME", str(tmp_path / "home"))
    profile = agent_browser.profile_dir()
    assert profile == tmp_path / "home" / "agent-browser"
    cmd = agent_browser.command("Google Chrome", profile, "https://example.com")
    assert cmd[:5] == ["/usr/bin/open", "-g", "-n", "-a", "Google Chrome"]
    assert f"--user-data-dir={profile}" in cmd and cmd[-1] == "https://example.com"
    assert agent_browser.main(["--print", "--app", "Brave Browser"]) == 0
    assert '-a "Brave Browser"' in capsys.readouterr().out
    assert not profile.exists()  # --print changes nothing
