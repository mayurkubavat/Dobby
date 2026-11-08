"""Dobby's control menu, opened from the control bar's ▾ (and, when enabled,
the menu bar icon or the name tag's ▾).

Pure data (no AppKit) so it can be tested anywhere; ``mac_helper`` turns the
entries into an NSMenu.
"""

from __future__ import annotations

from collections.abc import Iterable

Entry = tuple[str, str | None, bool | None]  # title, action selector, checkmark
SEPARATOR: Entry = ("-", None, None)


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def status_of(paused: bool, takeover: bool, working: bool, turbo: bool) -> tuple[str, str]:
    """(status text, one-glyph menu bar title). One glyph fits beside the notch."""
    if paused:
        status, title = "Paused", "‖"
    elif takeover:
        status, title = "You're in control", "✋"
    elif working:
        status, title = "Working", "◆"
    else:
        status, title = "Idle", "◇"
    if turbo:
        status += " (Turbo)"
        title = "⚡" if title in ("◆", "◇") else title
    return status, title


def menu_entries(
    status: str,
    steps: Iterable[str],
    halted: bool,
    turbo: bool,
    overlay_on: bool,
    trail_on: bool,
    bar_shown: bool | None,
    awake: bool | None = False,
) -> list[Entry]:
    """Menu items, top to bottom. ``bar_shown`` is None when there is no control bar.

    ``awake`` is None while Dobby works (no wake/sleep entry then).
    """
    out: list[Entry] = [(f"Dobby: {status}", None, None), SEPARATOR]
    if awake is not None:
        out.append(
            ("Put Dobby to sleep", "menuSleep:", None)
            if awake
            else ("Wake Dobby", "menuWake:", None)
        )
    out.append(("Recent steps", None, None))
    recent = list(reversed(list(steps)))
    out += [("   " + _short(line, 60), None, None) for line in (recent or ["(none yet)"])]
    out += [
        SEPARATOR,
        ("Resume" if halted else "Pause", "menuPauseToggle:", None),
        ("Stop", "menuStop:", None),
        SEPARATOR,
        ("Turbo mode (no prompts, ignores take-over)", "menuToggleTurbo:", turbo),
        ("Show overlay", "menuToggleOverlay:", overlay_on),
    ]
    if bar_shown is not None:
        out.append(("Show control bar (⌃⌥H)", "menuTogglePill:", bar_shown))
    out += [
        ("Show step trail", "menuToggleTrail:", trail_on),
        ("Clear step trail", "menuClearTrail:", None),
    ]
    return out


# ---------------------------------------------------------- Dobby picker
# (title, pid, [(item title, action selector)])
PickerGroup = tuple[str, int, list[tuple[str, str]]]


def picker_groups(
    agents: Iterable[dict], own_pid: int, own_paused: bool = False
) -> list[PickerGroup]:
    """One submenu per running Dobby, when more than one runs.

    ``agents`` come from ``dobby.control.commands.overview``. Each submenu
    pauses or resumes, or stops, just that Dobby. The last group pauses or
    resumes all of them (pid 0).
    """
    agents = list(agents)
    if len(agents) < 2:
        return []
    groups: list[PickerGroup] = []
    any_running = False
    for a in agents:
        pid = int(a.get("pid", 0))
        paused = bool(a.get("paused")) or (pid == own_pid and own_paused)
        any_running = any_running or not paused
        notes = []
        if pid == own_pid:
            notes.append("this bar")
        if paused:
            notes.append("paused")
        elif a.get("holding"):
            notes.append("in control")
        elif a.get("waiting"):
            notes.append("waiting")
        title = _short(str(a.get("name") or f"Dobby-{pid}"), 28)
        if notes:
            title += "  · " + ", ".join(notes)
        groups.append(
            (
                title,
                pid,
                [
                    ("Resume" if paused else "Pause", "menuAgentPauseToggle:"),
                    ("Stop", "menuAgentStop:"),
                ],
            )
        )
    groups.append(
        ("All Dobbys", 0, [("Pause all", "menuAllPause:"), ("Resume all", "menuAllResume:")])
    )
    return groups


# ------------------------------------------------------------ control bar
# (mode, text, primary button, secondary button, colour key, dot alpha)
BarView = tuple[str, str, str, str, str, float]


def bar_view(
    *,
    paused: bool,
    takeover: bool,
    takeover_resume: str,
    working: bool,
    turbo: bool,
    current_mode: str,
    bar_mode: str,
    owner: bool,
    awake: bool = False,
    ai_waiting: bool = False,
    handoff: bool = False,
    other_waiting: bool = False,
    locked: bool = False,
) -> BarView | None:
    """What the control bar shows, or None to hide it.

    Colour keys: "caution", "ok", or "risk" (the current risk colour).
    ``current_mode`` "stopped" is kept until the helper's timer clears it.
    ``awake``: the user woke Dobby from the bar; any helper shows it.
    ``handoff``: that wake went to an AI waiting in ``wait_for_wake``;
    otherwise no AI was waiting and the bar says so briefly.
    ``ai_waiting``: this helper's AI waits for a wake; any helper shows it, so
    the click reaches the AI that waits. ``other_waiting``: another helper's AI
    waits or another Dobby shows a bar (working, paused, Allow/Deny…), so the
    bar owner hides its plain idle bar instead of drawing it under that one.
    An Allow/Deny prompt is shown separately and is never hidden.
    ``locked``: the input lock is on (``inputlock``), so the person cannot
    click into Dobby's window; Pause is offered as "Take over".
    """
    if paused:
        return ("paused", "Dobby is paused", "Resume", "Stop", "caution", 1.0)
    if takeover:
        text = (
            "You're in control · Dobby waits until you stop"
            if takeover_resume == "auto"
            else "You're in control"
        )
        return ("takeover", text, "Resume now", "Stop", "caution", 1.0)
    if current_mode == "stopped":
        return ("stopped", "Dobby stopped", "OK", "Hide", "danger", 1.0)
    if working:
        label = "Dobby ⚡ Turbo" if turbo else "Dobby is working"
        return (
            "working",
            label + " · ⌃⌥⎋ stops",
            "Take over" if locked else "Pause",
            "Stop",
            "risk",
            1.0,
        )
    if awake:
        if handoff:
            label = (
                "Dobby ⚡ Turbo · awake · handing back to the AI"
                if turbo
                else "Dobby is awake · handing back to the AI"
            )
        else:
            label = "Dobby is awake · no AI is waiting — ask your AI for a task"
        return ("awake", label, "Pause", "Stop", "ok", 1.0)
    if ai_waiting:
        label = (
            "Dobby ⚡ Turbo · sleeping · click to wake"
            if turbo
            else "Dobby is sleeping · click to wake"
        )
        return ("ready", label, "Pause", "Stop", "ok", 0.45)
    if bar_mode == "always" and owner and not other_waiting:
        label = "Dobby ⚡ Turbo · sleeping" if turbo else "Dobby is sleeping"
        return ("ready", label, "Pause", "Stop", "ok", 0.45)
    return None


def bar_click(mode: str, which: str) -> str:
    """Action for a click on the bar's "primary" or "secondary" button."""
    if mode == "confirm":
        return "allow" if which == "primary" else "deny"
    if mode == "stopped":
        return "hide" if which == "secondary" else "dismiss"
    if which == "secondary":
        return "stop"
    if mode in ("working", "ready", "awake"):
        return "pause"
    if mode in ("paused", "takeover"):
        return "resume"
    return "none"


def bar_tip(mode: str, ai_waiting: bool = False, reason: str = "") -> str | None:
    """Tooltip for the bar itself (clicking it wakes Dobby or puts it to sleep)."""
    if mode == "ready":
        if not ai_waiting:
            return "Click to wake Dobby"
        tip = "Click to wake Dobby: your AI is waiting for you"
        return tip + (f" ({_short(reason, 80)})" if reason.strip() else "")
    if mode == "awake":
        return "Click to put Dobby to sleep"
    return None


def label_click(mode: str) -> str:
    """Action for a click on the bar itself, away from its buttons."""
    return {"ready": "wake", "awake": "sleep"}.get(mode, "none")


# ----------------------------------------------------- person vs Dobby
# Kinds of real input seen by a helper's global monitor.
INPUT_KINDS = ("move", "click", "drag", "scroll", "key")


def takes_over(
    kind: str, *, working: bool, paused: bool, takeover: bool, turbo: bool, confirming: bool
) -> bool:
    """Does this real input from the person count as taking over from Dobby?

    * Never while an Allow/Deny prompt is up: the person is reaching for it.
    * Never for a bare pointer move: moving the mouse to the bar's Pause or
      Stop (or anywhere) must not turn Pause into "Resume now" under the
      pointer, nor make Dobby skip its next step. Clicks, drags, scrolls and
      keys change what is on screen, so they do count.
    * Never while Dobby idles (the person is just using their computer) or
      in Turbo (only Stop / Pause halt Dobby), unless it is paused.
    """
    if confirming or kind == "move":
        return False
    if not (working or paused or takeover):
        return False
    return not (turbo and not paused)


def idle_cleanup(
    *,
    since_action: float,
    active_secs: float,
    linger_secs: float,
    cursor_state: str,
    pointer_visible: bool,
    awake: bool,
    halted: bool,
    confirming: bool,
) -> tuple[bool, bool]:
    """What to put away once Dobby has stopped acting: ``(reset_thinking, hide_pointer)``.

    ``since_action`` is the seconds since Dobby's last step. After
    ``active_secs + linger_secs`` of quiet the task is over as far as the
    screen is concerned: the "thinking" sweep stops (it was set by the last
    screen read and nothing else clears it) and the agent pointer and its
    tag go away. Nothing is put away while the person is asked Allow/Deny,
    while Dobby is paused or taken over, or while it is awake for a handoff.
    """
    if confirming or halted or awake:
        return False, False
    over = since_action > active_secs + max(0.0, linger_secs)
    return over and cursor_state == "thinking", over and pointer_visible
