"""Overlay helper for Linux X11: ``python -m pinlet.overlay`` when DISPLAY is set.

Speaks the same JSON-lines protocol as ``mac_helper`` (see ``protocol.py``),
drawn with GTK 3 + cairo through PyGObject (Debian/Ubuntu: python3-gi,
python3-gi-cairo, gir1.2-gtk-3.0).

What it draws:

* the agent pointer (cyan arrow) gliding to each target, with a ripple on
  clicks when a compositor is running;
* a capsule tag next to the pointer with the label, state and current intent;
* a glow frame around the tracked window and a thinner box around the target
  element (from the AT-SPI hit test when a move asks for a probe);
* an Allow / Deny window for ``confirm``;
* a tray icon in the top panel (``topbar``, AppIndicator / Ayatana when
  available, else the legacy notification area): Pinlet's mark, the number of
  running Pinlets and a menu with one row per Pinlet (what it does, Pause /
  Resume / Stop); only the bar owner shows it;
* the control bar at the top of the screen (same states as macOS, from
  ``menu.bar_view``): "Pinlet is sleeping" with Pause / Stop, "working",
  "paused", "stopped"; click the bar itself to wake Pinlet or put it back to
  sleep. Only the helper holding ``~/.pinlet/overlay/bar.lock`` shows the idle
  bar.

Every panel except the confirm window is an override-redirect popup with an
empty input shape, so clicks go straight through. Without a compositor the
pointer and tag are cut to their drawn shape with XShape, and the frames use
four thin edge windows, so nothing opaque covers the screen.

Controls: Ctrl+Alt+Esc sends ``stop`` (Ctrl+Alt+End when another app, such as
GNOME's cycle-panels, already grabs Esc); Ctrl+Alt+P toggles ``pause`` /
``resume`` (global key grabs on the root window).

Input lock (``PINLET_INPUT_LOCK=window``, see ``x11_lock``): while Pinlet works,
a shield with the "not-allowed" cursor covers its window and swallows the
person's clicks there; other windows stay usable.

Not ported from macOS yet: the numbered step trail, the scan sweep, the menu
tag menu and user take-over detection. Their ops are accepted and acked so the
server behaves the same.
"""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
import cairo  # noqa: E402
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from ..personality import Voice  # noqa: E402
from . import geometry, inputlock, protocol, style, topbar  # noqa: E402
from .barlock import (  # noqa: E402
    BAR_LOCK,
    BarOwner,
    BarRow,
    mark_waiting,
    others_showing,
    others_waiting,
    showing_dir,
    waiting_dir,
)
from .menu import GOAL_SECS, bar_click, bar_view, goal_over, idle_cleanup, label_click  # noqa: E402
from .protocol import ACTING_STATES  # noqa: E402
from .text import short  # noqa: E402

DEFAULT_CONFIG: dict[str, Any] = {
    "color": "#0EA5C6",
    "caution_color": "#F5A524",
    "danger_color": "#F04438",
    "label": "Pinlet",
    "speed": 1.0,
    "glow_width": 3,
    "box_width": 2,
    "track_hz": 5.0,
    "intent_ms": 1600,
    "turbo": False,
    "active_secs": 8.0,  # "working" lasts this long after an action
    "linger_secs": 20.0,  # then "thinking" stops and the pointer is put away
    "awake_secs": 120.0,  # woken from the bar: stays awake this long without a task
    "wake_note_secs": 4.0,  # woken while no AI waits: says so this long, then sleeps
    "pill": "auto",  # control bar: "auto" (only while working), "always" or "off"
    "narrate": True,  # modern look: say what Pinlet does on the bar and the tag
    "input_lock": "off",  # "window": shield Pinlet's window while it works (inputlock)
    "menu_bar": True,  # tray icon in the panel listing every Pinlet (topbar); bar owner only
}

# Classic arrow, y down, tip at (0, 0) (same outline as the macOS helper).
_ARROW = [(0, 0), (0, 18), (4.5, 14), (8, 21.5), (10.5, 20.5), (7, 13), (13, 13)]
_STATE_ICON = {
    "idle": "",
    "thinking": "…",
    "typing": "⌨",
    "keys": "⌨",
    "scrolling": "↕",
    "dragging": "✥",
    "waiting_user": "?",
}
# Ctrl+Alt+<key> per action, first free key wins. GNOME/Metacity grabs Ctrl+Alt+Esc
# (cycle-panels), so Stop falls back to Ctrl+Alt+End there.
_HOTKEYS = {"stop": ("Escape", "End"), "pause": ("p",)}
# The combo actually grabbed for each action, e.g. {"stop": "Ctrl+Alt+End"}; shown in tooltips.
HOTKEY_LABELS: dict[str, str] = {}


def _rgb(value: str) -> tuple[float, float, float]:
    return style.hex_rgb01(value)


# Palette "Frost": the card follows the desktop's light/dark
# theme; filled buttons use #0E7490 (white text passes 4.5:1, #0EA5C6 does not).
_CONFIRM_THEMES = {
    "light": {
        "bg": "rgba(247,250,252,0.97)",
        "text": "#0F1720",
        "dim": "#46525F",
        "count": "#6B7683",
        "border": "rgba(14,165,198,0.28)",
        "accent": "#0EA5C6",
        "neutral": "#E6EDF3",
        "neutral_hover": "#D8E2EA",
        "neutral_text": "#0F1720",
    },
    "dark": {
        "bg": "rgba(20,26,34,0.96)",
        "text": "#F1F6FA",
        "dim": "#C2CCD6",
        "count": "#8A96A3",
        "border": "rgba(103,232,249,0.35)",
        "accent": "#67E8F9",
        "neutral": "#2E3945",
        "neutral_hover": "#38444F",
        "neutral_text": "#F1F6FA",
    },
}
_CONFIRM_FILL, _CONFIRM_DANGER = "#0E7490", "#F04438"


def _desktop_is_dark() -> bool:
    if ":dark" in os.environ.get("GTK_THEME", "").lower():
        return True
    try:  # GNOME 42+ and others: the desktop-wide light/dark switch
        from gi.repository import Gio

        source = Gio.SettingsSchemaSource.get_default()
        if source is not None and source.lookup("org.gnome.desktop.interface", True) is not None:
            schema = Gio.Settings.new("org.gnome.desktop.interface")
            if (
                "color-scheme" in schema.list_keys()
                and schema.get_string("color-scheme") == "prefer-dark"
            ):
                return True
    except Exception:  # noqa: BLE001
        pass
    try:
        settings = Gtk.Settings.get_default()
        if settings.get_property("gtk-application-prefer-dark-theme"):
            return True
        return "dark" in str(settings.get_property("gtk-theme-name") or "").lower()
    except Exception:  # noqa: BLE001
        return False


# "dark" (default), "light" or "auto" (follow the desktop); X11Overlay sets it
# from its config (PINLET_OVERLAY_THEME).
_THEME = "dark"


def _overlay_is_dark() -> bool:
    """Whether the bar and prompts draw their dark look (``style.overlay_dark``)."""
    return style.overlay_dark(_THEME, _desktop_is_dark() if _THEME == "auto" else False)


def _confirm_css(kind: str, dark: bool) -> bytes:
    t = _CONFIRM_THEMES["dark" if dark else "light"]
    who = _CONFIRM_DANGER if kind == "danger" else t["accent"]
    return f"""
#pinlet-confirm {{ background-color: {t["bg"]}; border: 1px solid {t["border"]}; border-radius: 14px; }}
#pinlet-confirm .pinlet-who {{ color: {who}; font-weight: bold; font-size: 11px; letter-spacing: 1px; }}
#pinlet-confirm .pinlet-title {{ color: {t["text"]}; font-weight: bold; font-size: 15px; }}
#pinlet-confirm .pinlet-detail {{ color: {t["dim"]}; font-size: 12px; }}
#pinlet-confirm .pinlet-count {{ color: {t["count"]}; font-size: 10px; }}
#pinlet-confirm button {{ border-radius: 8px; padding: 4px 16px; min-height: 26px; background-image: none;
  box-shadow: none; text-shadow: none; }}
#pinlet-confirm button.pinlet-allow {{ background-color: {_CONFIRM_FILL}; color: #ffffff; border-color: {_CONFIRM_FILL}; }}
#pinlet-confirm button.pinlet-allow:hover {{ background-color: #6A50EA; }}
#pinlet-confirm button.pinlet-deny-danger {{ background-color: {_CONFIRM_DANGER}; color: #ffffff;
  border-color: {_CONFIRM_DANGER}; }}
#pinlet-confirm button.pinlet-deny {{ background-color: {t["neutral"]}; color: {t["neutral_text"]};
  border-color: {t["border"]}; }}
#pinlet-confirm button.pinlet-deny:hover {{ background-color: {t["neutral_hover"]}; }}
#pinlet-confirm button label {{ color: inherit; }}
""".encode()


def _confirm_window(
    text: str, detail: str, allow: str, deny: str, kind: str, who: str, accent: str, on_answer
) -> tuple[Any, Any]:
    """The Allow / Deny card: who asks, what, why, two clear buttons.

    Enter answers with the default button and Escape always denies. The
    default is Deny for a risky step and Allow for an app-access request.
    Returns (window, countdown label).
    """
    win = Gtk.Window(title=protocol.OVERLAY_WINDOW_TITLE)
    win.set_name("pinlet-confirm")
    win.set_type_hint(Gdk.WindowTypeHint.DIALOG)
    win.set_keep_above(True)
    win.set_resizable(False)
    win.set_decorated(False)
    win.set_skip_taskbar_hint(True)
    win.set_position(Gtk.WindowPosition.CENTER_ALWAYS)
    screen = win.get_screen()
    if screen is not None and screen.is_composited() and screen.get_rgba_visual() is not None:
        win.set_visual(screen.get_rgba_visual())  # round corners without black wedges
        win.set_app_paintable(False)
    provider = Gtk.CssProvider()
    provider.load_from_data(_confirm_css(kind, _overlay_is_dark()))
    win.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def styled(widget, cls):
        widget.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        child = widget.get_child() if isinstance(widget, Gtk.Button) else None
        if child is not None:
            child.get_style_context().add_provider(
                provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        if cls:
            widget.get_style_context().add_class(cls)
        return widget

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=20)
    head = styled(Gtk.Label(label=(who or "Pinlet").upper(), xalign=0), "pinlet-who")
    title = styled(Gtk.Label(label=text, xalign=0), "pinlet-title")
    title.set_line_wrap(True)
    title.set_max_width_chars(44)
    box.pack_start(head, False, False, 0)
    box.pack_start(title, False, False, 0)
    if detail:
        body = styled(Gtk.Label(label=detail, xalign=0), "pinlet-detail")
        body.set_line_wrap(True)
        body.set_max_width_chars(52)
        box.pack_start(body, False, False, 2)
    row = Gtk.Box(spacing=8, margin_top=12)
    countdown = styled(Gtk.Label(label="", xalign=0), "pinlet-count")
    row.pack_start(countdown, True, True, 0)
    deny_btn = styled(
        Gtk.Button(label=deny), "pinlet-deny-danger" if kind == "danger" else "pinlet-deny"
    )
    allow_btn = styled(Gtk.Button(label=allow), "pinlet-allow")
    deny_btn.connect("clicked", lambda *_: on_answer(False))
    allow_btn.connect("clicked", lambda *_: on_answer(True))
    row.pack_end(allow_btn, False, False, 0)
    row.pack_end(deny_btn, False, False, 0)
    box.pack_start(row, False, False, 0)
    win.add(box)
    default = allow_btn if kind == "access" else deny_btn
    default.set_can_default(True)
    win.set_default(default)
    win.connect("show", lambda *_: default.grab_focus())

    def on_key(_w, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            on_answer(False)
            return True
        return False

    win.connect("key-press-event", on_key)
    win.connect("delete-event", lambda *_: (on_answer(False), True)[1])
    return win, countdown


def _short(text: str, n: int = 48) -> str:
    return short(text, n)


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
        self.edges: list[_Panel] = []
        for _ in range(4):
            p = _Panel(False, 1, 1, painter=self._paint)
            self.edges.append(p)
        self.rect: tuple[int, int, int, int] | None = None
        self.visible = False

    def _paint(self, cr, w, h) -> None:
        cr.set_source_rgb(*self.color)
        cr.rectangle(0, 0, w, h)
        cr.fill()

    def set_color(self, rgb) -> None:
        self.color = rgb
        for e in self.edges:
            e.queue_draw()

    def place(self, rect: tuple[int, int, int, int] | None) -> None:
        self.rect = rect
        if rect is None:
            self.hide()
            return
        x, y, w, h = (int(v) for v in rect)
        t = self.width
        boxes = [
            (x - t, y - t, w + 2 * t, t),  # top
            (x - t, y + h, w + 2 * t, t),  # bottom
            (x - t, y, t, h),  # left
            (x + w, y, t, h),  # right
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


# The resting bar matches the macOS bar (mac_helper._Pill): a 34 px rectangle with
# 12 px corners like the Claude chat box, no hard edge. Linux has no Liquid Glass, so
# with a compositor it uses the Mac's no-glass fallback (translucent fill, thin
# Pinlet-colour border); without one the window cannot be see-through and the
# fill is the theme's near-opaque one.
BAR_HEIGHT, BAR_RADIUS, BAR_ROW_STEP = 34, 12, 40
_BAR_GLASSLESS = {"light": (0.72, 0.55), "dark": (0.42, 0.70)}  # fill alpha, border alpha


def _bar_css(dark: bool, composited: bool = True) -> bytes:
    """The bar in palette "Frost", following the desktop theme."""
    t = style.bar_theme(dark)
    r, g, b = t["bg"]
    fill_alpha, border_alpha = _BAR_GLASSLESS["dark" if dark else "light"]
    if not composited:
        fill_alpha = t["bg_alpha"]
    ar, ag, ab = (int(v * 255) for v in style.hex_rgb01(str(t["accent"])))
    hr, hg, hb, ha = t["hover"]
    return f"""
#pinlet-bar {{ background-color: rgba({r}, {g}, {b}, {fill_alpha}); border-radius: {BAR_RADIUS}px;
  border: 1px solid rgba({ar}, {ag}, {ab}, {border_alpha}); }}
#pinlet-bar label {{ color: {t["text"]}; font-size: 12.5pt; font-weight: 500;
  font-feature-settings: "tnum"; }}
#pinlet-bar button {{ padding: 0 6px; min-width: 28px; min-height: 24px; background-image: none;
  box-shadow: none; text-shadow: none; border-radius: 7px; background-color: transparent;
  border-color: transparent; }}
#pinlet-bar button:hover {{ background-color: rgba({hr}, {hg}, {hb}, {ha}); }}
#pinlet-bar button label {{ color: {t["text"]}; }}
#pinlet-bar button.pinlet-fill {{ background-color: {style.FILL_COLOR}; }}
#pinlet-bar button.pinlet-fill:hover {{ background-color: #6A50EA; }}
#pinlet-bar button.pinlet-fill label {{ color: #ffffff; }}
""".encode()


def _set_fill(button: Any, filled: bool) -> None:
    ctx = button.get_style_context()
    if filled:
        ctx.add_class("pinlet-fill")
    else:
        ctx.remove_class("pinlet-fill")


class _Bar:
    """The control bar: clickable (not click-through), top centre of the screen."""

    def __init__(self, on_label, on_button, bar_lock: str = BAR_LOCK) -> None:
        self.mode = "hidden"
        # Bars of several Pinlets stack one row apart instead of overlapping (see barlock).
        self.rows = BarRow(bar_lock)
        self.row = 0
        self.show_dir = showing_dir(bar_lock)
        self.marked_showing = False
        self.win = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.win.set_title(protocol.OVERLAY_WINDOW_TITLE)
        self.win.set_name("pinlet-bar")
        self.win.set_accept_focus(False)
        self.win.set_keep_above(True)
        screen = Gdk.Screen.get_default()
        visual = screen.get_rgba_visual() if screen is not None and screen.is_composited() else None
        self.composited = visual is not None
        if visual is not None:  # see-through corners and fill
            self.win.set_visual(visual)
        else:  # recut the rounded shape whenever the bar changes size
            self.win.connect(
                "size-allocate", lambda _w, alloc: self._round(alloc.width, alloc.height)
            )
        provider = Gtk.CssProvider()
        provider.load_from_data(_bar_css(_overlay_is_dark(), self.composited))
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._css = provider
        try:  # follow the desktop when it switches between light and dark
            settings = Gtk.Settings.get_default()
            for prop in ("gtk-theme-name", "gtk-application-prefer-dark-theme"):
                settings.connect(
                    f"notify::{prop}",
                    lambda *_: self._css.load_from_data(
                        _bar_css(_overlay_is_dark(), self.composited)
                    ),
                )
        except Exception:  # noqa: BLE001 - theme following is a nicety
            pass
        # Mac spacing: 12 left, dot, label, 10 to the buttons, 2 between them, 12 right.
        row = Gtk.Box(spacing=0, margin_start=12, margin_end=12)
        row.set_size_request(-1, BAR_HEIGHT)
        self.dot = Gtk.Label(label="●")
        self.text = Gtk.Label(label="")
        click = Gtk.EventBox()
        click.set_visible_window(False)
        inner = Gtk.Box(spacing=8)
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
        buttons = Gtk.Box(spacing=2, margin_start=10)
        buttons.set_valign(Gtk.Align.CENTER)
        buttons.pack_start(self.primary, False, False, 0)
        buttons.pack_start(self.secondary, False, False, 0)
        row.pack_start(click, True, True, 0)
        row.pack_start(buttons, False, False, 0)
        self.win.add(row)
        self.visible = False

    def _place(self) -> None:
        screen = Gdk.Screen.get_default()
        size = self.win.get_preferred_size()[1]
        self.win.move(max(0, (screen.get_width() - size.width) // 2), 8 + self.row * BAR_ROW_STEP)

    def _round(self, w: int, h: int) -> None:
        """No compositor: cut the corners off with XShape so they are not black squares."""
        surf = cairo.ImageSurface(cairo.FORMAT_A1, max(w, 1), max(h, 1))
        cr = cairo.Context(surf)
        r = min(BAR_RADIUS, h / 2)
        cr.new_sub_path()
        cr.arc(w - r, r, r, -math.pi / 2, 0)
        cr.arc(w - r, h - r, r, 0, math.pi / 2)
        cr.arc(r, h - r, r, math.pi / 2, math.pi)
        cr.arc(r, r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.fill()
        self.win.shape_combine_region(Gdk.cairo_region_create_from_surface(surf))

    def _sync_presence(self) -> None:
        """Hold a screen row and the "showing" marker while visible; drop them when hidden."""
        if self.visible:
            self.row = self.rows.claim()
        else:
            self.rows.release()
        showing = self.visible and self.mode != "ready"
        if showing != self.marked_showing:
            self.marked_showing = showing
            mark_waiting(self.show_dir, os.getpid(), showing)

    def show(
        self, mode: str, text: str, primary: str, secondary: str, rgb, dot_alpha: float = 1.0
    ) -> None:
        self.mode = mode
        self.text.set_text(text)
        if primary:
            self.primary.set_label(primary)
            _set_fill(self.primary, primary in style.FILL_TITLES)
        if secondary:
            self.secondary.set_label(secondary)
            _set_fill(self.secondary, secondary in style.FILL_TITLES)
        r, g, b = (int(v * 255) for v in rgb)
        self.dot.set_markup(
            f'<span foreground="#{r:02x}{g:02x}{b:02x}" alpha="{int(dot_alpha * 100)}%">●</span>'
        )
        self.visible = True
        self._sync_presence()
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
                _set_fill(btn, style.is_filled(b))
                tip = b.tip or ""
                if b.icon == "stop.fill" and tip:
                    keys = HOTKEY_LABELS.get("stop")
                    tip = tip.replace("(⌃⌥⎋)", f"({keys})" if keys else "").strip()
                btn.set_tooltip_text(tip or None)
        self.show(spec.mode, spec.text, "", "", rgb, dot_alpha=spec.dot_alpha)
        self.primary.set_visible("primary" in wanted)
        self.secondary.set_visible("secondary" in wanted)

    def hide(self, keep_mode: bool = False) -> None:
        if not keep_mode:
            self.mode = "hidden"
        self.visible = False
        if not keep_mode:  # hidden for a moment during a capture: keep the row
            self._sync_presence()
        self.win.hide()


class _Tray:
    """The panel tray icon and its menu (``topbar``).

    AppIndicator (Ayatana or the older libappindicator) exports the menu over
    D-Bus, so it is built ahead of time and its labels are updated in place;
    the legacy ``Gtk.StatusIcon`` builds it when clicked.
    """

    def __init__(self, overlay: X11Overlay) -> None:
        self.ov = overlay
        self.indicator = None
        self.status_icon = None
        self.menu: Gtk.Menu | None = None
        self.items: dict[str, Any] = {}
        self.shape: tuple | None = None
        self.icon_sig: tuple | None = None
        self.view: topbar.View | None = None
        self.ai = self._appindicator()
        if self.ai is not None:
            self.indicator = self.ai.Indicator.new(
                "pinlet", "dialog-information", self.ai.IndicatorCategory.APPLICATION_STATUS
            )
            self.indicator.set_status(self.ai.IndicatorStatus.ACTIVE)
            self.indicator.set_title("Pinlet")
        else:
            # Most panels today (GNOME Flashback's indicator applet, Ubuntu, KDE) only take
            # AppIndicator icons; the legacy icon needs a notification-area applet.
            sys.stderr.write(
                "pinlet.overlay: no AppIndicator binding (install gir1.2-ayatanaappindicator3-0.1); "
                "using the legacy tray icon, which shows only with a notification-area applet\n"
            )
            self.status_icon = Gtk.StatusIcon()
            self.status_icon.set_title("Pinlet")
            self.status_icon.connect("activate", self._popup)
            self.status_icon.connect(
                "popup-menu", lambda _i, button, t: self._popup(None, button, t)
            )

    @staticmethod
    def _appindicator():
        for name in ("AyatanaAppIndicator3", "AppIndicator3"):
            try:
                gi.require_version(name, "0.1")
                return __import__("gi.repository", fromlist=[name]).__dict__[name]
            except (ValueError, ImportError, KeyError, AttributeError):
                continue
        return None

    # ------------------------------------------------------------ icon
    def update(self, view: topbar.View) -> None:
        self.view = view
        state, count = topbar.icon_state(view), view.running
        sig = (state, count, topbar.tooltip(view))
        if sig != self.icon_sig:
            self.icon_sig = sig
            if self.indicator is not None:
                # A white "Frost" tile: reads on light and dark panels. Handed over by name from an
                # icon search path, so the panel scales it to its slot: a file path is drawn
                # at its own size and cropped (GNOME Flashback gives it 16 px).
                path = topbar.tray_icon_path(state, 64)
                if path:
                    folder, name = os.path.split(path)
                    self.indicator.set_icon_theme_path(folder)
                    self.indicator.set_icon_full(os.path.splitext(name)[0], sig[2])
                self.indicator.set_label(topbar.icon_title(view), "99")
            else:
                path = topbar.tray_icon_path(state, 44, count=count)
                if path:
                    self.status_icon.set_from_file(path)
                self.status_icon.set_tooltip_text(sig[2])
        if self.indicator is not None:
            self._sync_menu(view)

    # ------------------------------------------------------------ menu
    def _shape(self, view: topbar.View) -> tuple:
        return (
            tuple((r.pid, tuple(r.actions)) for r in view.rows),
            tuple(view.all_actions),
            bool(self.ov.cfg.get("turbo")),
        )

    @staticmethod
    def _row_label(row: topbar.Row) -> str:
        return f"{row.title}  ·  {row.detail}"

    def _sync_menu(self, view: topbar.View) -> None:
        shape = self._shape(view)
        if self.menu is None or shape != self.shape:
            self.shape = shape
            self.menu = self._build(view)
            self.indicator.set_menu(self.menu)
            return
        self.items["header"].set_label(f"{view.header} · {view.subtitle}")
        for row in view.rows:  # same rows and actions: relabel in place
            item = self.items.get(f"row{row.pid}")
            if item is None:
                continue
            item.set_label(self._row_label(row))
            path = topbar.row_icon_path(row.color, row.shape, 32)
            if path and getattr(item, "_pinlet_icon", None) != path:
                item.set_image(Gtk.Image.new_from_file(path))
                item._pinlet_icon = path

    def _item(
        self,
        menu: Gtk.Menu,
        label: str,
        action=None,
        icon: str | None = None,
        check: bool | None = None,
    ):
        if check is not None:
            item = Gtk.CheckMenuItem.new_with_label(label)
            item.set_active(check)
        elif icon:
            item = Gtk.ImageMenuItem.new_with_label(label)
            item.set_image(Gtk.Image.new_from_file(icon))
            item.set_always_show_image(True)
            item._pinlet_icon = icon
        else:
            item = Gtk.MenuItem.new_with_label(label)
        if action is None:
            item.set_sensitive(False)
        else:
            item.connect("activate", lambda _i: action())
        menu.append(item)
        return item

    def _build(self, view: topbar.View) -> Gtk.Menu:
        menu = Gtk.Menu()
        self.items = {"header": self._item(menu, f"{view.header} · {view.subtitle}")}
        menu.append(Gtk.SeparatorMenuItem())
        for row in view.rows:
            item = self._item(
                menu, self._row_label(row), icon=topbar.row_icon_path(row.color, row.shape, 32)
            )
            item.set_sensitive(True)
            sub = Gtk.Menu()
            for title, action in row.actions:
                self._item(sub, title, lambda a=action, pid=row.pid: self.ov.topbar_action(a, pid))
            item.set_submenu(sub)
            self.items[f"row{row.pid}"] = item
        if view.all_actions:
            menu.append(Gtk.SeparatorMenuItem())
            for title, action in view.all_actions:
                self._item(menu, title, lambda a=action: self.ov.topbar_action(a, 0))
        menu.append(Gtk.SeparatorMenuItem())
        self._item(
            menu,
            "Turbo mode (no prompts)",
            lambda: self.ov.topbar_action("turbo", 0),
            check=bool(self.ov.cfg.get("turbo")),
        )
        menu.show_all()
        return menu

    def _popup(self, _icon, button: int = 1, when: int | None = None) -> None:
        view = self.ov.topbar_view()
        self.update(view)
        self.menu = self._build(view)
        self.menu.popup(
            None,
            None,
            Gtk.StatusIcon.position_menu,
            self.status_icon,
            button,
            when if when is not None else Gtk.get_current_event_time(),
        )

    def close(self) -> None:
        if self.indicator is not None:
            self.indicator.set_status(self.ai.IndicatorStatus.PASSIVE)
        if self.status_icon is not None:
            self.status_icon.set_visible(False)


class X11Overlay:
    """The Linux (X11/GTK) overlay helper: pointer, glow, bar and Allow/Deny prompts.

    Runs in its own process and reads JSON-lines commands from the server on stdin.
    """

    def __init__(self, cfg: dict[str, Any], out=None) -> None:
        self.cfg = dict(DEFAULT_CONFIG)
        self.cfg.update(cfg or {})
        global _THEME
        _THEME = style.normalize_theme(self.cfg.get("theme"))
        self.out = out or sys.stdout
        self._wlock = threading.Lock()
        screen = Gdk.Screen.get_default()
        self.composited = bool(screen is not None and screen.is_composited())

        self.risk = "ok"
        self.state = "idle"
        self.style = style.normalize_style(self.cfg.get("style"))
        self.narrate = self.style == "modern" and bool(self.cfg.get("narrate", True))
        self.voice = Voice.from_config(self.cfg)  # Pinlet's words (PINLET_PERSONALITY)
        self.server_pid = int(os.environ.get("PINLET_SERVER_PID", "0") or 0)
        self.agents_next = 0.0
        # The task in progress, for the bar's live step and timer.
        self.task_started = 0.0
        self.goal = ""  # what the AI says it is doing, for the bar
        self.task_steps = 0
        self.current_step = ""
        self.bar_second = -1
        self.intent = ""
        self.intent_until = 0.0
        self.cursor: tuple[float, float] | None = None
        self._anim_id: int | None = None
        self.user_visible = True
        self.capture_hidden = False
        self.paused = False
        self.pending_confirm: tuple[Any, Gtk.Window, int] | None = None
        self.track_wid: int | None = None
        self.track_pid: int | None = None

        now = time.monotonic()
        self.last_action = now - 10 * float(self.cfg["active_secs"])  # start asleep
        self.awake_until = 0.0
        self.stopped_until = 0.0
        self.wake_pointer = False
        self.ai_waiting = 0  # AIs waiting in wait_for_wake
        self.handoff = False  # the current wake went to a waiting AI
        self.others_waiting = False  # another helper's AI waits (bar owner steps aside)
        self.others_checked = 0.0
        self.bar_mode = str(self.cfg.get("pill", "auto"))
        self.bar_owner = BarOwner(self.cfg.get("bar_lock", BAR_LOCK))
        self.bar_owner_retry = 0.0
        self.wait_dir = waiting_dir(self.cfg.get("bar_lock", BAR_LOCK))
        self.bar = (
            None
            if self.bar_mode == "off"
            else _Bar(self.on_bar_label, self.on_bar, self.cfg.get("bar_lock", BAR_LOCK))
        )
        self.tray: _Tray | None = None
        self.tray_next = 0.0
        self.publish_next = 0.0

        self.pointer = _Panel(self.composited, 26, 28, painter=self._paint_pointer)
        self.tag = _Panel(self.composited, 120, 26, painter=self._paint_tag)
        self.ripple = _Panel(True, 48, 48, painter=self._paint_ripple) if self.composited else None
        self._ripple_t = 1.0
        self.glow = _Frame(int(self.cfg["glow_width"]))
        self.box = _Frame(int(self.cfg["box_width"]))
        self._apply_colors()

        self._hal = None
        self._probe = None
        self.track_found: int | None = None  # the tracked window's id, as found
        self.lock_rect: tuple[int, int, int, int] | None = None
        self.lock = None
        self.lock_stopped = False  # Stop frees the window until Pinlet acts again
        self._lock_cursor = None
        if str(self.cfg.get("input_lock") or "off") != "off":
            self._start_lock()
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

    def emit(self, obj: dict[str, Any]) -> None:
        """Writes one JSON message to the server."""
        self._write(json.dumps(obj, separators=(",", ":")) + "\n")

    def send_event(self, name: str) -> None:
        """Sends a named event (pause, resume, stop, ...) to the server."""
        self._write(protocol.event(name))

    def ack(self, msg_id: Any, ok: bool = True, error: str | None = None) -> bool:
        """Acknowledges the command with ``msg_id``.

        Returns:
            Always False, so it can be used as a one-shot GLib callback.
        """
        if msg_id is not None:
            self._write(protocol.ack(msg_id, ok, error))
        return False  # usable as a one-shot GLib callback

    # ------------------------------------------------------------- colors
    def _color(self) -> tuple[float, float, float]:
        key = {"caution": "caution_color", "danger": "danger_color"}.get(self.risk, "color")
        return _rgb(self.cfg[key])

    def _tag_color(self) -> tuple[float, float, float]:
        """Colour for the pointer tag, which carries white text.

        Pinlet cyan #0EA5C6 is too light for that (2.9:1), so the tag uses the deeper
        #0E7490.
        """
        if (
            self.risk not in ("caution", "danger")
            and str(self.cfg["color"]).upper() == style.AGENT_PALETTE[0]
        ):
            return _rgb(style.FILL_COLOR)
        return self._color()

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
        cr.set_source_rgb(*self._tag_color())
        cr.fill()
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(12)
        if self.risk == "caution":
            cr.set_source_rgb(*_rgb("#241A00"))  # white on amber is unreadable
        else:
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

    def _place_pointer(self, pt: tuple[float, float]) -> None:
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
    def _duration(self, ms: float | None) -> float:
        speed = float(self.cfg["speed"])
        if speed <= 0 or not ms:
            return 0.0
        return float(ms) / 1000.0

    def _glide(self, target: tuple[float, float], dur: float, msg_id: Any) -> None:
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
            self._place_pointer(
                (start[0] + (target[0] - start[0]) * e, start[1] + (target[1] - start[1]) * e)
            )
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
                from pinlet.hal.linux import LinuxHAL

                self._hal = LinuxHAL()
            except Exception as exc:  # noqa: BLE001
                sys.stderr.write(f"pinlet.overlay: no X11 HAL: {exc}\n")
                self._hal = False
        return self._hal or None

    def _tracked_rect(self) -> tuple[int, int, int, int] | None:
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
                self.track_found = wid
                return (b.x, b.y, b.width, b.height)
        if self.track_pid is not None:
            for w in windows:  # front to back: the pid's topmost window
                if hal.window_pid(w.window_id) == self.track_pid:
                    b = w.bbox
                    self.track_found = protocol.window_id_int(w.window_id)
                    return (b.x, b.y, b.width, b.height)
        self.track_found = None
        return None

    # ---------------------------------------------------------- input lock
    def _start_lock(self) -> None:
        try:
            from .x11_lock import X11InputLock

            cursor_xid = None
            try:  # the theme's "not-allowed" sign, made by GDK, used by xid
                gi.require_version("GdkX11", "3.0")
                from gi.repository import GdkX11

                self._lock_cursor = Gdk.Cursor.new_from_name(
                    Gdk.Display.get_default(), "not-allowed"
                )
                if self._lock_cursor is not None:
                    cursor_xid = int(GdkX11.X11Cursor.get_xcursor(self._lock_cursor))
                    Gdk.flush()
            except Exception as exc:  # noqa: BLE001 - fall back to the X cursor font
                sys.stderr.write(f"pinlet.overlay: lock cursor: {exc}\n")
            self.lock = X11InputLock(
                self._on_lock_swallowed,
                self._on_lock_insist,
                lambda which: self._on_hotkey(which),
                cursor_xid=cursor_xid,
            )
            GLib.io_add_watch(
                self.lock.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN, self._lock_readable
            )
            sys.stderr.write("pinlet.overlay: input lock ready (window)\n")
        except Exception as exc:  # noqa: BLE001 - Pinlet works without the lock
            sys.stderr.write(f"pinlet.overlay: input lock unavailable: {exc}\n")
            self.lock = None

    def _lock_readable(self, _fd, _cond) -> bool:
        if self.lock is None:
            return False
        try:
            self.lock.pump()
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"pinlet.overlay: input lock: {exc}\n")
        return self.lock is not None

    def lock_armed(self) -> bool:
        """True when the input lock should be blocking the person's input right now."""
        return inputlock.armed(
            mode="window" if self.lock is not None else "off",
            working=self._working(),
            paused=self.paused or self.lock_stopped,
            takeover=False,
            confirming=self.pending_confirm is not None,
            has_target=self.lock_rect is not None,
        )

    def _lock_sync(self) -> None:
        if self.lock is not None:
            self.lock.update(self.lock_rect, self.track_found, self.lock_armed())

    def _on_lock_swallowed(self, kind: str) -> None:
        if self.bar is not None and self.bar.mode == "hidden":
            self._refresh_bar()  # the way out (Take over) must be visible

    def _on_lock_insist(self) -> None:
        self.set_paused(True)  # clicked again and again: the person takes over

    def _track_tick(self) -> bool:
        if self.lock is not None:
            self.lock.tick()
        if self.track_wid is None and self.track_pid is None:
            self.lock_rect = None
            self._lock_sync()
            return True
        rect = self._tracked_rect()
        self.lock_rect = rect
        self._lock_sync()
        if rect != self.glow.rect:
            self.glow.place(rect)
            if not self._overlay_shown():
                self.glow.hide()
        return True

    def _tag_tick(self) -> bool:
        if self.intent and (
            time.monotonic() >= self.intent_until or (self.narrate and not self._working())
        ):
            self.intent = ""  # narration: the action stays until Pinlet goes idle
            self._layout_tag()
        return True

    def _probe_at(self, x: float, y: float) -> tuple[str, tuple[int, int, int, int] | None]:
        if self._probe is None:
            try:
                from pinlet.perception.atspi import ATSPIInspector

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

    def _wants_owner(self) -> bool:
        return self.bar is not None or bool(self.cfg.get("menu_bar"))

    def _claim_bar(self) -> None:
        if self._wants_owner() and not self.bar_owner.owned:
            self.bar_owner.try_acquire()
        if self.bar_owner.owned and self.cfg.get("menu_bar") and self.tray is None:
            try:
                self.tray = _Tray(self)
            except Exception as exc:  # noqa: BLE001 - no panel tray on this desktop
                sys.stderr.write(f"pinlet.overlay: no tray icon: {exc}\n")
                self.cfg["menu_bar"] = False
            self._refresh_topbar(force=True)

    # ------------------------------------------------- tray item (topbar)
    def _app_name(self) -> str:
        if not self.track_pid:
            return ""
        try:
            with open(f"/proc/{int(self.track_pid)}/comm", encoding="utf-8") as fh:
                return fh.read().strip()
        except (OSError, ValueError):
            return ""

    def _publish_status(self) -> None:
        if not self.server_pid:
            return
        now = time.monotonic()
        working = self._working()
        mode = topbar.pinlet_mode(
            paused=self.paused,
            takeover=False,
            confirming=self.pending_confirm is not None,
            stopped=now < self.stopped_until,
            working=working,
            awake=self._awake(),
        )
        topbar.publish(
            self.server_pid,
            mode=mode,
            step=self.current_step if working else "",
            steps=self.task_steps if working else 0,
            elapsed=(now - self.task_started) if (working and self.task_started) else 0.0,
            turbo=bool(self.cfg.get("turbo")),
            app=self._app_name() if working else "",
        )
        self.publish_next = now + 5.0

    def topbar_view(self) -> topbar.View:
        """Builds the tray menu's content from the shared agent overview."""
        try:
            from pinlet.control import commands, control_root

            agents = commands.overview(control_root())
        except Exception:  # noqa: BLE001
            agents = []
        hiding = topbar.Hiding.load()
        hiding.prune(a["pid"] for a in agents)
        return topbar.topbar_view(agents, topbar.read_states(), self.server_pid, hiding=hiding)

    def _refresh_topbar(self, force: bool = False) -> None:
        if self.tray is None:
            return
        now = time.monotonic()
        if not force and now < self.tray_next:
            return
        self.tray_next = now + 1.0
        try:
            self.tray.update(self.topbar_view())
        except Exception as exc:  # noqa: BLE001 - never break the helper over the tray
            sys.stderr.write(f"pinlet.overlay: tray: {exc}\n")

    def topbar_action(self, action: str, pid: int) -> None:
        """A tray menu click: Pause / Resume / Stop one Pinlet or all, or Turbo."""
        from pinlet.control import commands, control_root

        root = control_root()
        if action == "turbo":
            self.cfg["turbo"] = not self.cfg.get("turbo")
            self._refresh_bar()
        elif topbar.menu_action(action, view=self.topbar_view(), pid=pid):
            pass  # Clear finished / Show hidden
        elif action == "wake" and pid is not None:
            from pinlet.control import inbox

            if pid == self.server_pid:
                self.wake()
            else:
                inbox.request_wake(root, pid)
        elif action in ("pause_toggle", "stop"):
            if action == "stop" and pid is not None:
                topbar.Hiding.load().hide([pid])  # stopped: out of the list
            if pid == self.server_pid:
                if action == "stop":
                    self.request_stop()
                elif self.paused:
                    self.resume()
                else:
                    self.set_paused(True)
            elif action == "stop":
                commands.request_stop(root, pid)
            else:
                commands.set_paused(root, pid, not commands.read(root, pid)["paused"])
        elif action in ("pause_all", "resume_all", "stop_all"):
            if action == "stop_all":
                topbar.Hiding.load().hide(a["pid"] for a in commands.overview(root))
            for agent in commands.overview(root):
                if agent["pid"] == self.server_pid:
                    if action == "stop_all":
                        self.request_stop()
                    elif self.paused != (action == "pause_all"):
                        self.set_paused(action == "pause_all")
                elif action == "stop_all":
                    commands.request_stop(root, agent["pid"])
                else:
                    commands.set_paused(root, agent["pid"], action == "pause_all")
        self._publish_status()
        self._refresh_topbar(force=True)

    def _refresh_bar(self) -> None:
        self._publish_status()
        if self.bar is None:
            return
        if self.capture_hidden:
            return
        if self.bar.mode == "stopped" and time.monotonic() < self.stopped_until:
            return
        view = bar_view(
            paused=self.paused,
            takeover=False,
            takeover_resume="auto",
            working=self._working(),
            turbo=bool(self.cfg.get("turbo")),
            current_mode=self.bar.mode,
            bar_mode=self.bar_mode,
            owner=self.bar_owner.owned,
            awake=self._awake(),
            ai_waiting=self.ai_waiting > 0,
            handoff=self.handoff,
            other_waiting=self.others_waiting,
            locked=self.lock is not None,
        )
        if view is None:
            self.bar.hide()
            return
        mode, text, primary, secondary, color, dot_alpha = view
        rgb = {
            "ok": _rgb(self.cfg["color"]),
            "caution": _rgb(self.cfg["caution_color"]),
            "danger": _rgb(self.cfg["danger_color"]),
        }.get(color) or self._color()
        if self.style == "classic":
            view = self.voice.dress(view, str(self.cfg["label"]), self.task_steps)
            mode, text, primary, secondary, color, dot_alpha = view
            self.bar.show(mode, text, primary, secondary, rgb, dot_alpha=dot_alpha)
            return
        now = time.monotonic()
        # No hover tracking here: the sleeping bar always shows its full text.
        spec = style.bar_spec(
            view,
            hovered=True,
            label=str(self.cfg["label"]),
            step=self.current_step,
            steps=self.task_steps,
            goal=self.goal,
            elapsed=(now - self.task_started) if self.task_started else 0.0,
            turbo=bool(self.cfg.get("turbo")),
            thinking=self.state == "thinking",
            narrate=self.narrate,
            voice=self.voice,
        )
        self.bar.show_spec(spec, rgb)

    def _refresh_agents(self) -> None:
        """Take this Pinlet's colour from the agent registry (modern look)."""
        if (
            self.style != "modern"
            or not self.server_pid
            or self.cfg.get("color_fixed")
            or not self.cfg.get("agent_colors", True)
        ):
            return
        try:
            from pinlet.control import commands, control_root

            own = style.agent_colors(commands.overview(control_root())).get(self.server_pid)
        except Exception:  # noqa: BLE001 - colour is cosmetic
            return
        if own and own.upper() != str(self.cfg["color"]).upper():
            self.cfg["color"] = own
            self._apply_colors()
            self._refresh_bar()

    def _put_away_when_done(self, now: float) -> None:
        """The task is over: stop "thinking", hide the agent pointer and tag."""
        reset_thinking, hide_pointer = idle_cleanup(
            since_action=now - self.last_action,
            active_secs=float(self.cfg["active_secs"]),
            linger_secs=float(self.cfg.get("linger_secs", 20.0)),
            cursor_state=self.state,
            pointer_visible=bool(self.pointer.get_visible()),
            awake=self._awake(),
            halted=self.paused,
            confirming=self.pending_confirm is not None,
        )
        if reset_thinking:
            self.state = "idle"
            self._layout_tag()
            self._refresh_bar()
        if goal_over(
            self.goal, now - self.last_action, float(self.cfg.get("goal_secs", GOAL_SECS))
        ):
            self.goal = ""  # long quiet: that task is over
            self._refresh_bar()
        if hide_pointer:
            self.pointer.hide()
            self.tag.hide()
            self.cursor = None

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
        if self.voice.refresh_due():
            self._refresh_bar()  # the "Pinlet did it!" cheer is over
        if self._wants_owner() and not self.bar_owner.owned and now >= self.bar_owner_retry:
            self.bar_owner_retry = now + 2.0
            self._claim_bar()
            if self.bar_owner.owned:
                self._refresh_bar()
        if now >= self.publish_next:
            self._publish_status()  # heartbeat, and the timer for the tray's menu
        self._refresh_topbar()
        if self.awake_until and now >= self.awake_until:
            self.sleep()
        self._put_away_when_done(now)
        if self.bar_owner.owned and now >= self.others_checked:
            self.others_checked = now + 1.0
            # Step aside while another helper's AI waits or another Pinlet's bar shows.
            others = not self.ai_waiting and (
                others_waiting(self.wait_dir, os.getpid())
                or others_showing(showing_dir(self.cfg.get("bar_lock", BAR_LOCK)), os.getpid())
            )
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
        self.lock_stopped = False
        self.wake_pointer = False  # Pinlet drives the pointer now
        if self.awake_until:
            self._end_awake()
        if self.bar is not None and self.bar.mode in ("hidden", "ready", "awake"):
            self._refresh_bar()

    def _end_awake(self) -> None:
        self.awake_until = 0.0
        self.handoff = False
        self.send_event("sleep")

    def on_bar_label(self) -> None:
        """Handles a click on the bar's label: wakes or puts Pinlet to sleep."""
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
        """Puts a woken Pinlet back to sleep."""
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
        """Handles a click on one of the bar's buttons."""
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
        """Pauses or resumes Pinlet and tells the server."""
        if paused == self.paused:
            return
        self.paused = paused
        self.send_event("pause" if paused else "resume")
        self._lock_sync()
        self._refresh_bar()
        self._layout_tag()

    def resume(self) -> None:
        """Ends a pause and tells the server."""
        if self.paused:
            self.set_paused(False)
        self._mark_active()
        self._refresh_bar()

    def request_stop(self) -> None:
        """Stops the current task: denies any pending prompt and tells the server."""
        if self.pending_confirm is not None:
            self._answer_confirm(False)
        self.paused = False
        self.lock_stopped = True
        self._lock_sync()
        if self.awake_until:
            self.sleep()
        self.send_event("stop")
        self.send_event("user_idle")
        self._set_intent("Stopped", 1500)
        if self.bar is not None:
            self.bar.show("stopped", "Pinlet stopped", "OK", "Hide", _rgb(self.cfg["danger_color"]))
            self.stopped_until = time.monotonic() + 2.0

    # ------------------------------------------------------------- confirm
    def _start_confirm(
        self,
        msg_id: Any,
        text: str,
        secs: float,
        detail: str = "",
        allow: str = "Allow",
        deny: str = "Deny",
        kind: str = "danger",
    ) -> None:
        if self.cfg.get("turbo"):
            self.ack(msg_id, True)
            return
        if self.pending_confirm is not None:
            self._answer_confirm(False)
        win, countdown = _confirm_window(
            text,
            detail,
            allow or "Allow",
            deny or "Deny",
            kind,
            str(self.cfg["label"]),
            self.cfg["color"] if kind == "access" else self.cfg["danger_color"],
            on_answer=self._answer_confirm,
        )
        deadline = time.monotonic() + max(secs, 1.0)

        def tick() -> bool:
            left = deadline - time.monotonic()
            if left <= 0:
                self._answer_confirm(False)
                return False
            countdown.set_text(f"No answer in {int(left + 0.99)} s counts as {deny or 'Deny'}")
            return True

        tick()
        timer = GLib.timeout_add(250, tick)
        win.show_all()
        win.present()
        self.pending_confirm = (msg_id, win, timer)
        self._lock_sync()  # the person must be able to answer

    def _answer_confirm(self, ok: bool) -> None:
        if self.pending_confirm is None:
            return
        msg_id, win, timer = self.pending_confirm
        self.pending_confirm = None
        GLib.idle_add(lambda: (self._lock_sync(), False)[1])
        try:
            GLib.source_remove(timer)
        except Exception:  # noqa: BLE001 - already fired
            pass
        win.destroy()
        self.ack(msg_id, ok)

    # --------------------------------------------------------------- input
    def handle_line(self, line: str) -> None:
        """Parses and handles one line from the server; bad lines are acknowledged with an error."""
        line = line.strip()
        if not line:
            return
        msg_id = None
        try:
            raw = json.loads(line)
            msg_id = raw.get("id") if isinstance(raw, dict) else None
            self.handle(protocol.validate(raw))
        except Exception as exc:  # noqa: BLE001 - never die on one bad message
            sys.stderr.write(f"pinlet.overlay: {exc}\n")
            self.ack(msg_id, False, str(exc))

    def handle(self, msg: dict[str, Any]) -> None:
        """Handles one decoded command from the server."""
        op, msg_id = msg["op"], msg.get("id")
        if op == "cursor.move":
            self._mark_active()
            target = (float(msg["x"]), float(msg["y"]))
            verb = msg.get("verb")
            if msg.get("probe"):
                name, frame = self._probe_at(*target)
                if (
                    frame is not None
                    and 3 <= frame[2] <= 1600
                    and 3 <= frame[3] <= 900
                    and geometry.contains(frame, target)
                ):
                    self.box.place(frame)
                    if not self._overlay_shown():
                        self.box.hide()
                else:
                    self.box.place(None)
                if verb:
                    self._set_intent(style.action_text(verb, name, msg.get("app")))
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
            if self.state in ("thinking", *ACTING_STATES):
                self._mark_active()
            if self.state in ACTING_STATES and self.cursor is None:
                self._place_pointer(self._real_mouse())
            self._layout_tag()
            self.ack(msg_id)
        elif op == "cursor.hide":
            self.pointer.hide()
            self.tag.hide()
            self.cursor = None
            self.ack(msg_id)
        elif op == "intent":
            if msg["text"]:  # clearing the tag after a step is not a new step
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
        elif op == "goal":
            if msg["text"]:
                self._mark_active()
            self.goal = msg["text"]
            self._refresh_bar()
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
            self._start_confirm(
                msg_id,
                msg["text"],
                float(msg.get("secs", 30.0)),
                detail=str(msg.get("detail") or ""),
                allow=str(msg.get("allow") or "Allow"),
                deny=str(msg.get("deny") or "Deny"),
                kind=str(msg.get("kind") or "danger"),
            )
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
        elif op == "turbo":
            self.cfg["turbo"] = bool(msg["on"])
            self._refresh_bar()
            self.ack(msg_id)
        elif op == "input.pass":
            if self.lock is not None:
                self.lock.set_pass(bool(msg["on"]))
                if not msg["on"]:
                    self._lock_sync()  # the window may have moved or changed
            self.ack(msg_id)
        elif op == "ping":
            self.ack(msg_id)
        elif op == "bye":
            self.ack(msg_id)
            self.quit()

    def _set_intent(self, text: str, ms: float | None = None) -> None:
        if text and text != "Stopped":
            self.task_steps += 1
            self.current_step = text
        self.intent = _short(text) if text else ""
        secs = style.intent_secs(ms, float(self.cfg["intent_ms"]), self.narrate)
        if not text:
            self.intent_until = 0.0
        else:  # None: until the next action, or until Pinlet is idle (see _tag_tick)
            self.intent_until = time.monotonic() + secs if secs is not None else float("inf")
        self._layout_tag()

    def _real_mouse(self) -> tuple[float, float]:
        try:
            seat = Gdk.Display.get_default().get_default_seat()
            _screen, x, y = seat.get_pointer().get_position()
            return (float(x), float(y))
        except Exception:  # noqa: BLE001
            return (0.0, 0.0)

    # ------------------------------------------------------------- hotkeys
    def start_hotkeys(self) -> None:
        """Starts the thread that listens for Pinlet's global hotkeys (e.g. Ctrl+Alt+End to stop)."""
        threading.Thread(target=self._hotkey_loop, name="hotkeys", daemon=True).start()

    def _hotkey_loop(self) -> None:
        try:
            from Xlib import X
            from Xlib import display as xdisplay

            d = xdisplay.Display()
            keys = grab_hotkeys(d, d.screen().root)
            mods = X.ControlMask | X.Mod1Mask
            while True:
                ev = d.next_event()
                if ev.type == X.KeyPress and (ev.state & mods) == mods and ev.detail in keys:
                    GLib.idle_add(self._on_hotkey, keys[ev.detail])
        except Exception as exc:  # noqa: BLE001 - hotkeys are a convenience
            sys.stderr.write(f"pinlet.overlay: hotkeys unavailable: {exc}\n")

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
        """Answers any pending prompt with Deny, releases the input lock and quits GTK."""
        if self.pending_confirm is not None:
            self._answer_confirm(False)
        if self.lock is not None:
            self.lock.close()
            self.lock = None
        if self.tray is not None:
            self.tray.close()
            self.tray = None
        if self.server_pid:
            topbar.clear(self.server_pid)
        self.bar_owner.release()
        mark_waiting(self.wait_dir, os.getpid(), False)
        if self.bar is not None:
            self.bar.hide()  # frees its row and "showing" marker
        Gtk.main_quit()
        return False


def grab_hotkeys(d, root) -> dict[int, str]:
    """Grab Ctrl+Alt+<key> for each action in ``_HOTKEYS``; returns {keycode: action}.

    A key another client already grabbed (BadAccess) is skipped for the next candidate,
    and ``HOTKEY_LABELS`` records the combo that is live.
    """
    from Xlib import XK, X, error

    mods = X.ControlMask | X.Mod1Mask
    # Also grab with NumLock (Mod2) and CapsLock (Lock) on, which X counts as modifiers.
    extras = (0, X.Mod2Mask, X.LockMask, X.Mod2Mask | X.LockMask)
    keys: dict[int, str] = {}
    for action, names in _HOTKEYS.items():
        for name in names:
            code = d.keysym_to_keycode(XK.string_to_keysym(name))
            if not code:
                continue
            catch = error.CatchError(error.BadAccess)
            for extra in extras:
                root.grab_key(
                    code, mods | extra, True, X.GrabModeAsync, X.GrabModeAsync, onerror=catch
                )
            d.sync()
            if catch.get_error() is None:
                keys[code] = action
                HOTKEY_LABELS[action] = "Ctrl+Alt+" + {"Escape": "Esc"}.get(name, name.capitalize())
                break
            for extra in extras:
                root.ungrab_key(code, mods | extra)
            d.sync()
            sys.stderr.write(f"pinlet.overlay: Ctrl+Alt+{name} is taken by another app\n")
    if "stop" in HOTKEY_LABELS:
        sys.stderr.write(f"pinlet.overlay: {HOTKEY_LABELS['stop']} stops Pinlet\n")
    return keys


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

    GLib.io_add_watch(
        sys.stdin.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, on_input
    )


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


def main(argv: list[str] | None = None) -> int:
    """Runs the helper: reads the config, builds the overlay and starts the GTK loop."""
    argv = list(argv or [])
    cfg: dict[str, Any] = {}
    raw = os.environ.get("PINLET_OVERLAY_CONFIG")
    if raw:
        try:
            cfg = json.loads(raw)
        except json.JSONDecodeError:
            cfg = {}
    if not Gtk.init_check(sys.argv)[0]:
        sys.stderr.write("pinlet.overlay: cannot open the X display\n")
        return 2
    overlay = X11Overlay(cfg)
    if "--demo" in argv:
        _demo(overlay)
    else:
        _stdin_watch(overlay)
        overlay.start_hotkeys()
        from pinlet.parentwatch import watch_parent

        watch_parent(lambda: GLib.idle_add(overlay.quit))  # server died, pipe held elsewhere
    overlay.emit({"ready": True, "version": protocol.PROTOCOL_VERSION, "pid": os.getpid()})
    Gtk.main()
    return 0
