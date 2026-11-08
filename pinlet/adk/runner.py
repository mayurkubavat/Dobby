"""The step loop: act in code, observe in code, judge with Gemma, escalate when unsure.

Per step:
  1. Call the plan's tool through the backend. A tool failure escalates at once.
  2. Wait ``settle_ms`` and observe: OCR text and accessible elements (no image).
  3. A new dialog goes to the triage judge; anything but a benign one escalates
     unless the step said ``allow_dialog``.
  4. Check ``expect`` cheapest first: OCR text (Tier 0, no model), then the
     Gemma verifier on the text observation (Tier 1), then ``ask_screen``
     (Gemma looking at the pixels). A verifier "pass" must quote evidence that is
     really in the observation.
  5. Not verified: observe again up to ``retries`` times (the tool is never
     re-run), then stop and return the report for the planner.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions
from google.genai import types
from pydantic import ConfigDict

from pinlet.adk.backends import DesktopBackend
from pinlet.adk.judges import Verdict, make_triage, make_verifier
from pinlet.adk.plan import Plan, Step
from pinlet.config import CONFIG
from pinlet.gemma.prompts import evidence_in_ocr
from pinlet.perception.ocr import text_matches

MAX_OBS_LINES = 60
MAX_AX_NODES = 40


def _lines(ocr: Any) -> list[str]:
    if isinstance(ocr, dict):
        return [str(b.get("text", "")) for b in ocr.get("lines", []) if b.get("text")]
    return []


def _nodes(ax: Any) -> list[dict[str, Any]]:
    if isinstance(ax, dict):
        ax = ax.get("nodes", [])
    return [n for n in (ax or []) if isinstance(n, dict)]


def _short(result: Any) -> Any:
    if isinstance(result, dict):
        keep = {k: result[k] for k in ("success", "message", "action_type", "error") if k in result}
        return keep or {k: result[k] for k in list(result)[:4]}
    return str(result)[:200]


class Observation:
    """What the runner read from the screen after a step: OCR lines and AX nodes."""

    def __init__(self, ocr_lines: list[str], nodes: list[dict[str, Any]]) -> None:
        self.ocr_lines = ocr_lines
        self.nodes = nodes

    @property
    def dialogs(self) -> list[str]:
        """Names of the dialogs among the accessibility nodes."""
        return [str(n.get("name") or "dialog") for n in self.nodes if n.get("role") == "dialog"]

    @property
    def names(self) -> list[str]:
        """Accessible names of every named node."""
        return [str(n.get("name")) for n in self.nodes if n.get("name")]

    def as_text(self) -> str:
        """Renders the observation as text for a judge prompt."""
        ocr = "\n".join(f"- {t}" for t in self.ocr_lines[:MAX_OBS_LINES]) or "(none)"
        ax = (
            "\n".join(
                f"- {n.get('role', '?')}: {n.get('name', '')}"
                for n in self.nodes[:MAX_AX_NODES]
                if n.get("name")
            )
            or "(none)"
        )
        return f"Text on screen:\n{ocr}\n\nAccessible elements:\n{ax}"


class DesktopPlanAgent(BaseAgent):
    """Runs a Plan step by step. Gemma judges; code acts; unsure stops the run."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    plan: Plan
    backend: Any
    verifier: Any
    triage: Any
    sleep: Any = None

    async def _observe(self) -> Observation:
        ocr = await self.backend.call("read_screen_text", {})
        try:
            ax = await self.backend.call("get_accessibility_tree", {})
        except Exception:
            ax = []
        return Observation(_lines(ocr), _nodes(ax))

    async def _judge(
        self, agent: Any, ctx: Any, key: str, text: str, out: dict[str, Any]
    ) -> AsyncGenerator[Event, None]:
        """Runs a judge sub-agent, yielding its events; ``out["value"]`` gets its output dict or None.

        Events are yielded as they come so the runner applies their state (input, output_key).
        """
        yield Event(
            author=self.name,
            actions=EventActions(state_delta={f"{agent.name}_input": text, key: None}),
        )
        try:
            async for ev in agent.run_async(ctx):
                yield ev
        except Exception as exc:  # model down, bad JSON, schema mismatch: unsure, never pass
            out["value"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
            return
        value = ctx.session.state.get(key)
        out["value"] = value if isinstance(value, dict) else None

    async def _check(
        self, step: Step, obs: Observation, ctx: Any, out: dict[str, Any]
    ) -> AsyncGenerator[Event, None]:
        """Checks ``step.expect`` against ``obs``; ``out["value"]`` gets {status, tier, ...}."""
        exp = step.expect
        if exp.empty:
            out["value"] = {"status": "passed", "tier": "none", "detail": "no expectation"}
            return
        screen = "\n".join(obs.ocr_lines + obs.names)
        missing = [t for t in exp.text if not text_matches(t, screen)]
        present = [t for t in exp.text_absent if text_matches(t, screen)]
        if present:
            out["value"] = {
                "status": "failed",
                "tier": "ocr",
                "detail": f"unwanted text on screen: {present}",
            }
            return
        if exp.text and not missing:
            out["value"] = {"status": "passed", "tier": "ocr", "detail": f"found {exp.text}"}
            return
        if not exp.question:
            out["value"] = {
                "status": "unsure",
                "tier": "ocr",
                "detail": f"text not found: {missing}",
            }
            return

        want = (
            exp.question
            if not exp.text
            else f"{exp.question} (expected text: {', '.join(exp.text)})"
        )
        holder: dict[str, Any] = {}
        async for ev in self._judge(
            self.verifier, ctx, "verdict", f"Expected outcome: {want}\n\n{obs.as_text()}", holder
        ):
            yield ev
        raw = holder.get("value")
        verdict = None
        if raw and "error" not in raw:
            try:
                verdict = Verdict.model_validate(raw)
            except Exception:
                verdict = None
        bar = CONFIG.gemma3_visual_min_confidence
        if verdict is not None:
            v = verdict.model_dump()
            grounded = bool(verdict.evidence) and evidence_in_ocr(
                verdict.evidence, obs.ocr_lines + obs.names
            )
            if verdict.outcome == "pass" and verdict.confidence >= bar and grounded:
                out["value"] = {"status": "passed", "tier": "gemma_text", "verdict": v}
                return
            if verdict.outcome == "fail" and verdict.confidence >= bar:
                out["value"] = {"status": "failed", "tier": "gemma_text", "verdict": v}
                return
            if verdict.outcome == "pass" and not grounded:
                v["note"] = "evidence not found in observation"
        else:
            v = raw or {"error": "no verdict"}

        seen = await self.backend.call("ask_screen", {"question": exp.question})
        seen = seen if isinstance(seen, dict) else {}
        ans, conf = seen.get("answer"), float(seen.get("confidence") or 0.0)
        looked = {
            "answer": ans,
            "confidence": conf,
            "evidence": seen.get("evidence"),
            "source": seen.get("source"),
        }
        if ans is True and conf >= bar:
            out["value"] = {
                "status": "passed",
                "tier": "gemma_vision",
                "verdict": v,
                "ask_screen": looked,
            }
        elif ans is False and conf >= bar:
            out["value"] = {
                "status": "failed",
                "tier": "gemma_vision",
                "verdict": v,
                "ask_screen": looked,
            }
        else:
            out["value"] = {
                "status": "unsure",
                "tier": "gemma_vision",
                "verdict": v,
                "ask_screen": looked,
            }

    async def _run_async_impl(self, ctx: Any) -> AsyncGenerator[Event, None]:
        sleep = self.sleep or asyncio.sleep
        report: dict[str, Any] = {"plan": self.plan.name, "status": "running", "steps": []}
        await self.backend.enable(self.plan.domains)
        before = await self._observe()

        for step in self.plan.steps:
            t0 = time.monotonic()
            row: dict[str, Any] = {"id": step.id, "tool": step.tool}
            obs: Observation | None = None
            result = await self.backend.call(step.tool, step.args)
            row["tool_result"] = _short(result)
            if isinstance(result, dict) and result.get("success") is False:
                row.update(status="failed", tier="tool", detail="tool reported failure")
            else:
                for attempt in range(step.retries + 1):
                    await sleep(step.settle_ms / 1000.0)
                    obs = await self._observe()
                    row["attempts"] = attempt + 1
                    new = [d for d in obs.dialogs if d not in before.dialogs]
                    if new and not step.expect.allow_dialog:
                        holder: dict[str, Any] = {}
                        async for ev in self._judge(
                            self.triage,
                            ctx,
                            "triage",
                            f"Dialog: {new[0]}\n\n{obs.as_text()}",
                            holder,
                        ):
                            yield ev
                        tri = holder.get("value")
                        row["dialog"] = {"name": new[0], "triage": tri}
                        tier = (tri or {}).get("risk_tier")
                        if tier not in ("BENIGN_DISMISSIBLE", "INFORMATIVE_ACK"):
                            row.update(
                                status="failed",
                                tier="dialog",
                                detail=f"unexpected dialog '{new[0]}' ({tier})",
                            )
                            break
                    holder = {}
                    async for ev in self._check(step, obs, ctx, holder):
                        yield ev
                    for key in ("verdict", "ask_screen", "detail"):
                        row.pop(key, None)  # report only the attempt that decided the step
                    row.update(holder["value"])
                    if row["status"] in ("passed", "failed"):
                        break
                if obs is not None:
                    before = obs
            row["ms"] = round((time.monotonic() - t0) * 1000)
            report["steps"].append(row)
            yield Event(
                author=self.name,
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            text=f"{step.id} {step.tool}: {row.get('status')} ({row.get('tier')})"
                        )
                    ],
                ),
                actions=EventActions(state_delta={"report": report}),
            )
            if row.get("status") != "passed":
                report.update(
                    status="escalated",
                    stopped_at=step.id,
                    reason=row.get("detail")
                    or f"step {step.id} not verified ({row.get('status')})",
                    observation=obs.as_text() if obs is not None else None,
                )
                break
        else:
            report["status"] = "done"
        yield Event(
            author=self.name, actions=EventActions(state_delta={"report": report}, escalate=True)
        )


async def run_plan(
    plan: Plan, backend: DesktopBackend, model: Any, sleep: Any = None
) -> dict[str, Any]:
    """Runs ``plan`` and returns the report dict."""
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService

    agent = DesktopPlanAgent(
        name="desktop_plan",
        description="Executes a desktop plan with local Gemma judges",
        plan=plan,
        backend=backend,
        verifier=make_verifier(model),
        triage=make_triage(model),
        sleep=sleep,
        sub_agents=[],
    )
    agent.sub_agents = [agent.verifier, agent.triage]
    sessions = InMemorySessionService()
    session = await sessions.create_session(app_name="pinlet", user_id="local", state={})
    runner = Runner(app_name="pinlet", agent=agent, session_service=sessions)
    msg = types.Content(role="user", parts=[types.Part(text=plan.goal or plan.name)])
    async for _ in runner.run_async(user_id="local", session_id=session.id, new_message=msg):
        pass
    session = await sessions.get_session(app_name="pinlet", user_id="local", session_id=session.id)
    return dict(session.state.get("report") or {"status": "error", "reason": "no report"})
