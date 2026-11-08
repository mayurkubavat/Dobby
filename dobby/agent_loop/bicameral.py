"""Bicameral Step Controller and Local History Compactor.

Implements the System 1 / System 2 bicameral control paradigm:
1. Tier 0 (<20ms, $0): Deterministic local OCR assertions and Accessibility Tree diffing.
2. Tier 1 (<90ms, $0): On-device SLM verification (Gemma 3 4B-IT / CoreML) for routine actions.
3. Tier 2 (2-4s, $$$): Escalation to Cloud VLM planner only when confidence drops below 0.82,
   an unknown modal dialog appears, or an execution stall is detected.
4. Local History Compaction: 8-Part Invariant COMPACT_PROMPT compressor reducing 60k token
   trajectories down to ~450 tokens.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class VerificationTier(str, Enum):
    """Execution and verification tiers."""

    TIER0_DETERMINISTIC = "tier0_deterministic_ocr_ax"
    TIER1_LOCAL_SLM = "tier1_local_gemma3"
    TIER2_CLOUD_VLM = "tier2_cloud_planner"


class EscalationPredicate(str, Enum):
    """The 7 observable predicates triggering escalation to Cloud System 2."""

    P1_LOW_CONFIDENCE = "p1_confidence_below_threshold"
    P2_UNEXPECTED_MODAL = "p2_unexpected_or_destructive_modal"
    P3_VISUAL_STALL = "p3_visual_damage_stall"
    P4_STRUCTURAL_STALL = "p4_accessibility_tree_stall"
    P5_EXECUTION_HANG = "p5_timeout_or_busy_cursor_hang"
    P6_GROUNDING_MISS = "p6_all_local_grounding_tiers_missed"
    P7_CONTRACT_FAILURE = "p7_post_condition_contract_breach"


class StepExecutionVerdict(BaseModel):
    """Result of bicamerally guarded step execution."""

    success: bool
    tier_used: VerificationTier
    escalated_to_cloud: bool = False
    escalation_reason: EscalationPredicate | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    tokens_consumed: int = 0
    action_result: dict[str, Any] | None = None


class StateCheckpoint(BaseModel):
    """Pydantic-validated 8-part invariant trajectory checkpoint."""

    user_instructions: str = Field(default="", description="Verbatim goal and success criteria")
    task_template: list[str] = Field(
        default_factory=list, description="Checklist with [x], [/], [ ]"
    )
    constraints: list[str] = Field(default_factory=list, description="Hard guardrails")
    actions_taken: list[str] = Field(
        default_factory=list, description="Chronological summary of turns"
    )
    errors_and_fixes: list[str] = Field(
        default_factory=list, description="Errors and verified workarounds"
    )
    progress_metrics: str = Field(default="", description="Quantitative completion state")
    current_screen_state: str = Field(default="", description="Active window, pane, modal, cursor")
    next_immediate_step: str = Field(
        default="", description="Preconditions and recommended next primitive"
    )
    compressor: str = Field(default="gemma3", description="Model or heuristic used")
    covers_turns: tuple[int, int] = Field(
        default=(0, 0), description="Range of turn indices compacted"
    )


COMPACT_PROMPT_TEMPLATE = """You are a deterministic Desktop Trajectory State Compressor.
Compress the provided conversation history turns into the following 8-part invariant schema:

1. [USER_INSTRUCTIONS]: Verbatim primary user objective and success criteria.
2. [TASK_TEMPLATE]: Active sub-goal checklist with [x] completed, [/] in-progress, [ ] pending.
3. [CONSTRAINTS]: Hard guardrails (forbidden actions, read-only paths, rate limits).
4. [ACTIONS_TAKEN]: Chronological summary of compacted turns (e.g., T1: Opened app -> T2: Typed query).
5. [ERRORS_AND_FIXES]: Failed actions, exact error messages, and verified corrective workarounds.
6. [PROGRESS_METRICS]: Quantitative completion state (e.g., '3/5 steps completed').
7. [CURRENT_SCREEN_STATE]: Active window title, focused sub-pane, modal status, cursor coordinates.
8. [NEXT_IMMEDIATE_STEP]: Exact preconditions and recommended next primitive action.
"""


class BicameralStepController:
    """Verifies routine actions locally and escalates to the cloud planner on doubt.

    Local System 1 checks each step; Cloud System 2 is consulted only when a step is
    ambiguous or fails.
    """

    def __init__(
        self,
        hal: Any,
        local_gemma_client: Any | None = None,
        cloud_vlm_client: Any | None = None,
        min_local_confidence: float = 0.82,
        text_reader: Any | None = None,
        settle_timeout_s: float = 1.5,
        settle_poll_s: float = 0.15,
        min_change_ratio: float = 0.002,
    ) -> None:
        self.hal = hal
        # read_screen_text(contains=...) -> {"available", "count", ...}. The controller passes
        # its local OCR lane; the replay HAL provides its own.
        self.text_reader = text_reader or getattr(hal, "read_screen_text", None)
        self.local_gemma = local_gemma_client
        self.cloud_vlm = cloud_vlm_client
        self.min_local_confidence = min_local_confidence
        # Pixel fallback for expect_ax_change where the platform has no accessibility diff (macOS).
        self.settle_timeout_s = settle_timeout_s
        self.settle_poll_s = settle_poll_s
        self.min_change_ratio = min_change_ratio

    def _grab(self) -> Any:
        if not hasattr(self.hal, "capture_screen"):
            return None
        try:
            return self.hal.capture_screen()
        except Exception:
            return None

    def _screen_change(self, before: Any) -> float:
        """Largest pixel change against ``before`` seen within the settle window."""
        import time

        from dobby.perception.capture import CapturePipeline

        def small(img: Any) -> Any:
            # compute_image_delta walks every pixel in Python; a thumbnail keeps each poll fast.
            thumb = img.convert("RGB")
            thumb.thumbnail((480, 480))
            return thumb

        base = small(before)
        deadline = time.monotonic() + self.settle_timeout_s
        ratio = 0.0
        while True:
            after = self._grab()
            if after is not None:
                ratio = max(ratio, CapturePipeline.compute_image_delta(base, small(after)))
                if ratio >= self.min_change_ratio:
                    return ratio
            if time.monotonic() >= deadline:
                return ratio
            time.sleep(self.settle_poll_s)

    def _hard_escalation(
        self,
        expected: dict[str, Any],
        ax_diff: dict[str, Any],
    ) -> str | None:
        """Predicates that skip the local model and go to the cloud planner.

        Benign modals and async promotion of a known long job do not escalate.
        """
        modal = expected.get("observed_modal")
        allow = set(expected.get("allow_modals") or [])
        risk = expected.get("modal_risk")
        if modal and modal not in allow and risk != "BENIGN_DISMISSIBLE":
            return f"unexpected modal: {modal} ({risk or 'unclassified'})"
        phase = expected.get("quiescence_phase")
        if (
            expected.get("requires_visual_change")
            and phase in ("SETTLING", "NO_REACTION")
            and not expected.get("dirty_area")
        ):
            return "visual stall: no damage before the reaction deadline"
        if (
            expected.get("expect_ax_change")
            and expected.get("stall_deadline_exceeded")
            and not ax_diff.get("changed")
        ):
            return "structural stall: accessibility tree did not change"
        if phase == "ASYNC_PROMOTED" and not expected.get("allow_async_promotion", True):
            return "hang: busy cursor past the quiescence ceiling"
        if expected.get("grounding_miss"):
            return "grounding miss"
        if expected.get("contract_failed"):
            return "post-condition contract failed after local retry"
        return None

    def _read_text(self, target: str) -> dict[str, Any] | None:
        """OCR result for ``target``, or None when OCR is missing or failed.

        None never counts as "text absent": an unavailable engine must not pass an
        assert_screen_text_not_present check.
        """
        if self.text_reader is None:
            return None
        try:
            res = self.text_reader(contains=target)
        except Exception:
            return None
        if not isinstance(res, dict) or res.get("available") is False or "count" not in res:
            return None
        return res

    def dispatch_hal_action(self, action_name: str, args: dict[str, Any]) -> Any:
        """Dispatches an action to the underlying HAL."""
        if hasattr(self.hal, "dispatch_action"):
            return self.hal.dispatch_action(action_name, args)

        # Fallback to direct method invocation on HAL
        if action_name == "click" or action_name == "mouse_click":
            return self.hal.mouse_click(
                x=args.get("x", 0),
                y=args.get("y", 0),
                button=args.get("button", "left"),
                click_count=args.get("click_count", 1),
            )
        elif action_name == "type" or action_name == "keyboard_type":
            return self.hal.keyboard_type(
                text=args.get("text", ""),
                delay_ms=args.get("delay_ms", 12.0),
            )
        elif action_name == "hotkey" or action_name == "keyboard_hotkey":
            keys = args.get("keys", [])
            if isinstance(keys, str):
                keys = [k.strip() for k in keys.split("+")]
            return self.hal.keyboard_hotkey(keys)
        elif action_name == "press_key" or action_name == "keyboard_press_key":
            return self.hal.keyboard_press_key(args.get("key", args.get("key_name", "enter")))
        elif action_name == "move" or action_name == "mouse_move":
            return self.hal.mouse_move(args.get("x", 0), args.get("y", 0))
        elif action_name == "scroll" or action_name == "mouse_scroll":
            return self.hal.mouse_scroll(args.get("dx", 0), args.get("dy", 0))
        elif action_name == "focus_window":
            return self.hal.focus_window(args.get("window_id", ""))
        else:
            raise ValueError(f"Unknown action: {action_name}")

    def execute_step(
        self,
        action_name: str,
        action_args: dict[str, Any],
        expected_outcome: dict[str, Any] | None = None,
    ) -> StepExecutionVerdict:
        """Executes an action with three-tier bicameral verification.

        Args:
            action_name: e.g. 'click', 'type_text', 'hotkey'
            action_args: parameters for the action
            expected_outcome: optional verification targets (assert_screen_text, expect_modal, etc.)
        """
        expected_outcome = expected_outcome or {}
        before = self._grab() if expected_outcome.get("expect_ax_change") else None

        # 1. Dispatch action physically via HAL
        dispatch_res = self.dispatch_hal_action(action_name, action_args)

        # 2. Tier 0: Deterministic Post-Check (<20ms, 0 tokens)
        if "assert_screen_text" in expected_outcome:
            target = expected_outcome["assert_screen_text"]
            ocr_res = self._read_text(target)
            if ocr_res is not None and ocr_res.get("count", 0) > 0:
                return StepExecutionVerdict(
                    success=True,
                    tier_used=VerificationTier.TIER0_DETERMINISTIC,
                    confidence=1.0,
                    reason=f"Tier 0 match: Text '{target}' confirmed on screen.",
                    tokens_consumed=0,
                    action_result=dispatch_res,
                )

        if "assert_screen_text_not_present" in expected_outcome:
            target = expected_outcome["assert_screen_text_not_present"]
            ocr_res = self._read_text(target)
            if ocr_res is not None and ocr_res.get("count", 0) == 0:
                return StepExecutionVerdict(
                    success=True,
                    tier_used=VerificationTier.TIER0_DETERMINISTIC,
                    confidence=1.0,
                    reason=f"Tier 0 match: Text '{target}' confirmed dismissed/absent.",
                    tokens_consumed=0,
                    action_result=dispatch_res,
                )

        # Check AX diff if supported
        ax_diff: dict[str, Any] = {}
        if hasattr(self.hal, "get_accessibility_tree"):
            try:
                ax_diff = self.hal.get_accessibility_tree(diff_only=True)
                if expected_outcome.get("expect_ax_change", False) and ax_diff.get(
                    "changed", False
                ):
                    return StepExecutionVerdict(
                        success=True,
                        tier_used=VerificationTier.TIER0_DETERMINISTIC,
                        confidence=0.95,
                        reason="Tier 0 match: Structural accessibility tree change confirmed.",
                        tokens_consumed=0,
                        action_result=dispatch_res,
                    )
            except Exception:
                pass

        if (
            expected_outcome.get("expect_ax_change")
            and ax_diff.get("available") is False
            and before is not None
        ):
            # No accessibility diff here, so "the UI changed" is checked on pixels instead of
            # asking the local model a vague "did it succeed?" about a single frame.
            ratio = self._screen_change(before)
            if ratio >= self.min_change_ratio:
                return StepExecutionVerdict(
                    success=True,
                    tier_used=VerificationTier.TIER0_DETERMINISTIC,
                    confidence=0.9,
                    reason=f"Tier 0 match: screen changed ({ratio:.1%} of pixels); no accessibility diff on this platform.",
                    tokens_consumed=0,
                    action_result=dispatch_res,
                )
            ax_diff = {**ax_diff, "pixel_change": ratio}
            if "visual_question" not in expected_outcome:
                return StepExecutionVerdict(
                    success=False,
                    tier_used=VerificationTier.TIER2_CLOUD_VLM,
                    escalated_to_cloud=True,
                    confidence=0.0,
                    reason=(
                        f"Not verified: the screen did not change within {self.settle_timeout_s:.1f}s "
                        "and this platform has no accessibility diff. Look at the screen before the next step."
                    ),
                    tokens_consumed=0,
                    action_result=dispatch_res,
                )

        hard = self._hard_escalation(expected_outcome, ax_diff)
        local_eval: dict[str, Any] | None = None
        if hard:
            local_eval = {"verified": False, "confidence": 0.0, "reason": hard}
        elif self.local_gemma is not None:
            try:
                if hasattr(self.local_gemma, "evaluate_step"):
                    image = None
                    if hasattr(self.hal, "capture_screen"):
                        try:
                            image = self.hal.capture_screen()
                        except Exception:
                            image = None
                    fn = self.local_gemma.evaluate_step
                    kwargs: dict[str, Any] = {
                        "action": action_name,
                        "args": action_args,
                        "expected": expected_outcome,
                        "ax_diff": ax_diff,
                    }
                    if "image" in inspect.signature(fn).parameters:
                        kwargs["image"] = image
                    local_eval = fn(**kwargs)
                elif hasattr(self.local_gemma, "verify_step"):
                    local_eval = self.local_gemma.verify_step(
                        action=action_name,
                        expected=expected_outcome,
                    )

                if local_eval and local_eval.get("verified", False):
                    conf = float(local_eval.get("confidence", 0.85))
                    if conf >= self.min_local_confidence:
                        return StepExecutionVerdict(
                            success=True,
                            tier_used=VerificationTier.TIER1_LOCAL_SLM,
                            confidence=conf,
                            reason=f"Tier 1 SLM verified: {local_eval.get('reason', 'Step visually confirmed')}",
                            tokens_consumed=0,
                            action_result=dispatch_res,
                        )
            except Exception as e:
                local_eval = {
                    "verified": False,
                    "confidence": 0.0,
                    "reason": f"Local evaluation exception: {e}",
                }

        # 4. Tier 2: Cloud System 2 Planner Escalation (Cloud VLM)
        low_conf = local_eval.get("confidence", 0.0) if local_eval else 0.0
        escalation_reason = (
            f"Escalation triggered: local confidence {low_conf:.2f} < {self.min_local_confidence}"
        )
        if local_eval and "reason" in local_eval:
            escalation_reason += f" ({local_eval['reason']})"

        if self.cloud_vlm is not None and hasattr(self.cloud_vlm, "diagnose_and_replan"):
            cloud_res = self.cloud_vlm.diagnose_and_replan(
                action=action_name,
                args=action_args,
                expected=expected_outcome,
                reason=escalation_reason,
            )
            return StepExecutionVerdict(
                success=cloud_res.get("success", False),
                tier_used=VerificationTier.TIER2_CLOUD_VLM,
                escalated_to_cloud=True,
                confidence=cloud_res.get("confidence", 0.90),
                reason=cloud_res.get("reason", escalation_reason),
                tokens_consumed=cloud_res.get("tokens_billed", 1650),
                action_result=dispatch_res,
            )

        # If no cloud planner configured, return escalation verdict
        return StepExecutionVerdict(
            success=False,
            tier_used=VerificationTier.TIER2_CLOUD_VLM,
            escalated_to_cloud=True,
            confidence=low_conf,
            reason=escalation_reason,
            tokens_consumed=0,
            action_result=dispatch_res,
        )

    @staticmethod
    def compact_trajectory_history(
        messages: list[dict[str, Any]],
        max_tokens: int = 12000,
        keep_last_n: int = 2,
        local_compressor_fn: Callable[[str], str] | None = None,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        """Compresses turns 1..N-keep_last_n into an 8-part invariant structured state checkpoint.

        Args:
            messages: List of message objects [{"role": "...", "content": "..."}]
            max_tokens: Threshold to trigger compaction
            keep_last_n: Number of most recent turns to retain raw
            local_compressor_fn: Optional custom callable to invoke local Gemma 3
            force: Compact even when the history is under ``max_tokens``.
        """
        total_text = "".join(str(m.get("content", "")) for m in messages)
        est_tokens = len(total_text) // 4

        if len(messages) <= keep_last_n + 1:
            return messages
        if not force and est_tokens <= max_tokens:
            return messages

        # Separate system message, turns to compact, and recent turns
        system_msg = messages[0] if messages and messages[0].get("role") == "system" else None
        start_idx = 1 if system_msg else 0
        end_idx = max(start_idx, len(messages) - keep_last_n)

        turns_to_compact = messages[start_idx:end_idx]
        recent_turns = messages[end_idx:]

        serialized_turns = json.dumps(turns_to_compact, indent=2)
        checkpoint: StateCheckpoint | None = None

        if local_compressor_fn:
            prompt = f"{COMPACT_PROMPT_TEMPLATE}\n\nHistory to compress:\n{serialized_turns}"
            try:
                compressed_body = local_compressor_fn(prompt)
            except Exception:
                compressed_body = ""
            checkpoint = _checkpoint_from_text(compressed_body)
            if checkpoint is None:
                body, checkpoint = _heuristic_checkpoint(turns_to_compact)
                compressed_body = compressed_body or body
        else:
            body, checkpoint = _heuristic_checkpoint(turns_to_compact)
            compressed_body = body

        compacted_msg = {
            "role": "user",
            "content": f"[STATE COMPACTOR OUTPUT]\n{compressed_body}",
            "checkpoint": checkpoint.model_dump(),
        }

        result = []
        if system_msg:
            result.append(system_msg)
        result.append(compacted_msg)
        result.extend(recent_turns)
        return result


def _heuristic_checkpoint(turns: list[dict[str, Any]]) -> tuple[str, StateCheckpoint]:
    """Builds the 8-part text the compactor tests lock, plus a structured checkpoint."""
    actions: list[str] = []
    errors: list[str] = []
    for turn in turns:
        content = str(turn.get("content", ""))
        if "error" in content.lower() or "fail" in content.lower():
            errors.append(content[:120].strip())
        elif "action" in content.lower() or "click" in content.lower() or "type" in content.lower():
            actions.append(content[:80].strip())
    actions_line = "; ".join(actions[-5:]) if actions else "Routine navigation steps"
    errors_line = "; ".join(errors[-2:]) if errors else "None"
    body = (
        f"### [COMPACTED TRAJECTORY STATE - {len(turns)} TURNS COMPRESSED]\n"
        f"1. [USER_INSTRUCTIONS]: Continue active task according to original user goal.\n"
        f"2. [TASK_TEMPLATE]: Progressing sub-goals: {len(actions)} actions executed.\n"
        f"3. [CONSTRAINTS]: Read-only / failsafe bounds maintained.\n"
        f"4. [ACTIONS_TAKEN]: {actions_line}\n"
        f"5. [ERRORS_AND_FIXES]: {errors_line}\n"
        f"6. [PROGRESS_METRICS]: Completed {len(turns)} turns compacted.\n"
        f"7. [CURRENT_SCREEN_STATE]: Settled post-action state.\n"
        f"8. [NEXT_IMMEDIATE_STEP]: Inspect latest raw turns and proceed with next planned primitive.\n"
    )
    checkpoint = StateCheckpoint(
        user_instructions="Continue active task according to original user goal.",
        task_template=[f"Progressing sub-goals: {len(actions)} actions executed."],
        constraints=["Read-only / failsafe bounds maintained."],
        actions_taken=actions[-5:] or ["Routine navigation steps"],
        errors_and_fixes=errors[-2:] or ["None"],
        progress_metrics=f"Completed {len(turns)} turns compacted.",
        current_screen_state="Settled post-action state.",
        next_immediate_step="Inspect latest raw turns and proceed with next planned primitive.",
        compressor="heuristic",
        covers_turns=(0, len(turns)),
    )
    return body, checkpoint


def _checkpoint_from_text(text: str) -> StateCheckpoint | None:
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return StateCheckpoint(**data)
    except Exception:
        return None


def _render_checkpoint(checkpoint: StateCheckpoint) -> str:
    actions = (
        "; ".join(checkpoint.actions_taken)
        if checkpoint.actions_taken
        else "Routine navigation steps"
    )
    errors = "; ".join(checkpoint.errors_and_fixes) if checkpoint.errors_and_fixes else "None"
    tasks = (
        "; ".join(checkpoint.task_template)
        if checkpoint.task_template
        else "Progressing sub-goals."
    )
    constraints = (
        "; ".join(checkpoint.constraints)
        if checkpoint.constraints
        else "Read-only / failsafe bounds maintained."
    )
    return (
        f"1. [USER_INSTRUCTIONS]: {checkpoint.user_instructions}\n"
        f"2. [TASK_TEMPLATE]: {tasks}\n"
        f"3. [CONSTRAINTS]: {constraints}\n"
        f"4. [ACTIONS_TAKEN]: {actions}\n"
        f"5. [ERRORS_AND_FIXES]: {errors}\n"
        f"6. [PROGRESS_METRICS]: {checkpoint.progress_metrics}\n"
        f"7. [CURRENT_SCREEN_STATE]: {checkpoint.current_screen_state}\n"
        f"8. [NEXT_IMMEDIATE_STEP]: {checkpoint.next_immediate_step}\n"
    )
