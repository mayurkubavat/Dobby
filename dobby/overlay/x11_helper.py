"""Overlay helper for Linux X11: ``python -m dobby.overlay`` when DISPLAY is set.

Speaks the same JSON-lines protocol as ``mac_helper`` (see ``protocol.py``),
drawn with GTK 3 + cairo through PyGObject (Debian/Ubuntu: python3-gi,
python3-gi-cairo, gir1.2-gtk-3.0).

What it draws:

* the agent pointer (violet arrow) gliding to each target, with a ripple on
  clicks when a compositor is running;
* a capsule tag next to the pointer with the label, state and current intent;
* a glow frame around the tracked window and a thinner box around the target
  element (from the AT-SPI hit test when a move asks for a probe);
* an Allow / Deny window for ``confirm``;
* the control bar at the top of the screen (same states as macOS, from
  ``menu.bar_view``): "Dobby is sleeping" with Pause / Stop, "working",
  "paused", "stopped"; click the bar itself to wake Dobby or put it back to
  sleep. Only the helper holding ``~/.dobby/overlay/bar.lock`` shows the idle
  bar.

Every panel except the confirm window is an override-redirect popup with an
empty input shape, so clicks go straight through. Without a compositor the
pointer and tag are cut to their drawn shape with XShape, and the frames use
four thin edge windows, so nothing opaque covers the screen.

Controls: Ctrl+Alt+Esc sends ``stop``; Ctrl+Alt+P toggles ``pause`` /
``resume`` (global key grabs on the root window).

Not ported from macOS yet: the numbered step trail, the scan sweep, the menu
bar icon / tag menu and user take-over detection. Their ops are accepted and acked so the
server behaves the same.
"""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
import cairo  # noqa: E402
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import geometry, protocol, style  # noqa: E402
from .barlock import BAR_LOCK, BarOwner, mark_waiting, others_waiting, waiting_dir  # noqa: E402
from .menu import bar_click, bar_view, label_click  # noqa: E402

DEFAULT_CONFIG: Dict[str, Any] = {
    "color": "#7C5CFF",
    "caution_color": "#F5A524",
    "danger_color": "#F04438",
    "label": "Dobby",
    "speed": 1.0,
    "glow_width": 3,
    "box_width": 2,
    "track_hz": 5.0,
    "intent_ms": 1600,
    "turbo": False,
    "active_secs": 8.0,   # "working" lasts this long after an action
    "awake_secs": 120.0,  # woken from the bar: stays awake this long without a task
    "wake_note_secs": 4.0,  # woken while no AI waits: says so this long, then sleeps
    "pill": "always",     # control bar: "always", "auto" (only while working) or "off"
    "narrate": True,      # modern look: say what Dobby does on the bar and the tag
}

# Classic arrow, y down, tip at (0, 0) (same outline as the macOS helper).
_ARROW = [(0, 0), (0, 18), (4.5, 14), (8, 21.5), (10.5, 20.5), (7, 13), (13, 13)]
_STATE_ICON = {"idle": "", "thinking": "…", "typing": "⌨", "waiting_user": "?"}


def _rgb(value: str) -> Tuple[float, float, float]:
    v = value.lstrip("#")
    return tuple(int(v[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _short(text: str, n: int = 48) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


class _Panel(Gtk.Window):
    """Click-through override-redirect window drawn with cairo."""

    def __init__(self, composited: bool, w: int, h: int, painter=None) -> None:
        super().__init__(type=Gtk.WindowType.POPUP)
        self.set_title(protocol.OVERLAY_WINDOW_TITLE)
        self.set_app_paintable(True)
        self.set_accept_focus(False)
        self.set_keep_above(True)
        self.composited = composited
        if composited:
            visual = self.get_screen().get_rgba_visual()
            if visual is not None:
                self.set_visual(visual)
        self.painter = painter
        self.set_default_size(w, h)
        self.resize(w, h)
        self.connect("draw", self._on_draw)
        self.connect("realize", self._on_realize)

    def _on_realize(self, *_a) -> None:
        # Empty input region: every click falls through to the app below.
        self.input_shape_combine_region(cairo.Region())

    def _on_draw(self, _w, cr) -> bool:
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        if self.painter is not None:
            self.painter(cr, self.get_allocated_width(), self.get_allocated_height())
        return False

    def reshape(self) -> None:
        """Without a compositor, cut the window to what the painter draws."""
        if self.composited or self.painter is None:
            return
        w, h = self.get_size()
        surf = cairo.ImageSurface(cairo.FORMAT_A1, max(w, 1), max(h, 1))
        cr = cairo.Context(surf)
        self.painter(cr, w, h)
        region = Gdk.cairo_region_create_from_surface(surf)
        self.shape_combine_region(region)


class _Frame:
    """A rectangle outline made of four solid edge windows (needs no compositor)."""

    def __init__(self, width: int) -> None:
        self.width = width
        self.color = (0.5, 0.4, 1.0)
        self.edges: List[_Panel] = []
        for _ in range(4):
            p = _Panel(False, 1, 1, painter=self._paint)
            self.edges.append(p)
        self.rect: Optional[Tuple[int, int, int, int]] = None
        self.visible = False

    def _paint(self, cr, w, h) -> None:
        cr.set_source_rgb(*self.color)
        cr.rectangle(0, 0, w, h)
        cr.fill()

    def set_color(self, rgb) -> None:
        self.color = rgb
        for e in self.edges:
            e.queue_draw()

    def place(self, rect: Optional[Tuple[int, int, int, int]]) -> None:
        self.rect = rect
        if rect is None:
            self.hide()
            return
        x, y, w, h = (int(v) for v in rect)
        t = self.width
        boxes = [
            (x - t, y - t, w + 2 * t, t),  # top
            (x - t, y + h, w + 2 * t, t),  # bottom
            (x - t, y, t, h),              # left
            (x + w, y, t, h),              # right
        ]
        for edge, (bx, by, bw, bh) in zip(self.edges, boxes):
            edge.resize(max(bw, 1), max(bh, 1))
            edge.move(bx, by)
        self.show()

    def show(self) -> None:
        if self.rect is None:
            return
        self.visible = True
        for e in self.edges:
            e.show_all()

    def hide(self) -> None:
        self.visible = False
        for e in self.edges:
            e.hide()


_BAR_CSS = b"""
#dobby-bar { background-color: rgba(28, 28, 32, 0.92); border-radius: 14px; }
#dobby-bar label { color: #ffffff; font-weight: bold; }
#dobby-bar button { padding: 0 10px; min-height: 22px; }
#dobby-bar button label { color: #1c1c20; }
"""


class _Bar:
    """The control bar: clickable (not click-through), top centre of the screen."""

    def __init__(self, on_label, on_button) -> None:
        self.mode = "hidden"
        self.win = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.win.set_title(protocol.OVERLAY_WINDOW_TITLE)
        self.win.set_name("dobby-bar")
        self.win.set_accept_focus(False)
        self.win.set_keep_above(True)
        provider = Gtk.CssProvider()
        provider.load_from_data(_BAR_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        row = Gtk.Box(spacing=8, margin_start=12, margin_end=8, margin_top=4, margin_bottom=4)
        self.dot = Gtk.Label(label="●")
        self.text = Gtk.Label(label="")
        click = Gtk.EventBox()
        inner = Gtk.Box(spacing=6)
        inner.pack_start(self.dot, False, False, 0)
        inner.pack_start(self.text, False, False, 0)
        click.add(inner)
        click.connect("button-press-event", lambda *_: (on_label(), True)[1])
        self.primary = Gtk.Button(label="Pause")
        self.secondary = Gtk.Button(label="Stop")
        self.primary.set_can_focus(False)
        self.secondary.set_can_focus(False)
        self.primary.connect("clicked", lambda *_: on_button("primary"))
        self.secondary.connect("clicked", lambda *_: on_button("secondary"))
        row.pack_start(click, True, True, 0)
        row.pack_start(self.primary, False, False, 0)
        row.pack_start(self.secondary, False, False, 0)
        self.win.add(row)
        self.visible = False

    def _place(self) -> None:
        screen = Gdk.Screen.get_default()
        w, _h = self.win.get_preferred_size()[1].width, 0
        self.win.move(max(0, (screen.get_width() - w) // 2), 8)

    def show(self, mode: str, text: str, primary: str, secondary: str, rgb, dot_alpha: float = 1.0) -> None:
        self.mode = mode
        self.text.set_text(text)
        if primary:
            self.primary.set_label(primary)
        if secondary:
            self.secondary.set_label(secondary)
        r, g, b = (int(v * 255) for v in rgb)
        self.dot.set_markup(f'<span foreground="#{r:02x}{g:02x}{b:02x}" alpha="{int(dot_alpha * 100)}%">●</span>')
        self.visible = True
        self.win.resize(1, 1)
        self._place()  # centre for the new text before mapping, so it does not jump
        self.win.show_all()

    def show_spec(self, spec, rgb) -> None:
        """Draw a style.BarSpec: its text, and only the buttons it asks for.

        GTK has no SF Symbols, so icon buttons use text glyphs here.
        """
        glyphs = {"pause.fill": "⏸", "play.fill": "▶", "stop.fill": "■"}
        wanted = {b.key: b for b in spec.buttons}
        for key, btn in (("primary", self.primary), ("secondary", self.secondary)):
            b = wanted.get(key)
            if b is not None:
                btn.set_label(glyphs.get(b.icon or "", b.title))
                btn.set_tooltip_text(b.tip or None)
        self.show(spec.mode, spec.text, "", "", rgb, dot_alpha=spec.dot_alpha)
        self.primary.set_visible("primary" in wanted)
        self.secondary.set_visible("secondary" in wanted)

    def hide(self, keep_mode: bool = False) -> None:
        if not keep_mode:
            self.mode = "hidden"
        self.visible = False
        self.win.hide()


class X11Overlay:
    def __init__(self, cfg: Dict[str, Any], out=None) -> None:
        self.cfg = dict(DEFAULT_CONFIG)
        self.cfg.update(cfg or {})
        self.out = out or sys.stdout
        self._wlock = threading.Lock()
        screen = Gdk.Screen.get_default()
        self.composited = bool(screen is not None and screen.is_composited())

        self.risk = "ok"
        self.state = "idle"
        self.style = style.normalize_style(self.cfg.get("style"))
        self.narrate = self.style == "modern" and bool(self.cfg.get("narrate", True))
        self.server_pid = int(os.environ.get("DOBBY_SERVER_PID", "0") or 0)
        self.agents_next = 0.0
        # The task in progress, for the bar's live step and timer.
        self.task_started = 0.0
        self.task_steps = 0
        self.current_step = ""
        self.bar_second = -1
        self.intent = ""
        self.intent_until = 0.0
        self.cursor: Optional[Tuple[float, float]] = None
        self._anim_id: Optional[int] = None
        self.user_visible = True
        self.capture_hidden = False
        self.paused = False
        self.pending_confirm: Optional[Tuple[Any, Gtk.Window, int]] = None
        self.track_wid: Optional[int] = None
        self.track_pid: Optional[int] = None

        now = time.monotonic()
        self.last_action = now - 10 * float(self.cfg["active_secs"])  # start asleep
        self.awake_until = 0.0
        self.stopped_until = 0.0
        self.wake_pointer = False
        self.ai_waiting = 0  # AIs waiting in wait_for_wake
        self.handoff = False  # the current wake went to a waiting AI
        self.others_waiting = False  # another helper's AI waits (bar owner steps aside)
        self.others_checked = 0.0
        self.bar_mode = str(self.cfg.get("pill", "always"))
        self.bar_owner = BarOwner(self.cfg.get("bar_lock", BAR_LOCK))
        self.bar_owner_retry = 0.0
        self.wait_dir = waiting_dir(self.cfg.get("bar_lock", BAR_LOCK))
        self.bar = None if self.bar_mode == "off" else _Bar(self.on_bar_label, self.on_bar)

        self.pointer = _Panel(self.composited, 26, 28, painter=self._paint_pointer)
        self.tag = _Panel(self.composited, 120, 26, painter=self._paint_tag)
        self.ripple = _Panel(True, 48, 48, painter=self._paint_ripple) if self.composited else None
        self._ripple_t = 1.0
        self.glow = _Frame(int(self.cfg["glow_width"]))
        self.box = _Frame(int(self.cfg["box_width"]))
        self._apply_colors()

        self._hal = None
        self._probe = None
        GLib.timeout_add(int(1000 / max(float(self.cfg["track_hz"]), 0.5)), self._track_tick)
        GLib.timeout_add(200, self._tag_tick)
        GLib.timeout_add(250, self._bar_tick)
        self._claim_bar()
        self._refresh_bar()

    # ------------------------------------------------------------- output
    def _write(self, text: str) -> None:
        with self._wlock:
            try:
                self.out.write(text)
                self.out.flush()
            except (BrokenPipeError, ValueError):
                GLib.idle_add(self.quit)

    def emit(self, obj: Dict[str, Any]) -> None:
        self._write(json.dumps(obj, separators=(",", ":")) + "\n")

    def send_event(self, name: str) -> None:
        self._write(protocol.event(name))

    def ack(self, msg_id: Any, ok: bool = True, error: Optional[str] = None) -> bool:
        if msg_id is not None:
            self._write(protocol.ack(msg_id, ok, error))
        return False  # usable as a one-shot GLib callback

    # ------------------------------------------------------------- colors
    def _color(self) -> Tuple[float, float, float]:
        key = {"caution": "caution_color", "danger": "danger_color"}.get(self.risk, "color")
        return _rgb(self.cfg[key])

    def _apply_colors(self) -> None:
        c = self._color()
        self.glow.set_color(c)
        self.box.set_color(tuple(min(1.0, v * 0.6 + 0.4) for v in c))
        self.pointer.queue_draw()
        self.tag.queue_draw()

    # ------------------------------------------------------------ painters
    def _paint_pointer(self, cr, w, h) -> None:
        s = 1.15
        cr.translate(2, 2)
        cr.move_to(*_ARROW[0])
        for x, y in _ARROW[1:]:
            cr.line_to(x * s, y * s)
        cr.close_path()
        cr.set_source_rgb(*self._color())
        cr.fill_preserve()
        cr.set_source_rgb(1, 1, 1)
        cr.set_line_width(1.6)
        cr.stroke()

    def _tag_text(self) -> str:
        parts = [self.cfg["label"]]
        icon = _STATE_ICON.get(self.state, "")
        if self.paused:
            icon = "⏸"
        if icon:
            parts.append(icon)
        intent = self.intent if (self.intent and time.monotonic() < self.intent_until) else ""
        detail = style.tag_detail(intent, self.state, self.narrate)
        if detail:
            parts.append(("› " + detail) if self.narrate else detail)
        return "  ".join(parts)

    def _paint_tag(self, cr, w, h) -> None:
        r = h / 2.0
        cr.new_sub_path()
        cr.arc(w - r, r, r, -math.pi / 2, math.pi / 2)
        cr.arc(r, r, r, math.pi / 2, 3 * math.pi / 2)
        cr.close_path()
        cr.set_source_rgb(*self._color())
        cr.fill()
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(12)
        cr.set_source_rgb(1, 1, 1)
        cr.move_to(10, h / 2.0 + 4.5)
        cr.show_text(self._tag_text())

    def _paint_ripple(self, cr, w, h) -> None:
        t = self._ripple_t
        if t >= 1.0:
            return
        r, g, b = self._color()
        cr.set_source_rgba(r, g, b, 0.8 * (1 - t))
        cr.set_line_width(3)
        cr.arc(w / 2, h / 2, 6 + t * (w / 2 - 8), 0, 2 * math.pi)
        cr.stroke()

    # ------------------------------------------------------------ layout
    def _overlay_shown(self) -> bool:
        return self.user_visible and not self.capture_hidden

    def _layout_tag(self) -> None:
        if self.cursor is None or not self._overlay_shown():
            self.tag.hide()
            return
        text = self._tag_text()
        probe = cairo.Context(cairo.ImageSurface(cairo.FORMAT_A1, 1, 1))
        probe.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        probe.set_font_size(12)
        width = int(probe.text_extents(text).x_advance) + 22
        self.tag.resize(width, 24)
        self.tag.move(int(self.cursor[0]) + 18, int(self.cursor[1]) + 20)
        self.tag.queue_draw()
        self.tag.show_all()
        GLib.idle_add(self._reshape_later, self.tag)

    @staticmethod
    def _reshape_later(panel: _Panel) -> bool:
        panel.reshape()
        return False

    def _place_pointer(self, pt: Tuple[float, float]) -> None:
        self.cursor = pt
        if not self._overlay_shown():
            return
        self.pointer.move(int(pt[0]), int(pt[1]))
        if not self.pointer.get_visible():
            self.pointer.show_all()
            self.pointer.reshape()
        self._layout_tag()

    def _apply_visibility(self) -> None:
        shown = self._overlay_shown()
        panels = [self.pointer, self.tag] + ([self.ripple] if self.ripple else [])
        if not shown:
            for p in panels:
                p.hide()
            self.glow.hide()
            self.box.hide()
            if self.bar is not None and self.capture_hidden:
                self.bar.hide(keep_mode=True)  # never in screenshots
            return
        if self.bar is not None and not self.bar.visible:
            self._refresh_bar()
        if self.cursor is not None:
            self._place_pointer(self.cursor)
        if self.glow.rect is not None:
            self.glow.show()
        if self.box.rect is not None:
            self.box.show()

    # ------------------------------------------------------------- motion
    def _duration(self, ms: Optional[float]) -> float:
        speed = float(self.cfg["speed"])
        if speed <= 0 or not ms:
            return 0.0
        return float(ms) / 1000.0

    def _glide(self, target: Tuple[float, float], dur: float, msg_id: Any) -> None:
        if self._anim_id is not None:
            GLib.source_remove(self._anim_id)
            self._anim_id = None
        start = self.cursor or target
        if dur <= 0:
            self._place_pointer(target)
            self.ack(msg_id)
            return
        t0 = time.monotonic()

        def step() -> bool:
            f = min((time.monotonic() - t0) / dur, 1.0)
            e = 1 - (1 - f) ** 3  # ease-out cubic
            self._place_pointer((start[0] + (target[0] - start[0]) * e, start[1] + (target[1] - start[1]) * e))
            if f >= 1.0:
                self._anim_id = None
                self.ack(msg_id)
                return False
            return True

        self._anim_id = GLib.timeout_add(16, step)

    def _ripple_at(self) -> None:
        if self.ripple is None or self.cursor is None or not self._overlay_shown():
            return
        self.ripple.move(int(self.cursor[0]) - 24, int(self.cursor[1]) - 24)
        self.ripple.show_all()
        t0 = time.monotonic()

        def step() -> bool:
            self._ripple_t = min((time.monotonic() - t0) / 0.35, 1.0)
            self.ripple.queue_draw()
            if self._ripple_t >= 1.0:
                self.ripple.hide()
                return False
            return True

        GLib.timeout_add(16, step)

    # ------------------------------------------------------------ tracking
    def _get_hal(self):
        if self._hal is None:
            try:
                from dobby.hal.linux import LinuxHAL

                self._hal = LinuxHAL()
            except Exception as exc:  # noqa: BLE001
                sys.stderr.write(f"dobby.overlay: no X11 HAL: {exc}\n")
                self._hal = False
        return self._hal or None

    def _tracked_rect(self) -> Optional[Tuple[int, int, int, int]]:
        hal = self._get_hal()
        if hal is None:
            return None
        try:
            windows = hal.list_windows()
        except Exception:
            return None
        for w in windows:
            wid = protocol.window_id_int(w.window_id)
            if self.track_wid is not None and wid == self.track_wid:
                b = w.bbox
                return (b.x, b.y, b.width, b.height)
        if self.track_pid is not None:
            for w in windows:  # front to back: the pid's topmost window
                if hal.window_pid(w.window_id) == self.track_pid:
                    b = w.bbox
                    return (b.x, b.y, b.width, b.height)
        return None

    def _track_tick(self) -> bool:
        if self.track_wid is None and self.track_pid is None:
            return True
        rect = self._tracked_rect()
        if rect != self.glow.rect:
            self.glow.place(rect)
            if not self._overlay_shown():
                self.glow.hide()
        return True

    def _tag_tick(self) -> bool:
        if self.intent and (time.monotonic() >= self.intent_until or (self.narrate and not self._working())):
            self.intent = ""  # narration: the action stays until Dobby goes idle
            self._layout_tag()
        return True

    def _probe_at(self, x: float, y: float) -> Tuple[str, Optional[Tuple[int, int, int, int]]]:
        if self._probe is None:
            try:
                from dobby.perception.atspi import ATSPIInspector

                insp = ATSPIInspector(hal=self._get_hal())
                self._probe = insp if insp.is_available else False
            except Exception:  # noqa: BLE001
                self._probe = False
        if not self._probe:
            return "", None
        hit = self._probe.hit_test(int(x), int(y))
        if not hit or not hit.get("bbox"):
            return "", None
        b = hit["bbox"]
        return hit.get("name") or "", (b["x"], b["y"], b["width"], b["height"])

    # ---------------------------------------------------------- control bar
    def _working(self) -> bool:
        return time.monotonic() - self.last_action <= float(self.cfg["active_secs"])

    def _awake(self) -> bool:
        return bool(self.awake_until) and time.monotonic() < self.awake_until

    def _claim_bar(self) -> None:
        if self.bar is not None and not self.bar_owner.owned:
            self.bar_owner.try_acquire()

    def _refresh_bar(self) -> None:
        if self.bar is None:
            return
        if self.capture_hidden:
            return
        if self.bar.mode == "stopped" and time.monotonic() < self.stopped_until:
            return
        view = bar_view(
            paused=self.paused, takeover=False, takeover_resume="auto",
            working=self._working(), turbo=bool(self.cfg.get("turbo")),
            current_mode=self.bar.mode, bar_mode=self.bar_mode,
            owner=self.bar_owner.owned, awake=self._awake(), ai_waiting=self.ai_waiting > 0,
            handoff=self.handoff, other_waiting=self.others_waiting,
        )
        if view is None:
            self.bar.hide()
            return
        mode, text, primary, secondary, color, dot_alpha = view
        rgb = {"ok": _rgb(self.cfg["color"]), "caution": _rgb(self.cfg["caution_color"]),
               "danger": _rgb(self.cfg["danger_color"])}.get(color) or self._color()
        if self.style == "classic":
            self.bar.show(mode, text, primary, secondary, rgb, dot_alpha=dot_alpha)
            return
        now = time.monotonic()
        # No hover tracking here: the sleeping bar always shows its full text.
        spec = style.bar_spec(view, hovered=True, label=str(self.cfg["label"]), step=self.current_step,
                              steps=self.task_steps,
                              elapsed=(now - self.task_started) if self.task_started else 0.0,
                              turbo=bool(self.cfg.get("turbo")), thinking=self.state == "thinking",
                              narrate=self.narrate)
        self.bar.show_spec(spec, rgb)

    def _refresh_agents(self) -> None:
        """Take this Dobby's colour from the agent registry (modern look)."""
        if (self.style != "modern" or not self.server_pid or self.cfg.get("color_fixed")
                or not self.cfg.get("agent_colors", True)):
            return
        try:
            from dobby.control import commands, control_root

            own = style.agent_colors(commands.overview(control_root())).get(self.server_pid)
        except Exception:  # noqa: BLE001 - colour is cosmetic
            return
        if own and own.upper() != str(self.cfg["color"]).upper():
            self.cfg["color"] = own
            self._apply_colors()
            self._refresh_bar()

    def _bar_tick(self) -> bool:
        now = time.monotonic()
        if now >= self.agents_next:
            self.agents_next = now + 2.0
            self._refresh_agents()
        if self.style == "modern" and self.bar is not None and self.bar.mode == "working":
            second = int(now - self.task_started)
            if second != self.bar_second:  # the bar's timer
                self.bar_second = second
                self._refresh_bar()
        if self.bar is not None and not self.bar_owner.owned and now >= self.bar_owner_retry:
            self.bar_owner_retry = now + 2.0
            if self.bar_owner.try_acquire():
                self._refresh_bar()
        if self.awake_until and now >= self.awake_until:
            self.sleep()
        if self.bar_owner.owned and now >= self.others_checked:
            self.others_checked = now + 1.0
            others = not self.ai_waiting and others_waiting(self.wait_dir, os.getpid())
            if others != self.others_waiting:
                self.others_waiting = others
                self._refresh_bar()
        if self.bar is not None:
            if self.bar.mode == "working" and not self._working():
                self._refresh_bar()
            elif self.bar.mode == "stopped" and now >= self.stopped_until:
                self.bar.mode = "ready"
                self._refresh_bar()
        return True

    def _mark_active(self) -> None:
        now = time.monotonic()
        if now - self.last_action > float(self.cfg["active_secs"]):
            self.task_started = now  # a new task: step count and timer start over
            self.task_steps = 0
            self.current_step = ""
        self.last_action = now
        self.wake_pointer = False  # Dobby drives the pointer now
        if self.awake_until:
            self._end_awake()
        if self.bar is not None and self.bar.mode in ("hidden", "ready", "awake"):
            self._refresh_bar()

    def _end_awake(self) -> None:
        self.awake_until = 0.0
        self.handoff = False
        self.send_event("sleep")

    def on_bar_label(self) -> None:
        if self.bar is None:
            return
        action = label_click(self.bar.mode)
        if action == "wake":
            self.wake()
        elif action == "sleep":
            self.sleep()

    def wake(self) -> None:
        """See ``mac_helper.OverlayController.wake``: hands back to a waiting AI."""
        if self.paused:
            self.set_paused(False)
        self.handoff = self.ai_waiting > 0
        secs = float(self.cfg["awake_secs"]) if self.handoff else float(self.cfg["wake_note_secs"])
        self.awake_until = time.monotonic() + max(0.5, secs)
        self.send_event("wake")
        self._refresh_bar()
        if self.cursor is None:
            screen = Gdk.Screen.get_default()
            self._place_pointer((screen.get_width() / 2.0, 110.0))
            self.wake_pointer = True
        self.state = "idle"
        self._layout_tag()

    def sleep(self) -> None:
        if not self.awake_until:
            return
        self._end_awake()
        if self.wake_pointer:
            self.wake_pointer = False
            self.pointer.hide()
            self.tag.hide()
            self.cursor = None
        self._refresh_bar()

    def _ai_wait(self, start: bool) -> None:
        if start:
            self.ai_waiting += 1
            if self.awake_until:
                self.sleep()
        else:
            self.ai_waiting = max(0, self.ai_waiting - 1)
        mark_waiting(self.wait_dir, os.getpid(), self.ai_waiting > 0)
        self._refresh_bar()

    def on_bar(self, which: str) -> None:
        if self.bar is None:
            return
        action = bar_click(self.bar.mode, which)
        if action in ("hide", "dismiss"):
            self.bar.mode = "ready"
            if action == "hide":
                self.bar.hide()
            else:
                self._refresh_bar()
        elif action == "stop":
            self.request_stop()
        elif action == "pause":
            self.set_paused(True)
        elif action == "resume":
            self.resume()

    def set_paused(self, paused: bool) -> None:
        if paused == self.paused:
            return
        self.paused = paused
        self.send_event("pause" if paused else "resume")
        self._refresh_bar()
        self._layout_tag()

    def resume(self) -> None:
        if self.paused:
            self.set_paused(False)
        self._mark_active()
        self._refresh_bar()

    def request_stop(self) -> None:
        if self.pending_confirm is not None:
            self._answer_confirm(False)
        self.paused = False
        if self.awake_until:
            self.sleep()
        self.send_event("stop")
        self.send_event("user_idle")
        self._set_intent("Stopped", 1500)
        if self.bar is not None:
            self.bar.show("stopped", "Dobby stopped", "OK", "Hide", _rgb(self.cfg["danger_color"]))
            self.stopped_until = time.monotonic() + 2.0

    # ------------------------------------------------------------- confirm
    def _start_confirm(self, msg_id: Any, text: str, secs: float) -> None:
        if self.cfg.get("turbo"):
            self.ack(msg_id, True)
            return
        if self.pending_confirm is not None:
            self._answer_confirm(False)
        win = Gtk.Window(title=protocol.OVERLAY_WINDOW_TITLE)
        win.set_keep_above(True)
        win.set_resizable(False)
        win.set_position(Gtk.WindowPosition.CENTER)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=16)
        label = Gtk.Label(label=f"{self.cfg['label']} wants to: {text}")
        label.set_line_wrap(True)
        label.set_max_width_chars(48)
        box.pack_start(label, True, True, 0)
        row = Gtk.Box(spacing=8)
        deny = Gtk.Button(label="Deny")
        allow = Gtk.Button(label="Allow")
        deny.connect("clicked", lambda *_: self._answer_confirm(False))
        allow.connect("clicked", lambda *_: self._answer_confirm(True))
        row.pack_end(allow, False, False, 0)
        row.pack_end(deny, False, False, 0)
        box.pack_start(row, False, False, 0)
        win.add(box)
        win.connect("delete-event", lambda *_: (self._answer_confirm(False), True)[1])
        win.show_all()
        timer = GLib.timeout_add(int(max(secs, 1.0) * 1000), lambda: (self._answer_confirm(False), False)[1])
        self.pending_confirm = (msg_id, win, timer)

    def _answer_confirm(self, ok: bool) -> None:
        if self.pending_confirm is None:
            return
        msg_id, win, timer = self.pending_confirm
        self.pending_confirm = None
        try:
            GLib.source_remove(timer)
        except Exception:  # noqa: BLE001 - already fired
            pass
        win.destroy()
        self.ack(msg_id, ok)

    # --------------------------------------------------------------- input
    def handle_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        msg_id = None
        try:
            raw = json.loads(line)
            msg_id = raw.get("id") if isinstance(raw, dict) else None
            self.handle(protocol.validate(raw))
        except Exception as exc:  # noqa: BLE001 - never die on one bad message
            sys.stderr.write(f"dobby.overlay: {exc}\n")
            self.ack(msg_id, False, str(exc))

    def handle(self, msg: Dict[str, Any]) -> None:
        op, msg_id = msg["op"], msg.get("id")
        if op == "cursor.move":
            self._mark_active()
            target = (float(msg["x"]), float(msg["y"]))
            verb = msg.get("verb")
            if msg.get("probe"):
                name, frame = self._probe_at(*target)
                if frame is not None and 3 <= frame[2] <= 1600 and 3 <= frame[3] <= 900 \
                        and geometry.contains(frame, target):
                    self.box.place(frame)
                    if not self._overlay_shown():
                        self.box.hide()
                else:
                    self.box.place(None)
                if verb:
                    self._set_intent(f"{verb} ‘{_short(name, 32)}’" if name else verb)
            elif verb and verb != "Move":
                self._set_intent(verb)
            if self.cursor is None:
                start = self._real_mouse()
                self._place_pointer(start)
            ms = msg.get("ms")
            if ms is None:
                ms = geometry.glide_ms(self.cursor, target, speed=float(self.cfg["speed"]))
            if self.state == "thinking":
                self.state = "idle"
            self._glide(target, self._duration(ms), msg_id)
        elif op == "cursor.click":
            self._mark_active()
            self._ripple_at()
            GLib.timeout_add(500, lambda: (self.box.place(None), False)[1])
            self.ack(msg_id)
        elif op == "cursor.state":
            self.state = msg["state"]
            if self.state in ("typing", "thinking"):
                self._mark_active()
            self._layout_tag()
            self.ack(msg_id)
        elif op == "cursor.hide":
            self.pointer.hide()
            self.tag.hide()
            self.cursor = None
            self.ack(msg_id)
        elif op == "intent":
            self._mark_active()
            self._set_intent(msg["text"], msg.get("ms"))
            self.ack(msg_id)
        elif op == "target.box":
            self.box.place((float(msg["x"]), float(msg["y"]), float(msg["w"]), float(msg["h"])))
            if not self._overlay_shown():
                self.box.hide()
            self.ack(msg_id)
        elif op == "target.clear":
            self.box.place(None)
            self.ack(msg_id)
        elif op == "risk":
            self.risk = msg["level"]
            self._apply_colors()
            self._layout_tag()
            self.ack(msg_id)
        elif op in ("scan", "trail.clear"):
            self.ack(msg_id)  # not drawn on Linux yet
        elif op == "confirm":
            self._mark_active()
            self._start_confirm(msg_id, msg["text"], float(msg.get("secs", 30.0)))
        elif op == "confirm.cancel":
            self._answer_confirm(False)
            self.ack(msg_id)
        elif op == "overlay.visible":
            self.user_visible = bool(msg["on"])
            self._apply_visibility()
            self.ack(msg_id)
        elif op == "glow.track":
            self.track_wid = protocol.window_id_int(msg.get("window_id"))
            self.track_pid = msg.get("pid")
            self.glow.rect = None
            self._track_tick()
            self.ack(msg_id)
        elif op == "glow.clear":
            self.track_wid = self.track_pid = None
            self.glow.place(None)
            self.ack(msg_id)
        elif op == "hide":
            self.capture_hidden = True
            self._apply_visibility()
            Gdk.flush()
            # Give the X server a moment to unmap before the capture.
            GLib.timeout_add(50, self.ack, msg_id)
        elif op == "show":
            self.capture_hidden = False
            self._apply_visibility()
            self.ack(msg_id)
        elif op == "sleep.wait":
            self._ai_wait(True)
            self.ack(msg_id)
        elif op == "sleep.done":
            self._ai_wait(False)
            self.ack(msg_id)
        elif op == "ping":
            self.ack(msg_id)
        elif op == "bye":
            self.ack(msg_id)
            self.quit()

    def _set_intent(self, text: str, ms: Optional[float] = None) -> None:
        if text and text != "Stopped":
            self.task_steps += 1
            self.current_step = text
        self.intent = _short(text) if text else ""
        secs = style.intent_secs(ms, float(self.cfg["intent_ms"]), self.narrate)
        if not text:
            self.intent_until = 0.0
        else:  # None: until the next action, or until Dobby is idle (see _tag_tick)
            self.intent_until = time.monotonic() + secs if secs is not None else float("inf")
        self._layout_tag()

    def _real_mouse(self) -> Tuple[float, float]:
        try:
            seat = Gdk.Display.get_default().get_default_seat()
            _screen, x, y = seat.get_pointer().get_position()
            return (float(x), float(y))
        except Exception:  # noqa: BLE001
            return (0.0, 0.0)

    # ------------------------------------------------------------- hotkeys
    def start_hotkeys(self) -> None:
        threading.Thread(target=self._hotkey_loop, name="hotkeys", daemon=True).start()

    def _hotkey_loop(self) -> None:
        try:
            from Xlib import X, XK, display as xdisplay

            d = xdisplay.Display()
            root = d.screen().root
            mods = X.ControlMask | X.Mod1Mask
            keys = {
                d.keysym_to_keycode(XK.string_to_keysym("Escape")): "stop",
                d.keysym_to_keycode(XK.string_to_keysym("p")): "pause",
            }
            # Also grab with NumLock (Mod2) and CapsLock (Lock) on, which X counts as modifiers.
            for code in keys:
                for extra in (0, X.Mod2Mask, X.LockMask, X.Mod2Mask | X.LockMask):
                    root.grab_key(code, mods | extra, True, X.GrabModeAsync, X.GrabModeAsync)
            d.sync()
            while True:
                ev = d.next_event()
                if ev.type == X.KeyPress and (ev.state & mods) == mods and ev.detail in keys:
                    GLib.idle_add(self._on_hotkey, keys[ev.detail])
        except Exception as exc:  # noqa: BLE001 - hotkeys are a convenience
            sys.stderr.write(f"dobby.overlay: hotkeys unavailable: {exc}\n")

    def _on_hotkey(self, which: str) -> bool:
        if which == "stop":
            self.request_stop()
        elif self.paused:
            self.resume()
        else:
            self.set_paused(True)
        return False

    # ---------------------------------------------------------------- life
    def quit(self) -> bool:
        if self.pending_confirm is not None:
            self._answer_confirm(False)
        self.bar_owner.release()
        mark_waiting(self.wait_dir, os.getpid(), False)
        Gtk.main_quit()
        return False


def _stdin_watch(overlay: X11Overlay) -> None:
    buf = {"data": b""}

    def on_input(fd, cond) -> bool:
        if cond & (GLib.IO_HUP | GLib.IO_ERR):
            chunk = b""
        else:
            chunk = os.read(fd, 65536)
        if not chunk:
            overlay.quit()  # server went away
            return False
        buf["data"] += chunk
        while b"\n" in buf["data"]:
            line, buf["data"] = buf["data"].split(b"\n", 1)
            overlay.handle_line(line.decode("utf-8", "replace"))
        return True

    GLib.io_add_watch(sys.stdin.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, on_input)


def _demo(overlay: X11Overlay) -> None:
    screen = Gdk.Screen.get_default()
    w, h = screen.get_width(), screen.get_height()
    steps = [
        (0.2, {"op": "cursor.move", "x": w * 0.3, "y": h * 0.3, "verb": "Click"}),
        (0.9, {"op": "cursor.click"}),
        (1.4, {"op": "cursor.state", "state": "thinking"}),
        (2.2, {"op": "cursor.move", "x": w * 0.7, "y": h * 0.4, "verb": "Double-click"}),
        (2.9, {"op": "cursor.click", "count": 2}),
        (3.3, {"op": "intent", "text": "Type “hello”"}),
        (3.3, {"op": "cursor.state", "state": "typing"}),
        (4.4, {"op": "cursor.state", "state": "idle"}),
        (4.6, {"op": "risk", "level": "caution"}),
        (4.8, {"op": "target.box", "x": w * 0.45, "y": h * 0.55, "w": 160, "h": 40}),
        (4.8, {"op": "cursor.move", "x": w * 0.5, "y": h * 0.58, "verb": "Click"}),
        (5.6, {"op": "cursor.click"}),
        (6.0, {"op": "risk", "level": "danger"}),
        (6.0, {"op": "intent", "text": "Press Ctrl+Q (quit the app)", "ms": 2000}),
        (8.0, {"op": "risk", "level": "ok"}),
        (9.0, {"op": "bye"}),
    ]
    for delay, msg in steps:
        GLib.timeout_add(int(delay * 1000), lambda m=msg: (overlay.handle(m), False)[1])


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(argv or [])
    cfg: Dict[str, Any] = {}
    raw = os.environ.get("DOBBY_OVERLAY_CONFIG")
    if raw:
        try:
            cfg = json.loads(raw)
        except json.JSONDecodeError:
            cfg = {}
    if not Gtk.init_check(sys.argv)[0]:
        sys.stderr.write("dobby.overlay: cannot open the X display\n")
        return 2
    overlay = X11Overlay(cfg)
    if "--demo" in argv:
        _demo(overlay)
    else:
        _stdin_watch(overlay)
        overlay.start_hotkeys()
    overlay.emit({"ready": True, "version": protocol.PROTOCOL_VERSION, "pid": os.getpid()})
    Gtk.main()
    return 0
