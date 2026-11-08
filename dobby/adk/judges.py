"""Gemma judges as ADK agents: structured output only, no tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from dobby.config import CONFIG
from dobby.gemma.prompts import get_prompt


class Verdict(BaseModel):
    """A judge's ruling on whether a step's expectation holds."""

    outcome: Literal["pass", "fail", "unsure"]
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = Field(default="", max_length=200)


class DialogTriage(BaseModel):
    """A judge's classification of a dialog that appeared during a step."""

    risk_tier: Literal[
        "BENIGN_DISMISSIBLE", "INFORMATIVE_ACK", "WORKFLOW_BRANCHING", "DESTRUCTIVE_OR_SYSTEM"
    ]
    suggested_button: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


def gemma_model(model: str | None = None, api_base: str | None = None) -> Any:
    """ADK model object for local Gemma on Ollama (via LiteLLM)."""
    from google.adk.models.lite_llm import LiteLlm

    from dobby.gemma.client import normalize_base_url

    name = model or f"ollama_chat/{CONFIG.gemma3_text_model}"
    kwargs = {}
    if name.startswith("ollama"):
        kwargs["api_base"] = api_base or normalize_base_url(CONFIG.gemma3_endpoint)
    return LiteLlm(model=name, **kwargs)


def _instruction(prompt_id: str, state_key: str):
    def provider(ctx: Any) -> str:
        return f"{get_prompt(prompt_id).system}\n\n{ctx.state.get(state_key, '')}"

    return provider


def make_verifier(model: Any) -> Any:
    """Builds the ADK agent that checks a step's expectation against the observation."""
    from google.adk.agents import LlmAgent

    return LlmAgent(
        name="verifier",
        description="Judges whether a desktop step's expected outcome is visible. Never acts.",
        model=model,
        instruction=_instruction("adk_verifier", "verifier_input"),
        output_schema=Verdict,
        output_key="verdict",
        include_contents="none",
    )


def make_triage(model: Any) -> Any:
    """Builds the ADK agent that classifies an unexpected dialog by risk."""
    from google.adk.agents import LlmAgent

    return LlmAgent(
        name="triage",
        description="Classifies the risk of a dialog that appeared. Never clicks.",
        model=model,
        instruction=_instruction("adk_triage", "triage_input"),
        output_schema=DialogTriage,
        output_key="triage",
        include_contents="none",
    )
