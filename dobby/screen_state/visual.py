"""Choose from the screenshot when the text view is not enough.

Some apps draw controls the accessibility tree does not name (toolbar icons in
EDA tools, canvas apps, custom widgets). ``decide_visual_element`` shows local
Gemma the window with lettered boxes drawn on it (Set-of-Marks) and scores the
letters from logprobs, the same typed decision as the text lane. Coordinates
always come from the boxes, never from the model.

The pick is then checked with an absolute yes/no question on a crop of the
chosen box, because a choice is only relative to the boxes it was shown.
"""

from __future__ import annotations

import logging
from typing import Any

from dobby.screen_state.observe import observe
from dobby.screen_state.state import ScreenElement, StateTracker

logger = logging.getLogger(__name__)

MAX_MARKS = 20  # letters A..T, plus "None of these"
VISUAL_MIN_P = 0.85  # stricter than the text lane: boxes carry no names to anchor the choice
VISUAL_MAY_ACT = False  # flip once cassette calibration shows the visual lane can say "none"


def _candidates(
    elements: list[ScreenElement], window: tuple[int, int, int, int], limit: int
) -> list[ScreenElement]:
    """Elements drawn inside the screenshot: unnamed and icon elements first (that
    is why we look), then named controls, in reading order. Menu bar items and
    anything else outside the window are left out: Gemma could only pick them
    from their text, and the text lane already covers them.
    """
    wx, wy, ww, wh = window

    def inside(e: ScreenElement) -> bool:
        cx, cy = e.center
        return e.position != "menu bar" and wx <= cx < wx + ww and wy <= cy < wy + wh

    def chrome(e: ScreenElement) -> bool:
        # Window controls (close / minimize / zoom) live in the title bar's left corner
        # and are unlabeled on macOS: never offer them as a visual pick.
        cx, cy = e.center
        return cy - wy < 30 and cx - wx < 90 and e.role in ("push button", "icon", "button")

    shown = [e for e in elements if inside(e) and not chrome(e)]
    unnamed = [e for e in shown if e.source == "detector" or not e.label]
    named = [e for e in shown if e not in unnamed and e.role != "text"]
    return (unnamed + named)[: max(1, limit)]


def _clip(box: tuple[int, int, int, int], w: int, h: int) -> tuple[int, int, int, int]:
    x0, y0 = max(0, min(w - 1, box[0])), max(0, min(h - 1, box[1]))
    x1, y1 = max(x0 + 1, min(w, box[0] + box[2])), max(y0 + 1, min(h, box[1] + box[3]))
    return (x0, y0, x1 - x0, y1 - y0)


def decide_visual_element(
    controller: Any,
    tracker: StateTracker,
    decider: Any,
    goal: str,
    verb: str = "click",
    authorized: bool = False,
    max_marks: int = MAX_MARKS,
    app_name_filter: str | None = None,
    confirm: bool = True,
    confirm_at: float = 0.9,
) -> dict[str, Any]:
    """Observes with the icon detector, marks up to ``max_marks`` elements with
    letters on a window screenshot, and lets the local decider pick one by
    letter. Gated like the text lane; acts on nothing. When the verdict is
    "act", call act_on_element with the returned ``eid`` and ``generation``.
    """
    from dobby.gemma.client import encode_image
    from dobby.gemma.decide import option_labels
    from dobby.gemma.gate import GateResult, Verdict, gate
    from dobby.vdom.gemma_marks import draw_marks

    state = observe(
        controller, tracker, app_name_filter=app_name_filter, settle="auto", use_detector=True
    )
    if state.window is None:
        return {
            "generation": state.generation,
            "eid": None,
            "gate": {"verdict": "escalate", "reason": "no active window to look at"},
        }
    cands = _candidates(state.elements, state.window, min(int(max_marks), MAX_MARKS))
    if not cands:
        return {
            "generation": state.generation,
            "eid": None,
            "gate": {"verdict": "escalate", "reason": "nothing on screen to mark"},
        }

    wx, wy, ww, wh = state.window
    try:
        shot = controller.hal.capture_screen(region=state.window)
    except Exception as e:
        return {
            "generation": state.generation,
            "eid": None,
            "gate": {"verdict": "escalate", "reason": f"screenshot failed: {e}"},
        }
    # Screen points -> screenshot pixels (Retina captures are 2x the window size).
    sx = shot.width / float(ww) if ww else 1.0
    sy = shot.height / float(wh) if wh else 1.0
    boxes = [
        _clip(
            (
                int((e.x - wx) * sx),
                int((e.y - wy) * sy),
                max(1, int(e.width * sx)),
                max(1, int(e.height * sy)),
            ),
            shot.width,
            shot.height,
        )
        for e in cands
    ]
    letters = option_labels(len(cands))
    max_side = int(getattr(getattr(decider, "client", None), "max_side", 0) or 1280)
    marked = draw_marks(shot, boxes, max_side, labels=letters)

    options = [
        f"the box marked {l} ({e.role}{' ' + repr(e.label[:40]) if e.label else ', no label'}, {e.position})"
        for l, e in zip(letters, cands)
    ]
    ctx = (
        f"Goal: {goal.strip()}\n"
        f"The image is the {state.app or 'active'} window with lettered boxes drawn on candidate controls."
    )
    decision = decider.choose(
        ctx,
        f"Which box should be used to {verb} next to advance the goal?",
        options,
        images=[encode_image(marked, quality=85)],
        question_id="visual_choice",
    )
    target = None if decision.is_none else cands[decision.index]
    result = gate(decision, verb, authorized=authorized)
    if result.act and decision.p < VISUAL_MIN_P:
        result = GateResult(
            Verdict.ESCALATE,
            f"visual pick below {VISUAL_MIN_P:.2f} (p={decision.p:.2f})",
            result.risk,
        )

    confirmation = None
    if confirm and target is not None and result.act:
        bx, by, bw, bh = boxes[decision.index]
        pad = max(12, int(0.5 * max(bw, bh)))
        crop = shot.crop(
            (
                max(0, bx - pad),
                max(0, by - pad),
                min(shot.width, bx + bw + pad),
                min(shot.height, by + bh + pad),
            )
        )
        try:
            confirmation = decider.noul(
                f"Goal: {goal.strip()}\nThe image is a close-up of one control in the {state.app or 'active'} window.",
                f"To {goal.strip()}, the next step is to {verb} the control in the center of this image.",
                images=[encode_image(crop, quality=90)],
                question_id="visual_confirm",
            )
        except Exception as e:  # a failed check must not turn into "act"
            logger.debug("visual confirmation failed: %s", e)
        if confirmation is None or confirmation.p_true < confirm_at:
            p_txt = (
                "failed"
                if confirmation is None
                else f"p={confirmation.p_true:.2f} < {confirm_at:.2f}"
            )
            result = GateResult(
                Verdict.ESCALATE, f"close-up check disagrees ({p_txt})", result.risk
            )

    if result.act and not VISUAL_MAY_ACT:
        # Live tests: Gemma 4B picked a wrong box with p~1.0 and a confirming close-up,
        # even when no box was right. Until the visual lane is calibrated it only suggests.
        result = GateResult(
            Verdict.ESCALATE,
            "visual pick is a suggestion only: check eid before acting",
            result.risk,
        )

    return {
        "generation": state.generation,
        "eid": target.eid if target else None,
        "element": target.as_dict() if target else None,
        "marks": len(cands),
        "decision": decision.as_dict(),
        "gate": result.as_dict(),
        "confirmation": confirmation.as_dict() if confirmation is not None else None,
        "settle": state.settle,
        "source": "visual",
    }
