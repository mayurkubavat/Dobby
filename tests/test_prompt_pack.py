"""Gemma prompt pack: registry, overrides, three-valued answers, OCR-checked evidence."""

from PIL import Image, ImageDraw
from test_gemma3_sidecar import FakeOllama

from pinlet.gemma.client import GemmaClient
from pinlet.gemma.prompts import (
    PROMPTS,
    evidence_in_ocr,
    get_prompt,
    list_prompts,
    parse_answer,
    yes_no_user,
)
from pinlet.vdom.gemma3_sidecar import Gemma3Grounder

IMG = Image.new("RGB", (200, 100), (240, 240, 240))
ImageDraw.Draw(IMG).rectangle([20, 20, 120, 60], fill=(30, 30, 30))  # not blank


def test_blank_crop_is_unsure_without_calling_gemma():
    fake = FakeOllama()  # no replies: a Gemma call would fail
    g = Gemma3Grounder(client=GemmaClient(transport=fake))
    res = g.ask_yes_no(Image.new("RGB", (400, 200), (128, 128, 128)), "Did the save succeed?")
    assert res["answer"] is None and res["source"] == "blank_crop"
    assert fake.chat_calls == []


def _ask(reply, ocr=None):
    fake = FakeOllama(replies=[reply])
    g = Gemma3Grounder(client=GemmaClient(transport=fake))
    return g.ask_yes_no(IMG, "Did synthesis finish?", ocr_lines=ocr), fake


def test_registry_has_every_gemma_call():
    assert set(PROMPTS) == {
        "yes_no",
        "modal_triage",
        "mark_label",
        "mark_find",
        "ground_roi",
        "compress_history",
        "adk_verifier",
        "adk_triage",
        "yes_no_logprob",
        "decide_choice",
    }
    assert get_prompt("yes_no", prompt_dir="").tag == "yes_no@4"
    assert all(p["system"] for p in list_prompts())


def test_local_override(tmp_path):
    (tmp_path / "yes_no.txt").write_text("Custom checker prompt.")
    spec = get_prompt("yes_no", prompt_dir=str(tmp_path))
    assert spec.system == "Custom checker prompt." and spec.tag == "yes_no@4+local"
    assert spec.schema == PROMPTS["yes_no"].schema


def test_parse_answer():
    assert parse_answer("yes") is True and parse_answer("No") is False
    assert parse_answer("unsure") is None and parse_answer(None) is None
    assert parse_answer(True) is True  # v1 replies


def test_evidence_in_ocr():
    lines = ["Synthesis Completed", "Run Implementation"]
    assert evidence_in_ocr("'synthesis completed'", lines)
    assert evidence_in_ocr("Completed synthesis", lines)  # word overlap
    assert not evidence_in_ocr("Implementation failed with errors", lines)
    assert not evidence_in_ocr("", lines)
    assert not evidence_in_ocr("Saved", ["Unsaved changes"])


def test_user_message_includes_ocr_and_truncates():
    msg = yes_no_user("Is it done?", ["Done", "x" * 2000])
    assert msg.startswith("Question: Is it done?") and "- Done" in msg and msg.endswith("...")
    assert yes_no_user("Q?", []) == "Question: Q?"


def test_unsure_is_none_not_a_guess():
    res, _ = _ask({"answer": "unsure", "confidence": 0.7, "evidence_type": "none", "evidence": ""})
    assert res["answer"] is None and res["confidence"] == 0.0 and res["source"] == "gemma"
    assert res["prompt"] == "yes_no@4"


def test_text_evidence_confirmed_by_ocr_keeps_confidence():
    res, fake = _ask(
        {
            "answer": "yes",
            "confidence": 0.93,
            "evidence_type": "text",
            "evidence": "Synthesis Completed",
        },
        ocr=["Synthesis Completed", "OK"],
    )
    assert res["answer"] is True and res["evidence_verified"] is True and res["confidence"] == 0.93
    assert "OCR text in this crop" in fake.chat_calls[0]["messages"][1]["content"]


def test_hallucinated_text_evidence_is_capped():
    res, _ = _ask(
        {
            "answer": "yes",
            "confidence": 0.95,
            "evidence_type": "text",
            "evidence": "Synthesis Completed",
        },
        ocr=["Running synth_design", "Cancel"],
    )
    assert res["answer"] is True and res["evidence_verified"] is False
    assert res["confidence"] <= 0.5  # below the 0.6 VISUAL_QUERY and 0.82 tier-1 bars


def test_visual_evidence_is_not_checked():
    res, _ = _ask(
        {
            "answer": "no",
            "confidence": 0.8,
            "evidence_type": "visual",
            "evidence": "red status icon",
        },
        ocr=["Status"],
    )
    assert res["answer"] is False and res["evidence_verified"] is None and res["confidence"] == 0.8


def test_bad_answer_falls_back():
    res, _ = _ask({"answer": "maybe", "confidence": 0.9, "evidence_type": "none", "evidence": ""})
    assert res["answer"] is None and res["source"] == "fallback"


def test_verify_only_does_not_show_ocr_to_gemma():
    fake = FakeOllama(
        replies=[
            {"answer": "yes", "confidence": 0.95, "evidence_type": "text", "evidence": "Saved"}
        ]
    )
    g = Gemma3Grounder(client=GemmaClient(transport=fake))
    res = g.ask_yes_no(IMG, "Was it saved?", verify_lines=["Unsaved changes"])
    assert "OCR text" not in fake.chat_calls[0]["messages"][1]["content"]
    assert res["evidence_verified"] is False and res["confidence"] <= 0.5


def test_ask_screen_verifies_with_ocr_by_default():
    from unittest.mock import MagicMock

    from pinlet.agent_loop.controller import FeedbackLoopController
    from pinlet.perception.ocr import CachedOCR, OCREngine, OCRLine

    class _Ocr(OCREngine):
        name = "fake"

        @property
        def is_available(self):
            return True

        def _recognize(self, image):
            return [OCRLine("Simulation finished", 10, 10, 150, 20, 0.97)]

    fake = FakeOllama(
        replies=[
            {
                "answer": "yes",
                "confidence": 0.9,
                "evidence_type": "text",
                "evidence": "Simulation finished",
            }
        ]
    )
    hal = MagicMock()
    hal.get_active_window.return_value = None
    hal.capture_screen.return_value = IMG
    ctrl = FeedbackLoopController(hal)
    ctrl.neural_grounder.gemma3_grounder = Gemma3Grounder(client=GemmaClient(transport=fake))
    ctrl.ocr = CachedOCR(_Ocr())
    res = ctrl.ask_screen("Is the simulation done?")
    assert res["answer"] is True and res["evidence_verified"] is True
    assert "OCR text" not in fake.chat_calls[0]["messages"][1]["content"]  # context off by default
