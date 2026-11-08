"""Scorecard for local models on Dobby's three decision jobs.

Runs the same synthetic cases through each model and scores what matters for
acting safely, not just accuracy:

* ``check``        yes/no on screenshot crops (role "vision")
* ``pick_text``    pick an element from a text screen state, or "None of these" (role "text")
* ``pick_visual``  pick a lettered box on a drawn window, or "None of these" (role "visual_pick")

For each model and task: accuracy, confident mistakes (wrong with p >= 0.9:
the expensive kind), how often it says "none"/"unsure" when the target is
absent, median latency, and whether probabilities came from logprobs.

Read-only and offline apart from the local Ollama: never touches the desktop,
never downloads a model (missing models are reported as "not installed").
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageDraw, ImageFont

CONFIDENT = 0.9
YES_AT, NO_AT = 0.75, 0.25  # three-band routing from the Gemini report


def _font(size: int):
    for path in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNS.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


# ------------------------------------------------------------------ cases


def _dialog(title: str, body: str, buttons: Sequence[str]) -> Image.Image:
    img = Image.new("RGB", (520, 220), (236, 236, 236))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 519, 34], fill=(210, 210, 214))
    d.text((14, 8), title, fill=(20, 20, 20), font=_font(18))
    d.text((24, 70), body, fill=(10, 10, 10), font=_font(20))
    x = 500
    for label in reversed(list(buttons)):
        w = 24 + 11 * len(label)
        x -= w + 12
        d.rounded_rectangle(
            [x, 160, x + w, 194], radius=6, fill=(255, 255, 255), outline=(150, 150, 150)
        )
        d.text((x + 12, 167), label, fill=(0, 0, 0), font=_font(17))
    return img


def _status(text: str, dot=None) -> Image.Image:
    img = Image.new("RGB", (600, 60), (248, 248, 248))
    d = ImageDraw.Draw(img)
    if dot:
        d.ellipse([16, 18, 40, 42], fill=dot)
    d.text((54 if dot else 16, 18), text, fill=(30, 30, 30), font=_font(20))
    return img


def check_cases() -> list[dict[str, Any]]:
    """Yes/no on crops. ``expect`` lists acceptable answers among yes / no / unsure."""
    specs = [
        (
            _dialog("Vivado", "Synthesis Completed successfully.", ["OK"]),
            [("Did synthesis complete?", ["yes"]), ("Is there an error message?", ["no"])],
        ),
        (
            _dialog("Error", "License checkout failed for Synthesis.", ["Retry", "Cancel"]),
            [("Did the license checkout succeed?", ["no"]), ("Is an error shown?", ["yes"])],
        ),
        (
            _status("Running synth_design ..."),
            [("Has synthesis finished?", ["no", "unsure"]), ("Is synthesis running?", ["yes"])],
        ),
        (_status("Status", dot=(30, 170, 60)), [("Is the status indicator green?", ["yes"])]),
        (_status("Status", dot=(210, 40, 40)), [("Is the status indicator green?", ["no"])]),
        (
            Image.new("RGB", (400, 200), (128, 128, 128)),
            [("Did the file save succeed?", ["unsure", "no"])],
        ),
        (
            _dialog(
                "Save Changes",
                "Save changes to project.xpr before closing?",
                ["Save", "Discard", "Cancel"],
            ),
            [
                ("Is the Save dialog closed?", ["no"]),
                ("Does the dialog offer to discard changes?", ["yes"]),
            ],
        ),
    ]
    return [
        {"image": img, "question": q, "expect": e, "absent": e == ["unsure", "no"]}
        for img, qs in specs
        for q, e in qs
    ]


_TEXT_SCREENS = {
    "editor": (
        'Window: TextEdit - "notes.txt"',
        [
            'e1 menu item "File" (menu bar)',
            'e2 menu item "Edit" (menu bar)',
            'e3 push button "Save" (top right)',
            'e4 push button "Share" (top right)',
            'e5 entry "Search" (top center)',
            'e6 push button "Bold" (top left)',
        ],
    ),
    "dialog": (
        'Window: Vivado - "Save Changes"',
        [
            'e1 label "Save changes to project.xpr before closing?"',
            'e2 push button "Save" (bottom right)',
            'e3 push button "Discard" (bottom right)',
            'e4 push button "Cancel" (bottom right)',
        ],
    ),
    "chat": (
        'Window: Claude - "New chat"',
        [
            'e1 push button "New chat" (top left)',
            'e2 push button "Projects" (top left)',
            'e3 entry "Reply to Claude" (bottom center, focused)',
            'e4 push button "Send" (bottom right, disabled)',
            'e5 push button "Attach file" (bottom left)',
        ],
    ),
}


def pick_text_cases() -> list[dict[str, Any]]:
    """``expect`` is the 0-based option index, or None when the right answer is "None of these"."""
    rows = [
        ("editor", "save the document", 2),
        ("editor", "open the File menu", 0),
        ("editor", "make the text bold", 5),
        ("editor", "turn on Bluetooth", None),
        ("dialog", "keep the changes and close the project", 1),
        ("dialog", "close without saving", 2),
        ("dialog", "open the timing report", None),
        ("chat", "start a new chat", 0),
        ("chat", "attach a file to the message", 4),
        ("chat", "change the font size", None),
    ]
    out = []
    for screen, goal, expect in rows:
        window, elements = _TEXT_SCREENS[screen]
        out.append(
            {
                "state": f"Goal: {goal}\n{window}",
                "goal": goal,
                "options": elements,
                "expect": expect,
                "absent": expect is None,
            }
        )
    return out


def _magnifier(d: ImageDraw.ImageDraw, x: int, y: int) -> None:
    d.ellipse([x + 4, y + 4, x + 18, y + 18], outline=(40, 40, 40), width=3)
    d.line([x + 16, y + 16, x + 24, y + 24], fill=(40, 40, 40), width=3)


def _gear(d: ImageDraw.ImageDraw, x: int, y: int) -> None:
    d.ellipse([x + 5, y + 5, x + 23, y + 23], outline=(40, 40, 40), width=4)
    for dx, dy in ((14, 1), (14, 27), (1, 14), (27, 14)):
        d.rectangle([x + dx - 2, y + dy - 2, x + dx + 2, y + dy + 2], fill=(40, 40, 40))


def _trash(d: ImageDraw.ImageDraw, x: int, y: int) -> None:
    d.rectangle([x + 7, y + 9, x + 21, y + 25], outline=(40, 40, 40), width=3)
    d.line([x + 4, y + 7, x + 24, y + 7], fill=(40, 40, 40), width=3)


def _plus(d: ImageDraw.ImageDraw, x: int, y: int) -> None:
    d.line([x + 14, y + 4, x + 14, y + 24], fill=(40, 40, 40), width=4)
    d.line([x + 4, y + 14, x + 24, y + 14], fill=(40, 40, 40), width=4)


def visual_window() -> tuple[Image.Image, list[tuple[int, int, int, int]], list[str]]:
    """A drawn app window with an icon toolbar and two text buttons. Returns image, boxes, names."""
    img = Image.new("RGB", (640, 400), (250, 250, 250))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 639, 36], fill=(228, 228, 232))
    d.text((260, 9), "Waveform Viewer", fill=(30, 30, 30), font=_font(16))
    icons = [
        ("search (magnifier icon)", _magnifier),
        ("settings (gear icon)", _gear),
        ("delete (trash icon)", _trash),
        ("add (plus icon)", _plus),
    ]
    boxes, names = [], []
    for i, (name, draw) in enumerate(icons):
        x, y = 16 + i * 44, 52
        d.rounded_rectangle([x, y, x + 30, y + 30], radius=5, outline=(200, 200, 200))
        draw(d, x + 1, y + 1)
        boxes.append((x, y, 31, 31))
        names.append(name)
    for i, label in enumerate(("Run", "Export")):
        x, y = 420 + i * 100, 340
        d.rounded_rectangle(
            [x, y, x + 84, y + 34], radius=6, fill=(255, 255, 255), outline=(150, 150, 150)
        )
        d.text((x + 14, y + 8), label, fill=(0, 0, 0), font=_font(17))
        boxes.append((x, y, 85, 35))
        names.append(label)
    return img, boxes, names


def pick_visual_cases() -> list[dict[str, Any]]:
    """Synthetic Set-of-Marks cases for the visual pick task, each with the right mark."""
    rows = [
        ("search the signals", 0),
        ("open the settings", 1),
        ("delete the selected signal", 2),
        ("add a new signal", 3),
        ("run the simulation", 4),
        ("export the waveform", 5),
        ("open the Bluetooth settings", None),
        ("print the schematic", None),
    ]
    return [{"goal": g, "expect": e, "absent": e is None} for g, e in rows]


# ----------------------------------------------------------------- scoring


@dataclass
class TaskScore:
    """Accuracy and timing for one benchmark task on one model."""

    task: str
    n: int = 0
    correct: int = 0
    confident_wrong: int = 0
    absent: int = 0
    absent_ok: int = 0
    calibrated: int = 0
    latencies: list[float] = field(default_factory=list)
    errors: int = 0
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        """The score as a JSON-ready dict."""
        return {
            "task": self.task,
            "n": self.n,
            "correct": self.correct,
            "accuracy": round(self.correct / self.n, 3) if self.n else None,
            "confident_wrong": self.confident_wrong,
            "absent_ok": f"{self.absent_ok}/{self.absent}",
            "p50_ms": round(statistics.median(self.latencies), 1) if self.latencies else None,
            "logprobs": f"{self.calibrated}/{self.n}",
            "errors": self.errors,
            "note": self.note,
        }


def _band(p: float) -> str:
    return "yes" if p >= YES_AT else ("no" if p <= NO_AT else "unsure")


def run_check(
    decider: Any, cases: list[dict[str, Any]], encode: Callable[[Image.Image], str]
) -> TaskScore:
    """Scores yes/no crop checks: is the quoted text visible in the image?"""
    s = TaskScore("check")
    for c in cases:
        s.n += 1
        try:
            r = decider.noul(
                "The image is a crop of a desktop app window.",
                f"The answer to this question is yes: {c['question']}",
                images=[encode(c["image"])],
                question_id="bench_check",
            )
        except Exception:
            s.errors += 1
            continue
        s.latencies.append(r.latency_ms)
        s.calibrated += int(r.calibrated)
        label = _band(r.p_true)
        ok = label in c["expect"]
        s.correct += int(ok)
        if not ok and label != "unsure" and max(r.p_true, 1 - r.p_true) >= CONFIDENT:
            s.confident_wrong += 1
        if c["absent"]:
            s.absent += 1
            s.absent_ok += int(ok)
    return s


def _score_choice(s: TaskScore, decision: Any, expect: int | None, absent: bool) -> None:
    s.latencies.append(decision.latency_ms)
    s.calibrated += int(decision.calibrated)
    got = None if decision.is_none else decision.index
    ok = got == expect
    s.correct += int(ok)
    if not ok and decision.p >= CONFIDENT:
        s.confident_wrong += 1
    if absent:
        s.absent += 1
        s.absent_ok += int(ok)


def run_pick_text(decider: Any, cases: list[dict[str, Any]]) -> TaskScore:
    """Scores picking the right element from a text list of screen elements."""
    s = TaskScore("pick_text")
    for c in cases:
        s.n += 1
        try:
            d = decider.choose(
                c["state"],
                "Which element should be used next to advance the goal?",
                c["options"],
                question_id="bench_pick_text",
            )
        except Exception:
            s.errors += 1
            continue
        _score_choice(s, d, c["expect"], c["absent"])
    return s


def run_pick_visual(
    decider: Any, cases: list[dict[str, Any]], encode: Callable[[Image.Image], str]
) -> TaskScore:
    """Scores picking the right lettered mark on a marked-up screenshot."""
    from dobby.gemma.decide import option_labels
    from dobby.vdom.gemma_marks import draw_marks

    s = TaskScore("pick_visual")
    img, boxes, _ = visual_window()
    letters = option_labels(len(boxes))
    marked = encode(draw_marks(img, boxes, 1280, labels=letters))
    options = [f"the box marked {l}" for l in letters]
    for c in cases:
        s.n += 1
        try:
            d = decider.choose(
                f"Goal: {c['goal']}\nThe image is an app window with lettered boxes on candidate controls.",
                "Which box should be used next to advance the goal?",
                options,
                images=[marked],
                question_id="bench_pick_visual",
            )
        except Exception:
            s.errors += 1
            continue
        _score_choice(s, d, c["expect"], c["absent"])
    return s


def bench_model(
    client: Any,
    model: str,
    tasks: Sequence[str] = ("check", "pick_text", "pick_visual"),
    vision: bool | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Runs ``tasks`` on one model. Returns {"model", "installed", "tasks": [TaskScore dicts]}."""
    from dobby.gemma.client import encode_image
    from dobby.gemma.decide import GemmaDecider

    if not client.has_model(model):
        return {"model": model, "installed": False, "tasks": []}
    decider = GemmaDecider(client, model=model, timeout=timeout)
    enc = lambda im: encode_image(im, quality=90)
    out = []
    for task in tasks:
        start = time.perf_counter()
        if task in ("check", "pick_visual") and vision is False:
            out.append(TaskScore(task, note="text-only model").as_dict())
            continue
        if task == "check":
            score = run_check(decider, check_cases(), enc)
        elif task == "pick_text":
            score = run_pick_text(decider, pick_text_cases())
        elif task == "pick_visual":
            score = run_pick_visual(decider, pick_visual_cases(), enc)
        else:
            raise ValueError(f"unknown task {task}")
        row = score.as_dict()
        row["wall_s"] = round(time.perf_counter() - start, 1)
        out.append(row)
    return {"model": model, "installed": True, "tasks": out}


def markdown(results: list[dict[str, Any]]) -> str:
    """Formats benchmark results as a Markdown table."""
    lines = [
        "| model | task | accuracy | confident wrong | says none when absent | p50 ms | logprobs |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if not r["installed"]:
            lines.append(f"| {r['model']} | - | not installed | | | | |")
            continue
        for t in r["tasks"]:
            if t.get("note") and not t["n"]:
                lines.append(f"| {r['model']} | {t['task']} | {t['note']} | | | | |")
                continue
            acc = "-" if t["accuracy"] is None else f"{t['accuracy']:.0%}"
            lines.append(
                f"| {r['model']} | {t['task']} | {acc} ({t['correct']}/{t['n']}) | {t['confident_wrong']} | "
                f"{t['absent_ok']} | {t['p50_ms']} | {t['logprobs']} |"
            )
    return "\n".join(lines)
