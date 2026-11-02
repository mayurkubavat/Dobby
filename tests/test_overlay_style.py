"""The overlay's look, decided in pure code (dobby.overlay.style): per-Dobby
colours, the top bar's layout per state, glow states and corners, the step
trail and where the name tag goes near screen edges."""

from __future__ import annotations

import pytest

from dobby.control import commands, registry
from dobby.overlay import geometry, overlay_config, style
from dobby.overlay.menu import bar_view

ARROW_W, ARROW_H, CHIP_H = 13 * 1.25, 21.5 * 1.25, 20.0


def view(**kw):
    base = dict(paused=False, takeover=False, takeover_resume="auto", working=False, turbo=False,
                current_mode="hidden", bar_mode="always", owner=True)
    base.update(kw)
    return bar_view(**base)


def keys(spec):
    return [b.key for b in spec.buttons]


# ------------------------------------------------------------ config
def test_style_from_env():
    assert overlay_config({"DOBBY_OVERLAY_STYLE": "classic"})["style"] == "classic"
    assert "style" not in overlay_config({"DOBBY_OVERLAY_STYLE": "fancy"})
    assert overlay_config({"DOBBY_OVERLAY_AGENT_COLORS": "0"})["agent_colors"] is False
    assert style.normalize_style(None) == "modern" and style.normalize_style("CLASSIC") == "classic"


# ------------------------------------------------------- agent colours
class TestAgentColors:
    def test_first_dobby_is_violet(self):
        assert style.pick_agent_color([]) == "#7C5CFF"

    def test_next_free_colour(self):
        assert style.pick_agent_color(["#7c5cff"]) == style.AGENT_PALETTE[1]
        assert style.pick_agent_color(["#7C5CFF", style.AGENT_PALETTE[2]]) == style.AGENT_PALETTE[1]

    def test_wraps_to_least_used(self):
        taken = list(style.AGENT_PALETTE) + [style.AGENT_PALETTE[0]]
        assert style.pick_agent_color(taken) == style.AGENT_PALETTE[1]

    def test_no_risk_colours_in_palette(self):
        assert not {"#F5A524", "#F04438"} & {c.upper() for c in style.AGENT_PALETTE}

    def test_stored_colour_wins_and_start_order_fills_the_rest(self):
        agents = [{"pid": 3, "started": 30}, {"pid": 1, "started": 10, "color": "#14B8A6"}, {"pid": 2, "started": 20}]
        got = style.agent_colors(agents)
        assert got[1] == "#14B8A6"
        assert got[2] == "#7C5CFF" and got[3] == style.AGENT_PALETTE[2]

    def test_registry_gives_each_dobby_its_own_colour(self, tmp_path, monkeypatch):
        monkeypatch.setattr(registry, "pid_alive", lambda pid: True)
        registry.register(tmp_path, "a", "claude-code", 101)
        registry.register(tmp_path, "b", "antigravity", 102)
        recs = {r["pid"]: r for r in registry.list_agents(tmp_path)}
        assert recs[101]["color"] == "#7C5CFF" and recs[102]["color"] == style.AGENT_PALETTE[1]
        started = recs[101]["started"]
        registry.register(tmp_path, "a", "Claude Code", 101)  # rename keeps colour and start time
        rec = {r["pid"]: r for r in registry.list_agents(tmp_path)}[101]
        assert rec["color"] == "#7C5CFF" and rec["started"] == started
        over = {a["pid"]: a for a in commands.overview(tmp_path)}
        assert over[102]["color"] == style.AGENT_PALETTE[1] and over[102]["started"] > 0

    def test_tag_names_the_dobby_only_when_several_run(self):
        assert style.tag_label("Dobby", "claude-code", 1) == "Dobby"
        assert style.tag_label("Dobby", "claude-code", 2) == "Dobby · claude-code"
        assert style.tag_label("Dobby", "Dobby-123", 2) == "Dobby"
        assert style.tag_label("Builder", "claude-code", 2) == "Builder"  # a configured label wins
        assert style.tag_label("Dobby", "a very long client name indeed", 2).endswith("…")

    def test_dots_only_with_two_or_more(self):
        one = [{"pid": 1, "started": 1}]
        assert style.agent_dots(one, {1: "#7C5CFF"}, 1) == []
        two = [{"pid": 2, "started": 2, "holding": True}, {"pid": 1, "started": 1}]
        dots = style.agent_dots(two, {1: "#7C5CFF", 2: "#14B8A6"}, 1)
        assert dots == [(1, "#7C5CFF", False, True), (2, "#14B8A6", True, False)]

    def test_narration_shows_dots_only_for_busy_dobbys(self):
        colors = {1: "#7C5CFF", 2: "#14B8A6", 3: "#EC4899"}
        # Three AI apps loaded Dobby, one holds control: no dots, no mystery.
        idle = [{"pid": 1, "started": 1, "holding": True}, {"pid": 2, "started": 2}, {"pid": 3, "started": 3}]
        assert style.agent_dots(idle, colors, 1, busy_only=True) == []
        assert len(style.agent_dots(idle, colors, 1)) == 3  # quiet look: every running Dobby
        busy = [{"pid": 1, "started": 1, "holding": True}, {"pid": 2, "started": 2, "waiting": True},
                {"pid": 3, "started": 3}]
        dots = style.agent_dots(busy, colors, 1, busy_only=True)
        assert [d[0] for d in dots] == [1, 2]
        assert style.dots_caption(dots) == "2 Dobbys" and style.dots_caption([]) == ""
        assert style.agent_busy({"paused": True}) and not style.agent_busy({"pid": 4})


# ------------------------------------------------------------ top bar
class TestBar:
    def test_sleeping_bar_is_compact_without_controls(self):
        spec = style.bar_spec(view())
        assert spec.compact and spec.text == "Dobby is sleeping" and spec.buttons == ()
        assert style.bar_spec(view(), narrate=False).text == "Dobby"
        assert spec.dot_alpha < 1.0 and spec.tip == "Click to wake Dobby"

    def test_hover_grows_it_with_hide_and_menu_only(self):
        spec = style.bar_spec(view(), hovered=True)
        assert not spec.compact and spec.text == "Dobby is sleeping · click to wake"
        assert keys(spec) == ["hide", "menu"]

    def test_ai_waiting_says_click_to_wake_even_compact(self):
        v = view(ai_waiting=True, owner=False)
        spec = style.bar_spec(v)
        assert spec.compact and spec.text == "Dobby is sleeping · click to wake" and spec.dot_alpha == 1.0
        assert style.bar_spec(v, narrate=False).text == "Dobby · click to wake"
        assert style.bar_spec(v, hovered=True).text.count("click to wake") == 1

    def test_awake_has_no_pause_or_stop(self):
        spec = style.bar_spec(view(awake=True, handoff=True))
        assert spec.mode == "awake" and keys(spec) == ["hide", "menu"]

    def test_working_shows_live_step_count_and_timer(self):
        spec = style.bar_spec(view(working=True), step="Click ‘Send’", steps=7, elapsed=42)
        assert spec.text == "Dobby is working · Click ‘Send’ · step 7 · 0:42"
        quiet = style.bar_spec(view(working=True), step="Click ‘Send’", steps=7, elapsed=42, narrate=False)
        assert quiet.text == "Click ‘Send’ · step 7 · 0:42"
        assert keys(spec) == ["primary", "secondary", "hide", "menu"]
        icons = {b.key: b.icon for b in spec.buttons}
        assert icons["primary"] == "pause.fill" and icons["secondary"] == "stop.fill"
        stop = spec.buttons[1]
        assert stop.role == "danger" and "⌃⌥⎋" in stop.tip

    def test_working_turbo_and_thinking(self):
        spec = style.bar_spec(view(working=True, turbo=True), step="", steps=0, elapsed=5, turbo=True, thinking=True)
        assert spec.text == "⚡ Turbo · Dobby is thinking… · 0:05" and spec.shimmer
        quiet = style.bar_spec(view(working=True, turbo=True), step="", steps=0, elapsed=5, turbo=True,
                               thinking=True, narrate=False)
        assert quiet.text == "⚡ Turbo · Working · 0:05"

    def test_narration_uses_the_configured_label(self):
        spec = style.bar_spec(view(working=True), label="Builder", step="Press ⌘S", steps=2, elapsed=3)
        assert spec.text == "Builder is working · Press ⌘S · step 2 · 0:03"

    def test_paused_uses_play_icon(self):
        spec = style.bar_spec(view(paused=True))
        assert spec.buttons[0].icon == "play.fill" and spec.text == "Dobby is paused"

    def test_stopped_keeps_words(self):
        spec = style.bar_spec(("stopped", "Dobby stopped", "OK", "Hide", "danger", 1.0))
        assert [b.title for b in spec.buttons[:2]] == ["OK", "Hide"] and spec.buttons[0].icon is None

    def test_confirm_is_words_and_cannot_hide(self):
        spec = style.confirm_spec("Allow Dobby to quit?")
        assert [b.title for b in spec.buttons] == ["Allow", "Deny"] and "hide" not in keys(spec)

    def test_classic_is_the_old_bar(self):
        spec = style.bar_spec(view(), style="classic")
        assert spec.text == "Dobby is sleeping" and not spec.compact
        assert [b.title for b in spec.buttons[:3]] == ["Pause", "Stop", "–"]
        assert all(b.icon is None for b in spec.buttons[:3])
        assert style.bar_spec(view(working=True), style="classic").text.startswith("Dobby is working")

    def test_bar_hidden_stays_hidden(self):
        assert style.bar_spec(None) is None

    @pytest.mark.parametrize("secs,text", [(0, "0:00"), (7, "0:07"), (102, "1:42"), (3723, "1:02:03")])
    def test_timer_format(self, secs, text):
        assert style.fmt_elapsed(secs) == text

    def test_long_step_is_cut(self):
        text = style.working_text("Type " + "x" * 80, 2, 1)
        assert "…" in text and len(text) < 60


# --------------------------------------------------------------- glow
class TestGlow:
    def test_states(self):
        g = lambda **kw: style.glow_state(**{**dict(paused=False, takeover=False, working=False,
                                                     thinking=False, risk="ok"), **kw})
        assert g() == "idle" and g(working=True) == "acting" and g(thinking=True) == "thinking"
        assert g(paused=True, working=True) == "halted" and g(takeover=True) == "halted"
        assert g(risk="danger", paused=True) == "risk"

    def test_colours_and_opacity(self):
        assert style.glow_color_key("halted", "ok") == "caution"
        assert style.glow_color_key("risk", "danger") == "danger"
        assert style.glow_color_key("acting", "caution") == "caution"
        assert style.glow_color_key("thinking", "ok") == "ok"
        assert style.glow_opacity("idle", False) == 0.0
        assert style.glow_opacity("acting", True) == 0.3 and style.glow_opacity("acting", False) == 1.0

    def test_corner_radius(self):
        screens = [(0, 0, 1512, 982)]
        assert style.corner_radius((100, 100, 800, 600), screens, 26) == 16.0
        assert style.corner_radius((100, 100, 800, 600), screens, 15) == 10.0
        assert style.corner_radius((0, 0, 1512, 982), screens, 26) == 0.0  # full screen
        assert style.corner_radius((0, 33, 1512, 949), screens, 26) == 0.0  # zoomed under the menu bar
        assert style.corner_radius((100, 100, 800, 600), screens, 26, 11) == 11.0  # configured


# -------------------------------------------------------------- trail
class TestTrail:
    def test_modern_keeps_three_unnumbered_dots_that_fade(self):
        pts = [(i, (i * 10.0, 0.0), 100.0 + i) for i in range(1, 6)]
        marks = style.trail_view(pts, now=106.0, fade_secs=10.0)
        assert [m[1][0] for m in marks] == [30.0, 40.0, 50.0]
        assert all(m[0] is None and m[3] == 8.0 for m in marks)
        assert marks[-1][2] > marks[0][2]  # newest brightest
        assert marks[-1][4] == pytest.approx(9.0)

    def test_modern_drops_old_clicks(self):
        assert style.trail_view([(1, (0.0, 0.0), 0.0)], now=10.5, fade_secs=10.0) == []

    def test_classic_numbered_badges_never_fade(self):
        pts = [(i, (0.0, 0.0), 0.0) for i in range(1, 8)]
        marks = style.trail_view(pts, now=1000.0, style="classic", keep=5)
        assert [m[0] for m in marks] == [3, 4, 5, 6, 7] and marks[-1][2] == pytest.approx(0.85)


    def test_narrated_trail_is_numbered_and_still_fades(self):
        pts = [(i, (i * 10.0, 0.0), 100.0 + i) for i in range(1, 6)]
        marks = style.trail_view(pts, now=106.0, fade_secs=10.0, numbered=True)
        assert [m[0] for m in marks] == [3, 4, 5] and all(m[3] == 16.0 for m in marks)
        assert marks[-1][2] > marks[0][2] and marks[-1][4] == pytest.approx(9.0)
        assert style.trail_view([(1, (0.0, 0.0), 0.0)], now=10.5, fade_secs=10.0, numbered=True) == []


# -------------------------------------------------------- tag narration
class TestNarration:
    def test_action_stays_until_next_or_idle(self):
        assert style.intent_secs(None, 1600, narrate=True) is None
        assert style.intent_secs(None, 1600, narrate=False) == pytest.approx(1.6)
        assert style.intent_secs(60000, 1600, narrate=True) == pytest.approx(60.0)  # a tool's step() wins
        assert style.intent_secs(0, 1600, narrate=True) == 0.0

    def test_tag_says_thinking_between_actions(self):
        assert style.tag_detail("Click ‘Send’", "thinking", True) == "Thinking…"
        assert style.tag_detail("Click ‘Send’", "idle", True) == "Click ‘Send’"
        assert style.tag_detail("Click ‘Send’", "thinking", False) == "Click ‘Send’"
        assert style.tag_detail("", "idle", True) == ""


# ----------------------------------------------------------- name tag
class TestTagFlip:
    SCREEN = (0.0, 0.0, 1440.0, 900.0)

    def flip(self, x, y, w=120.0):
        return style.tag_flip((x, y), w, CHIP_H, ARROW_W, ARROW_H, self.SCREEN)

    def test_normal_position(self):
        assert self.flip(400, 300) == (False, False)
        x, y = style.tag_origin((400, 300), 120, CHIP_H, ARROW_W, ARROW_H, (False, False))
        assert (x, y, 120, CHIP_H) == geometry.chip_rect((400, 300), 120, ARROW_W, ARROW_H, CHIP_H)

    def test_right_edge_goes_left(self):
        assert self.flip(1400, 300) == (True, False)
        x, _ = style.tag_origin((1400, 300), 120, CHIP_H, ARROW_W, ARROW_H, (True, False))
        assert x + 120 <= 1400

    def test_bottom_edge_goes_up(self):
        assert self.flip(400, 880) == (False, True)
        _, y = style.tag_origin((400, 880), 120, CHIP_H, ARROW_W, ARROW_H, (False, True))
        assert y + CHIP_H <= 880

    def test_corner_flips_both(self):
        assert self.flip(1430, 890) == (True, True)

    def test_no_screen_no_flip(self):
        assert style.tag_flip((5000, 5000), 100, CHIP_H, ARROW_W, ARROW_H, None) == (False, False)
