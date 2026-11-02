"""Look of the overlay: per-Dobby colours, the top bar's layout, glow states,
step trail and name tag placement.

Pure data (no AppKit, no GTK) so every decision can be tested anywhere; the
helpers (``mac_helper``, ``x11_helper``) only draw what these functions say.

``style`` is "modern" (default) or "classic" (the look before this module:
text buttons, wide sleeping bar, numbered trail). ``DOBBY_OVERLAY_STYLE``
picks it.

``narrate`` (modern only, on by default; ``DOBBY_OVERLAY_NARRATE=0`` turns it
off) says in words what Dobby does: "Dobby is working · Click ‘Send’ · …" on
the bar, the current action on the pointer tag until the next one, numbered
trail marks, and Dobby dots only while two or more Dobbys are busy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

if TYPE_CHECKING:  # pragma: no cover
    from dobby.personality import Voice

Rect = Tuple[float, float, float, float]  # x, y, w, h (global, top-left origin)
Point = Tuple[float, float]

STYLES = ("modern", "classic")

# One colour per running Dobby, in the order they start. Violet first so a
# single Dobby looks as before. No amber or red: those mean caution/danger.
AGENT_PALETTE: Tuple[str, ...] = ("#7C5CFF", "#14B8A6", "#EC4899", "#3B82F6", "#84CC16", "#A855F7")


def normalize_style(value: Optional[str]) -> str:
    value = (value or "").strip().lower()
    return value if value in STYLES else "modern"


# ------------------------------------------------------------ agent colours
def pick_agent_color(taken: Iterable[str], palette: Sequence[str] = AGENT_PALETTE) -> str:
    """First palette colour no running Dobby uses yet (wraps when all are taken)."""
    used = [str(c).upper() for c in taken if c]
    for colour in palette:
        if colour.upper() not in used:
            return colour
    # Everything taken: the least used one, earliest in the palette.
    return min(palette, key=lambda c: (used.count(c.upper()), palette.index(c)))


def agent_colors(agents: Iterable[Mapping[str, object]],
                 palette: Sequence[str] = AGENT_PALETTE) -> Dict[int, str]:
    """pid -> colour for each running Dobby.

    A colour stored in the agent's registry record wins; older records
    without one get palette colours by start order, skipping stored ones.
    """
    agents = sorted(agents, key=lambda a: (float(a.get("started", 0) or 0), int(a.get("pid", 0) or 0)))
    out: Dict[int, str] = {}
    for a in agents:
        stored = a.get("color")
        if isinstance(stored, str) and stored.startswith("#"):
            out[int(a.get("pid", 0) or 0)] = stored
    for a in agents:
        pid = int(a.get("pid", 0) or 0)
        if pid not in out:
            out[pid] = pick_agent_color(out.values(), palette)
    return out


def tag_label(base: str, own_name: Optional[str], agent_count: int, default_label: str = "Dobby") -> str:
    """Name on the pointer tag. With two or more Dobbys, say which one this is."""
    if agent_count < 2 or base != default_label or not own_name:
        return base
    name = " ".join(str(own_name).split())
    if not name or name.lower() == base.lower() or name.lower().startswith("dobby-"):
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


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def working_text(step: str, steps: int, elapsed: float, turbo: bool = False, limit: int = 38,
                 label: Optional[str] = None, thinking: bool = False, lead: Optional[str] = None) -> str:
    """What the bar says while Dobby works: "Click ‘Send’ · step 7 · 0:42".

    With ``label`` (narration) it starts with a sentence:
    "Dobby is working · Click ‘Send’ · step 7 · 0:42", or "Dobby is thinking…".
    ``lead`` (Dobby's personality) replaces that sentence, or "Working" when
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
    key: str  # "primary" | "secondary" | "hide" | "menu"
    title: str  # text title, used when there is no icon (or no SF Symbols)
    icon: Optional[str] = None  # SF Symbol name
    tip: str = ""
    role: str = "normal"  # "normal" | "danger" (stop, deny) | "accent" (allow)


@dataclass(frozen=True)
class BarSpec:
    mode: str
    text: str
    buttons: Tuple[Button, ...] = field(default_factory=tuple)
    color: str = "ok"  # colour key, as in menu.bar_view
    dot_alpha: float = 1.0
    compact: bool = False
    shimmer: bool = False  # soft light sweeping over the bar (Dobby thinking)
    tip: Optional[str] = None


_ICONS = {
    "Pause": ("pause.fill", "Pause Dobby (⌃⌥P)"),
    "Resume": ("play.fill", "Resume (⌃⌥P)"),
    "Resume now": ("play.fill", "Resume now"),
    "Stop": ("stop.fill", "Stop Dobby (⌃⌥⎋)"),
}
_HIDE = Button("hide", "–", "minus", "Hide this bar (⌃⌥H shows it again)")
_MENU = Button("menu", "▾", "chevron.down", "Dobby menu")


def _action_button(key: str, title: str, style: str) -> Button:
    role = "danger" if title in ("Stop", "Deny") else ("accent" if title == "Allow" else "normal")
    icon, tip = _ICONS.get(title, (None, ""))
    if style == "classic":
        icon = None
    return Button(key, title, icon, tip, role)


def bar_spec(view: Optional[tuple], *, style: str = "modern", hovered: bool = False, label: str = "Dobby",
             step: str = "", steps: int = 0, elapsed: float = 0.0, turbo: bool = False,
             thinking: bool = False, narrate: bool = True, voice: Optional["Voice"] = None) -> Optional[BarSpec]:
    """Turn ``menu.bar_view``'s answer into what the bar draws.

    Modern: the sleeping bar is a small capsule (dot + name) that grows on
    hover; nothing running means no Pause/Stop (they stay in the ▾ menu);
    working shows the live step, step count and a timer; icon buttons.
    Classic: exactly the old text-button bar.
    ``narrate``: the compact bar says "Dobby is sleeping" and the working
    bar starts with "Dobby is working" / "Dobby is thinking…".
    ``voice`` (``dobby.personality``): Dobby's own words for the sleeping,
    working, thinking, paused and stopped bars, and a cheer when a task ends.
    """
    if voice is not None:
        view = voice.dress(view, label, steps)
    if view is None:
        return None
    mode, text, primary, secondary, color, dot_alpha = view
    talk = voice is not None and voice.on
    if style == "classic":
        buttons = (_action_button("primary", primary, style), _action_button("secondary", secondary, style),
                   Button("hide", "–", None, _HIDE.tip), Button("menu", "▾", "chevron.down", _MENU.tip))
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
            return BarSpec(mode, short, (), color, 1.0 if waiting else dot_alpha, compact=True,
                           tip="Click to wake Dobby")
        sleeping = text.replace("Dobby is sleeping", f"{label} is sleeping") if label != "Dobby" else text
        if not waiting:
            sleeping += " · click to wake"
        return BarSpec(mode, sleeping, (_HIDE, _MENU), color, dot_alpha, tip="Click to wake Dobby")
    if mode == "awake":
        return BarSpec(mode, text, (_HIDE, _MENU), color, dot_alpha, tip="Click to put Dobby to sleep")
    if mode == "working":
        lead = None
        if talk:
            lead = voice.egg(steps=steps, elapsed=elapsed, name=label)
            if lead is None and narrate and thinking:
                lead = voice.line("thinking", f"{label} is thinking…", label)
            elif lead is None:
                lead = voice.line("working", f"{label} is working" if narrate else "Working", label)
        if narrate:
            live = working_text(step, steps, elapsed, turbo, limit=32, label=label, thinking=thinking, lead=lead)
        else:
            live = working_text(step, steps, elapsed, turbo, lead=lead)
        buttons = (_action_button("primary", primary, style), _action_button("secondary", secondary, style),
                   _HIDE, _MENU)
        return BarSpec(mode, live, buttons, color, dot_alpha, shimmer=thinking)
    if mode in ("paused", "takeover"):
        buttons = (_action_button("primary", primary, style), _action_button("secondary", secondary, style),
                   _HIDE, _MENU)
        return BarSpec(mode, text, buttons, color, dot_alpha)
    # stopped (OK / Hide) and anything else: plain text buttons
    buttons = (Button("primary", primary), Button("secondary", secondary))
    return BarSpec(mode, text, buttons + (_HIDE, _MENU), color, dot_alpha)


def confirm_spec(text: str) -> BarSpec:
    """An Allow/Deny prompt: words, not icons, and no way to hide it."""
    return BarSpec("confirm", text, (Button("primary", "Allow", None, "", "accent"),
                                     Button("secondary", "Deny", None, "", "danger")), "danger", 1.0)


def agent_busy(agent: Mapping[str, object]) -> bool:
    """Doing something now: holding control, waiting for it, or paused mid-task."""
    return bool(agent.get("holding") or agent.get("waiting") or agent.get("paused"))


def agent_dots(agents: Iterable[Mapping[str, object]], colors: Mapping[int, str], own_pid: int,
               busy_only: bool = False) -> List[Tuple[int, str, bool, bool]]:
    """(pid, colour, holding control, is this bar's Dobby) for the bar's Dobby dots.

    Empty with fewer than two Dobbys: one Dobby needs no picker.
    ``busy_only`` (narration): count only Dobbys that are doing something, so
    idle servers (one per AI app that loaded Dobby) add no dots. They stay in
    the ▾ menu's picker.
    """
    agents = list(agents)
    if busy_only:
        agents = [a for a in agents if agent_busy(a)]
    if len(agents) < 2:
        return []
    out = []
    for a in sorted(agents, key=lambda a: (float(a.get("started", 0) or 0), int(a.get("pid", 0) or 0))):
        pid = int(a.get("pid", 0) or 0)
        out.append((pid, colors.get(pid, AGENT_PALETTE[0]), bool(a.get("holding")), pid == own_pid))
    return out


def dots_caption(dots: Sequence[object]) -> str:
    """Words next to the Dobby dots, so they are not a mystery: "2 Dobbys"."""
    return f"{len(dots)} Dobbys" if len(dots) >= 2 else ""


# ------------------------------------------------------------- pointer tag
def intent_secs(ms: Optional[float], default_ms: float, narrate: bool) -> Optional[float]:
    """How long the tag shows an action. None: until the next action or until Dobby is idle.

    An explicit ``ms`` (a tool's ``step()``, a clear) always wins.
    """
    if ms is not None:
        return max(0.0, float(ms)) / 1000.0
    if narrate:
        return None
    return float(default_ms) / 1000.0


def tag_detail(intent: str, state: str, narrate: bool) -> str:
    """Text after "Dobby ›" on the pointer tag: the action, or "Thinking…" between actions."""
    if narrate and state == "thinking":
        return "Thinking…"
    return intent


# --------------------------------------------------------------------- glow
def glow_state(*, paused: bool, takeover: bool, working: bool, thinking: bool, risk: str) -> str:
    """"risk" | "halted" | "thinking" | "acting" | "idle"."""
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


def corner_radius(rect: Rect, screens: Iterable[Rect], os_major: int, configured: object = "auto") -> float:
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
def trail_view(points: Sequence[Tuple[int, Point, float]], now: float, style: str = "modern",
               keep: int = 3, fade_secs: float = 10.0,
               numbered: bool = False) -> List[Tuple[Optional[int], Point, float, float, float]]:
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
        return [(num, pt, max(0.2, 0.85 - 0.17 * (n - 1 - i)), 18.0, 0.0)
                for i, (num, pt, _t) in enumerate(pts)]
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
def tag_flip(tip: Point, tag_w: float, tag_h: float, arrow_w: float, arrow_h: float,
             screen: Optional[Rect], gap: float = 4.0, margin: float = 6.0) -> Tuple[bool, bool]:
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


def tag_origin(tip: Point, tag_w: float, tag_h: float, arrow_w: float, arrow_h: float,
               flip: Tuple[bool, bool], gap: float = 4.0) -> Point:
    """Global top-left of the tag for a given flip (matches ``geometry.chip_rect`` unflipped)."""
    left, up = flip
    x = tip[0] - tag_w - gap if left else tip[0] + arrow_w - gap
    y = tip[1] - tag_h - gap if up else tip[1] + arrow_h + gap
    return (x, y)
