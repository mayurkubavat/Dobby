"""Model roles (plug-and-play models) and the model benchmark, with a fake Ollama."""

import json
import math

import pytest

from dobby import local_models as lm
from dobby.config import CONFIG
from dobby.gemma.client import GemmaClient
from dobby.model_bench import bench_model, markdown, pick_text_cases


# conftest._isolate_model_roles gives each test clean roles and tmp_path/models.json.


def test_defaults_follow_legacy_gemma_settings():
    assert lm.model_for("vision") == CONFIG.gemma3_vision_model
    assert lm.model_for("text") == CONFIG.gemma3_text_model
    assert lm.source_of("visual_pick") == "default"
    with pytest.raises(ValueError):
        lm.model_for("planner")


def test_precedence_env_then_runtime_then_file_then_config(monkeypatch, tmp_path):
    monkeypatch.setattr(CONFIG, "model_vision", "cfg:1")
    assert lm.model_for("vision") == "cfg:1" and lm.source_of("vision") == "config"
    (tmp_path / "models.json").write_text(json.dumps({"vision": "file:1", "bogus": "x"}))
    assert lm.model_for("vision") == "file:1" and lm.source_of("vision") == "models.json"
    lm.set_role("vision", "rt:1")
    assert lm.model_for("vision") == "rt:1" and lm.source_of("vision") == "runtime"
    monkeypatch.setenv("DOBBY_MODEL_VISION", "env:1")
    assert lm.model_for("vision") == "env:1" and lm.source_of("vision") == "env"


def test_roles_table_marks_installed():
    lm.set_role("visual_pick", "qwen2.5vl:7b")
    rows = {r["role"]: r for r in lm.roles_table({"gemma3:4b"})}
    assert rows["vision"]["installed"] and not rows["visual_pick"]["installed"]


def test_grounder_decider_uses_role_models():
    from dobby.vdom.gemma3_sidecar import Gemma3Grounder

    g = Gemma3Grounder(client=GemmaClient(transport=lambda *a: {"models": []}))
    lm.set_role("text", "qwen3:4b")
    lm.set_role("visual_pick", "qwen2.5vl:7b")
    assert g.decider(role="text").model == "qwen3:4b"
    assert g.decider(role="visual_pick").model == "qwen2.5vl:7b"
    assert g.decider().model == CONFIG.gemma3_vision_model == g.vision_model
    lm.set_role("vision", "minicpm-v:8b")
    assert g.vision_model == "minicpm-v:8b" and g.marks.vision_model == "minicpm-v:8b"


class Oracle:
    """Fake Ollama that answers every bench case right (or always picks A when ``dumb``)."""

    def __init__(self, dumb=False):
        self.dumb = dumb

    def __call__(self, method, url, body, timeout):
        if url.endswith("/api/tags"):
            return {"models": [{"name": "good:1"}, {"name": "dumb:1"}]}
        user = body["messages"][1]["content"]
        if "Statement:" in user:
            tok = "yes"
        elif self.dumb:
            tok = "A"
        else:
            goal = user.split("Goal: ")[1].split("\n")[0]
            case = next((c for c in pick_text_cases() if c["goal"] == goal), None)
            n = len(case["options"]) if case else 6
            idx = (case["expect"] if case else None)
            tok = "ABCDEFGHIJ"[n if idx is None else idx]
        top = [(tok, math.log(0.97)), ("Z", math.log(0.03))]
        return {"message": {"content": tok},
                "logprobs": [{"token": tok, "logprob": top[0][1],
                              "top_logprobs": [{"token": t, "logprob": lp} for t, lp in top]}]}


def test_bench_scores_confident_mistakes_and_none_answers():
    good = bench_model(GemmaClient(transport=Oracle()), "good:1", tasks=("pick_text",))
    dumb = bench_model(GemmaClient(transport=Oracle(dumb=True)), "dumb:1", tasks=("pick_text", "pick_visual"))
    g, d = good["tasks"][0], dumb["tasks"][0]
    assert g["accuracy"] == 1.0 and g["confident_wrong"] == 0 and g["absent_ok"] == "3/3"
    assert d["confident_wrong"] > 0 and d["absent_ok"] == "0/3" and d["logprobs"] == "10/10"
    assert dumb["tasks"][1]["n"] == 8
    missing = bench_model(GemmaClient(transport=Oracle()), "nope:1")
    assert not missing["installed"]
    table = markdown([good, dumb, missing])
    assert "not installed" in table and "| good:1 | pick_text | 100% (10/10)" in table


def test_bench_check_task_runs_on_images():
    res = bench_model(GemmaClient(transport=Oracle()), "good:1", tasks=("check",))["tasks"][0]
    assert res["n"] == 11 and res["errors"] == 0 and res["correct"] >= 5  # always-yes gets the yes cases


def test_tests_never_read_the_developers_own_model_file(monkeypatch):
    """Guard for conftest._isolate_model_roles: your ~/.dobby/models.json must not leak in."""
    from pathlib import Path
    import os

    assert lm.MODELS_FILE != Path(os.path.expanduser("~/.dobby/models.json"))
    assert all(lm.source_of(r) == "default" for r in lm.ROLES)
