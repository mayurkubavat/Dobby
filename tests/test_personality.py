"""Dobby's house-elf voice (dobby.personality) and how the overlay bar uses it."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from dobby import personality
from dobby.overlay import overlay_config, style
from dobby.overlay.menu import bar_view
from dobby.personality import Voice


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


NOON = lambda: dt.datetime(2026, 11, 2, 12, 0)  # noqa: E731


def voice(level="elf", **kw):
    clock = kw.pop("clock", Clock())
    return Voice(level, seed=7, clock=clock, today=kw.pop("today", NOON), **kw), clock


def view(**kw):
    base = dict(paused=False, takeover=False, takeover_resume="auto", working=False, turbo=False,
                current_mode="hidden", bar_mode="always", owner=True)
    base.update(kw)
    return bar_view(**base)


def spec(v, **kw):
    kw.setdefault("hovered", True)
    return style.bar_spec(kw.pop("view"), voice=v, **kw)


# ------------------------------------------------------------ settings
def test_levels_from_env():
    assert personality.normalize_level(None) == "subtle"
    assert personality.normalize_level("OFF") == "off"
    assert personality.normalize_level("0") == "off"
    assert personality.normalize_level("elf") == "elf"
    assert personality.normalize_level("bogus") == "subtle"
    cfg = overlay_config({"DOBBY_PERSONALITY": "elf", "DOBBY_USER_NAME": " Sam ", "DOBBY_PERSONALITY_LINES": "x.json"})
    assert cfg["personality"] == "elf" and cfg["user_name"] == "Sam" and cfg["personality_lines"] == "x.json"
    assert "personality" not in overlay_config({})


def test_every_line_fits_the_bar():
    for line in personality.all_lines():
        filled = line.replace("{name}", "Dobby").replace("{you}", ", sir")
        assert len(filled) <= 38, filled


def test_working_lines_leave_room_for_the_step():
    for line in personality.ELF["working"] + (personality.SUBTLE["working"],):
        assert len(line.replace("{name}", "Dobby")) <= 20, line


def test_lines_file_replaces_a_moment(tmp_path):
    path = tmp_path / "lines.json"
    path.write_text(json.dumps({"working": ["{name} hums"], "nope": ["x"], "done": "not a list"}))
    lines = personality.load_lines(str(path))
    assert lines == {"working": ("{name} hums",)}
    v, _ = voice(lines=lines)
    assert v.line("working", "Working") == "Dobby hums"
    assert personality.load_lines(str(tmp_path / "missing.json")) == {}


def test_user_name_and_agent_name():
    v, _ = voice(user="Sam", lines={"done": ["Done{you}!"], "working": ["{name} is on it"]})
    assert v.line("done", "") == "Done, Sam!"
    assert v.line("working", "", name="Antigravity") == "Antigravity is on it"
    v2, _ = voice(lines={"done": ["Done{you}!"]})
    assert v2.line("done", "") == "Done, sir!"


# ------------------------------------------------------------ choosing lines
def test_off_returns_the_default():
    v, _ = voice("off")
    assert v.line("working", "Working") == "Working"
    assert v.startup() == ""


def test_subtle_is_fixed():
    v, clock = voice("subtle")
    first = v.line("working", "Working")
    clock.t += 500
    assert v.line("working", "Working") == first == "Dobby is on it"
    assert v.egg(steps=500, elapsed=9999) is None


def test_elf_holds_a_line_for_the_cooldown_then_rotates():
    v, clock = voice()
    first = v.line("working", "")
    clock.t += personality.COOLDOWN_SECS - 1
    assert v.line("working", "") == first
    clock.t += 2
    assert v.line("working", "") != first


def test_elf_does_not_repeat_recent_lines():
    v, clock = voice()
    seen = []
    for _ in range(5):
        seen.append(v.line("working", ""))
        clock.t += personality.COOLDOWN_SECS
    assert len(set(seen)) == 5  # the pool has 6 lines, none of the last 5 repeats


def test_no_rotate_keeps_the_line():
    v, clock = voice()
    first = v.line("sleeping", "", rotate=False)
    clock.t += 10_000
    assert v.line("sleeping", "", rotate=False) == first


# ------------------------------------------------------------ easter eggs
def test_hundred_steps_once_a_day():
    v, clock = voice()
    assert v.egg(steps=99, elapsed=10) is None
    assert "One hundred steps" in v.egg(steps=100, elapsed=10)
    clock.t += personality.COOLDOWN_SECS + 1
    assert v.egg(steps=150, elapsed=10) is None  # already shown today


def test_late_night_and_long_tasks():
    v, _ = voice(today=lambda: dt.datetime(2026, 11, 2, 2, 30))
    assert "So late" in v.egg(steps=3, elapsed=10)
    w, _ = voice()
    assert "An hour" in w.egg(steps=3, elapsed=3600)


# ------------------------------------------------------------ the bar
def test_off_bar_is_unchanged():
    v, _ = voice("off")
    for kw in ({}, {"working": True}, {"paused": True}, {"current_mode": "stopped"}, {"takeover": True}):
        vw = view(**kw)
        assert style.bar_spec(vw, voice=v, hovered=True, step="Click ‘Send’", steps=3) == \
            style.bar_spec(vw, hovered=True, step="Click ‘Send’", steps=3)


def test_subtle_working_bar_keeps_the_facts():
    v, _ = voice("subtle")
    s = spec(v, view=view(working=True), step="Click ‘Send’", steps=7, elapsed=42)
    assert s.text == "Dobby is on it · Click ‘Send’ · step 7 · 0:42"
    s = spec(v, view=view(working=True), step="Click ‘Send’", steps=7, elapsed=42, narrate=False)
    assert s.text == "Click ‘Send’ · step 7 · 0:42"  # a step leaves no room for words
    s = spec(v, view=view(working=True), steps=0, elapsed=4, narrate=False)
    assert s.text == "Dobby is on it · 0:04"


def test_elf_thinking_and_sleeping():
    v, _ = voice()
    s = spec(v, view=view(working=True), thinking=True, steps=2, elapsed=5)
    assert s.text.split(" · ")[0] in [x.replace("{name}", "Dobby") for x in personality.ELF["thinking"]]
    v, _ = voice()  # a fresh Dobby: no task just ended, so no cheer
    s = spec(v, view=view(), hovered=False)
    assert s.compact and s.text in [x.replace("{name}", "Dobby") for x in personality.ELF["sleeping"]]


def test_safety_texts_stay_plain():
    v, _ = voice()
    assert spec(v, view=view(takeover=True)).text.startswith("You're in control")
    confirm = style.confirm_spec("Delete the file?")
    assert confirm.text == "Delete the file?"


def test_paused_and_stopped_get_words():
    v, _ = voice()
    assert spec(v, view=view(paused=True)).text in ["Dobby is paused · Dobby waits", "Dobby is paused for you, sir"]
    assert spec(v, view=view(current_mode="stopped")).text in ["Dobby stopped, as you asked", "Dobby stops at once, sir!"]


def test_cheer_when_a_task_ends_then_back_to_sleep():
    v, clock = voice()
    spec(v, view=view(working=True), step="Click", steps=4, elapsed=3)
    done = spec(v, view=view(), hovered=False)
    cheers = [x.replace("{name}", "Dobby").replace("{you}", ", sir") for x in personality.ELF["done"]]
    assert done.text in cheers
    assert v.refresh_due() is False
    clock.t += personality.DONE_SECS + 0.1
    assert v.refresh_due() is True and v.refresh_due() is False
    assert spec(v, view=view(), hovered=False).text not in cheers


def test_no_cheer_after_stop_or_an_empty_task():
    v, _ = voice()
    spec(v, view=view(working=True), steps=0)
    assert spec(v, view=view(), hovered=False).text not in personality.ELF["done"]
    spec(v, view=view(working=True), steps=3)
    spec(v, view=view(current_mode="stopped"))
    assert "did it" not in spec(v, view=view(), hovered=False).text


def test_cheer_keeps_click_to_wake():
    v, _ = voice("subtle")
    spec(v, view=view(working=True), steps=2)
    s = spec(v, view=view(ai_waiting=True), hovered=False)
    assert s.text == "Dobby is done! · click to wake"


def test_classic_bar_uses_the_dressed_text():
    v, _ = voice("subtle")
    s = style.bar_spec(view(), style="classic", voice=v)
    assert s.text == "Dobby is resting"


def test_startup_line():
    assert personality.startup_line({"DOBBY_PERSONALITY": "off"}) == ""
    assert personality.startup_line({}) == "Dobby is ready."
    assert "Antigravity" in personality.startup_line({"DOBBY_PERSONALITY": "elf", "DOBBY_AGENT_NAME": "Antigravity"})


@pytest.mark.parametrize("line", personality.all_lines())
def test_no_film_quotes(line):
    for quote in ("has no master", "bad Dobby", "a free elf", "Harry Potter"):
        assert quote.lower() not in line.lower()
