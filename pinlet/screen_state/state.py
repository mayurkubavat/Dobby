"""Builds :class:`ScreenState` from accessibility elements and OCR lines.

Also re-binds an element chosen on one observation to a newer one.

Pure functions over plain data: no desktop backend is touched here, so the
module is fully testable and replayable from cassettes. :mod:`.observe` wires
it to the live HAL, AX inspector and OCR engine.
"""

from __future__ import annotations

import re
import sys
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

# AT-SPI role names (the AX inspector maps macOS roles onto these).
EDITABLE_ROLES = {
    "entry",
    "text",
    "combo box",
    "spin button",
    "password text",
    "search field",
    "editbar",
}
SECURE_ROLES = {"password text"}
# macOS menu bar band (points from the top of the main screen). Linux and Windows
# menus live inside the window, so there is no band to treat specially.
MENU_BAR_HEIGHT = 40 if sys.platform == "darwin" else 0
CLICKABLE_ROLES = {
    "push button",
    "toggle button",
    "check box",
    "radio button",
    "menu item",
    "link",
    "page tab",
    "combo box",
    "list item",
    "table cell",
    "entry",
    "text",
    "slider",
    "spin button",
    "password text",
    "icon",
}
SCROLLABLE_ROLES = {"scroll pane", "list", "tree", "tree table", "text"}
_SECRET_WORDS = re.compile(r"\b(password|passcode|passphrase|pin code|api key)\b", re.I)

ALWAYS_OPERATIONS = ("click", "hotkey", "press_key", "wait", "done")


@dataclass(frozen=True)
class ScreenElement:
    """One thing on screen the decider can name: an AX control or an OCR text run."""

    eid: str
    role: str
    label: str
    x: int
    y: int
    width: int
    height: int
    source: str = "ax"  # "ax", "ax+ocr" (label from OCR), "ocr", or "detector" (icon box, no name)
    states: tuple[str, ...] = ()
    position: str = ""

    @property
    def center(self) -> tuple[int, int]:
        """Centre of the element's bounding box."""
        return (self.x + self.width // 2, self.y + self.height // 2)

    @property
    def focused(self) -> bool:
        """True when the element has keyboard focus."""
        return "focused" in self.states

    @property
    def enabled(self) -> bool:
        """True unless the element is disabled."""
        return "disabled" not in self.states

    @property
    def editable(self) -> bool:
        """True when the element accepts typed text."""
        return self.role in EDITABLE_ROLES

    @property
    def secure(self) -> bool:
        """True for password fields and anything labelled like a secret."""
        return self.role in SECURE_ROLES or bool(_SECRET_WORDS.search(self.label or ""))

    def describe(self) -> str:
        """One line for a decider: ``e3 push button "Save" (bottom right, disabled)``."""
        bits = [self.position] if self.position else []
        if self.focused:
            bits.append("focused")
        if not self.enabled:
            bits.append("disabled")
        if "selected" in self.states:
            bits.append("selected")
        if self.source == "ocr":
            bits.append("text only")
        elif self.source == "detector":
            bits.append("seen, not named")
        label = self.label.replace('"', "'")[:80] if self.label else "(no label)"
        extra = f" ({', '.join(bits)})" if bits else ""
        return f'{self.eid} {self.role} "{label}"{extra}'

    def as_dict(self) -> dict[str, Any]:
        """The element as a JSON-ready dict."""
        return {
            "eid": self.eid,
            "role": self.role,
            "label": self.label,
            "bbox": {"x": self.x, "y": self.y, "width": self.width, "height": self.height},
            "center": list(self.center),
            "position": self.position,
            "states": list(self.states),
            "source": self.source,
        }


@dataclass
class ScreenState:
    """A numbered, text-only observation of the active window."""

    generation: int
    captured_at: float
    app: str = ""
    window_title: str = ""
    window: tuple[int, int, int, int] | None = None
    elements: list[ScreenElement] = field(default_factory=list)
    text: list[str] = field(default_factory=list)  # OCR lines not attached to any element
    operations: list[str] = field(default_factory=list)
    window_id: str | None = None
    settle: dict[str, Any] | None = None  # how long observe waited for the screen to stop changing

    def get(self, eid: str) -> ScreenElement | None:
        """The element with id ``eid``, or None."""
        for el in self.elements:
            if el.eid == eid:
                return el
        return None

    @property
    def focused(self) -> ScreenElement | None:
        """The focused element, or None."""
        return next((e for e in self.elements if e.focused), None)

    @property
    def secure_input(self) -> bool:
        """True when the focused element is a password or secret field."""
        f = self.focused
        return bool(f and f.secure)

    def to_text(
        self,
        max_elements: int = 60,
        max_text_lines: int = 20,
        elements: Sequence[ScreenElement] | None = None,
    ) -> str:
        """The state as the decider reads it: window, focus, operations, numbered elements, loose text."""
        els = list(elements if elements is not None else self.elements)[:max_elements]
        head = [
            f"Window: {self.app or 'unknown app'}"
            + (f' - "{self.window_title}"' if self.window_title else "")
        ]
        f = self.focused
        head.append(
            f'Focused: {f.eid} {f.role} "{f.label[:60]}"' if f else "Focused: nothing editable"
        )
        if self.secure_input:
            head.append("Secure input is focused: typing is blocked.")
        head.append("Available operations: " + ", ".join(self.operations))
        head.append(f"Elements ({len(els)} of {len(self.elements)}):")
        head += [e.describe() for e in els]
        if self.text and max_text_lines > 0:
            head.append("Other visible text:")
            head += [f"- {t[:120]}" for t in self.text[:max_text_lines]]
        return "\n".join(head)

    def as_dict(self, max_elements: int = 60) -> dict[str, Any]:
        """The state as a JSON-ready dict, with at most ``max_elements`` elements."""
        return {
            "generation": self.generation,
            "captured_at": round(self.captured_at, 3),
            "app": self.app,
            "window_title": self.window_title,
            "window": list(self.window) if self.window else None,
            "focused": self.focused.eid if self.focused else None,
            "secure_input": self.secure_input,
            "operations": list(self.operations),
            "element_count": len(self.elements),
            "elements": [e.as_dict() for e in self.elements[:max_elements]],
            "text": list(self.text[:40]),
        }


# ----------------------------------------------------------------------- build


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _bbox(obj: Any) -> tuple[int, int, int, int] | None:
    b = _get(obj, "bbox")
    if b is None:
        return None
    try:
        return (int(_get(b, "x")), int(_get(b, "y")), int(_get(b, "width")), int(_get(b, "height")))
    except (TypeError, ValueError):
        return None


def coarse_position(cx: int, cy: int, frame: tuple[int, int, int, int] | None) -> str:
    """Describes where a point sits in ``frame`` (the window), from "top left" to "bottom right".

    Returns "" without a frame.
    """
    if not frame or frame[2] <= 0 or frame[3] <= 0:
        return ""
    fx, fy, fw, fh = frame
    if cy < fy and cy <= MENU_BAR_HEIGHT:
        return "menu bar"
    rx = (cx - fx) / fw
    ry = (cy - fy) / fh
    row = "top" if ry < 1 / 3 else ("middle" if ry < 2 / 3 else "bottom")
    col = "left" if rx < 1 / 3 else ("center" if rx < 2 / 3 else "right")
    return "center" if (row, col) == ("middle", "center") else f"{row} {col}"


def _inside(px: int, py: int, box: tuple[int, int, int, int]) -> bool:
    x, y, w, h = box
    return x <= px <= x + w and y <= py <= y + h


def build_state(
    ax_elements: Iterable[Any],
    ocr_lines: Iterable[Any] = (),
    window: Any = None,
    generation: int = 0,
    captured_at: float | None = None,
    max_ocr_elements: int = 40,
    min_ocr_confidence: float = 0.3,
    detector_boxes: Iterable[tuple[int, int, int, int]] = (),
    max_detector_elements: int = 20,
) -> ScreenState:
    """Merges AX elements and OCR lines (both in screen coordinates) into a :class:`ScreenState`.

    - AX controls keep their role and states; an empty label is filled from OCR
      text inside its box (placeholders such as "Message" often carry the only name).
    - OCR lines outside every AX control become ``text`` elements (clickable by
      their box) up to ``max_ocr_elements``, so canvas apps still get targets.
    - Elements are ordered top-to-bottom, left-to-right and numbered ``e1``...
    - ``detector_boxes`` (icon detector, screen coordinates) that no AX control
      or OCR text already covers become unnamed ``icon`` elements, so controls
      the accessibility tree cannot see still get an id (for visual choice).
    - ``type`` is offered only when an editable, non-secure element has focus;
      ``scroll`` only when something scrollable is visible.
    """
    frame = _bbox(window) if window is not None else None
    app = str(_get(window, "app_name", "") or "") if window is not None else ""
    title = str(_get(window, "title", "") or "") if window is not None else ""

    lines = []
    for ln in ocr_lines:
        text = str(_get(ln, "text", "") or "").strip()
        conf = _get(ln, "confidence", 1.0)
        if not text or (conf is not None and float(conf) < min_ocr_confidence):
            continue
        try:
            box = (
                int(_get(ln, "x")),
                int(_get(ln, "y")),
                int(_get(ln, "width")),
                int(_get(ln, "height")),
            )
        except (TypeError, ValueError):
            b = _bbox(ln)
            if b is None:
                continue
            box = b
        lines.append((text, box))
    used = [False] * len(lines)

    raw: list[dict[str, Any]] = []
    scrollable = False
    for el in ax_elements:
        box = _bbox(el)
        if box is None or box[2] < 4 or box[3] < 4:
            continue
        role = str(_get(el, "role", "unknown") or "unknown")
        if role in SCROLLABLE_ROLES:
            scrollable = True
        interactable = _get(el, "interactable", True)
        if not interactable and role not in CLICKABLE_ROLES and role != "dialog":
            continue
        label = str(_get(el, "name", "") or _get(el, "label", "") or "").strip()
        source = "ax"
        if not label:
            inside = [
                i
                for i, (_, b) in enumerate(lines)
                if _inside(b[0] + b[2] // 2, b[1] + b[3] // 2, box)
            ]
            if inside:
                inside.sort(key=lambda i: (lines[i][1][1], lines[i][1][0]))
                label = " ".join(lines[i][0] for i in inside)[:120]
                source = "ax+ocr"
        for i, (_, b) in enumerate(lines):
            if not used[i] and _inside(b[0] + b[2] // 2, b[1] + b[3] // 2, box):
                used[i] = True
        states = tuple(str(s) for s in (_get(el, "states", ()) or ()))
        raw.append({"role": role, "label": label, "box": box, "source": source, "states": states})

    loose: list[str] = []
    ocr_added = 0
    for i, (text, box) in enumerate(lines):
        if used[i]:
            continue
        if ocr_added < max_ocr_elements:
            raw.append(
                {"role": "text", "label": text[:120], "box": box, "source": "ocr", "states": ()}
            )
            ocr_added += 1
        else:
            loose.append(text)

    det_added = 0
    for box in detector_boxes:
        if det_added >= max_detector_elements:
            break
        try:
            box = tuple(int(v) for v in box)
        except (TypeError, ValueError):
            continue
        if len(box) != 4 or box[2] < 8 or box[3] < 8:
            continue
        cx, cy = box[0] + box[2] // 2, box[1] + box[3] // 2
        if frame is not None and not _inside(cx, cy, frame):
            continue
        # Already known: its center sits inside a named element, or a named element sits inside it.
        if any(
            _inside(cx, cy, r["box"])
            or _inside(r["box"][0] + r["box"][2] // 2, r["box"][1] + r["box"][3] // 2, box)
            for r in raw
        ):
            continue
        raw.append({"role": "icon", "label": "", "box": box, "source": "detector", "states": ()})
        det_added += 1

    raw.sort(key=lambda r: (r["box"][1] // 8, r["box"][0]))
    elements = []
    for n, r in enumerate(raw, start=1):
        x, y, w, h = r["box"]
        elements.append(
            ScreenElement(
                eid=f"e{n}",
                role=r["role"],
                label=r["label"],
                x=x,
                y=y,
                width=w,
                height=h,
                source=r["source"],
                states=r["states"],
                position=coarse_position(x + w // 2, y + h // 2, frame),
            )
        )

    state = ScreenState(
        generation=generation,
        captured_at=captured_at if captured_at is not None else time.time(),
        app=app,
        window_title=title,
        window=frame,
        elements=elements,
        text=loose,
    )
    ops = list(ALWAYS_OPERATIONS)
    f = state.focused
    if f is not None and f.editable and not f.secure and f.enabled:
        ops.insert(1, "type")
    if scrollable or any(e.role in SCROLLABLE_ROLES for e in elements):
        ops.insert(1, "scroll")
    state.operations = ops
    return state


# ---------------------------------------------------------------------- rebind


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def rebind(
    element: ScreenElement, fresh: ScreenState, max_shift: int = 120
) -> ScreenElement | None:
    """Finds ``element`` (chosen on an older observation) in ``fresh``.

    Same role and label wins, nearest to the old center when several match.
    Without a label, the same role whose center moved at most ``max_shift``
    pixels. Returns None when the element is gone or has moved too far: the
    caller must not click on a stale target.
    """
    ox, oy = element.center

    def dist(e: ScreenElement) -> float:
        cx, cy = e.center
        return ((cx - ox) ** 2 + (cy - oy) ** 2) ** 0.5

    label = _norm(element.label)
    if label:
        same = [e for e in fresh.elements if e.role == element.role and _norm(e.label) == label]
        if not same:  # role can flip between AX and OCR sources; label match alone, close by
            same = [e for e in fresh.elements if _norm(e.label) == label and dist(e) <= max_shift]
        if same:
            best = min(same, key=dist)
            return (
                best
                if dist(best) <= max(max_shift, 4 * max(element.width, element.height))
                else None
            )
        return None
    near = [
        e
        for e in fresh.elements
        if e.role == element.role and not _norm(e.label) and dist(e) <= max_shift
    ]
    return min(near, key=dist) if near else None


# --------------------------------------------------------------------- tracker


class StateTracker:
    """Hands out increasing generation numbers and remembers recent states."""

    def __init__(self, keep: int = 8) -> None:
        self._lock = threading.Lock()
        self._generation = 0
        self._keep = keep
        self._states: dict[int, ScreenState] = {}
        self.last_action_at: float = 0.0

    def next_generation(self) -> int:
        """Returns the next generation number."""
        with self._lock:
            self._generation += 1
            return self._generation

    def remember(self, state: ScreenState) -> ScreenState:
        """Stores ``state``, dropping the oldest beyond ``keep``, and returns it."""
        with self._lock:
            self._states[state.generation] = state
            for g in sorted(self._states)[: -self._keep]:
                del self._states[g]
        return state

    def get(self, generation: int) -> ScreenState | None:
        """The remembered state for ``generation``, or None if it was dropped."""
        with self._lock:
            return self._states.get(generation)

    @property
    def latest(self) -> ScreenState | None:
        """The newest remembered state, or None."""
        with self._lock:
            return self._states[max(self._states)] if self._states else None

    def mark_action(self) -> float:
        """Notes that an action just happened, so older states are no longer fresh."""
        self.last_action_at = time.time()
        return self.last_action_at

    def is_fresh(self, state: ScreenState) -> bool:
        """True when ``state`` was captured after the last action (safe to verify against)."""
        return state.captured_at > self.last_action_at
