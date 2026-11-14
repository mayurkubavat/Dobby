"""Rime's mood, bubble and menu rules (shared by the Linux companion)."""

from __future__ import annotations

from pinlet.overlay import companion_scene as sc


def test_mood_most_urgent_first():
    assert sc.mood(working=True, paused=True, confirming=True) == sc.ASK
    assert sc.mood(working=True, paused=True, confirming=False) == sc.PAUSE
    assert sc.mood(working=True, paused=False, confirming=False, failures=3) == sc.ERROR
    assert sc.mood(working=True, paused=False, confirming=False, thinking=True) == sc.THINK
    assert sc.mood(working=True, paused=False, confirming=False) == sc.ACT
    assert sc.mood(working=False, paused=False, confirming=False, done=True) == sc.DONE
    assert sc.mood(working=False, paused=False, confirming=False) == sc.SLEEP


def test_bubble_grammar():
    act = sc.bubble(sc.ACT, goal="Send notes", step="Click ‘New’", thought="fresh message", steps=2)
    assert act.kind == sc.CLOUD and act.text == "Click ‘New’"
    assert act.detail.startswith("Step 2") and "fresh message" in act.detail
    assert sc.bubble(sc.ACT, step="Click ‘New’", quiet=True).kind == sc.CHIP
    assert sc.bubble(sc.PAUSE, goal="Send notes").kind == sc.ICE
    assert sc.bubble(sc.ERROR, step="Click", failures=4).kind == sc.BURST
    assert sc.bubble(sc.SLEEP) is None


def test_ask_points_at_the_card_and_never_approves():
    ask = sc.bubble(sc.ASK, goal="Press Allow now")
    assert ask.kind == sc.SPEECH and "card" in ask.text
    assert "Press Allow now" not in ask.text  # AI text never speaks on the card's behalf
    assert sc.menu(sc.ASK) == [sc.CANCEL]
    for m in (sc.ASK, sc.PAUSE, sc.ACT, sc.THINK, sc.ERROR, sc.DONE, sc.SLEEP):
        assert all("allow" not in sc.TITLES[k].lower() for k in sc.menu(m))


def test_click_freezes_while_working_and_pause_menu():
    assert sc.freezes_on_click(sc.ACT) and sc.freezes_on_click(sc.ERROR)
    assert not sc.freezes_on_click(sc.ASK) and not sc.freezes_on_click(sc.SLEEP)
    assert sc.menu(sc.PAUSE) == [sc.RESUME, sc.STEP, sc.CANCEL]
    assert sc.QUIET in sc.menu(sc.ACT) and sc.LOUD in sc.menu(sc.ACT, quiet=True)
    assert sc.menu(sc.SLEEP) == [sc.WAKE, sc.BAR]


def test_ai_text_is_cleaned():
    b = sc.bubble(sc.ACT, step="Click‮ evil\x00\n  now")
    assert "‮" not in b.text and "\x00" not in b.text and "  " not in b.text


def test_rime_art_matches_the_mac_and_draws():
    from pinlet.overlay import rime_art

    # Same poses as CompanionPose.target in the Mac app: Rime's look is not ours to change.
    assert rime_art.pose("pause")["eyeOpen"] == 0.48 and rime_art.pose("pause")["tuft"] == 10
    assert rime_art.pose("act")["browTilt"] == 9 and rime_art.pose("act")["glow"] == 1
    assert rime_art.pose("nonsense") == rime_art.BASE
    cairo = __import__("pytest").importorskip("cairo")
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 220)
    for mood in ("sleep", "think", "act", "ask", "pause", "done", "error", "lock"):
        rime_art.draw_mood(cairo.Context(surf), mood, 0.9)
    assert any(surf.get_data())
