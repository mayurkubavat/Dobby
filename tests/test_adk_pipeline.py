"""ADK pipeline with a fake Gemma: act in code, judge with structured output, escalate when unsure."""

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("google.adk")

from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.genai import types

from dobby.adk.plan import ACTION_TOOLS, Plan
from dobby.adk.runner import run_plan

ROOT = Path(__file__).resolve().parents[1]


class FakeGemma(BaseLlm):
    """Replies with queued JSON; records the system instruction of each call."""

    model: str = "fake-gemma"
    replies: list = []
    seen: list = []

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False):
        self.seen.append(str(llm_request.config.system_instruction))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=json.dumps(reply))])
        )


class FakeDesktop:
    """Backend whose screen changes per action; records every call."""

    def __init__(self, screens, ax=None, fail_tools=()):
        self.screens = list(screens)
        self.ax = list(ax or [[] for _ in screens])
        self.i = 0
        self.calls = []
        self.fail_tools = set(fail_tools)
        self.ask_screen_reply = {
            "answer": None,
            "confidence": 0.0,
            "evidence": "unsure",
            "source": "gemma",
        }

    async def enable(self, domains):
        self.calls.append(("enable", domains))

    async def call(self, tool, args):
        self.calls.append((tool, args))
        if tool == "read_screen_text":
            return {"lines": [{"text": t} for t in self.screens[self.i]]}
        if tool == "get_accessibility_tree":
            return self.ax[self.i]
        if tool == "ask_screen":
            return self.ask_screen_reply
        if tool in self.fail_tools:
            return {"success": False, "message": "boom"}
        self.i = min(self.i + 1, len(self.screens) - 1)
        return {"success": True}


async def _nosleep(_):
    return None


def _run(plan, desk, replies):
    model = FakeGemma(replies=list(replies), seen=[])
    report = asyncio.run(run_plan(Plan.model_validate(plan), desk, model, sleep=_nosleep))
    return report, model


def test_ocr_tier_needs_no_model():
    desk = FakeDesktop([["Idle"], ["Running synth_design"]])
    report, model = _run(
        {"steps": [{"tool": "click", "args": {"x": 1, "y": 2}, "expect": {"text": "Running"}}]},
        desk,
        [],
    )
    assert report["status"] == "done"
    assert report["steps"][0]["tier"] == "ocr"
    assert model.seen == []


def test_gemma_text_verdict_must_be_grounded():
    desk = FakeDesktop([["Idle"], ["Synthesis Completed"]])
    plan = {
        "steps": [
            {
                "tool": "press_key",
                "args": {"key": "Return"},
                "expect": {"question": "Did synthesis finish?"},
            }
        ]
    }
    report, model = _run(
        plan, desk, [{"outcome": "pass", "confidence": 0.9, "evidence": "Synthesis Completed"}]
    )
    assert report["status"] == "done" and report["steps"][0]["tier"] == "gemma_text"
    assert "verify one step" in model.seen[0] and "Synthesis Completed" in model.seen[0]


def test_ungrounded_pass_falls_to_vision_then_escalates():
    desk = FakeDesktop([["Idle"], ["Running synth_design"]])
    plan = {
        "steps": [
            {
                "tool": "press_key",
                "args": {"key": "Return"},
                "expect": {"question": "Did synthesis finish?"},
                "retries": 1,
            }
        ]
    }
    invented = {"outcome": "pass", "confidence": 0.95, "evidence": "Synthesis Completed"}
    report, _ = _run(plan, desk, [invented, invented])
    assert report["status"] == "escalated" and report["stopped_at"] == "s1"
    row = report["steps"][0]
    assert row["status"] == "unsure" and row["attempts"] == 2
    assert row["verdict"]["note"] == "evidence not found in observation"
    assert [c[0] for c in desk.calls].count("press_key") == 1  # never re-acts
    assert "Running synth_design" in report["observation"]


def test_report_shows_only_the_deciding_attempt():
    desk = FakeDesktop([["Idle"], ["Synthesis Completed"]])
    plan = {
        "steps": [
            {
                "tool": "press_key",
                "args": {"key": "Return"},
                "expect": {"question": "Done?"},
                "retries": 1,
            }
        ]
    }
    report, _ = _run(
        plan,
        desk,
        [
            {"outcome": "unsure", "confidence": 0.0, "evidence": ""},
            {"outcome": "pass", "confidence": 0.9, "evidence": "Synthesis Completed"},
        ],
    )
    row = report["steps"][0]
    assert row["status"] == "passed" and row["attempts"] == 2 and "ask_screen" not in row


def test_vision_tier_can_pass():
    desk = FakeDesktop([["Idle"], ["Status"]])
    desk.ask_screen_reply = {
        "answer": True,
        "confidence": 0.9,
        "evidence": "green circle",
        "source": "gemma",
    }
    plan = {
        "steps": [
            {
                "tool": "click",
                "args": {"x": 1, "y": 1},
                "expect": {"question": "Is the status green?"},
            }
        ]
    }
    report, _ = _run(plan, desk, [{"outcome": "unsure", "confidence": 0.0, "evidence": ""}])
    assert report["status"] == "done" and report["steps"][0]["tier"] == "gemma_vision"


def test_model_error_is_unsure_not_pass():
    desk = FakeDesktop([["Idle"], ["Saved"]])
    plan = {
        "steps": [
            {
                "tool": "hotkey",
                "args": {"combo": "cmd+s"},
                "expect": {"question": "Was it saved?"},
                "retries": 0,
            }
        ]
    }
    report, _ = _run(plan, desk, [RuntimeError("ollama down")])
    assert report["status"] == "escalated"
    assert "error" in report["steps"][0]["verdict"]


def test_tool_failure_and_unwanted_text_stop_the_run():
    desk = FakeDesktop([["a"], ["b"]], fail_tools={"click"})
    report, _ = _run(
        {
            "steps": [
                {"tool": "click", "args": {"x": 1, "y": 1}},
                {"tool": "press_key", "args": {"key": "a"}},
            ]
        },
        desk,
        [],
    )
    assert (
        report["status"] == "escalated"
        and report["steps"][0]["tier"] == "tool"
        and len(report["steps"]) == 1
    )

    desk = FakeDesktop([["a"], ["Error: license"]])
    report, _ = _run(
        {
            "steps": [
                {"tool": "click", "args": {"x": 1, "y": 1}, "expect": {"text_absent": "Error"}}
            ]
        },
        desk,
        [],
    )
    assert report["steps"][0]["status"] == "failed" and report["steps"][0]["tier"] == "ocr"


def test_unexpected_dialog_is_triaged():
    dialog = [
        {"role": "dialog", "name": "Delete project?"},
        {"role": "push button", "name": "Delete"},
    ]
    desk = FakeDesktop([["a"], ["Delete project?"]], ax=[[], dialog])
    report, model = _run(
        {"steps": [{"tool": "click", "args": {"x": 1, "y": 1}}]},
        desk,
        [
            {
                "risk_tier": "DESTRUCTIVE_OR_SYSTEM",
                "suggested_button": None,
                "confidence": 0.9,
                "reason": "deletes",
            }
        ],
    )
    row = report["steps"][0]
    assert report["status"] == "escalated" and row["tier"] == "dialog"
    assert row["dialog"]["triage"]["risk_tier"] == "DESTRUCTIVE_OR_SYSTEM"
    assert "safety triage" in model.seen[0]


def test_plan_validation_and_domains():
    with pytest.raises(ValueError):
        Plan.model_validate({"steps": [{"tool": "run_terminal_command", "args": {}}]})
    plan = Plan.model_validate(
        {"steps": [{"tool": "focus_window", "args": {"window_id": "1"}}, {"tool": "click"}]}
    )
    assert plan.domains == ["window_mgmt"] and [s.id for s in plan.steps] == ["s1", "s2"]


def test_action_tool_domains_match_server():
    import dobby.server as server

    for tool, domain in ACTION_TOOLS.items():
        assert server._TOOL_DOMAINS[tool][0] == domain, tool


def test_example_plan_on_cassette_in_process(monkeypatch):
    """The shipped example plan runs end to end on the example cassette (no Ollama, no desktop)."""
    import dobby.server as server
    from dobby.adk.backends import InProcessBackend
    from dobby.agent_loop.controller import FeedbackLoopController
    from dobby.hal.factory import get_desktop_hal, reset_desktop_hal

    reset_desktop_hal()
    session = get_desktop_hal(
        kind="replay", cassette=ROOT / "tests" / "cassettes" / "example_click"
    )
    replay = session.backend
    monkeypatch.setattr(server, "hal", session)
    monkeypatch.setattr(server, "controller", FeedbackLoopController(session))

    plan = Plan.load(ROOT / "examples" / "adk" / "example_click_plan.json")
    model = FakeGemma(
        replies=[{"outcome": "pass", "confidence": 0.9, "evidence": "Synthesis Completed"}], seen=[]
    )
    report = asyncio.run(run_plan(plan, InProcessBackend(server), model, sleep=_nosleep))
    assert report["status"] == "done", report
    assert [r["tier"] for r in report["steps"]] == ["ocr", "gemma_text"]
    assert replay.current_state.frame_id == "f002"
    reset_desktop_hal()
