"""Confidence gate for local decisions: act, escalate to the planner, or stop.

Each verb has a risk tier, and each tier has minimum probability and
confidence. A decision that clears its tier acts; one that does not goes to
the planner (Claude) with the reason; a consequential action without explicit
authorization, an unknown verb, or a run past its budget stops. Thresholds are
never lowered to recover from a miss.

Defaults follow the fka.dev Jev harness (0.55 / 0.85 / 0.90) and TypeSafe's
confidence guidance; tune them per prompt from replayed cassettes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from dobby.gemma.decide import Decision, Noul


class Risk(str, Enum):
    """How much harm an action can do, which sets how sure the decider must be."""

    READ = "read"  # looks only: focus, hover, scroll, read text
    NORMAL = "normal"  # reversible changes: click, type into a field, open
    CONSEQUENTIAL = (
        "consequential"  # hard to undo or outward-facing: send, delete, submit, buy, close unsaved
    )


class Verdict(str, Enum):
    """What the gate lets the agent do with a decision."""

    ACT = "act"
    ESCALATE = "escalate"
    STOP = "stop"


DEFAULT_VERB_RISK: dict[str, Risk] = {
    "focus": Risk.READ,
    "hover": Risk.READ,
    "scroll": Risk.READ,
    "read": Risk.READ,
    "wait": Risk.READ,
    "done": Risk.READ,
    "click": Risk.NORMAL,
    "double_click": Risk.NORMAL,
    "type": Risk.NORMAL,
    "hotkey": Risk.NORMAL,
    "press_key": Risk.NORMAL,
    "open": Risk.NORMAL,
    "select": Risk.NORMAL,
    "drag": Risk.NORMAL,
    "send": Risk.CONSEQUENTIAL,
    "submit": Risk.CONSEQUENTIAL,
    "delete": Risk.CONSEQUENTIAL,
    "close": Risk.CONSEQUENTIAL,
    "buy": Risk.CONSEQUENTIAL,
    "publish": Risk.CONSEQUENTIAL,
    "overwrite": Risk.CONSEQUENTIAL,
    "run_command": Risk.CONSEQUENTIAL,
}


@dataclass(frozen=True)
class Threshold:
    """Minimum probability and confidence a decision needs at one risk level."""

    min_p: float
    min_confidence: float


@dataclass
class GatePolicy:
    """Thresholds and verb risks the gate applies to local decisions."""

    thresholds: dict[Risk, Threshold] = field(
        default_factory=lambda: {
            Risk.READ: Threshold(0.50, 0.30),
            Risk.NORMAL: Threshold(0.55, 0.35),
            Risk.CONSEQUENTIAL: Threshold(0.85, 0.75),
        }
    )
    verb_risk: dict[str, Risk] = field(default_factory=lambda: dict(DEFAULT_VERB_RISK))
    min_margin: float = 0.10  # top-two gap; a near tie escalates even above threshold
    min_coverage: float = 0.50  # share of Gemma's mass on a valid label
    min_authorization: float = 0.90  # consequential actions need this much evidence the user asked
    done_min_p: float = 0.90  # "done" needs a verified-completion Noul at least this high
    max_decisions: int = 30
    max_mutations: int = 20
    max_repeats: int = 2


@dataclass(frozen=True)
class GateResult:
    """The gate's verdict on one decision, with the reason and the risk it judged."""

    verdict: Verdict
    reason: str
    risk: Risk | None = None

    @property
    def act(self) -> bool:
        """True when the verdict lets the agent act."""
        return self.verdict is Verdict.ACT

    def as_dict(self) -> dict[str, Any]:
        """The result as a JSON-ready dict."""
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "risk": self.risk.value if self.risk else None,
        }


Authorization = None | bool | float | Noul


def _auth_p(authorized: Authorization) -> float:
    if authorized is None:
        return 0.0
    if isinstance(authorized, bool):
        return 1.0 if authorized else 0.0
    if isinstance(authorized, Noul):
        return authorized.p_true if authorized.calibrated else 0.0
    return float(authorized)


def gate(
    decision: Decision | Noul,
    verb: str,
    policy: GatePolicy | None = None,
    authorized: Authorization = None,
    completion: Noul | None = None,
) -> GateResult:
    """Decides what to do with ``decision`` for an action of kind ``verb``.

    ``authorized`` is evidence that the user asked for a consequential action:
    True from an explicit instruction, or a Noul judged on the user's request.
    ``completion`` is the verified-completion Noul required before "done".
    """
    pol = policy or GatePolicy()
    risk = pol.verb_risk.get((verb or "").lower())
    if risk is None:
        return GateResult(Verdict.STOP, f"no policy for verb '{verb}'")

    if isinstance(decision, Noul):
        p, conf, margin, coverage = (
            decision.p_true,
            decision.confidence,
            abs(2 * decision.p_true - 1),
            decision.coverage,
        )
        if p < 0.5:
            return GateResult(Verdict.ESCALATE, f"statement judged false (p={p:.2f})", risk)
    else:
        if decision.is_none:
            return GateResult(Verdict.ESCALATE, "none of the options fit", risk)
        p, conf, margin, coverage = (
            decision.p,
            decision.confidence,
            decision.margin,
            decision.coverage,
        )

    if not decision.calibrated:
        return GateResult(Verdict.ESCALATE, "uncalibrated answer (no logprobs)", risk)
    if coverage < pol.min_coverage:
        return GateResult(
            Verdict.ESCALATE, f"low coverage {coverage:.2f}: Gemma wanted another answer", risk
        )

    th = pol.thresholds[risk]
    below = p < th.min_p or conf < th.min_confidence
    if risk is Risk.CONSEQUENTIAL:
        if below:
            return GateResult(
                Verdict.STOP,
                f"consequential action below threshold (p={p:.2f}, conf={conf:.2f})",
                risk,
            )
        if _auth_p(authorized) < pol.min_authorization:
            return GateResult(
                Verdict.STOP, "consequential action without explicit authorization", risk
            )
    elif below:
        return GateResult(
            Verdict.ESCALATE, f"below {risk.value} threshold (p={p:.2f}, conf={conf:.2f})", risk
        )

    if isinstance(decision, Decision) and margin < pol.min_margin:
        return GateResult(Verdict.ESCALATE, f"near tie (margin {margin:.2f})", risk)

    if (verb or "").lower() == "done":
        if completion is None or not completion.calibrated or completion.p_true < pol.done_min_p:
            got = f"{completion.p_true:.2f}" if completion is not None else "none"
            return GateResult(Verdict.ESCALATE, f"completion not verified (p={got})", risk)

    return GateResult(Verdict.ACT, f"{risk.value}: p={p:.2f}, conf={conf:.2f}", risk)


@dataclass
class RunBudget:
    """Hard caps per task, so a confident loop cannot run forever."""

    policy: GatePolicy = field(default_factory=GatePolicy)
    decisions: int = 0
    mutations: int = 0
    _last: str | None = None
    _repeats: int = 0

    def check(self, action_key: str, mutating: bool = True) -> GateResult:
        """Counts one decision; STOP once a cap or the repeat limit is hit."""
        pol = self.policy
        self.decisions += 1
        if mutating:
            self.mutations += 1
        self._repeats = self._repeats + 1 if action_key == self._last else 0
        self._last = action_key
        if self.decisions > pol.max_decisions:
            return GateResult(Verdict.STOP, f"decision budget {pol.max_decisions} spent")
        if self.mutations > pol.max_mutations:
            return GateResult(Verdict.STOP, f"mutation budget {pol.max_mutations} spent")
        if self._repeats >= pol.max_repeats:
            return GateResult(
                Verdict.STOP, f"same action repeated {self._repeats + 1} times: {action_key}"
            )
        return GateResult(Verdict.ACT, "within budget")


__all__ = [
    "DEFAULT_VERB_RISK",
    "GatePolicy",
    "GateResult",
    "Risk",
    "RunBudget",
    "Threshold",
    "Verdict",
    "gate",
]
