"""Live wiring for screen state.

Observes the active window, decides on an element with the local decision core,
and acts on it after re-binding to a fresh frame.

Everything goes through the controller (HAL, AX inspector, OCR, verification),
so cassette replay works unchanged.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from dobby.config import CONFIG
from dobby.screen_state.occlusion import check_occlusion
from dobby.screen_state.settle import settle_window
from dobby.screen_state.state import MENU_BAR_HEIGHT, ScreenState, StateTracker, build_state, rebind

logger = logging.getLogger(__name__)


def _window_region(win: Any) -> tuple[int, int, int, int] | None:
    try:
        b = win.bbox
        if b.width > 0 and b.height > 0:
            return (b.x, b.y, b.width, b.height)
    except Exception:
        pass
    return None


def _overlaps(box: dict[str, Any], region: tuple[int, int, int, int]) -> bool:
    try:
        x, y, w, h = int(box["x"]), int(box["y"]), int(box["width"]), int(box["height"])
    except (KeyError, TypeError, ValueError):
        return False
    rx, ry, rw, rh = region
    return x + w > rx and y + h > ry and x < rx + rw and y < ry + rh


def _in_menu_bar(node: dict[str, Any]) -> bool:
    """A menu bar item of the frontmost app (outside the window, at the top of the screen)."""
    box = node.get("bbox") or {}
    try:
        return (
            node.get("role") == "menu item"
            and int(box["y"]) + int(box["height"]) <= MENU_BAR_HEIGHT
        )
    except (KeyError, TypeError, ValueError):
        return False


def observe(
    controller: Any,
    tracker: StateTracker,
    app_name_filter: str | None = None,
    use_ocr: bool = True,
    clip_to_window: bool = True,
    settle: Any = False,
    use_detector: bool = False,
) -> ScreenState:
    """One fresh observation of the active window as a numbered :class:`ScreenState`.

    ``settle`` first waits until the window stops changing (loading pages,
    sliding sheets), so a half-drawn screen is not read as the result.
    ``settle="auto"`` waits only within ``settle_after_action_s`` of the last
    action, since each settle costs two captures (~0.5 s on a Mac).
    ``use_detector`` adds unnamed icon boxes the accessibility tree and OCR
    missed (for :func:`decide_visual_element`).
    """
    generation = tracker.next_generation()
    win = None
    try:
        win = controller.hal.get_active_window()
    except Exception as e:
        logger.debug("active window unavailable: %s", e)
    region = _window_region(win) if win is not None else None

    settled = None
    if settle == "auto":
        settle = (time.time() - tracker.last_action_at) < CONFIG.settle_after_action_s
    if settle:
        settled = settle_window(
            controller,
            region,
            timeout_s=CONFIG.settle_timeout_s,
            interval_s=CONFIG.settle_interval_s,
            max_delta=CONFIG.settle_max_delta,
        ).as_dict()

    try:
        nodes = (
            controller.get_accessibility_tree(
                app_name_filter=app_name_filter, filter_interactable=True
            )
            or []
        )
    except Exception as e:
        logger.debug("AX tree unavailable: %s", e)
        nodes = []
    nodes = [n.model_dump() if hasattr(n, "model_dump") else n for n in nodes if n]
    if clip_to_window and region is not None:
        # The app's menu bar lives outside its window; keep it so "open the File menu" has a target.
        nodes = [
            n
            for n in nodes
            if isinstance(n, dict) and (_overlaps(n.get("bbox") or {}, region) or _in_menu_bar(n))
        ]

    lines = []
    if use_ocr:
        try:
            lines = controller.read_screen_lines(region)
        except Exception as e:
            logger.debug("OCR unavailable: %s", e)
            lines = []

    boxes = detect_icon_boxes(controller, region) if use_detector else []

    state = build_state(
        nodes,
        lines,
        window=win,
        generation=generation,
        detector_boxes=boxes,
        max_detector_elements=CONFIG.screen_state_max_icons,
    )
    state.window_id = (
        str(win.window_id)
        if win is not None and getattr(win, "window_id", None) is not None
        else None
    )
    state.settle = settled
    return tracker.remember(state)


def _px_boxes(proposals: Any) -> list[tuple[int, int, int, int]]:
    out = []
    for p in proposals or []:
        b = p.get("bbox") if isinstance(p, dict) else None
        if not b:
            continue
        try:
            out.append((int(b["x"]), int(b["y"]), int(b["width"]), int(b["height"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def looks_degenerate(
    boxes: list[tuple[int, int, int, int]], min_boxes: int = 6, share: float = 0.4
) -> bool:
    """True when most boxes have the exact same size: a detector decoding anchors, not seeing icons."""
    if len(boxes) < min_boxes:
        return False
    sizes: dict[tuple[int, int], int] = {}
    for _, _, w, h in boxes:
        sizes[(w, h)] = sizes.get((w, h), 0) + 1
    return max(sizes.values()) >= share * len(boxes)


def detect_icon_boxes(
    controller: Any,
    region: tuple[int, int, int, int] | None,
    min_side: int = 10,
    max_side: int = 96,
    max_aspect: float = 4.0,
) -> list[tuple[int, int, int, int]]:
    """Icon-sized detector boxes in screen points inside ``region``, largest first; [] when unavailable.

    The capture is in pixels (2x on Retina), so boxes are scaled back to points.
    When the neural detector returns fixed-size anchor boxes (a decode fault seen
    on the Mac), its output is discarded and the OpenCV contour detector is used.
    Only icon-sized boxes are kept; boxes that contain named elements are dropped
    later by ``build_state``.
    """
    grounder = getattr(controller, "neural_grounder", None)
    if grounder is None or not hasattr(grounder, "generate_proposals") or region is None:
        return []
    try:
        img = controller.hal.capture_screen(region=region)
        rx, ry, rw, rh = region
        sx = img.width / float(rw) if rw else 1.0
        sy = img.height / float(rh) if rh else 1.0

        def icons(px: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
            kept = []
            for x, y, w, h in px:
                pw, ph = w / sx, h / sy
                if not (min_side <= pw <= max_side and min_side <= ph <= max_side):
                    continue
                if max(pw, ph) / max(1.0, min(pw, ph)) > max_aspect:
                    continue
                bx, by = rx + x / sx, ry + y / sy
                # Boxes hugging the window frame are corners/edges of the window, not controls.
                if (
                    bx - rx < 4
                    or by - ry < 4
                    or (rx + rw) - (bx + pw) < 4
                    or (ry + rh) - (by + ph) < 4
                ):
                    continue
                kept.append((int(bx), int(by), int(round(pw)), int(round(ph))))
            return kept

        mode = (CONFIG.icon_detector or "auto").strip().lower()
        can_contour = hasattr(grounder, "_generate_cv_proposals")
        if mode == "contours" and can_contour:
            return sorted(
                icons(_px_boxes(grounder._generate_cv_proposals(img, (0, 0)))),
                key=lambda bx: bx[2] * bx[3],
                reverse=True,
            )
        raw = _px_boxes(grounder.generate_proposals(img, offset=(0, 0)))
        out = icons(raw)
        # The neural detector is unstable on macOS (fixed anchor boxes, or panels only):
        # when it gives few icon-sized boxes, use OpenCV contours instead.
        if mode == "auto" and (looks_degenerate(raw) or len(out) < 3) and can_contour:
            logger.debug(
                "icon detector gave %d icon boxes of %d; using contours", len(out), len(raw)
            )
            out = icons(_px_boxes(grounder._generate_cv_proposals(img, (0, 0))))
    except Exception as e:
        logger.debug("icon detector unavailable: %s", e)
        return []
    out.sort(key=lambda bx: bx[2] * bx[3], reverse=True)
    return out


def summarize_change(before: ScreenState, after: ScreenState) -> dict[str, Any]:
    """What changed between two observations, by (role, label)."""
    b = {(e.role, e.label) for e in before.elements}
    a = {(e.role, e.label) for e in after.elements}
    added = sorted(a - b)[:10]
    removed = sorted(b - a)[:10]
    return {
        "changed": bool(added or removed) or before.window_title != after.window_title,
        "window_changed": before.window_title != after.window_title or before.app != after.app,
        "added": [f'{r} "{l}"' for r, l in added],
        "removed": [f'{r} "{l}"' for r, l in removed],
    }


def act_on_element(
    controller: Any,
    tracker: StateTracker,
    eid: str,
    generation: int,
    action: str = "click",
    text: str | None = None,
    press_enter: bool = False,
    app_name_filter: str | None = None,
    verify_delta: bool = True,
) -> dict[str, Any]:
    """Re-binds ``eid`` from observation ``generation`` on a fresh frame, then acts once.

    ``action`` is ``click``, ``double_click`` or ``type`` (click to focus, then type
    ``text``; Return only when ``press_enter``). Refuses stale, vanished,
    disabled or secure targets instead of clicking where the element used to be.
    """
    old = tracker.get(int(generation))
    if old is None:
        return {
            "ok": False,
            "reason": f"unknown generation {generation}: call get_screen_state again",
        }
    chosen = old.get(eid)
    if chosen is None:
        return {"ok": False, "reason": f"{eid} is not in generation {generation}"}
    if action not in ("click", "double_click", "type"):
        return {"ok": False, "reason": f"unsupported action '{action}'"}
    if action == "type" and not text:
        return {"ok": False, "reason": "type needs text"}

    fresh = observe(
        controller,
        tracker,
        app_name_filter=app_name_filter,
        use_detector=chosen.source == "detector",
    )
    target = rebind(chosen, fresh)
    if target is None:
        return {
            "ok": False,
            "reason": f"{chosen.describe()} is gone or moved since generation {generation}",
            "generation": fresh.generation,
        }
    if not target.enabled:
        return {
            "ok": False,
            "reason": f"{target.describe()} is disabled",
            "generation": fresh.generation,
        }
    if action == "type" and (target.secure or not target.editable):
        why = "a secure field" if target.secure else "not editable"
        return {
            "ok": False,
            "reason": f"{target.describe()} is {why}",
            "generation": fresh.generation,
        }

    cx, cy = target.center
    hal = controller.hal

    inspector = getattr(controller, "atspi_inspector", None)
    occlusion = check_occlusion(
        hal,
        (target.x, target.y, target.width, target.height),
        (cx, cy),
        target_window_id=fresh.window_id,
        target_role=target.role,
        hit_test=getattr(inspector, "hit_test", None),
        in_menu_bar=target.position == "menu bar",
    )
    if occlusion.covered:
        return {
            "ok": False,
            "reason": f"{target.describe()} is {occlusion.reason}",
            "occlusion": occlusion.as_dict(),
            "generation": fresh.generation,
        }

    def _do() -> None:
        hal.mouse_click(cx, cy, button="left", click_count=2 if action == "double_click" else 1)
        if action == "type":
            hal.keyboard_type(text)
            if press_enter:
                hal.keyboard_press_key("enter")

    tracker.mark_action()
    try:
        res = controller.execute_with_verification(
            action_name=f"{action}({target.eid} {target.role} '{target.label[:40]}')",
            action_func=_do,
            verify_delta=verify_delta,
        )
        verification = res.model_dump() if hasattr(res, "model_dump") else res
    except Exception as e:
        return {"ok": False, "reason": f"action failed: {e}", "generation": fresh.generation}

    after = observe(controller, tracker, app_name_filter=app_name_filter, settle=True)
    change = summarize_change(fresh, after)
    return {
        "ok": True,
        "acted_on": target.as_dict(),
        "rebound_from": chosen.eid if chosen.eid != target.eid else None,
        "generation": after.generation,
        "fresh_after_action": tracker.is_fresh(after),
        "change": change,
        "settle": after.settle,
        "occlusion": occlusion.as_dict(),
        "verification": {
            k: verification.get(k)
            for k in ("success", "delta_detected", "change_ratio", "message")
            if isinstance(verification, dict) and k in verification
        },
    }


def decide_next_element(
    controller: Any,
    tracker: StateTracker,
    decider: Any,
    goal: str,
    verb: str = "click",
    authorized: bool = False,
    k: int = 12,
    app_name_filter: str | None = None,
    confirm: bool = True,
    confirm_at: float = 0.9,
    visual_fallback: bool = False,
    visual_decider: Any = None,
) -> dict[str, Any]:
    """Observes, picks an element for the goal, and gates it by risk.

    Shortlists elements by the goal, lets the local decider choose one, and gates the
    choice by the verb's risk.

    Acts on nothing: the caller acts only when ``gate.verdict`` is "act" (then
    :func:`act_on_element`).

    With ``confirm`` a second, overlapping yes/no question checks the pick
    ("using X advances the goal"). A choice is always relative to the
    shortlist, so it stays confident when the right target is missing; the
    yes/no is absolute, so a "no" there downgrades "act" to "escalate".

    With ``visual_fallback``, an "escalate" from the text lane retries once on
    the screenshot (:func:`dobby.screen_state.visual.decide_visual_element`),
    for controls that have no text label; the text result is kept under
    ``text_lane``.
    """
    from dobby.gemma.decide import shortlist
    from dobby.gemma.gate import GateResult, Verdict, gate

    state = observe(controller, tracker, app_name_filter=app_name_filter, settle="auto")
    candidates = shortlist(
        goal, state.elements, k=max(1, min(25, k)), text=lambda e: f"{e.role} {e.label}"
    )
    if not candidates:
        out = {
            "generation": state.generation,
            "eid": None,
            "gate": {"verdict": "escalate", "reason": "no elements on screen"},
        }
        return (
            _visual_retry(
                out,
                controller,
                tracker,
                visual_decider or decider,
                goal,
                verb,
                authorized,
                app_name_filter,
            )
            if visual_fallback
            else out
        )
    ctx = f"Goal: {goal.strip()}\n" + state.to_text(elements=[], max_text_lines=10)
    options = [e.describe() for e in candidates]
    decision = decider.choose(
        ctx, f"Which element should be used to {verb} next to advance the goal?", options
    )
    eid = None if decision.is_none else candidates[decision.index].eid
    result = gate(decision, verb, authorized=authorized)
    confirmation = None
    if confirm and eid and result.act:
        target = candidates[decision.index]
        try:
            confirmation = decider.noul(
                f"Goal: {goal.strip()}\n" + state.to_text(max_elements=40, max_text_lines=10),
                f"To {goal.strip()}, the next step is to {verb} {target.describe()}.",
                question_id="confirm_element",
            )
        except Exception as e:  # a failed check must not turn into "act"
            logger.debug("confirmation failed: %s", e)
        if confirmation is None or confirmation.p_true < confirm_at:
            p_txt = (
                "failed"
                if confirmation is None
                else f"p={confirmation.p_true:.2f} < {confirm_at:.2f}"
            )
            result = GateResult(
                Verdict.ESCALATE,
                f"confirmation disagrees ({p_txt}): target may not be on screen",
                result.risk,
            )
    out = {
        "generation": state.generation,
        "eid": eid,
        "element": candidates[decision.index].as_dict() if eid else None,
        "decision": decision.as_dict(),
        "gate": result.as_dict(),
        "confirmation": confirmation.as_dict() if confirmation is not None else None,
        "settle": state.settle,
        "source": "text",
    }
    if visual_fallback and result.verdict == Verdict.ESCALATE:
        return _visual_retry(
            out,
            controller,
            tracker,
            visual_decider or decider,
            goal,
            verb,
            authorized,
            app_name_filter,
        )
    return out


def _visual_retry(
    text_out: dict[str, Any],
    controller: Any,
    tracker: StateTracker,
    decider: Any,
    goal: str,
    verb: str,
    authorized: bool,
    app_name_filter: str | None,
) -> dict[str, Any]:
    from dobby.screen_state.visual import decide_visual_element

    try:
        visual = decide_visual_element(
            controller,
            tracker,
            decider,
            goal,
            verb=verb,
            authorized=authorized,
            app_name_filter=app_name_filter,
        )
    except Exception as e:
        logger.debug("visual fallback failed: %s", e)
        return {**text_out, "visual_error": str(e)}
    brief = {k: text_out.get(k) for k in ("generation", "eid", "gate", "confirmation")}
    return {**visual, "text_lane": brief}
