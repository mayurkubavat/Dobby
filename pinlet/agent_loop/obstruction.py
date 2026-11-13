"""Autonomous Obstruction and Semantic Modal Dialog Triage & Policy Router.

Replaces naive auto-clicking with risk-aware semantic triage:
- Tier 0 (BENIGN_DISMISSIBLE): Auto-dismisses tips, cookie consent banners, telemetry prompts.
- Tier 1 (INFORMATIVE_ACK): Auto-acknowledges completion modals (e.g. 'Synthesis Complete') and extracts text.
- Tier 2 (WORKFLOW_BRANCHING): Halts and escalates multi-choice decisions to the orchestrator.
- Tier 3 (DESTRUCTIVE_OR_SYSTEM): Blocks execution on license failures or destructive file mutations.
"""

import logging
import re
import time
from collections.abc import Callable
from typing import Any

from pinlet.hal.base import IDesktopHAL
from pinlet.perception.atspi import ATSPIInspector
from pinlet.perception.ax import get_accessibility_inspector
from pinlet.types import (
    ModalButtonOption,
    ModalEscalationPayload,
    ModalRiskTier,
    ModalTriageResult,
    UIElement,
)

logger = logging.getLogger(__name__)

# (dialog_text, button_labels) -> {"risk_tier", "suggested_button", "confidence", "reason", "source"}
ModalClassifier = Callable[[str, list[str]], dict[str, Any]]


class SemanticModalRouter:
    """Classifies modal dialogs into risk tiers and routes execution policy."""

    DESTRUCTIVE_KEYWORDS = re.compile(
        r"\b(overwrite|delete|remove|discard|lose changes|force|corrupt|license|flexlm|expired|terminate|kill|reset project|drop)\b",
        re.IGNORECASE,
    )
    BRANCHING_KEYWORDS = re.compile(
        r"\b(out-of-date|unsaved|modified|save changes|re-run|launch|conflict|merge|compile|rebuild|upgrade)\b",
        re.IGNORECASE,
    )
    BENIGN_TITLES = re.compile(
        r"\b(tip of the day|welcome|what's new|cookie|privacy|telemetry|feedback|permission|show notifications)\b",
        re.IGNORECASE,
    )
    INFORMATIVE_TITLES = re.compile(
        r"\b(completed successfully|finished|success|info|notice|summary|report)\b",
        re.IGNORECASE,
    )

    # Buttons a model verdict may ever press on its own. Anything else escalates.
    SAFE_DISMISS_LABELS = {
        "ok",
        "close",
        "got it",
        "dismiss",
        "x",
        "no thanks",
        "acknowledge",
        "not now",
        "later",
        "skip",
    }

    def __init__(
        self, classifier: ModalClassifier | None = None, min_confidence: float = 0.8
    ) -> None:
        self.classifier = classifier
        self.min_confidence = min_confidence
        self.last_source = "rules"
        self.last_reason: str | None = None

    def _ask_classifier(self, text: str, buttons: list[ModalButtonOption]) -> dict[str, Any] | None:
        if self.classifier is None:
            return None
        try:
            verdict = self.classifier(text, [b.label for b in buttons])
        except Exception as e:  # a classifier failure must never auto-click
            logger.warning(f"Modal classifier failed: {e}")
            return None
        if not isinstance(verdict, dict) or verdict.get("source") != "gemma":
            return None
        return verdict

    def classify_and_route(
        self,
        window_title: str,
        body_text: str,
        buttons: list[ModalButtonOption],
    ) -> tuple[ModalRiskTier, ModalButtonOption | None, ModalEscalationPayload | None]:
        """Evaluates dialog title, body text, and button options to determine policy.

        Returns:
            Tuple of (risk_tier, auto_click_button_if_safe, escalation_payload_if_unsafe).
        """
        combined_text = f"{window_title} {body_text}".strip()
        btn_labels = [b.label.lower().strip() for b in buttons]
        self.last_source = "rules"
        self.last_reason = None

        # 1. Tier 3: Destructive mutation or system/license errors (Highest Priority)
        if self.DESTRUCTIVE_KEYWORDS.search(combined_text):
            for b in buttons:
                if re.search(r"\b(cancel|no|abort|exit)\b", b.label, re.IGNORECASE):
                    b.inferred_consequence = "Safe abort"
                else:
                    b.inferred_consequence = "Destructive state mutation"

            payload = ModalEscalationPayload(
                window_title=window_title,
                dialog_body_text=body_text,
                risk_tier=ModalRiskTier.DESTRUCTIVE_OR_SYSTEM,
                detected_buttons=buttons,
            )
            return (ModalRiskTier.DESTRUCTIVE_OR_SYSTEM, None, payload)

        # 2. Tier 0: Benign dismissible dialogs (Tips, cookies, telemetry)
        if self.BENIGN_TITLES.search(combined_text):
            safe_btn = next(
                (
                    b
                    for b in buttons
                    if b.label.lower() in {"close", "ok", "got it", "dismiss", "x", "no thanks"}
                ),
                None,
            )
            if safe_btn:
                safe_btn.inferred_consequence = "Safe dismissal"
                return (ModalRiskTier.BENIGN_DISMISSIBLE, safe_btn, None)

        # 3. Tier 1: Single-button informative notice (Synthesis completed, DRC report)
        if len(buttons) == 1 and btn_labels[0] in {"ok", "close", "acknowledge", "got it"}:
            if not self.BRANCHING_KEYWORDS.search(combined_text):
                buttons[0].inferred_consequence = "Safe acknowledgment"
                return (ModalRiskTier.INFORMATIVE_ACK, buttons[0], None)

        # 3b. Tie-breaker: no rule matched at all, so ask the local model. It may
        # only (a) make the verdict stricter, or (b) approve a known-safe dismiss
        # button with high confidence. Every other outcome escalates as before.
        if not self.BRANCHING_KEYWORDS.search(combined_text):
            verdict = self._ask_classifier(combined_text, buttons)
            if verdict is not None:
                tier_value = verdict.get("risk_tier")
                self.last_source = "gemma"
                self.last_reason = verdict.get("reason")
                if tier_value == ModalRiskTier.DESTRUCTIVE_OR_SYSTEM.value:
                    for b in buttons:
                        b.inferred_consequence = (
                            "Safe abort"
                            if re.search(r"\b(cancel|no|abort|exit)\b", b.label, re.IGNORECASE)
                            else "Destructive state mutation (model verdict)"
                        )
                    payload = ModalEscalationPayload(
                        window_title=window_title,
                        dialog_body_text=body_text,
                        risk_tier=ModalRiskTier.DESTRUCTIVE_OR_SYSTEM,
                        detected_buttons=buttons,
                    )
                    return (ModalRiskTier.DESTRUCTIVE_OR_SYSTEM, None, payload)

                suggested = (verdict.get("suggested_button") or "").lower().strip()
                confident = float(verdict.get("confidence") or 0.0) >= self.min_confidence
                if (
                    tier_value
                    in (ModalRiskTier.BENIGN_DISMISSIBLE.value, ModalRiskTier.INFORMATIVE_ACK.value)
                    and confident
                    and suggested in self.SAFE_DISMISS_LABELS
                ):
                    btn = next((b for b in buttons if b.label.lower().strip() == suggested), None)
                    if btn is not None:
                        btn.inferred_consequence = "Safe dismissal (model verdict)"
                        return (ModalRiskTier(tier_value), btn, None)

        # 4. Tier 2: Workflow branching decision (Halt and escalate to planner)
        for b in buttons:
            lbl = b.label.lower()
            if any(kw in lbl for kw in {"cancel", "no", "abort", "discard", "leave"}):
                b.inferred_consequence = "Preserve current state"
            elif any(
                kw in lbl for kw in {"save", "yes", "re-run", "launch", "apply", "run", "continue"}
            ):
                b.inferred_consequence = "Mutate workflow path"
            else:
                b.inferred_consequence = "Branching choice"

        payload = ModalEscalationPayload(
            window_title=window_title,
            dialog_body_text=body_text,
            risk_tier=ModalRiskTier.WORKFLOW_BRANCHING,
            detected_buttons=buttons,
        )
        return (ModalRiskTier.WORKFLOW_BRANCHING, None, payload)


class ObstructionInterceptor:
    """Intelligent modal dialog interceptor and policy coordinator.

    Scans for blocking dialogs, classifies their risk tier, auto-dismisses safe dialogs, and
    escalates critical/branching choices to the orchestrator.
    """

    def __init__(
        self,
        hal: IDesktopHAL,
        atspi_inspector: ATSPIInspector | None = None,
        modal_classifier: ModalClassifier | None = None,
        min_confidence: float = 0.8,
    ) -> None:
        self.hal = hal
        self.atspi = atspi_inspector or get_accessibility_inspector()
        self.router = SemanticModalRouter(
            classifier=modal_classifier, min_confidence=min_confidence
        )
        self.pending_escalation: ModalEscalationPayload | None = None
        self.pending_buttons: dict[str, ModalButtonOption] = {}

    def inspect_active_dialog(self) -> tuple[str, str, list[ModalButtonOption]] | None:
        """Inspects the accessibility tree and window manager for active modal dialogs.

        Returns (window_title, body_text, buttons) if a dialog is present, else None.
        """
        # 1. Check window hierarchy for transient dialog windows
        try:
            self.hal.list_windows()
            active_win = self.hal.get_active_window()
            active_title = active_win.title if active_win else ""

            # Check if active window is clearly an alert or dialog
            is_dialog_win = any(
                kw in active_title.lower()
                for kw in [
                    "dialog",
                    "alert",
                    "warning",
                    "error",
                    "question",
                    "confirm",
                    "tip",
                    "notice",
                ]
            )
        except Exception:
            active_title = ""
            is_dialog_win = False

        if not self.atspi.is_available:
            return None

        # 2. Inspect AT-SPI desktop elements
        elements = self.atspi.inspect_desktop(filter_interactable=False)
        if not elements:
            return None

        dialog_elements: list[UIElement] = []
        body_texts: list[str] = []
        button_options: list[ModalButtonOption] = []
        dialog_title = active_title

        # Look for dialog containers
        for el in elements:
            role = el.role.lower()
            name = (el.name or "").strip()

            if role in {"dialog", "alert", "notification", "popup menu"}:
                dialog_elements.append(el)
                if name and not dialog_title:
                    dialog_title = name

            elif role in {"push_button", "button", "toggle_button"}:
                if name and el.interactable:
                    btn_id = f"btn_{len(button_options)}"
                    is_focused = any("focused" in st.lower() for st in el.states)
                    button_options.append(
                        ModalButtonOption(
                            button_id=btn_id,
                            label=name,
                            bbox=el.bbox,
                            is_default_focused=is_focused,
                        )
                    )

            elif role in {"label", "static_text", "text", "description"} and name:
                # Accumulate body text
                if len(name) > 3 and name not in body_texts:
                    body_texts.append(name)

        # Must actually be a dialog element or dialog window
        if not dialog_elements and not is_dialog_win:
            return None

        if not button_options:
            return None

        body_str = " ".join(body_texts)
        if not dialog_title and dialog_elements:
            dialog_title = dialog_elements[0].name or "Dialog"

        return (dialog_title or "System Dialog", body_str, button_options)

    def triage_and_resolve(self) -> ModalTriageResult:
        """Performs semantic triage on active obstructions.

        Auto-resolves safe dialogs, or halts and returns an escalation payload.
        """
        inspection = self.inspect_active_dialog()
        if not inspection:
            self.pending_escalation = None
            self.pending_buttons.clear()
            return ModalTriageResult(
                detected=False,
                action_taken="No active modal dialog detected",
            )

        title, body, buttons = inspection
        tier, safe_btn, escalation = self.router.classify_and_route(title, body, buttons)

        if tier == ModalRiskTier.BENIGN_DISMISSIBLE and safe_btn is not None:
            # Auto-dismiss benign dialog
            cx, cy = safe_btn.bbox.center
            self.hal.mouse_click(cx, cy, button="left")
            time.sleep(0.2)
            self.pending_escalation = None
            self.pending_buttons.clear()
            return ModalTriageResult(
                detected=True,
                window_title=title,
                body_text=body,
                risk_tier=tier,
                classified_by=self.router.last_source,
                auto_dismissed=True,
                action_taken=f"Auto-dismissed benign dialog via '{safe_btn.label}' at ({cx}, {cy})",
            )

        elif tier == ModalRiskTier.INFORMATIVE_ACK and safe_btn is not None:
            # Auto-acknowledge informative notice and return summary
            cx, cy = safe_btn.bbox.center
            self.hal.mouse_click(cx, cy, button="left")
            time.sleep(0.2)
            self.pending_escalation = None
            self.pending_buttons.clear()
            return ModalTriageResult(
                detected=True,
                window_title=title,
                body_text=body,
                risk_tier=tier,
                classified_by=self.router.last_source,
                auto_dismissed=True,
                extracted_summary=body,
                action_taken=f"Auto-acknowledged informative notice via '{safe_btn.label}'",
            )

        else:
            # Workflow branching or destructive confirmation: escalate to orchestrator
            self.pending_escalation = escalation
            self.pending_buttons = {b.button_id: b for b in buttons}
            # Also index by normalized label for natural language addressing
            for b in buttons:
                self.pending_buttons[b.label.lower().strip()] = b

            return ModalTriageResult(
                detected=True,
                window_title=title,
                body_text=body,
                risk_tier=tier,
                classified_by=self.router.last_source,
                auto_dismissed=False,
                action_taken=f"BLOCKED: Modal requires orchestrator decision [{tier.value}]",
                escalation=escalation,
            )

    def resolve_pending_modal(
        self,
        button_id: str | None = None,
        button_label: str | None = None,
    ) -> dict[str, Any]:
        """Executes explicit orchestrator choice on an escalated modal."""
        if not self.pending_escalation or not self.pending_buttons:
            return {
                "success": False,
                "error": "No escalated modal dialog is currently pending decision",
            }

        target_btn: ModalButtonOption | None = None

        if button_id and button_id in self.pending_buttons:
            target_btn = self.pending_buttons[button_id]
        elif button_label and button_label.lower().strip() in self.pending_buttons:
            target_btn = self.pending_buttons[button_label.lower().strip()]
        else:
            # Fuzzy match on label
            query = (button_label or button_id or "").lower().strip()
            for _key, btn in self.pending_buttons.items():
                if query in btn.label.lower():
                    target_btn = btn
                    break

        if not target_btn:
            available = [
                f"{b.button_id} ('{b.label}')" for b in self.pending_escalation.detected_buttons
            ]
            return {
                "success": False,
                "error": f"Button '{button_id or button_label}' not found on dialog. Available: {', '.join(available)}",
            }

        cx, cy = target_btn.bbox.center
        self.hal.mouse_click(cx, cy, button="left")
        time.sleep(0.3)

        resolved_title = self.pending_escalation.window_title
        self.pending_escalation = None
        self.pending_buttons.clear()

        return {
            "success": True,
            "dialog_title": resolved_title,
            "clicked_button": target_btn.label,
            "coordinates": (cx, cy),
            "message": f"Successfully resolved modal dialog by clicking '{target_btn.label}'",
        }

    def find_obstructions(self) -> list[UIElement]:
        """Backward-compatible scan returning dismissal button candidates."""
        if not self.atspi.is_available:
            return []

        elements = self.atspi.inspect_desktop(filter_interactable=True)
        dismiss_keywords = [
            "block",
            "dismiss",
            "close",
            "cancel",
            "not now",
            "reject",
            "got it",
            "x",
        ]
        candidates = []

        for elem in elements:
            name_lower = (elem.name or "").lower().strip()
            if elem.interactable and any(
                kw == name_lower or f" {kw} " in f" {name_lower} " for kw in dismiss_keywords
            ):
                candidates.append(elem)

        return candidates

    # Backward compatibility
    def dismiss_if_present(self, max_attempts: int = 2) -> dict[str, Any]:
        """Backward-compatible wrapper mapping to triage_and_resolve."""
        result = self.triage_and_resolve()
        if result.detected and result.auto_dismissed:
            return {
                "obstruction_cleared": True,
                "actions_taken": [result.action_taken] if result.action_taken else [],
                "attempts": 1,
                "triage_result": result.model_dump(),
            }

        # Fallback: check if direct dismissal candidates exist (e.g. standalone browser popups)
        candidates = self.find_obstructions()
        if candidates:
            target = candidates[0]
            cx, cy = target.bbox.center
            self.hal.mouse_click(cx, cy, button="left", click_count=1)
            time.sleep(0.1)
            return {
                "obstruction_cleared": True,
                "actions_taken": [
                    f"Clicked dismissal button '{target.name}' at {target.bbox.center}"
                ],
                "attempts": 1,
                "triage_result": result.model_dump(),
            }

        return {
            "obstruction_cleared": result.auto_dismissed,
            "actions_taken": [result.action_taken] if result.action_taken else [],
            "attempts": 1 if result.detected else 0,
            "triage_result": result.model_dump(),
        }
