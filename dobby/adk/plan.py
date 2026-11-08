"""Plan format: what the planner hands to the ADK runner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

# Tools a plan may call, and the toolset domain each needs enabled on the server.
# Read-only perception tools are called by the runner itself, not by plans.
ACTION_TOOLS: dict[str, str] = {
    "click": "core",
    "find_and_click": "core",
    "type_text": "core",
    "press_key": "core",
    "hotkey": "core",
    "scroll": "core",
    "scroll_until_visible": "developer",
    "focus_window": "window_mgmt",
    "invoke_plugin_feature": "plugins",
    "execute_crystallized_skill": "skills",
}


class Expect(BaseModel):
    """What must be true after a step. Checked cheapest first."""

    text: list[str] = Field(
        default_factory=list, description="OCR text that must be on screen (Tier 0)."
    )
    text_absent: list[str] = Field(
        default_factory=list, description="OCR text that must not be on screen."
    )
    question: str | None = Field(
        default=None, description="Yes/no question for Gemma when text is not enough."
    )
    allow_dialog: bool = Field(
        default=False, description="A new dialog after this step is expected."
    )

    @field_validator("text", "text_absent", mode="before")
    @classmethod
    def _one_or_many(cls, v):
        return [v] if isinstance(v, str) else v

    @property
    def empty(self) -> bool:
        return not (self.text or self.text_absent or self.question)


class Step(BaseModel):
    id: str = ""
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    expect: Expect = Field(default_factory=Expect)
    settle_ms: int = 400
    retries: int = Field(
        default=2, description="Extra observe-and-check rounds before escalating. Never re-acts."
    )

    @field_validator("tool")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in ACTION_TOOLS:
            raise ValueError(
                f"tool {v!r} is not allowed in a plan; allowed: {sorted(ACTION_TOOLS)}"
            )
        return v


class Plan(BaseModel):
    name: str = "plan"
    goal: str = ""
    steps: list[Step]

    def model_post_init(self, _ctx: Any) -> None:
        for i, step in enumerate(self.steps):
            if not step.id:
                step.id = f"s{i + 1}"

    @property
    def domains(self) -> list[str]:
        return sorted({ACTION_TOOLS[s.tool] for s in self.steps} - {"core"})

    @classmethod
    def load(cls, path: str | Path) -> Plan:
        return cls.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
