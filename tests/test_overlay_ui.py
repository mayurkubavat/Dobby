"""UI behaviour of the overlay that does not need a Mac: control bar states
and clicks, the shared Dobby menu (menu bar icon and the tag's ▾), the
clickable tag area, and how replays draw the capsule."""

from __future__ import annotations

import pytest

from dobby.overlay import geometry
from dobby.overlay.menu import bar_click, bar_tip, bar_view, label_click, menu_entries, status_of

ARROW_W, ARROW_H, CHIP_H = 13 * 1.25, 21.5 * 1.25, 20.0


def view(**kw):
    base = dict(
        paused=False,
        takeover=False,
        takeover_resume="auto",
        working=False,
        turbo=False,
        current_mode="hidden",
        bar_mode="always",
        owner=True,
    )
    base.update(kw)
    return bar_view(**base)


# ------------------------------------------------------------ control bar
class TestBarStaysOn:
    def test_idle_bar_says_sleeping_and_stays(self):
        v = view()
        assert v is not None and v[0] == "ready" and v[1] == "Dobby is sleeping"
        assert (v[2], v[3]) == ("Pause", "Stop")

    def test_idle_dot_is_dimmed(self):
        assert view()[5] < 1.0 and view(working=True)[5] == 1.0

    def test_auto_mode_hides_when_idle(self):
        assert view(bar_mode="auto") is None
        assert view(bar_mode="auto", working=True)[0] == "working"

    def test_only_owner_shows_idle_bar(self):
        assert view(owner=False) is None
        # ...but any helper shows its bar while it works or is paused
        assert view(owner=False, working=True)[0] == "working"
        assert view(owner=False, paused=True)[0] == "paused"

    def test_working_uses_risk_colour_and_names_stop_hotkey(self):
        v = view(working=True)
        assert v[4] == "risk" and "⌃⌥⎋" in v[1]


class TestBarStates:
    def test_paused_beats_everything(self):
        v = view(paused=True, takeover=True, working=True, current_mode="stopped")
        assert v[:4] == ("paused", "Dobby is paused", "Resume", "Stop") and v[4] == "caution"

    @pytest.mark.parametrize(
        "resume,text", [("auto", "waits until you stop"), ("manual", "You're in control")]
    )
    def test_takeover_text(self, resume, text):
        v = view(takeover=True, takeover_resume=resume)
        assert v[0] == "takeover" and text in v[1] and v[2] == "Resume now"

    def test_stopped_shows_before_working(self):
        v = view(current_mode="stopped", working=True)
        assert v[:4] == ("stopped", "Dobby stopped", "OK", "Hide") and v[4] == "danger"

    @pytest.mark.parametrize(
        "working,text", [(True, "Dobby ⚡ Turbo"), (False, "Dobby ⚡ Turbo · sleeping")]
    )
    def test_turbo_labels(self, working, text):
        assert view(turbo=True, working=working)[1].startswith(text)


class TestBarAwake:
    def test_awake_without_a_waiting_ai_says_so(self):
        v = view(awake=True)
        assert (
            v[0] == "awake" and v[1] == "Dobby is awake · no AI is waiting — ask your AI for a task"
        )
        assert v[2:] == ("Pause", "Stop", "ok", 1.0)

    def test_awake_hands_back_to_a_waiting_ai(self):
        assert view(awake=True, handoff=True)[:2] == (
            "awake",
            "Dobby is awake · handing back to the AI",
        )
        assert "handing back" in view(awake=True, handoff=True, turbo=True)[1]

    def test_sleeping_dot_stays_dimmed(self):
        v = view(awake=False)
        assert v[0] == "ready" and v[5] == 0.45

    def test_any_helper_shows_awake(self):
        assert view(owner=False, awake=True)[0] == "awake"
        assert view(bar_mode="auto", awake=True)[0] == "awake"

    @pytest.mark.parametrize(
        "kw,mode",
        [
            (dict(working=True), "working"),
            (dict(paused=True), "paused"),
            (dict(takeover=True), "takeover"),
            (dict(current_mode="stopped"), "stopped"),
        ],
    )
    def test_awake_loses_to_other_states(self, kw, mode):
        assert view(awake=True, **kw)[0] == mode


class TestBarAiWaiting:
    def test_waiting_ai_bar_invites_a_click(self):
        v = view(ai_waiting=True)
        assert (
            v[:2] == ("ready", "Dobby is sleeping · click to wake") and label_click(v[0]) == "wake"
        )
        assert "click to wake" in view(ai_waiting=True, turbo=True)[1]

    def test_any_helper_shows_its_waiting_ai(self):
        # the bar lock does not hide it: the click must reach this helper's AI
        assert view(ai_waiting=True, owner=False)[0] == "ready"

    def test_auto_bar_stays_out_of_sight_while_resting(self):
        # The default: wake from the menu bar item or the activity panel instead.
        assert view(ai_waiting=True, bar_mode="auto") is None
        assert view(ai_waiting=True, bar_mode="auto", working=True)[0] == "working"
        assert view(ai_waiting=True, bar_mode="auto", paused=True)[0] == "paused"

    def test_owner_steps_aside_while_another_helpers_ai_waits(self):
        assert view(other_waiting=True) is None
        assert view(other_waiting=True, ai_waiting=True)[0] == "ready"
        assert view(other_waiting=True, working=True)[0] == "working"

    def test_wake_beats_waiting_and_other_states_beat_both(self):
        assert view(ai_waiting=True, awake=True, handoff=True)[0] == "awake"
        assert view(ai_waiting=True, paused=True)[0] == "paused"
        assert view(ai_waiting=True, working=True)[0] == "working"

    def test_tooltips(self):
        assert bar_tip("ready") == "Click to wake Dobby"
        assert bar_tip("ready", True) == "Click to wake Dobby: your AI is waiting for you"
        assert (
            bar_tip("ready", True, "report done")
            == "Click to wake Dobby: your AI is waiting for you (report done)"
        )
        assert bar_tip("awake") == "Click to put Dobby to sleep"
        assert bar_tip("working", True) is None


@pytest.mark.parametrize(
    "mode,action",
    [
        ("ready", "wake"),
        ("awake", "sleep"),
        ("working", "none"),
        ("paused", "none"),
        ("takeover", "none"),
        ("stopped", "none"),
        ("confirm", "none"),
        ("hidden", "none"),
    ],
)
def test_bar_label_click(mode, action):
    assert label_click(mode) == action


@pytest.mark.parametrize(
    "mode,which,action",
    [
        ("confirm", "primary", "allow"),
        ("confirm", "secondary", "deny"),
        ("working", "primary", "pause"),
        ("ready", "primary", "pause"),
        ("working", "secondary", "stop"),
        ("ready", "secondary", "stop"),
        ("awake", "primary", "pause"),
        ("awake", "secondary", "stop"),
        ("paused", "primary", "resume"),
        ("takeover", "primary", "resume"),
        ("paused", "secondary", "stop"),
        ("takeover", "secondary", "stop"),
        ("stopped", "primary", "dismiss"),
        ("stopped", "secondary", "hide"),
    ],
)
def test_bar_buttons(mode, which, action):
    assert bar_click(mode, which) == action


# --------------------------------------------------------------- the menu
@pytest.mark.parametrize(
    "paused,takeover,working,turbo,status,glyph",
    [
        (False, False, False, False, "Idle", "◇"),
        (False, False, True, False, "Working", "◆"),
        (True, False, True, False, "Paused", "‖"),
        (False, True, False, False, "You're in control", "✋"),
        (False, False, True, True, "Working (Turbo)", "⚡"),
        (True, False, False, True, "Paused (Turbo)", "‖"),
    ],
)
def test_status_glyphs(paused, takeover, working, turbo, status, glyph):
    assert status_of(paused, takeover, working, turbo) == (status, glyph)
    assert len(glyph) == 1  # one glyph keeps the icon clear of the notch


def _entries(**kw):
    base = dict(
        status="Idle",
        steps=[],
        halted=False,
        turbo=False,
        overlay_on=True,
        trail_on=True,
        bar_shown=True,
    )
    base.update(kw)
    return menu_entries(**base)


def _by_title(entries):
    return {t: (a, s) for t, a, s in entries}


def test_menu_has_every_control():
    m = _by_title(_entries())
    for title, action in [
        ("Pause", "menuPauseToggle:"),
        ("Stop", "menuStop:"),
        ("Show overlay", "menuToggleOverlay:"),
        ("Show control bar (⌃⌥H)", "menuTogglePill:"),
        ("Show step trail", "menuToggleTrail:"),
        ("Clear step trail", "menuClearTrail:"),
    ]:
        assert m[title][0] == action
    assert m["Turbo mode (no prompts, ignores take-over)"][0] == "menuToggleTurbo:"


def test_menu_checkmarks_follow_state():
    m = _by_title(_entries(turbo=True, overlay_on=False, trail_on=False, bar_shown=False))
    assert m["Turbo mode (no prompts, ignores take-over)"][1] is True
    assert m["Show overlay"][1] is False
    assert m["Show control bar (⌃⌥H)"][1] is False
    assert m["Show step trail"][1] is False


def test_menu_pause_becomes_resume_when_halted():
    m = _by_title(_entries(halted=True))
    assert "Resume" in m and "Pause" not in m


def test_menu_without_bar_has_no_bar_toggle():
    assert not any("control bar" in t for t, _, _ in _entries(bar_shown=None))


def test_menu_recent_steps_newest_first_and_short():
    long = "Click " + "x" * 200
    titles = [t for t, _, _ in _entries(steps=["first", "second", long])]
    i = titles.index("Recent steps")
    assert titles[i + 1].strip().startswith("Click x") and titles[i + 1].endswith("…")
    assert len(titles[i + 1].strip()) <= 60
    assert titles[i + 2].strip() == "second" and titles[i + 3].strip() == "first"
    assert titles[0] == "Dobby: Idle"


@pytest.mark.parametrize(
    "awake,title,action",
    [
        (False, "Wake Dobby", "menuWake:"),
        (True, "Put Dobby to sleep", "menuSleep:"),
    ],
)
def test_menu_wake_and_sleep_entry(awake, title, action):
    entries = _entries(awake=awake)
    assert entries[1] == ("-", None, None) and entries[2] == (title, action, None)


def test_menu_has_no_wake_entry_while_working():
    titles = [t for t, _, _ in _entries(awake=None)]
    assert "Wake Dobby" not in titles and "Put Dobby to sleep" not in titles


def test_menu_with_no_steps_says_so():
    assert "   (none yet)" in [t for t, _, _ in _entries()]


# ------------------------------------------------- clickable tag (the ▾)
@pytest.mark.parametrize("tip", [(0, 0), (500, 300), (1500.5, 980.25)])
@pytest.mark.parametrize("width", [60, 140, 400])
def test_tag_click_area_never_covers_the_pointer_tip(tip, width):
    """Dobby clicks at the tip, so the clickable tag must not cover it."""
    rect = geometry.chip_rect(tip, width, ARROW_W, ARROW_H, CHIP_H)
    assert not geometry.contains(rect, tip)
    x, y, w, h = rect
    assert x > tip[0] and y > tip[1]  # below-right of the tip
    assert (w, h) == (width, CHIP_H)


def test_tag_click_area_sits_below_the_arrow():
    _x, y, _w, _h = geometry.chip_rect((100, 100), 80, ARROW_W, ARROW_H, CHIP_H)
    assert y >= 100 + ARROW_H  # clear of the arrow body too


# ------------------------------------------------ replay capsule drawing
@pytest.fixture
def draw():
    PIL = pytest.importorskip("PIL")
    from PIL import Image

    from dobby.overlay.annotate import TrackState, annotate

    def _draw(**state):
        img = Image.new("RGB", (420, 90), (255, 255, 255))
        return annotate(img, TrackState(cursor=(10, 0), **state))

    _ = PIL
    return _draw


def _nonwhite_columns(img, y):
    px = img.load()
    return [x for x in range(img.width) if px[x, y] != (255, 255, 255)]


def test_capsule_has_round_ends(draw):
    img = draw()
    top_row = _nonwhite_columns(img, 22 + 1)
    mid_row = _nonwhite_columns(img, 22 + 11)
    # corners are cut away: the top edge is shorter than the middle row
    assert (max(top_row) - min(top_row)) < (max(mid_row) - min(mid_row))


def test_capsule_thinking_icon_widens_tag(draw):
    assert max(_nonwhite_columns(draw(state="thinking"), 33)) > max(_nonwhite_columns(draw(), 33))


def _label_only_width(text="Dobby"):
    from PIL import Image, ImageDraw

    from dobby.overlay.annotate import _font

    return 9 + ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(text, font=_font(12)) + 9


def test_capsule_idle_has_no_chevron(draw):
    img = draw()
    left, w = 10 + 12, _label_only_width()
    # the capsule (plus its 1px shadow) is exactly a label-only capsule wide
    assert max(_nonwhite_columns(img, 22 + 11)) == pytest.approx(left + w + 1, abs=2)
    # and its trailing pad holds no white chevron strokes
    px = img.load()
    for x in range(int(left + w - 9 + 3), int(left + w - 3)):
        for y in range(22 + 5, 22 + 18):
            assert min(px[x, y]) < 230, (x, y)


def test_capsule_detail_has_breadcrumb_and_is_wider(draw):
    idle = max(_nonwhite_columns(draw(), 33))
    busy = max(_nonwhite_columns(draw(intent="Click Run"), 33))
    assert busy > idle + 8 + 5 + 20  # › + gap + some detail text


@pytest.mark.parametrize("risk,channel", [("ok", 2), ("caution", 0), ("danger", 0)])
def test_capsule_colour_follows_risk(draw, risk, channel):
    r, g, b = draw(risk=risk).getpixel((26, 22 + 15))
    assert (r, g, b)[channel] == max(r, g, b)
