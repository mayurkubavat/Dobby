"""Jev-style decisions: logprob-scored choice/noul, confidence gate, shortlist, run budget."""

import math

import pytest
from PIL import Image, ImageDraw

from dobby.config import CONFIG
from dobby.gemma.client import GemmaClient, first_token_logprobs
from dobby.gemma.decide import (
    NONE_OPTION,
    Decision,
    GemmaDecider,
    Noul,
    choice_confidence,
    label_distribution,
    option_labels,
    shortlist,
)
from dobby.gemma.gate import GatePolicy, RunBudget, Verdict, gate
from dobby.vdom.gemma3_sidecar import Gemma3Grounder


class FakeLogprobOllama:
    """Serves /api/tags and /api/chat replies that carry Ollama-style logprobs."""

    def __init__(self, tops=None, contents=None, models=("gemma3:4b",), with_logprobs=True):
        self.tops = list(tops or [])
        self.contents = list(contents or [])
        self.models = list(models)
        self.with_logprobs = with_logprobs
        self.calls = []

    def __call__(self, method, url, body, timeout):
        if url.endswith("/api/tags"):
            return {"models": [{"name": m} for m in self.models]}
        self.calls.append(body)
        top = self.tops.pop(0) if self.tops else []
        content = self.contents.pop(0) if self.contents else (top[0][0] if top else "")
        reply = {"message": {"role": "assistant", "content": content}}
        if self.with_logprobs:
            reply["logprobs"] = [
                {
                    "token": content,
                    "logprob": top[0][1] if top else 0.0,
                    "top_logprobs": [{"token": t, "logprob": lp} for t, lp in top],
                }
            ]
        return reply


def lp(p):
    return math.log(p)


def _decider(fake):
    return GemmaDecider(GemmaClient(transport=fake))


# ------------------------------------------------------------------ primitives


def test_choice_confidence_formula():
    assert choice_confidence([1.0, 0.0, 0.0]) == pytest.approx(1.0)
    assert choice_confidence([1 / 3] * 3) == pytest.approx(0.0)
    # Only the top probability counts.
    assert choice_confidence([0.6, 0.3, 0.1]) == pytest.approx(0.4)
    assert choice_confidence([0.6, 0.2, 0.2]) == pytest.approx(0.4)


def test_option_labels_cap():
    assert option_labels(3) == ["A", "B", "C"]
    with pytest.raises(ValueError):
        option_labels(27)


def test_label_distribution_merges_variants_and_reports_coverage():
    top = [(" B", lp(0.5)), ("B.", lp(0.2)), ("A", lp(0.1)), ("The", lp(0.2))]
    probs, cov = label_distribution(top, ["A", "B", "C"])
    assert probs[1] == pytest.approx(0.7 / 0.8)
    assert probs[0] == pytest.approx(0.1 / 0.8)
    assert probs[2] == 0.0
    assert cov == pytest.approx(0.8)


def test_label_distribution_temperature_flattens():
    top = [("A", lp(0.9)), ("B", lp(0.1))]
    sharp, _ = label_distribution(top, ["A", "B"])
    flat, _ = label_distribution(top, ["A", "B"], temperature=2.0)
    assert flat[0] < sharp[0] and flat[0] > 0.5


def test_first_token_logprobs_shapes():
    ollama = {
        "logprobs": [
            {"token": "A", "logprob": -0.1, "top_logprobs": [{"token": "A", "logprob": -0.1}]}
        ]
    }
    assert first_token_logprobs(ollama) == [("A", -0.1)]
    openai = {
        "choices": [
            {"logprobs": {"content": [{"token": "B", "logprob": -0.2, "top_logprobs": []}]}}
        ]
    }
    assert first_token_logprobs(openai) == [("B", -0.2)]
    assert first_token_logprobs({"message": {"content": "A"}}) is None


# --------------------------------------------------------------------- decider


def test_choose_reads_logprobs_and_adds_none_option():
    fake = FakeLogprobOllama(tops=[[("B", lp(0.85)), ("A", lp(0.1)), ("C", lp(0.05))]])
    d = _decider(fake).choose("Goal: save the file", "Which button?", ["Cancel", "Save"])
    assert d.options == ("Cancel", "Save", NONE_OPTION)
    assert d.choice == "Save" and d.calibrated and d.source == "gemma_logprobs"
    assert d.p == pytest.approx(0.85)
    assert d.margin == pytest.approx(0.75)
    body = fake.calls[0]
    assert body["logprobs"] is True and body["top_logprobs"] == 20
    assert body["options"]["num_predict"] == 1 and "format" not in body
    assert "C. None of these" in body["messages"][1]["content"]


def test_choose_without_logprobs_is_uncalibrated():
    fake = FakeLogprobOllama(contents=["B"], with_logprobs=False)
    d = _decider(fake).choose("Goal", "Which?", ["x", "y"])
    assert d.choice == "y" and not d.calibrated and d.source == "gemma_generated"
    assert gate(d, "click").verdict is Verdict.ESCALATE


def test_noul_case_insensitive():
    fake = FakeLogprobOllama(tops=[[("Yes", lp(0.6)), ("yes", lp(0.3)), ("No", lp(0.1))]])
    n = _decider(fake).noul("Dialog shows 'Saved'", "The file was saved")
    assert n.p_true == pytest.approx(0.9) and n.calibrated
    assert n.answer() is True and n.confidence == pytest.approx(0.8)


# ------------------------------------------------------------------------ gate


def _d(probs, calibrated=True, coverage=1.0, options=None):
    opts = options or tuple(f"o{i}" for i in range(len(probs)))
    return Decision(tuple(opts), tuple(probs), calibrated, coverage)


def test_gate_normal_act_and_escalate():
    assert gate(_d([0.8, 0.1, 0.1]), "click").verdict is Verdict.ACT
    low = gate(_d([0.5, 0.3, 0.2]), "click")
    assert low.verdict is Verdict.ESCALATE and "threshold" in low.reason


def test_gate_near_tie_escalates_even_above_threshold():
    res = gate(_d([0.55, 0.45, 0.0]), "scroll", policy=GatePolicy(min_margin=0.2))
    assert res.verdict is Verdict.ESCALATE and "tie" in res.reason


def test_gate_consequential_needs_authorization_and_high_p():
    strong = _d([0.95, 0.05])
    assert gate(strong, "send").verdict is Verdict.STOP  # no authorization
    assert gate(strong, "send", authorized=True).verdict is Verdict.ACT
    assert gate(strong, "delete", authorized=Noul(0.95, True, 1.0)).verdict is Verdict.ACT
    assert gate(strong, "delete", authorized=Noul(0.95, False, 1.0)).verdict is Verdict.STOP
    assert gate(_d([0.7, 0.3]), "send", authorized=True).verdict is Verdict.STOP


def test_gate_unknown_verb_none_option_and_low_coverage():
    assert gate(_d([0.9, 0.1]), "format_disk").verdict is Verdict.STOP
    none = _d([0.1, 0.9], options=("Save", NONE_OPTION))
    assert gate(none, "click").verdict is Verdict.ESCALATE
    assert "coverage" in gate(_d([0.9, 0.1], coverage=0.3), "click").reason


def test_gate_done_requires_verified_completion():
    d = _d([0.95, 0.05])
    assert gate(d, "done").verdict is Verdict.ESCALATE
    assert gate(d, "done", completion=Noul(0.8, True, 1.0)).verdict is Verdict.ESCALATE
    assert gate(d, "done", completion=Noul(0.95, True, 1.0)).verdict is Verdict.ACT


def test_gate_noul_decision():
    assert gate(Noul(0.92, True, 1.0), "click").verdict is Verdict.ACT
    assert gate(Noul(0.2, True, 1.0), "click").verdict is Verdict.ESCALATE


def test_run_budget_stops_repeats_and_caps():
    b = RunBudget()
    assert b.check("click:Save").act
    assert b.check("click:Save").act
    assert b.check("click:Save").verdict is Verdict.STOP
    b2 = RunBudget(policy=GatePolicy(max_mutations=1))
    assert b2.check("a").act and b2.check("b").verdict is Verdict.STOP


# ------------------------------------------------------------------- shortlist


def test_shortlist_ranks_by_goal_overlap():
    els = [
        {"role": "AXButton", "title": "Cancel"},
        {"role": "AXButton", "title": "Run Synthesis"},
        {"role": "AXMenuItem", "title": "Open Project"},
        {"role": "AXButton", "title": "Synthesis Settings"},
    ]
    out = shortlist("start synthesis run", els, k=2)
    assert out[0]["title"] == "Run Synthesis" and out[1]["title"] == "Synthesis Settings"
    assert len(shortlist("x", els, k=10)) == 4


def test_shortlist_prefix_matches():
    out = shortlist("save the document", ["Saved versions", "Print", "Close"], k=1)
    assert out == ["Saved versions"]


# ----------------------------------------------------- yes/no in logprob mode


IMG = Image.new("RGB", (200, 100), (240, 240, 240))
ImageDraw.Draw(IMG).rectangle([20, 20, 120, 60], fill=(30, 30, 30))


def test_ask_yes_no_logprob_mode(monkeypatch):
    monkeypatch.setattr(CONFIG, "gemma3_yes_no_logprobs", True)
    fake = FakeLogprobOllama(tops=[[("yes", lp(0.93)), ("no", lp(0.07))]])
    g = Gemma3Grounder(client=GemmaClient(transport=fake))
    res = g.ask_yes_no(IMG, "Did synthesis finish?")
    assert res["answer"] is True and res["source"] == "gemma_logprobs"
    assert res["p_yes"] == pytest.approx(0.93) and res["prompt"] == "yes_no_logprob@1"
    assert res["confidence"] == pytest.approx(0.86)


def test_ask_yes_no_logprob_band_is_unsure(monkeypatch):
    monkeypatch.setattr(CONFIG, "gemma3_yes_no_logprobs", True)
    fake = FakeLogprobOllama(tops=[[("yes", lp(0.55)), ("no", lp(0.45))]])
    res = Gemma3Grounder(client=GemmaClient(transport=fake)).ask_yes_no(IMG, "Is it red?")
    assert res["answer"] is None and res["confidence"] == 0.0


def test_ask_yes_no_falls_back_to_json_without_logprobs(monkeypatch):
    monkeypatch.setattr(CONFIG, "gemma3_yes_no_logprobs", True)

    class Fallback(FakeLogprobOllama):
        def __call__(self, method, url, body, timeout):
            if url.endswith("/api/tags"):
                return {"models": [{"name": "gemma3:4b"}]}
            self.calls.append(body)
            if body.get("logprobs"):
                return {"message": {"content": "yes"}}  # old Ollama: no logprobs field
            return {
                "message": {
                    "content": '{"answer": "yes", "confidence": 0.9, "evidence_type": "visual", "evidence": "green"}'
                }
            }

    fake = Fallback()
    res = Gemma3Grounder(client=GemmaClient(transport=fake)).ask_yes_no(IMG, "Is it green?")
    assert res["source"] == "gemma" and res["answer"] is True
    assert len(fake.calls) == 2
