"""What the companion shows, as plain data: its mood, its bubble and its menu.

Pure and toolkit-free so the Linux companion (``x11_companion``) and its tests
share one set of rules. The macOS app keeps the same rules in Swift
(``CompanionStage.swift``); keep the two in step.

Two promises hold everywhere:

* the pause control is always one click away while an AI works;
* nothing on the companion approves anything. A pending Allow / Deny card
  gets a speech bubble that points at the card, and the menu offers only
  Cancel there.
"""

from __future__ import annotations

from dataclasses import dataclass

from .text import clean_thought, short

# Moods, most urgent first.
ASK = "ask"
ERROR = "error"
PAUSE = "pause"
ACT = "act"
THINK = "think"
DONE = "done"
SLEEP = "sleep"

# Bubble kinds, the manga grammar: a cloud for thinking, speech for asking,
# a burst for trouble, ice for frozen, a chip when the cloud is quieted.
CLOUD = "cloud"
SPEECH = "speech"
BURST = "burst"
ICE = "ice"
CHIP = "chip"

# Menu keys. There is deliberately no "allow".
RESUME = "resume"
STEP = "step"
CANCEL = "cancel"
FREEZE = "freeze"
QUIET = "quiet"
LOUD = "loud"
WAKE = "wake"
BAR = "bar"

TITLES = {
    RESUME: "Resume",
    STEP: "Let one step run",
    CANCEL: "Cancel task",
    FREEZE: "Pause",
    QUIET: "Quieter cloud",
    LOUD: "Full cloud",
    WAKE: "Wake Pinlet",
    BAR: "Show the bar instead",
}

FAILURE_BURST = 3  # this many failed steps in a row shows the burst


def mood(
    *,
    working: bool,
    paused: bool,
    confirming: bool,
    thinking: bool = False,
    failures: int = 0,
    done: bool = False,
) -> str:
    """The companion's mood from what the helper knows, most urgent first."""
    if confirming:
        return ASK
    if paused:
        return PAUSE
    if working and failures >= FAILURE_BURST:
        return ERROR
    if working:
        return THINK if thinking else ACT
    if done:
        return DONE
    return SLEEP


@dataclass(frozen=True)
class Bubble:
    """One bubble: its kind, who speaks, the main line and a small detail line."""

    kind: str
    who: str
    text: str
    detail: str = ""


def bubble(
    m: str,
    *,
    name: str = "Rime",
    goal: str = "",
    step: str = "",
    thought: str = "",
    steps: int = 0,
    failures: int = 0,
    quiet: bool = False,
) -> Bubble | None:
    """The bubble for mood ``m``, or None when the companion has nothing to say.

    AI text (goal, step, thought) is cleaned and trimmed: it is shown, never
    obeyed, and never tells the person to press anything.
    """
    goal = clean_thought(goal, 70)
    step = clean_thought(step, 60)
    thought = clean_thought(thought)
    if m == ASK:
        return Bubble(SPEECH, name, "Your answer is on the card", "Only you can press Allow there")
    if m == PAUSE:
        return Bubble(ICE, f"{name} · frozen", goal or "Paused", "Click me to resume or cancel")
    if m == ERROR:
        return Bubble(
            BURST, f"{name} · stuck", step or goal or "That didn't work", f"Tried {failures} times"
        )
    if m in (ACT, THINK):
        line = step or goal or ("Thinking…" if m == THINK else "Working…")
        if quiet:
            return Bubble(CHIP, name, short(line, 32))
        meta = f"Step {steps}" if steps else ""
        detail = thought or (goal if goal and goal != line else "")
        if meta:
            detail = f"{meta} · {detail}" if detail else meta
        who = f"{name} · thinking" if m == THINK else f"{name} · doing"
        return Bubble(CLOUD, who, line, detail)
    if m == DONE:
        return Bubble(CLOUD, name, "Done", short(goal, 48) if goal else "")
    return None


def menu(m: str, *, quiet: bool = False, bar_switch: bool = True) -> list[str]:
    """The menu keys for a click on the companion in mood ``m``."""
    if m == ASK:
        return [CANCEL]
    if m == PAUSE:
        return [RESUME, STEP, CANCEL]
    if m in (ACT, THINK, ERROR):
        return [FREEZE, CANCEL, LOUD if quiet else QUIET]
    items = [WAKE]
    if bar_switch:
        items.append(BAR)
    return items


def freezes_on_click(m: str) -> bool:
    """Whether the first click freezes the AI before the menu opens."""
    return m in (ACT, THINK, ERROR)


__all__ = (
    "ACT",
    "ASK",
    "BAR",
    "BURST",
    "CANCEL",
    "CHIP",
    "CLOUD",
    "DONE",
    "ERROR",
    "FREEZE",
    "ICE",
    "LOUD",
    "PAUSE",
    "QUIET",
    "RESUME",
    "SLEEP",
    "SPEECH",
    "STEP",
    "THINK",
    "TITLES",
    "WAKE",
    "Bubble",
    "bubble",
    "freezes_on_click",
    "menu",
    "mood",
)
