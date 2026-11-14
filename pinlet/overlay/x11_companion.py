"""Rime on Linux X11: the companion that stands in for the floating bar.

Off unless ``PINLET_COMPANION=1`` (or ``"companion": true`` in the overlay
config). While Rime is on screen the bar stays hidden; Rime shows what the AI
is doing in a thought cloud, and a click on Rime freezes the AI and opens a
small menu (Resume, Let one step run, Cancel task). Nothing here approves:
a pending Allow / Deny card gets a speech bubble that points at the card.

The window is an override-redirect popup. Only Rime's own body takes clicks
(an input shape); the bubble lets clicks through to the app below. Drag Rime
to move it; it steps aside when the AI's pointer heads for it.

Wayland has no override-redirect windows or global input shapes, so there the
bar stays (this module only runs inside the X11 helper).

The rules for mood, bubble and menu live in ``companion_scene`` (pure).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
import cairo  # noqa: E402
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import companion_scene as scene  # noqa: E402
from . import rime_art  # noqa: E402

W, H = 300, 210  # the window: bubble above, Rime at the bottom right
BODY_R = 30.0
RIME_SCALE = 0.42  # Rime's 200 x 220 art box, scaled to about 84 x 92
BODY_CX, BODY_CY = W - 48.0, H - 44.0
BUBBLE = (8.0, 14.0, W - 40.0, 92.0)  # x, y, w, h
EDGE = 18  # gap to the screen edge
AVOID_MARGIN = 28.0
HOME_AFTER_S = 1.5
DRAG_PX = 4.0

ICE_RGB = (0.62, 0.86, 0.96)
CYAN = (0.05, 0.71, 0.83)
INK = (0.08, 0.13, 0.18)


def _rounded(cr, x: float, y: float, w: float, h: float, r: float) -> None:
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _wrap(cr, text: str, width: float, lines: int) -> list[str]:
    words, out, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if cr.text_extents(trial).x_advance <= width or not line:
            line = trial
        else:
            out.append(line)
            line = word
            if len(out) == lines:
                break
    if line and len(out) < lines:
        out.append(line)
    if len(out) == lines and " ".join(out) != " ".join(words):
        last = out[-1]
        while last and cr.text_extents(last + "…").x_advance > width:
            last = last[:-1]
        out[-1] = last.rstrip() + "…"
    return out


class X11Companion:
    """Rime's window. The helper feeds it state with ``update`` and gets clicks back.

    ``on_menu(key)`` runs a menu choice (see ``companion_scene`` keys);
    ``on_freeze()`` runs on the first click while the AI works.
    """

    def __init__(
        self,
        composited: bool,
        on_menu: Callable[[str], None],
        on_freeze: Callable[[], None],
        name: str = "Rime",
    ) -> None:
        self.composited = composited
        self.on_menu = on_menu
        self.on_freeze = on_freeze
        self.name = name
        self.mood = scene.SLEEP
        self.bubble: scene.Bubble | None = None
        self.quiet = False
        self.visible = False
        self.phase = 0.0
        self.home: tuple[int, int] | None = None  # where the person put Rime
        self.dodged_until = 0.0
        self._press: tuple[float, float, int, int] | None = None
        self._dragged = False
        self._menu: Gtk.Menu | None = None

        win = Gtk.Window(type=Gtk.WindowType.POPUP)
        win.set_title("Pinlet companion")
        win.set_app_paintable(True)
        win.set_accept_focus(False)
        win.set_keep_above(True)
        if composited:
            visual = win.get_screen().get_rgba_visual()
            if visual is not None:
                win.set_visual(visual)
        win.set_default_size(W, H)
        win.resize(W, H)
        win.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        win.connect("draw", self._on_draw)
        win.connect("realize", self._on_realize)
        win.connect("button-press-event", self._on_press)
        win.connect("motion-notify-event", self._on_motion)
        win.connect("button-release-event", self._on_release)
        self.win = win
        GLib.timeout_add(80, self._tick)

    # ------------------------------------------------------------ placement
    @staticmethod
    def _workarea() -> tuple[int, int, int, int]:
        display = Gdk.Display.get_default()
        mon = display.get_primary_monitor() or display.get_monitor(0)
        area = mon.get_workarea()
        return area.x, area.y, area.width, area.height

    def _corner(self, left: bool = False) -> tuple[int, int]:
        x, y, w, h = self._workarea()
        cx = x + EDGE if left else x + w - W - EDGE
        return cx, y + h - H - EDGE

    def frame(self) -> tuple[float, float, float, float]:
        """Rime's body on screen, as x, y, w, h (the part that takes clicks)."""
        wx, wy = self.win.get_position()
        return (wx + BODY_CX - BODY_R, wy + BODY_CY - BODY_R, 2 * BODY_R, 2 * BODY_R)

    def avoid(self, point: tuple[float, float]) -> bool:
        """Step aside when the AI's pointer heads for Rime. True when it moved."""
        if not self.visible:
            return False
        x, y, w, h = self.frame()
        px, py = point
        m = AVOID_MARGIN
        bx, by = self.win.get_position()
        in_body = x - m <= px <= x + w + m and y - m <= py <= y + h + m
        in_bubble = self.bubble is not None and (
            bx + BUBBLE[0] <= px <= bx + BUBBLE[0] + BUBBLE[2]
            and by + BUBBLE[1] <= py <= by + BUBBLE[1] + BUBBLE[3]
        )
        if not (in_body or in_bubble):
            return False
        area_x, _ay, area_w, _ah = self._workarea()
        on_right = bx + W / 2 > area_x + area_w / 2
        self.win.move(*self._corner(left=on_right))
        Gdk.Display.get_default().flush()
        self.dodged_until = time.monotonic() + HOME_AFTER_S
        return True

    # ------------------------------------------------------------ state
    def update(self, mood: str, bubble: scene.Bubble | None, show: bool) -> None:
        """New mood and bubble from the helper; ``show`` puts Rime on or off screen."""
        changed = mood != self.mood or bubble != self.bubble
        self.mood, self.bubble = mood, bubble
        if show and not self.visible:
            self.win.move(*(self.home or self._corner()))
            self.win.show_all()
            self.visible = True
            changed = True
        elif not show and self.visible:
            self.win.hide()
            self.visible = False
            if self._menu is not None:
                self._menu.popdown()
        if changed and self.visible:
            self._reshape()
            self.win.queue_draw()

    def _tick(self) -> bool:
        if not self.visible:
            return True
        self.phase = (self.phase + 0.08) % (2 * math.pi)
        if self.dodged_until and time.monotonic() >= self.dodged_until:
            self.dodged_until = 0.0
            self.win.move(*(self.home or self._corner()))
        if self.composited:
            self.win.queue_draw()  # the gentle bob and Rime's sparkles; frozen Rime holds still
        return True

    # ------------------------------------------------------------ input
    def _on_realize(self, *_a) -> None:
        self._reshape()

    def _reshape(self) -> None:
        """Only Rime's body takes clicks; without a compositor, cut to the drawing."""
        if self.win.get_window() is None:
            return
        r = int(BODY_R) + 4
        body = cairo.RectangleInt(int(BODY_CX) - r, int(BODY_CY) - r, 2 * r, 2 * r)
        self.win.input_shape_combine_region(cairo.Region(body))
        if not self.composited:
            surf = cairo.ImageSurface(cairo.FORMAT_A1, W, H)
            self._paint(cairo.Context(surf), still=True)
            self.win.shape_combine_region(Gdk.cairo_region_create_from_surface(surf))

    def _on_press(self, _w, event) -> bool:
        if event.button != 1:
            self._open_menu(event)
            return True
        wx, wy = self.win.get_position()
        self._press = (event.x_root, event.y_root, wx, wy)
        self._dragged = False
        return True

    def _on_motion(self, _w, event) -> bool:
        if self._press is None:
            return False
        sx, sy, wx, wy = self._press
        dx, dy = event.x_root - sx, event.y_root - sy
        if self._dragged or math.hypot(dx, dy) > DRAG_PX:
            self._dragged = True
            self.win.move(int(wx + dx), int(wy + dy))
        return True

    def _on_release(self, _w, event) -> bool:
        if self._press is None:
            return False
        self._press = None
        if self._dragged:
            self.home = self.win.get_position()
            self.dodged_until = 0.0
            return True
        if scene.freezes_on_click(self.mood):
            self.on_freeze()  # freeze first: the menu is for what comes next
            self.mood = scene.PAUSE
        self._open_menu(event)
        return True

    def _open_menu(self, event) -> None:
        menu = Gtk.Menu()
        keys = scene.menu(self.mood, quiet=self.quiet)
        for key in keys:
            if key == scene.CANCEL:
                item = Gtk.MenuItem(label=scene.TITLES[key])
                sub = Gtk.Menu()  # a second, deliberate choice: no accidental cancel
                yes = Gtk.MenuItem(label="Yes, cancel the task")
                yes.connect("activate", lambda _i: self.on_menu(scene.CANCEL))
                sub.append(yes)
                item.set_submenu(sub)
            else:
                item = Gtk.MenuItem(label=scene.TITLES[key])
                item.connect("activate", lambda _i, k=key: self._choose(k))
            menu.append(item)
        menu.show_all()
        self._menu = menu
        menu.popup_at_pointer(event)

    def _choose(self, key: str) -> None:
        if key in (scene.QUIET, scene.LOUD):
            self.quiet = key == scene.QUIET
        self.on_menu(key)

    # ------------------------------------------------------------ drawing
    def _on_draw(self, _w, cr) -> bool:
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        self._paint(cr)
        return False

    def _paint(self, cr, still: bool = False) -> None:
        if self.bubble is not None:
            self._paint_bubble(cr, self.bubble)
        bob = 0.0 if still or self.mood == scene.PAUSE else math.sin(self.phase) * 2.5
        self._paint_rime(cr, BODY_CX, BODY_CY + bob)

    def _paint_rime(self, cr, cx: float, cy: float) -> None:
        """Rime itself, drawn by ``rime_art`` exactly as on the Mac; only scaled."""
        cr.save()
        cr.translate(cx - 100 * RIME_SCALE, cy - 138 * RIME_SCALE)
        cr.scale(RIME_SCALE, RIME_SCALE)
        rime_art.draw_mood(cr, self.mood, time.monotonic())
        cr.restore()

    def _paint_bubble(self, cr, b: scene.Bubble) -> None:
        x, y, w, h = BUBBLE
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        if b.kind == scene.CHIP:
            cr.set_font_size(12)
            tw = min(cr.text_extents(b.text).x_advance + 24, w)
            cx, cy = x + w - tw, y + h - 30
            _rounded(cr, cx, cy, tw, 26, 13)
            cr.set_source_rgba(1, 1, 1, 0.94)
            cr.fill()
            cr.set_source_rgba(*INK, 1)
            cr.move_to(cx + 12, cy + 17)
            cr.show_text(b.text)
            return
        fill = (1, 1, 1, 0.95)
        edge = (*CYAN, 0.9)
        if b.kind == scene.ICE:
            fill, edge = (0.90, 0.97, 1.0, 0.96), (*ICE_RGB, 1)
        elif b.kind == scene.BURST:
            fill, edge = (1.0, 0.95, 0.92, 0.97), (0.86, 0.33, 0.24, 1)
        if b.kind == scene.BURST:
            cx, cy = x + w / 2, y + h / 2
            points = 14
            for i in range(points * 2 + 1):
                a = math.pi * i / points
                rx = (w / 2) if i % 2 == 0 else (w / 2 - 10)
                ry = (h / 2 + 4) if i % 2 == 0 else (h / 2 - 8)
                px, py = cx + rx * math.cos(a), cy + ry * math.sin(a)
                cr.line_to(px, py) if i else cr.move_to(px, py)
            cr.close_path()
        else:
            corner = 6 if b.kind == scene.ICE else 22
            _rounded(cr, x, y, w, h, corner)
        cr.set_source_rgba(*fill)
        cr.fill_preserve()
        cr.set_source_rgba(*edge)
        cr.set_line_width(1.6)
        cr.stroke()
        # The tail: puffs for a thought, a point for speech.
        tx, ty = BODY_CX - 20, y + h
        if b.kind == scene.SPEECH:
            cr.move_to(tx - 18, ty - 1)
            cr.line_to(tx + 4, ty + 22)
            cr.line_to(tx - 2, ty - 1)
            cr.close_path()
            cr.set_source_rgba(*fill)
            cr.fill_preserve()
            cr.set_source_rgba(*edge)
            cr.stroke()
        elif b.kind == scene.CLOUD:
            for i, r in enumerate((7, 5, 3.5)):
                cr.arc(tx - 6 + i * 8, ty + 8 + i * 9, r, 0, 2 * math.pi)
                cr.set_source_rgba(*fill)
                cr.fill_preserve()
                cr.set_source_rgba(*edge)
                cr.stroke()
        # Text.
        pad = 30 if b.kind == scene.BURST else 16
        cr.set_source_rgba(*CYAN if b.kind != scene.BURST else (0.75, 0.25, 0.18), 1)
        cr.set_font_size(10.5)
        top = 8 if b.kind == scene.BURST else 0  # clear of the spikes
        cr.move_to(x + pad, y + 22 + top)
        cr.show_text(b.who)
        cr.set_source_rgba(*INK, 1)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(13)
        line_y = y + 42 + top
        for line in _wrap(cr, b.text, w - 2 * pad, 2):
            cr.move_to(x + pad, line_y)
            cr.show_text(line)
            line_y += 17
        if b.detail:
            cr.select_font_face("Sans", cairo.FONT_SLANT_ITALIC, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(11)
            cr.set_source_rgba(*INK, 0.7)
            for line in _wrap(cr, b.detail, w - 2 * pad, 2 if line_y < y + 60 else 1):
                cr.move_to(x + pad, line_y + 2)
                cr.show_text(line)
                line_y += 15


__all__ = ("X11Companion",)
