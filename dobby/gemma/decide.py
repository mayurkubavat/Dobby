"""Jev-style typed decisions from a local Gemma model.

Instead of letting Gemma write an answer, the decider shows a closed list of
labelled options, asks for one token, and reads the probability Gemma gives
each option label from the first token's log-probabilities. The result is a
:class:`Decision` with a probability per option, a confidence (how peaked the
distribution is), the margin between the top two options, and the share of
probability mass that landed on a valid label (coverage).

Two primitives, after TypeSafe's Jev:

- :meth:`GemmaDecider.choose` picks one of N named options (a "Choice").
- :meth:`GemmaDecider.noul` gives the probability that a statement is true.

When the Ollama in use returns no logprobs, the decider falls back to the
generated token and marks the decision ``calibrated=False``; the gate
(:mod:`dobby.gemma.gate`) never acts on those alone.

:func:`shortlist` cuts a long element list down to the few that plausibly match
the goal, so Gemma chooses among about a dozen options, never hundreds.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from dobby.gemma.client import GemmaClient, GemmaError

NONE_OPTION = "None of these"
MAX_OPTIONS = 26  # one capital letter per option

CHOICE_SYSTEM = (
    "You choose the single best option for a desktop automation step.\n"
    "Read the goal, the screen state and the options. Options are labelled with capital letters.\n"
    "Reply with exactly one capital letter and nothing else.\n"
    "If no option fits the goal, choose the option that says none of these."
)

NOUL_SYSTEM = (
    "You judge whether a statement about a desktop application is true, based only on the state given.\n"
    "Reply with exactly one word: yes or no."
)


@dataclass(frozen=True)
class Decision:
    """A typed answer with its probability distribution."""

    options: tuple[str, ...]
    probabilities: tuple[float, ...]
    calibrated: bool  # True when read from logprobs, False for a generated fallback
    coverage: float  # probability mass that landed on a valid label, 0..1
    latency_ms: float = 0.0
    raw: str = ""
    source: str = "gemma_logprobs"

    @property
    def index(self) -> int:
        return max(range(len(self.probabilities)), key=lambda i: self.probabilities[i])

    @property
    def choice(self) -> str:
        return self.options[self.index]

    @property
    def p(self) -> float:
        return self.probabilities[self.index]

    @property
    def confidence(self) -> float:
        return choice_confidence(self.probabilities)

    @property
    def margin(self) -> float:
        ranked = sorted(self.probabilities, reverse=True)
        return ranked[0] - (ranked[1] if len(ranked) > 1 else 0.0)

    @property
    def is_none(self) -> bool:
        return self.choice == NONE_OPTION

    def as_dict(self) -> dict[str, Any]:
        return {
            "choice": self.choice,
            "p": round(self.p, 4),
            "confidence": round(self.confidence, 4),
            "margin": round(self.margin, 4),
            "coverage": round(self.coverage, 4),
            "calibrated": self.calibrated,
            "probabilities": {o: round(p, 4) for o, p in zip(self.options, self.probabilities)},
            "latency_ms": round(self.latency_ms, 1),
            "source": self.source,
        }


@dataclass(frozen=True)
class Noul:
    """Probability that a statement is true."""

    p_true: float
    calibrated: bool
    coverage: float
    latency_ms: float = 0.0
    raw: str = ""
    source: str = "gemma_logprobs"

    @property
    def confidence(self) -> float:
        return abs(2.0 * self.p_true - 1.0)

    def answer(self, yes_at: float = 0.7, no_at: float = 0.3) -> bool | None:
        """True / False outside the band, None (unsure) inside it."""
        if self.p_true >= yes_at:
            return True
        if self.p_true <= no_at:
            return False
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "p_true": round(self.p_true, 4),
            "confidence": round(self.confidence, 4),
            "coverage": round(self.coverage, 4),
            "calibrated": self.calibrated,
            "latency_ms": round(self.latency_ms, 1),
            "source": self.source,
        }


def choice_confidence(probabilities: Sequence[float]) -> float:
    """(p_max - 1/n) / (1 - 1/n): 1 when all mass is on one option, 0 when spread evenly."""
    n = len(probabilities)
    if n < 2:
        return 1.0 if n == 1 else 0.0
    p_max = max(probabilities)
    return max(0.0, min(1.0, (p_max - 1.0 / n) / (1.0 - 1.0 / n)))


def option_labels(n: int) -> list[str]:
    if n > MAX_OPTIONS:
        raise ValueError(f"at most {MAX_OPTIONS} options, got {n}; shortlist first")
    return [chr(ord("A") + i) for i in range(n)]


def _norm_token(token: str) -> str:
    return token.strip().strip(".:)\"'*").strip()


def label_distribution(
    top: Iterable[tuple[str, float]],
    labels: Sequence[str],
    case_sensitive: bool = True,
    temperature: float = 1.0,
) -> tuple[list[float], float]:
    """Turns first-token alternatives into a distribution over ``labels``.

    Tokens are matched after stripping spaces and punctuation (" B", "B.", "B)").
    Variants of the same label add up. Returns ``(probabilities, coverage)``,
    where coverage is the matched share of the probability mass Gemma showed.
    A ``temperature`` above 1 flattens over-confident distributions (fit it per
    prompt from replayed cassettes).
    """
    t = temperature if temperature and temperature > 0 else 1.0
    mass = [0.0] * len(labels)
    total = 0.0
    keys = list(labels) if case_sensitive else [l.lower() for l in labels]
    for token, logprob in top:
        p = math.exp(logprob)
        total += p
        tok = _norm_token(token)
        if not case_sensitive:
            tok = tok.lower()
        if tok in keys:
            mass[keys.index(tok)] += p
    matched = sum(mass)
    if matched <= 0.0:
        return [1.0 / len(labels)] * len(labels), 0.0
    coverage = matched / total if total > 0 else 0.0
    if t != 1.0:
        logs = [math.log(m) / t if m > 0 else -1e9 for m in mass]
        hi = max(logs)
        exps = [math.exp(l - hi) for l in logs]
        s = sum(exps)
        return [e / s for e in exps], min(1.0, coverage)
    return [m / matched for m in mass], min(1.0, coverage)


# --------------------------------------------------------------------- shortlist

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {
    "the",
    "a",
    "an",
    "to",
    "of",
    "in",
    "on",
    "and",
    "or",
    "for",
    "with",
    "my",
    "this",
    "that",
    "it",
    "is",
    "click",
    "open",
    "press",
    "button",
}


def _words(text: str) -> set:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOP and len(w) > 1}


def element_text(el: Any) -> str:
    """Label words of an AX element dict or a plain string."""
    if isinstance(el, str):
        return el
    if isinstance(el, dict):
        parts = [
            el.get(k)
            for k in (
                "title",
                "label",
                "name",
                "value",
                "description",
                "placeholder",
                "help",
                "role",
            )
        ]
        return " ".join(str(p) for p in parts if p)
    return str(el)


def shortlist(
    goal: str,
    elements: Sequence[Any],
    k: int = 12,
    text: Callable[[Any], str] = element_text,
) -> list[Any]:
    """The ``k`` elements whose label words best overlap the goal, best first.

    Scoring is word overlap (with prefix matches counted half), so it is fast,
    deterministic and needs no model. Elements with no overlap are kept only to
    fill the list when fewer than ``k`` match, in their original order.
    """
    goal_words = _words(goal)
    scored = []
    for i, el in enumerate(elements):
        words = _words(text(el))
        exact = len(goal_words & words)
        prefix = sum(
            1
            for g in goal_words - words
            for w in words
            if len(g) >= 3 and (w.startswith(g) or g.startswith(w))
        )
        scored.append((exact + 0.5 * prefix, i, el))
    hits = sorted((s for s in scored if s[0] > 0), key=lambda s: (-s[0], s[1]))
    rest = [s for s in scored if s[0] <= 0]
    return [el for _, _, el in (hits + rest)[: max(0, k)]]


# ----------------------------------------------------------------------- decider


def _prompt_system(prompt_id: str, default: str) -> str:
    """System text from the prompt pack (honours local overrides), else ``default``."""
    try:
        from dobby.gemma.prompts import get_prompt

        return get_prompt(prompt_id).system
    except Exception:
        return default


@dataclass
class GemmaDecider:
    """Logit-scored Choice and Noul questions against one local Gemma model."""

    client: GemmaClient
    model: str = "gemma3:4b"
    timeout: float = 25.0
    temperatures: dict[str, float] = field(
        default_factory=dict
    )  # per question id, from calibration

    def choose(
        self,
        state: str,
        question: str,
        options: Sequence[str],
        images: list[str] | None = None,
        add_none: bool = True,
        question_id: str = "choice",
        system: str | None = None,
    ) -> Decision:
        """Picks one of ``options`` (plus "None of these" when ``add_none``).

        ``state`` is the text the decision rests on (goal, window, numbered
        elements); ``images`` are optional base64 crops. Raises
        :class:`GemmaError` when Gemma cannot be reached.
        """
        if system is None:
            system = _prompt_system("decide_choice", CHOICE_SYSTEM)
        opts = [str(o) for o in options]
        if add_none and NONE_OPTION not in opts:
            opts.append(NONE_OPTION)
        if not opts:
            raise ValueError("no options to choose from")
        labels = option_labels(len(opts))
        listing = "\n".join(f"{l}. {o}" for l, o in zip(labels, opts))
        user = f"{state.strip()}\n\nQuestion: {question.strip()}\n\nOptions:\n{listing}\n\nAnswer with one letter."
        start = time.perf_counter()
        reply = self.client.chat_logprobs(
            self.model, system, user, images=images, timeout=self.timeout
        )
        ms = (time.perf_counter() - start) * 1000.0
        top = reply.get("top")
        if top:
            probs, coverage = label_distribution(
                top, labels, temperature=self.temperatures.get(question_id, 1.0)
            )
            return Decision(tuple(opts), tuple(probs), True, coverage, ms, reply.get("content", ""))
        # No logprobs from this Ollama: use the generated letter, uncalibrated.
        tok = _norm_token(reply.get("content", ""))[:1].upper()
        probs = [1.0 if l == tok else 0.0 for l in labels]
        if not any(probs):
            probs = [1.0 / len(labels)] * len(labels)
        return Decision(
            tuple(opts),
            tuple(probs),
            False,
            1.0 if tok in labels else 0.0,
            ms,
            reply.get("content", ""),
            source="gemma_generated",
        )

    def noul(
        self,
        state: str,
        statement: str,
        images: list[str] | None = None,
        question_id: str = "noul",
        system: str = NOUL_SYSTEM,
    ) -> Noul:
        """Probability that ``statement`` is true given ``state`` (and optional crops)."""
        user = f"{state.strip()}\n\nStatement: {statement.strip()}\n\nIs the statement true? Answer yes or no."
        start = time.perf_counter()
        reply = self.client.chat_logprobs(
            self.model, system, user, images=images, timeout=self.timeout
        )
        ms = (time.perf_counter() - start) * 1000.0
        top = reply.get("top")
        if top:
            probs, coverage = label_distribution(
                top,
                ["yes", "no"],
                case_sensitive=False,
                temperature=self.temperatures.get(question_id, 1.0),
            )
            return Noul(probs[0], True, coverage, ms, reply.get("content", ""))
        word = _norm_token(reply.get("content", "")).lower()
        if word.startswith("yes"):
            p, cov = 1.0, 1.0
        elif word.startswith("no"):
            p, cov = 0.0, 1.0
        else:
            p, cov = 0.5, 0.0
        return Noul(p, False, cov, ms, reply.get("content", ""), source="gemma_generated")


__all__ = [
    "NONE_OPTION",
    "Decision",
    "GemmaDecider",
    "GemmaError",
    "Noul",
    "choice_confidence",
    "label_distribution",
    "option_labels",
    "shortlist",
]
