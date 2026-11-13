"""Look of the overlay.

Per-Pinlet colours, the top bar's layout, glow states, step trail and name tag
placement.

Pure data (no AppKit, no GTK) so every decision can be tested anywhere; the
helpers (``mac_helper``, ``x11_helper``) only draw what these functions say.

``style`` is "modern" (default) or "classic" (the look before this module:
text buttons, wide sleeping bar, numbered trail). ``PINLET_OVERLAY_STYLE``
picks it.

``narrate`` (modern only, on by default; ``PINLET_OVERLAY_NARRATE=0`` turns it
off) says in words what Pinlet does: "Pinlet is working · Click ‘Send’ · …" on
the bar, the current action on the pointer tag until the next one, numbered
trail marks, and Pinlet dots only while two or more Pinlets are busy.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..envflags import FALSE
from .text import short as _short

if TYPE_CHECKING:  # pragma: no cover
    from pinlet.personality import Voice

Rect = tuple[float, float, float, float]  # x, y, w, h (global, top-left origin)
Point = tuple[float, float]

STYLES = ("modern", "classic")

# One colour per running Pinlet, in the order they start. Pinlet cyan first so
# a single Pinlet looks like the brand. No amber or red: those mean
# caution/danger. Palette "Frost" (chosen 2026-11-08, replacing "Elf Violet,
# tuned"). Violet takes the second slot so it stays far from the cyan; no
# teal, which would look like a second cyan.
AGENT_PALETTE: tuple[str, ...] = ("#0EA5C6", "#7C5CFF", "#EC4899", "#3B82F6", "#84CC16", "#B07A4F")

# Filled buttons (Take over, Allow) and the pointer tag: white text on
# #0EA5C6 is only 2.9:1, so fills use the deeper #0E7490 (5.4:1).
FILL_COLOR = "#0E7490"
FILL_TITLES = frozenset({"Take over", "Allow"})

# The bar follows the desktop's light or dark theme.
BAR_THEMES: dict[str, dict[str, object]] = {
    "light": {
        "bg": (247, 250, 252),
        "bg_alpha": 0.94,
        "text": "#0F1720",
        "dim": "#46525F",
        "border": (14, 165, 198, 0.28),
        "accent": "#0EA5C6",
        "hover": (0, 0, 0, 0.07),
        "button": "#E6EDF3",
    },
    "dark": {
        "bg": (20, 26, 34),
        "bg_alpha": 0.92,
        "text": "#F1F6FA",
        "dim": "#C2CCD6",
        "border": (103, 232, 249, 0.35),
        "accent": "#67E8F9",
        "hover": (255, 255, 255, 0.16),
        "button": "#2E3945",
    },
}


def bar_theme(dark: bool) -> dict[str, object]:
    """Colours and materials of the bar for the dark or light appearance."""
    return BAR_THEMES["dark" if dark else "light"]


# Light or dark bar: "dark" (default, the Pinlet look from the design system),
# "light", or "auto" to follow the desktop. ``PINLET_OVERLAY_THEME`` picks it.
THEMES = ("dark", "light", "auto")


def normalize_theme(value: object) -> str:
    """One of :data:`THEMES`; anything else is the default, ``"dark"``."""
    value = str(value or "").strip().lower()
    return value if value in THEMES else "dark"


def overlay_dark(theme: object, desktop_dark: bool) -> bool:
    """Whether the overlay draws its dark look for this theme setting and desktop."""
    theme = normalize_theme(theme)
    if theme == "auto":
        return bool(desktop_dark)
    return theme == "dark"


# Liquid Glass (macOS 26+). "auto" (default): clear glass while the bar is the
# empty sleeping chip over the desktop, regular glass as soon as it carries text
# to read (Apple: clear only over rich backgrounds with little content).
# "clear" or "regular" pin one style; "off" draws the frosted bar instead.
GLASS_CHOICES = ("auto", "clear", "regular", "off")

# Palette tint over the glass, by (appearance, glass style). Regular glass with
# narration gets a stronger tint so the words read over a bright desktop.
GLASS_TINT = {
    ("dark", "clear"): 0.18,
    ("dark", "regular"): 0.34,
    ("light", "clear"): 0.10,
    ("light", "regular"): 0.18,
}


def normalize_glass(value: object) -> str:
    """One of :data:`GLASS_CHOICES` (``0``/``false``/``no`` mean ``"off"``); default ``"auto"``."""
    value = str(value or "").strip().lower()
    if value in FALSE:
        return "off"
    return value if value in GLASS_CHOICES else "auto"


def glass_style(choice: object, compact: bool) -> str:
    """``"clear"`` or ``"regular"`` glass for a bar; ``compact`` is the sleeping chip."""
    choice = normalize_glass(choice)
    if choice in ("clear", "regular"):
        return choice
    return "clear" if compact else "regular"


def glass_tint(dark: bool, glass: str) -> float:
    """Opacity of the palette tint laid over the glass."""
    return GLASS_TINT[("dark" if dark else "light", "regular" if glass == "regular" else "clear")]


def glass_rim(dark: bool, compact: bool) -> float:
    """Opacity of the thin Pinlet-colour rim over the glass: none on the sleeping chip."""
    if compact:
        return 0.0
    return 0.35 if dark else 0.28


def hex_rgb01(value: str) -> tuple[float, float, float]:
    """Converts ``#RRGGBB`` to an ``(r, g, b)`` tuple of floats in 0..1."""
    v = value.lstrip("#")
    return tuple(int(v[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def is_filled(button: Button) -> bool:
    """Take over and Allow are filled #0E7490 buttons with white text."""
    return button.role == "accent" or button.title in FILL_TITLES


def normalize_style(value: str | None) -> str:
    """A known overlay style name, defaulting to ``"modern"``."""
    value = (value or "").strip().lower()
    return value if value in STYLES else "modern"


# ------------------------------------------------------------ agent colours
def pick_agent_color(taken: Iterable[str], palette: Sequence[str] = AGENT_PALETTE) -> str:
    """First palette colour no running Pinlet uses yet (wraps when all are taken)."""
    used = [str(c).upper() for c in taken if c]
    for colour in palette:
        if colour.upper() not in used:
            return colour
    # Everything taken: the least used one, earliest in the palette.
    return min(palette, key=lambda c: (used.count(c.upper()), palette.index(c)))


def agent_colors(
    agents: Iterable[Mapping[str, object]], palette: Sequence[str] = AGENT_PALETTE
) -> dict[int, str]:
    """Pid -> colour for each running Pinlet.

    A colour stored in the agent's registry record wins; older records
    without one get palette colours by start order, skipping stored ones.
    """
    agents = sorted(
        agents, key=lambda a: (float(a.get("started", 0) or 0), int(a.get("pid", 0) or 0))
    )
    out: dict[int, str] = {}
    for a in agents:
        stored = a.get("color")
        if isinstance(stored, str) and stored.startswith("#"):
            out[int(a.get("pid", 0) or 0)] = stored
    for a in agents:
        pid = int(a.get("pid", 0) or 0)
        if pid not in out:
            out[pid] = pick_agent_color(out.values(), palette)
    return out


def tag_label(
    base: str, own_name: str | None, agent_count: int, default_label: str = "Pinlet"
) -> str:
    """Name on the pointer tag. With two or more Pinlets, say which one this is."""
    if agent_count < 2 or base != default_label or not own_name:
        return base
    name = " ".join(str(own_name).split())
    if not name or name.lower() == base.lower() or name.lower().startswith("pinlet-"):
        return base
    if len(name) > 18:
        name = name[:17] + "…"
    return f"{base} · {name}"


# ------------------------------------------------------------------ top bar
def fmt_elapsed(secs: float) -> str:
    """0:07, 1:42, 1:02:03."""
    secs = max(0, int(secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def working_text(
    step: str,
    steps: int,
    elapsed: float,
    turbo: bool = False,
    limit: int = 38,
    label: str | None = None,
    thinking: bool = False,
    lead: str | None = None,
) -> str:
    """What the bar says while Pinlet works: "Click ‘Send’ · step 7 · 0:42".

    With ``label`` (narration) it starts with a sentence:
    "Pinlet is working · Click ‘Send’ · step 7 · 0:42", or "Pinlet is thinking…".
    ``lead`` (Pinlet's personality) replaces that sentence, or "Working" when
    there is no step yet; the step, its count and the timer always stay.
    """
    if label:
        parts = [lead or (f"{label} is thinking…" if thinking else f"{label} is working")]
        if step:
            parts.append(_short(step, limit))
    else:
        parts = [_short(step, limit) if step else (lead or "Working")]
    if steps > 0:
        parts.append(f"step {steps}")
    parts.append(fmt_elapsed(elapsed))
    text = " · ".join(parts)
    return ("⚡ Turbo · " + text) if turbo else text


@dataclass(frozen=True)
class Button:
    """One button on the bar: its key, title and optional SF Symbol icon."""

    key: str  # "primary" | "secondary" | "hide" | "menu"
    title: str  # text title, used when there is no icon (or no SF Symbols)
    icon: str | None = None  # SF Symbol name
    tip: str = ""
    role: str = "normal"  # "normal" | "danger" (stop, deny) | "accent" (allow)


@dataclass(frozen=True)
class BarSpec:
    """What the bar shows in one mode: its text and buttons."""

    mode: str
    text: str
    buttons: tuple[Button, ...] = field(default_factory=tuple)
    color: str = "ok"  # colour key, as in menu.bar_view
    dot_alpha: float = 1.0
    compact: bool = False
    shimmer: bool = False  # soft light sweeping over the bar (Pinlet thinking)
    tip: str | None = None


_ICONS = {
    "Pause": ("pause.fill", "Pause Pinlet (⌃⌥P)"),
    "Take over": ("hand.raised.fill", "Take over: pause Pinlet and use this window (⌃⌥P)"),
    "Resume": ("play.fill", "Resume (⌃⌥P)"),
    "Resume now": ("play.fill", "Resume now"),
    "Stop": ("stop.fill", "Stop Pinlet (⌃⌥⎋)"),
}
_HIDE = Button("hide", "–", "minus", "Hide this bar (⌃⌥H shows it again)")
_MENU = Button("menu", "▾", "chevron.down", "Pinlet menu")


def _action_button(key: str, title: str, style: str) -> Button:
    role = "danger" if title in ("Stop", "Deny") else ("accent" if title == "Allow" else "normal")
    icon, tip = _ICONS.get(title, (None, ""))
    if style == "classic":
        icon = None
    return Button(key, title, icon, tip, role)


def bar_spec(
    view: tuple | None,
    *,
    style: str = "modern",
    hovered: bool = False,
    label: str = "Pinlet",
    step: str = "",
    steps: int = 0,
    elapsed: float = 0.0,
    turbo: bool = False,
    thinking: bool = False,
    narrate: bool = True,
    voice: Voice | None = None,
) -> BarSpec | None:
    """Turn ``menu.bar_view``'s answer into what the bar draws.

    Modern: the sleeping bar is a small capsule (dot + name) that grows on
    hover; nothing running means no Pause/Stop (they stay in the ▾ menu);
    working shows the live step, step count and a timer; icon buttons.
    Classic: exactly the old text-button bar.
    ``narrate``: the compact bar says "Pinlet is sleeping" and the working
    bar starts with "Pinlet is working" / "Pinlet is thinking…".
    ``voice`` (``pinlet.personality``): Pinlet's own words for the sleeping,
    working, thinking, paused and stopped bars, and a cheer when a task ends.
    """
    if voice is not None:
        view = voice.dress(view, label, steps)
    if view is None:
        return None
    mode, text, primary, secondary, color, dot_alpha = view
    talk = voice is not None and voice.on
    if style == "classic":
        buttons = (
            _action_button("primary", primary, style),
            _action_button("secondary", secondary, style),
            Button("hide", "–", None, _HIDE.tip),
            Button("menu", "▾", "chevron.down", _MENU.tip),
        )
        return BarSpec(mode, text, buttons, color, dot_alpha)
    if mode == "ready":
        # An AI waiting in wait_for_wake: the bar already says "click to wake".
        waiting = "click to wake" in text
        if not hovered:
            name = f"{label} is sleeping" if narrate else label
            if talk:
                cheer = voice.done(label)
                if cheer:
                    name = cheer
                elif narrate:
                    name = voice.line("sleeping", name, label, rotate=False)
            short = f"{name} · click to wake" if waiting else name
            return BarSpec(
                mode,
                short,
                (),
                color,
                1.0 if waiting else dot_alpha,
                compact=True,
                tip="Click to wake Pinlet",
            )
        sleeping = (
            text.replace("Pinlet is sleeping", f"{label} is sleeping")
            if label != "Pinlet"
            else text
        )
        if not waiting:
            sleeping += " · click to wake"
        return BarSpec(mode, sleeping, (_HIDE, _MENU), color, dot_alpha, tip="Click to wake Pinlet")
    if mode == "awake":
        return BarSpec(
            mode, text, (_HIDE, _MENU), color, dot_alpha, tip="Click to put Pinlet to sleep"
        )
    if mode == "working":
        lead = None
        if talk:
            lead = voice.egg(steps=steps, elapsed=elapsed, name=label)
            if lead is None and narrate and thinking:
                lead = voice.line("thinking", f"{label} is thinking…", label)
            elif lead is None:
                lead = voice.line("working", f"{label} is working" if narrate else "Working", label)
        if narrate:
            live = working_text(
                step, steps, elapsed, turbo, limit=32, label=label, thinking=thinking, lead=lead
            )
        else:
            live = working_text(step, steps, elapsed, turbo, lead=lead)
        buttons = (
            _action_button("primary", primary, style),
            _action_button("secondary", secondary, style),
            _HIDE,
            _MENU,
        )
        return BarSpec(mode, live, buttons, color, dot_alpha, shimmer=thinking)
    if mode in ("paused", "takeover"):
        buttons = (
            _action_button("primary", primary, style),
            _action_button("secondary", secondary, style),
            _HIDE,
            _MENU,
        )
        return BarSpec(mode, text, buttons, color, dot_alpha)
    # stopped (OK / Hide) and anything else: plain text buttons
    buttons = (Button("primary", primary), Button("secondary", secondary))
    return BarSpec(mode, text, buttons + (_HIDE, _MENU), color, dot_alpha)


def app_label(app: str | None) -> str:
    """A short display name for an app: "Vivado", not "vivado.bin" or "com.apple.Mail"."""
    name = " ".join(str(app or "").split())
    if not name:
        return ""
    low = name.lower()
    for suffix in (".app", ".bin", ".exe", "-bin", "-server"):
        if low.endswith(suffix) and len(name) > len(suffix):
            name, low = name[: -len(suffix)], low[: -len(suffix)]
    if "." in name and " " not in name and name.count(".") >= 2:  # bundle id
        name = name.rsplit(".", 1)[-1]
    if name.islower():
        name = name[:1].upper() + name[1:]
    return _short(name, 18)


def action_text(verb: str, name: str | None = None, app: str | None = None) -> str:
    """What Pinlet does, and to what: "Click ‘Run Synthesis’ in Vivado".

    The element's name comes from the accessibility hit test, the app from
    the window under the point. Either may be missing.
    """
    text = f"{verb} ‘{_short(name, 32)}’" if name else verb
    where = app_label(app)
    return f"{text} in {where}" if where else text


def confirm_spec(
    text: str, allow: str = "Allow", deny: str = "Deny", kind: str = "danger", detail: str = ""
) -> BarSpec:
    """An Allow/Deny prompt: words, not icons, and no way to hide it.

    ``kind`` "danger" is a risky step (red); "access" asks whether Pinlet may
    use an app at all, so it uses Pinlet's own colour, not alarm red.
    ``detail`` is the longer explanation, shown as the bar's tooltip.
    """
    return BarSpec(
        "confirm",
        text,
        (
            Button("primary", allow or "Allow", None, "", "accent"),
            Button("secondary", deny or "Deny", None, "", "danger"),
        ),
        "ok" if kind == "access" else "danger",
        1.0,
        tip=detail or None,
    )


def agent_busy(agent: Mapping[str, object]) -> bool:
    """Doing something now: holding control, waiting for it, or paused mid-task."""
    return bool(agent.get("holding") or agent.get("waiting") or agent.get("paused"))


def agent_dots(
    agents: Iterable[Mapping[str, object]],
    colors: Mapping[int, str],
    own_pid: int,
    busy_only: bool = False,
) -> list[tuple[int, str, bool, bool]]:
    """(pid, colour, holding control, is this bar's Pinlet) for the bar's Pinlet dots.

    Empty with fewer than two Pinlets: one Pinlet needs no picker.
    ``busy_only`` (narration): count only Pinlets that are doing something, so
    idle servers (one per AI app that loaded Pinlet) add no dots. They stay in
    the ▾ menu's picker.
    """
    agents = list(agents)
    if busy_only:
        agents = [a for a in agents if agent_busy(a)]
    if len(agents) < 2:
        return []
    out = []
    for a in sorted(
        agents, key=lambda a: (float(a.get("started", 0) or 0), int(a.get("pid", 0) or 0))
    ):
        pid = int(a.get("pid", 0) or 0)
        out.append((pid, colors.get(pid, AGENT_PALETTE[0]), bool(a.get("holding")), pid == own_pid))
    return out


def dots_caption(dots: Sequence[object]) -> str:
    """Words next to the Pinlet dots, so they are not a mystery: "2 Pinlets"."""
    return f"{len(dots)} Pinlets" if len(dots) >= 2 else ""


# ------------------------------------------------------------- pointer tag
def intent_secs(ms: float | None, default_ms: float, narrate: bool) -> float | None:
    """How long the tag shows an action. None: until the next action or until Pinlet is idle.

    An explicit ``ms`` (a tool's ``step()``, a clear) always wins.
    """
    if ms is not None:
        return max(0.0, float(ms)) / 1000.0
    if narrate:
        return None
    return float(default_ms) / 1000.0


def tag_detail(intent: str, state: str, narrate: bool) -> str:
    """Text after "Pinlet ›" on the pointer tag: the action, or "Thinking…" between actions."""
    if narrate and state == "thinking":
        return "Thinking…"
    return intent


# --------------------------------------------------------------------- glow
def glow_state(*, paused: bool, takeover: bool, working: bool, thinking: bool, risk: str) -> str:
    """Returns the glow state: "risk", "halted", "thinking", "acting" or "idle"."""
    if risk == "danger":
        return "risk"
    if paused or takeover:
        return "halted"
    if thinking:
        return "thinking"
    if working:
        return "acting"
    return "idle"


def glow_color_key(state: str, risk: str) -> str:
    """Colour key for a glow state: halted → caution, risk → danger, else the risk level's."""
    if state == "halted":
        return "caution"
    if state == "risk":
        return "danger"
    return risk if risk in ("caution", "danger") else "ok"


def glow_opacity(state: str, dim: bool) -> float:
    """Idle fades away; a window mostly covered by others gets a faint glow."""
    if state == "idle":
        return 0.0
    return 0.3 if dim else 1.0


def corner_radius(
    rect: Rect, screens: Iterable[Rect], os_major: int, configured: object = "auto"
) -> float:
    """Corner radius for the glow around ``rect``.

    A number in the config wins. "auto": 0 for a window that fills its screen
    (full screen or zoomed edge to edge), the system window radius otherwise:
    about 16 pt on macOS 26+ (larger, rounder windows), 10 pt before.
    """
    if not isinstance(configured, str):
        try:
            return max(0.0, float(configured))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            pass
    x, y, w, h = rect
    for sx, sy, sw, sh in screens:
        if abs(x - sx) <= 1 and abs(w - sw) <= 1 and y <= sy + 40 and abs((y + h) - (sy + sh)) <= 1:
            return 0.0
    return 16.0 if os_major >= 26 else 10.0


# -------------------------------------------------------------------- trail
def trail_view(
    points: Sequence[tuple[int, Point, float]],
    now: float,
    style: str = "modern",
    keep: int = 3,
    fade_secs: float = 10.0,
    numbered: bool = False,
) -> list[tuple[int | None, Point, float, float, float]]:
    """(number or None, point, opacity, diameter, seconds left) per trail mark, oldest first.

    ``points`` are (step number, point, time added). Modern: the last
    ``keep`` clicks as small unnumbered dots that fade out over ``fade_secs``
    (seconds left says how long the fade still runs); ``numbered``
    (narration) gives them their step number in a 16 pt badge, matching the
    bar's "step 7". Classic: the last
    ``keep`` numbered badges, older ones fainter, never fading.
    """
    if style == "classic":
        pts = list(points)[-keep:] if keep > 0 else []
        n = len(pts)
        return [
            (num, pt, max(0.2, 0.85 - 0.17 * (n - 1 - i)), 18.0, 0.0)
            for i, (num, pt, _t) in enumerate(pts)
        ]
    out = []
    pts = [p for p in points if now - p[2] < fade_secs]
    pts = pts[-keep:] if keep > 0 else []
    n = len(pts)
    for i, (num, pt, t) in enumerate(pts):
        age = n - 1 - i
        left = max(0.0, fade_secs - (now - t))
        life = left / fade_secs if fade_secs > 0 else 0.0
        opacity = round(max(0.15, 0.7 - 0.2 * age) * life, 3)
        if opacity > 0.01:
            if numbered:
                badge = round(max(0.45, 0.9 - 0.2 * age) * life, 3)  # text must stay readable
                out.append((num, pt, badge, 16.0, round(left, 3)))
            else:
                out.append((None, pt, opacity, 8.0, round(left, 3)))
    return out


# --------------------------------------------------------------- name tag
def tag_flip(
    tip: Point,
    tag_w: float,
    tag_h: float,
    arrow_w: float,
    arrow_h: float,
    screen: Rect | None,
    gap: float = 4.0,
    margin: float = 6.0,
) -> tuple[bool, bool]:
    """(flip left, flip up) so the tag stays on the pointer's screen.

    Normally the tag sits below-right of the tip. Near the right edge it goes
    to the left of the tip; near the bottom edge it goes above the tip.
    """
    if screen is None:
        return (False, False)
    sx, sy, sw, sh = screen
    right = tip[0] + arrow_w - gap + tag_w
    bottom = tip[1] + arrow_h + gap + tag_h
    left = right > sx + sw - margin and tip[0] - tag_w - gap >= sx
    up = bottom > sy + sh - margin and tip[1] - tag_h - gap >= sy
    return (left, up)


def tag_origin(
    tip: Point,
    tag_w: float,
    tag_h: float,
    arrow_w: float,
    arrow_h: float,
    flip: tuple[bool, bool],
    gap: float = 4.0,
) -> Point:
    """Global top-left of the tag for a given flip (matches ``geometry.chip_rect`` unflipped)."""
    left, up = flip
    x = tip[0] - tag_w - gap if left else tip[0] + arrow_w - gap
    y = tip[1] - tag_h - gap if up else tip[1] + arrow_h + gap
    return (x, y)
