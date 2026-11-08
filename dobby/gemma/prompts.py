"""Versioned prompt pack for the local Gemma checker.

Every Gemma call in Dobby takes its system prompt, JSON schema and
token budget from here, so a prompt change is one diff, carries a version, and
can be measured with ``scripts/eval_prompts.py`` before it lands.

Rules the prompts follow (they are a checker, not a planner):

- Gemma never emits click coordinates for the planner; boxes and marks are chosen
  from candidates the code drew.
- Answers are three-valued where the crop may not show enough: ``yes``, ``no``,
  ``unsure``. ``unsure`` means "send a screenshot to the planner", never a guess.
- Evidence is the exact visible text when there is any, so it can be checked
  against local OCR (see :func:`evidence_in_ocr`).

A local override can replace any system prompt without editing code: put
``<id>.txt`` in ``CONFIG.gemma3_prompt_dir``. The version then reads
``<version>+local`` in results and eval reports.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from dobby.config import CONFIG


@dataclass(frozen=True)
class PromptSpec:
    id: str
    version: int
    system: str
    schema: dict[str, Any]
    max_tokens: int
    local: bool = False

    @property
    def tag(self) -> str:
        return f"{self.id}@{self.version}{'+local' if self.local else ''}"


# --------------------------------------------------------------------- yes/no

YES_NO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "enum": ["yes", "no", "unsure"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence_type": {"type": "string", "enum": ["text", "visual", "none"]},
        "evidence": {"type": "string", "maxLength": 120},
    },
    "required": ["answer", "confidence", "evidence_type", "evidence"],
}

YES_NO = PromptSpec(
    id="yes_no",
    version=4,
    system=(
        "You check the state of a desktop application from a screenshot crop.\n"
        "Answer the question with yes, no, or unsure, based only on what is visible.\n"
        "- If the thing asked about is visible, answer yes or no. If it is visible but differs from "
        "the question (another color, another state, a dialog still open), the answer is no.\n"
        "- Use unsure when the crop is blank, cut off, blurred, or does not show the thing asked about. "
        "Do not infer success or failure from an empty screen. A wrong yes or no is worse than unsure.\n"
        "- Describe only what you can see. Never claim an element is visible unless it is in the crop.\n"
        "- evidence_type is text when your answer rests on words visible in the crop; then evidence "
        "is those words copied exactly, nothing else.\n"
        "- evidence_type is visual when it rests on a color, icon, shape or layout; then evidence "
        "describes it in at most 10 words.\n"
        "- evidence_type is none only with unsure.\n"
        "If an OCR text list is given, it is what a text reader found in the same crop."
    ),
    schema=YES_NO_SCHEMA,
    max_tokens=80,
)


def yes_no_user(
    question: str, ocr_lines: Iterable[str] | None = None, max_chars: int = 1200
) -> str:
    """User message: the question, plus OCR text of the crop when available."""
    text = f"Question: {question.strip()}"
    lines = [ln.strip() for ln in (ocr_lines or []) if ln and ln.strip()]
    if lines:
        joined, used = [], 0
        for ln in lines:
            if used + len(ln) > max_chars:
                joined.append("...")
                break
            joined.append(f"- {ln}")
            used += len(ln) + 3
        text += "\n\nOCR text in this crop:\n" + "\n".join(joined)
    return text


# ----------------------------------------------------------------- modal triage

MODAL_TRIAGE = PromptSpec(
    id="modal_triage",
    version=1,
    system=(
        "You are a safety triage classifier for desktop and EDA software dialogs.\n"
        "Classify the dialog into exactly one risk tier:\n"
        "- BENIGN_DISMISSIBLE: tips, telemetry, cookie or update nags; safe to close.\n"
        "- INFORMATIVE_ACK: a finished-task or status notice with a single acknowledge button.\n"
        "- WORKFLOW_BRANCHING: a choice that changes what the workflow does next.\n"
        "- DESTRUCTIVE_OR_SYSTEM: overwrites or deletes files, discards work, kills runs, license or system errors.\n"
        "suggested_button must be copied exactly from the button list, or null. "
        "Only suggest a button for BENIGN_DISMISSIBLE or INFORMATIVE_ACK. When unsure, use WORKFLOW_BRANCHING."
    ),
    schema={},  # filled by gemma3_sidecar (needs ModalRiskTier values)
    max_tokens=150,
)

# ----------------------------------------------------------- Set-of-Marks

MARK_LABEL = PromptSpec(
    id="mark_label",
    version=1,
    system=(
        "You name UI elements in a screenshot crop. Each element has a colored box with a number badge.\n"
        "For each numbered box, give a short label (the visible text, or what the icon does, 1-4 words) "
        "and its role. Skip boxes you cannot identify. Use only the numbers shown."
    ),
    schema={},  # filled by gemma_marks
    max_tokens=40,  # per mark; caller adds 20
)

MARK_FIND = PromptSpec(
    id="mark_find",
    version=1,
    system=(
        "You select a UI element in a screenshot crop. Each candidate has a colored box with a number badge.\n"
        "Return the number of the box that best matches the description, or null if none match."
    ),
    schema={},  # filled by gemma_marks
    max_tokens=80,
)

GROUND_ROI = PromptSpec(
    id="ground_roi",
    version=1,
    system=(
        "You are a UI grounding engine operating on a cropped desktop region.\n"
        "Identify interactive UI elements (buttons, inputs, dropdowns, icons, waveform signals, schematic pins).\n"
        "box_2d is [ymin, xmin, ymax, xmax] as integers from 0 to 1000 relative to the image."
    ),
    schema={},  # filled by gemma3_sidecar
    max_tokens=0,  # client default
)

COMPRESS = PromptSpec(
    id="compress_history",
    version=1,
    system="Compress the trajectory into the schema. Emit JSON only.",
    schema={},
    max_tokens=400,
)

ADK_VERIFIER = PromptSpec(
    id="adk_verifier",
    version=1,
    system=(
        "You verify one step of a desktop automation run. You get the step's expected outcome and what "
        "the screen shows now: the text a text reader found and the accessible UI elements.\n"
        "- outcome pass: the observation shows the expected outcome.\n"
        "- outcome fail: the observation shows it did not happen (an error, the old state, a different state).\n"
        "- outcome unsure: the observation does not show enough to tell. A wrong pass is the worst answer.\n"
        "- evidence: the exact words from the observation you relied on, or empty with unsure.\n"
        "Judge only from the observation; do not assume anything that is not listed."
    ),
    schema={},  # dobby.adk.judges.Verdict
    max_tokens=120,
)

ADK_TRIAGE = PromptSpec(
    id="adk_triage",
    version=1,
    system=MODAL_TRIAGE.system + "\nYou get the dialog's text and buttons as read from the screen.",
    schema={},  # dobby.adk.judges.DialogTriage
    max_tokens=150,
)


# ------------------------------------------------------- logit-scored decisions
# These prompts are answered with one token whose log-probabilities are read
# (dobby.gemma.decide), so they carry no JSON schema.

YES_NO_LOGPROB = PromptSpec(
    id="yes_no_logprob",
    version=1,
    system=(
        "You check the state of a desktop application from a screenshot crop.\n"
        "Decide whether the statement in the question is true, based only on what is visible.\n"
        "If the thing asked about is visible but differs (another color, another state, a dialog still open), "
        "the answer is no. If the crop is blank, cut off or does not show it, lean neither way.\n"
        "Reply with exactly one word: yes or no."
    ),
    schema={},
    max_tokens=1,
)

DECIDE_CHOICE = PromptSpec(
    id="decide_choice",
    version=1,
    system=(
        "You choose the single best option for a desktop automation step.\n"
        "Read the goal, the screen state and the options. Options are labelled with capital letters.\n"
        "Reply with exactly one capital letter and nothing else.\n"
        "If no option fits the goal, choose the option that says none of these."
    ),
    schema={},
    max_tokens=1,
)

PROMPTS: dict[str, PromptSpec] = {
    p.id: p
    for p in (
        YES_NO,
        MODAL_TRIAGE,
        MARK_LABEL,
        MARK_FIND,
        GROUND_ROI,
        COMPRESS,
        ADK_VERIFIER,
        ADK_TRIAGE,
        YES_NO_LOGPROB,
        DECIDE_CHOICE,
    )
}


def get_prompt(prompt_id: str, prompt_dir: str | None = None) -> PromptSpec:
    """The registered prompt, with its system text replaced by a local override if present."""
    spec = PROMPTS[prompt_id]
    folder = prompt_dir if prompt_dir is not None else CONFIG.gemma3_prompt_dir
    if folder:
        path = Path(folder).expanduser() / f"{prompt_id}.txt"
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    return replace(spec, system=text, local=True)
        except OSError:
            pass
    return spec


def list_prompts() -> list[dict[str, Any]]:
    out = []
    for pid in PROMPTS:
        spec = get_prompt(pid)
        out.append(
            {"id": pid, "tag": spec.tag, "max_tokens": spec.max_tokens, "system": spec.system}
        )
    return out


# ------------------------------------------------------------ evidence check

_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def evidence_in_ocr(evidence: str, ocr_lines: Iterable[str], min_overlap: float = 0.75) -> bool:
    """Returns True when the quoted evidence is visible in the OCR text.

    Either a direct (normalized) substring of one line matches, or at least
    ``min_overlap`` of its words appear in the crop's OCR words.

    Quotes and punctuation are ignored.
    """
    ev_words = _words(evidence)
    if not ev_words:
        return False
    lines = [ln for ln in ocr_lines if ln]
    ev_norm = f" {' '.join(ev_words)} "
    for ln in lines:
        if ev_norm in f" {' '.join(_words(ln))} ":  # whole words: "saved" is not in "unsaved"
            return True
    pool = set()
    for ln in lines:
        pool.update(_words(ln))
    hits = sum(1 for w in ev_words if w in pool)
    return hits / len(ev_words) >= min_overlap


def parse_answer(raw: Any) -> bool | None:
    """'yes'/'no'/'unsure' (prompt v2) or a bool (v1 replies) -> True/False/None."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        low = raw.strip().lower()
        if low in ("yes", "true"):
            return True
        if low in ("no", "false"):
            return False
    return None
