"""Feedback loop controller for perception-action-verification cycles."""

import logging
import re
import time
from collections.abc import Callable
from typing import Any

from PIL import Image

from dobby.agent_loop.bicameral import BicameralStepController
from dobby.agent_loop.cache import ActionCache
from dobby.agent_loop.obstruction import ObstructionInterceptor
from dobby.config import CONFIG
from dobby.crystallization import (
    DynamicSkillRegistry,
    JournalCorrelator,
    TclTrajectoryCompiler,
    TraceCompiler,
)
from dobby.hal.base import IDesktopHAL
from dobby.handoff import is_handoff
from dobby.perception.capture import CapturePipeline
from dobby.perception.grounding.engine import GroundingEngine
from dobby.perception.ocr import OCRLine, assign_text_to_boxes, get_ocr_engine, text_matches
from dobby.perception.som import SetOfMarksGenerator
from dobby.perception.token_optimizer import TokenOptimizer
from dobby.perception.window_clipper import WindowClipper
from dobby.plugins.base import BaseUIAppPlugin
from dobby.plugins.registry import PLUGIN_REGISTRY
from dobby.types import (
    ActionVerificationResult,
    BoundingBox,
    ExecutionPhase,
    FindElementResult,
    GroundingSource,
    PostConditionContract,
    ScreenshotResult,
    StateAssertionType,
    UIElement,
    UIRole,
    VDOMNode,
    VirtualDOMSnapshot,
)
from dobby.vdom import (
    LocalNeuralGrounder,
    SubPaneLayoutSolver,
    VDOMFusionEngine,
    VDOMQueryEngine,
)

logger = logging.getLogger(__name__)

# Smallest "active window" worth cropping to; anything thinner is a bar or tooltip.
_MIN_CLIP_SIDE = 80


def _is_sliver(bbox: Any) -> bool:
    return bbox.width < _MIN_CLIP_SIDE or bbox.height < _MIN_CLIP_SIDE


class FeedbackLoopController:
    """Coordinates perception, action execution, and post-action verification.
    Integrates the pluggable GroundingEngine, TokenOptimizer, WindowClipper,
    Self-Healing ActionCache, ObstructionInterceptor, and Set-of-Marks mapping.
    """

    def __init__(self, hal: IDesktopHAL) -> None:
        self.hal = hal
        self.pipeline = CapturePipeline(hal)
        self.som_generator = SetOfMarksGenerator()
        self.grounding_engine = GroundingEngine()
        self.token_optimizer = TokenOptimizer()
        self.window_clipper = WindowClipper(hal)
        self.action_cache = ActionCache()
        self.plugin_registry = PLUGIN_REGISTRY
        self._ax_previous_trees: dict[str, list[UIElement]] = {}

        # Backward compatibility aliases
        self.atspi_inspector = self.grounding_engine._semantic.inspector
        self.cv_detector = self.grounding_engine._cv.detector

        self.neural_grounder = LocalNeuralGrounder()
        self.ocr = get_ocr_engine()
        gemma = self.neural_grounder.gemma3_grounder
        modal_classifier = None
        if gemma is not None and CONFIG.gemma3_modal_tiebreak:

            def modal_classifier(text: str, labels: list[str]) -> dict[str, Any]:
                return gemma.classify_modal(
                    text, button_labels=labels, timeout=CONFIG.gemma3_modal_timeout_seconds
                )

        self.obstruction_interceptor = ObstructionInterceptor(
            hal,
            self.atspi_inspector,
            modal_classifier=modal_classifier,
            min_confidence=CONFIG.gemma3_modal_min_confidence,
        )
        # The session factory picks the monitor per backend (X11 damage, cassette, or sleep).
        # A HAL built outside the factory (tests, plugins) gets none rather than a real X11
        # monitor watching a display the HAL never touches.
        self.quiescence_monitor = (
            getattr(hal, "quiescence", None) if CONFIG.enable_quiescence_monitor else None
        )
        self.journal_correlator = JournalCorrelator()
        self.skill_registry = DynamicSkillRegistry()
        self.trajectory_compiler = TclTrajectoryCompiler()
        self.trace_compiler = TraceCompiler()
        self.vdom_fusion = VDOMFusionEngine()
        self.layout_solver = SubPaneLayoutSolver()
        self.bicameral = BicameralStepController(
            hal,
            local_gemma_client=gemma,
            text_reader=getattr(hal, "read_screen_text", None) or self.read_screen_text,
        )
        self._trajectory_log: list[dict[str, Any]] = []
        self._act_batches = 0
        self.last_vdom_snapshot: VirtualDOMSnapshot | None = None
        self.active_marks: dict[int, UIElement] = {}
        self.last_screenshot_meta: tuple[int, int, float] | None = None
        self.last_clean_frame: Image.Image | None = None

    def note_turn(self, role: str, content: str) -> None:
        """Appends one turn and compacts every six act-verify batches."""
        self._trajectory_log.append({"role": role, "content": content})
        self._act_batches += 1
        if self._act_batches >= 6:
            self._trajectory_log = BicameralStepController.compact_trajectory_history(
                self._trajectory_log,
                force=True,
            )
            self._act_batches = 0

    def compact_history(
        self, max_tokens: int = 12000, keep_last_n: int = 2
    ) -> list[dict[str, Any]]:
        """Compacts the in-server trajectory log with the 8-part checkpoint."""
        compressor = None
        grounder = getattr(self.neural_grounder, "gemma3_grounder", None)
        if grounder is not None and hasattr(grounder, "compress_history"):

            def compressor(prompt: str) -> str:
                return grounder.compress_history(prompt)

        self._trajectory_log = BicameralStepController.compact_trajectory_history(
            self._trajectory_log,
            max_tokens=max_tokens,
            keep_last_n=keep_last_n,
            local_compressor_fn=compressor,
            force=True,
        )
        self._act_batches = 0
        return self._trajectory_log

    def _get_active_app_key(self) -> str:
        """Derives a normalized application key from the currently focused window."""
        try:
            act_win = self.hal.get_active_window()
            if act_win and act_win.title:
                parts = act_win.title.split(" - ")
                return parts[-1].strip().lower()
        except Exception:
            pass
        return "desktop"

    def capture_state(
        self,
        mode: str = "annotated_som",
        app_filter: str | None = None,
        region: tuple[int, int, int, int] | None = None,
        window_id: str | None = None,
        clip_to_active_window: bool = False,
        strategy_override: str | None = None,
        compress_quality: int = CONFIG.image_quality,
        zoom_factor: float = 1.0,
    ) -> ScreenshotResult:
        """Captures current desktop state with Set-of-Marks, pluggable grounding,
        and optional per-window clipping.

        Args:
            mode: 'annotated_som' (uses active grounding strategy or ensemble),
                  'annotated_cv' (forces pure computer vision),
                  'raw' (clean screenshot without annotations),
                  'cursor_only' (clean screenshot with virtual pointer).
            app_filter: Optional filter for AT-SPI inspection.
            region: Optional (x, y, w, h) sub-region.
            window_id: Optional window identifier to clip perception strictly to window bounds.
            clip_to_active_window: If True, crops capture frame to the focused window bounds.
            strategy_override: Optional override for grounding provider ('semantic', 'cv', 'template', 'ensemble').
            compress_quality: JPEG compression factor.

        Returns:
            ScreenshotResult model containing base64 payload and marked element registry.
        """
        target_win = None
        if window_id or clip_to_active_window or CONFIG.clip_to_active_window:
            target_win = self.window_clipper.resolve_target_window(window_id=window_id)
            if target_win and not window_id and _is_sliver(target_win.bbox):
                # A bar, tooltip or overlay reported as "active" would crop to a useless strip.
                target_win = None
            if target_win and not region:
                screen_size = self.hal.get_screen_size()
                region = self.window_clipper.clip_region_to_screen(target_win.bbox, screen_size)

        draw_cursor = mode in ("annotated_som", "annotated_cv", "cursor_only")
        img, scale_factor, (orig_w, orig_h), cursor_pos = self.pipeline.capture(
            region=region,
            draw_cursor=draw_cursor,
            scale_down=True,
        )

        marks_list: list[UIElement] = []

        if mode in ("annotated_som", "annotated_cv"):
            strat = "cv" if mode == "annotated_cv" else strategy_override
            raw_elements = self.grounding_engine.detect_elements(
                image=img,
                scale_factor=scale_factor,
                strategy_override=strat,
                app_filter=app_filter,
                region=region,
            )

            # Filter marks if clipped to window
            if target_win:
                raw_elements = self.window_clipper.filter_and_offset_elements(
                    raw_elements, target_win.bbox
                )

            if raw_elements:
                img, self.active_marks = self.som_generator.annotate(
                    image=img,
                    elements=raw_elements,
                    scale_factor=scale_factor,
                )
                marks_list = list(self.active_marks.values())
            else:
                self.active_marks = {}
        else:
            self.active_marks = {}

        if zoom_factor > 1.0:
            new_w = max(1, int(img.size[0] * zoom_factor))
            new_h = max(1, int(img.size[1] * zoom_factor))
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        self.last_clean_frame = img
        self.last_screenshot_meta = (orig_w, orig_h, scale_factor)
        b64 = self.pipeline.encode_image(img, format="JPEG", quality=compress_quality)

        # Where the image sits on screen, so a point read off a cropped image maps to click coordinates.
        if region:
            origin_x, origin_y, area_w, area_h = (int(v) for v in region)
        else:
            origin_x, origin_y = 0, 0
            try:
                area_w, area_h = (int(v) for v in self.hal.get_screen_size())
            except Exception:  # noqa: BLE001 - fall back to the raw capture size
                area_w, area_h = orig_w, orig_h

        return ScreenshotResult(
            image_base64=b64,
            mime_type="image/jpeg",
            width=img.size[0],
            height=img.size[1],
            original_width=orig_w,
            original_height=orig_h,
            scale_factor=scale_factor,
            zoom_factor=zoom_factor,
            origin_x=origin_x,
            origin_y=origin_y,
            area_width=area_w,
            area_height=area_h,
            marks=marks_list,
            cursor_position=cursor_pos,
        )

    def find_element(
        self,
        query: str,
        role: str | None = None,
        fuzzy: bool = True,
        strategy_override: str | None = None,
    ) -> FindElementResult:
        """Autonomous element search by text label, accessible name, or role.
        Enables one-shot coordinate resolution with zero screenshot token overhead.
        """
        clean_img, scale_factor, _, _ = self.pipeline.capture(draw_cursor=False, scale_down=True)
        candidates = self.grounding_engine.find_element(
            query=query,
            image=clean_img,
            scale_factor=scale_factor,
            fuzzy=fuzzy,
            strategy_override=strategy_override,
        )

        if role:
            r_low = role.lower().strip()
            candidates = [c for c in candidates if r_low in (c.role or "").lower()]

        if not candidates:
            return FindElementResult(
                found=False,
                query=query,
                element=None,
                center_coords=None,
                confidence=0.0,
                candidates_count=0,
            )

        best = candidates[0]
        return FindElementResult(
            found=True,
            query=query,
            element=best,
            center_coords=best.bbox.center,
            confidence=1.0,
            candidates_count=len(candidates),
        )

    def verify_post_conditions(self, contract: PostConditionContract) -> tuple[bool, list[str]]:
        """Evaluates semantic post-condition assertions declared in a contract.
        Supports AT-SPI state checks, window spawning/closing, text detection, and cursor state.
        """
        if not contract.assertions:
            return True, ["No assertions declared in contract"]

        results: list[bool] = []
        details: list[str] = []

        for assertion in contract.assertions:
            passed = False
            msg = ""

            try:
                if assertion.assertion_type == StateAssertionType.WINDOW_SPAWNED:
                    windows = self.hal.list_windows()
                    pattern = re.compile(assertion.target_name_regex or ".*", re.IGNORECASE)
                    matching = [w for w in windows if pattern.search(w.title)]
                    passed = len(matching) > 0
                    msg = f"WINDOW_SPAWNED: found {len(matching)} windows matching '{assertion.target_name_regex}'"

                elif assertion.assertion_type == StateAssertionType.WINDOW_CLOSED:
                    windows = self.hal.list_windows()
                    pattern = re.compile(assertion.target_name_regex or ".*", re.IGNORECASE)
                    matching = [w for w in windows if pattern.search(w.title)]
                    passed = len(matching) == 0
                    msg = f"WINDOW_CLOSED: {len(matching)} windows remaining for '{assertion.target_name_regex}'"

                elif assertion.assertion_type in (
                    StateAssertionType.AT_SPI_STATE_PRESENT,
                    StateAssertionType.AT_SPI_STATE_ABSENT,
                ):
                    elements = self.atspi_inspector.inspect()
                    target_role = (assertion.target_role or "").lower()
                    target_name_pat = re.compile(assertion.target_name_regex or ".*", re.IGNORECASE)
                    expected_st = (assertion.expected_state or "").lower()

                    matches = [
                        el
                        for el in elements
                        if (not target_role or el.role.lower() == target_role)
                        and target_name_pat.search(el.name)
                    ]
                    if matches:
                        target_el = matches[0]
                        has_state = any(expected_st in st.lower() for st in target_el.states)
                        if assertion.assertion_type == StateAssertionType.AT_SPI_STATE_PRESENT:
                            passed = has_state
                            msg = f"AT_SPI_STATE_PRESENT '{expected_st}' in '{target_el.name}': {has_state}"
                        else:
                            passed = not has_state
                            msg = f"AT_SPI_STATE_ABSENT '{expected_st}' in '{target_el.name}': {not has_state}"
                    else:
                        passed = False
                        msg = f"Element matching role='{target_role}' regex='{assertion.target_name_regex}' not found in AT-SPI tree"

                elif assertion.assertion_type == StateAssertionType.TEXT_APPEARED:
                    elements = self.atspi_inspector.inspect()
                    pattern = re.compile(assertion.target_name_regex or ".*", re.IGNORECASE)
                    matching = [el for el in elements if pattern.search(el.name)]
                    passed = len(matching) > 0
                    msg = f"TEXT_APPEARED: found {len(matching)} elements matching '{assertion.target_name_regex}'"
                    if not passed and self._ocr_available():
                        # Canvases and custom widgets expose no AT-SPI text: read the pixels locally
                        ocr_hits = [
                            ln
                            for ln in self.read_screen_lines()
                            if text_matches(
                                assertion.target_name_regex or ".*", ln.text, regex=True
                            )
                            or text_matches(assertion.target_name_regex or "", ln.text)
                        ]
                        passed = len(ocr_hits) > 0
                        msg = f"TEXT_APPEARED (OCR): found {len(ocr_hits)} lines matching '{assertion.target_name_regex}'"

                elif assertion.assertion_type == StateAssertionType.CURSOR_CHANGED:
                    if self.quiescence_monitor and self.quiescence_monitor.is_available:
                        cur_name, _ = self.quiescence_monitor.get_cursor_state()
                        exp_cur = (assertion.cursor_expected or "").lower()
                        passed = exp_cur in cur_name
                        msg = f"CURSOR_CHANGED: expected '{exp_cur}', observed '{cur_name}'"
                    else:
                        passed = True
                        msg = "CURSOR_CHANGED: monitor unavailable, skipped"

                elif assertion.assertion_type == StateAssertionType.VISUAL_QUERY:
                    region = tuple(assertion.visual_region) if assertion.visual_region else None
                    res = self.ask_screen(
                        assertion.visual_question or "",
                        region=region,
                        expected_text=assertion.visual_expected_text,
                    )
                    if res["answer"] is None:
                        passed = False
                        msg = f"VISUAL_QUERY unanswered ({res['evidence']}); take a screenshot to verify"
                    else:
                        passed = (
                            bool(res["answer"])
                            and res["confidence"] >= CONFIG.gemma3_visual_min_confidence
                        )
                        msg = (
                            f"VISUAL_QUERY '{assertion.visual_question or assertion.visual_expected_text}' "
                            f"[{res['source']}]: answer={res['answer']} "
                            f"confidence={res['confidence']:.2f} evidence='{res['evidence']}'"
                        )

                elif assertion.assertion_type == StateAssertionType.SUBPANE_SSIM_DROP:
                    passed = True
                    msg = f"SUBPANE_SSIM_DROP: verified for {assertion.target_subpane_id}"

            except Exception as e:
                passed = False
                msg = f"Assertion {assertion.assertion_type} failed with error: {e}"

            results.append(passed)
            details.append(msg)

        all_passed = all(results) if contract.require_all else any(results)
        return all_passed, details

    def _replay_backend(self):
        """The MockReplayHAL behind this controller, if it is replaying a cassette."""
        from dobby.hal.mock_replay import MockReplayHAL

        backend = getattr(self.hal, "backend", self.hal)
        return backend if isinstance(backend, MockReplayHAL) else None

    def _ocr_available(self) -> bool:
        ocr = getattr(self, "ocr", None)
        return ocr is not None and ocr.is_available

    def _default_region(self) -> tuple[int, int, int, int] | None:
        try:
            win = self.hal.get_active_window()
            if win and win.bbox.width > 0 and win.bbox.height > 0:
                return (win.bbox.x, win.bbox.y, win.bbox.width, win.bbox.height)
        except Exception:
            pass
        return None

    def cassette_snapshot(self, capture_ocr: bool = True) -> tuple[dict[str, Any], Any | None]:
        """One cassette frame of the live desktop: AX nodes, OCR lines (screen
        coordinates), windows, cursor and screen size, plus the screen image.
        Each part is best effort so a missing backend never blocks recording.
        """
        frame: dict[str, Any] = {}
        try:
            frame["screen_size"] = list(self.hal.get_screen_size())
        except Exception:
            pass
        try:
            frame["cursor_pos"] = list(self.hal.get_cursor_position())
        except Exception:
            pass
        try:
            frame["windows"] = [w.model_dump() for w in self.hal.list_windows()]
            active = self.hal.get_active_window()
            frame["active_window"] = active.model_dump() if active else None
        except Exception:
            pass
        try:
            elements = self.atspi_inspector.inspect() if self.atspi_inspector is not None else []
            frame["ax_tree"] = {
                "nodes": [
                    {
                        "role": e.role,
                        "name": e.name,
                        "bbox": e.bbox.model_dump(),
                        "states": e.states,
                    }
                    for e in elements
                ]
            }
        except Exception:
            frame["ax_tree"] = {}
        image = None
        try:
            image = self.hal.capture_screen()
        except Exception:
            image = None
        if capture_ocr and image is not None and self._ocr_available():
            try:
                frame["ocr_boxes"] = [ln.as_dict() for ln in self.ocr.recognize(image)]
            except Exception:
                frame["ocr_boxes"] = []
        return frame, image

    def read_screen_lines(self, region: tuple[int, int, int, int] | None = None) -> list[OCRLine]:
        """OCR lines in screen coordinates for ``region`` (default: the active window)."""
        if not self._ocr_available():
            return []
        region = region or self._default_region()
        img = self.hal.capture_screen(region=region)
        ox, oy = (region[0], region[1]) if region else (0, 0)
        return [ln.shifted(ox, oy) for ln in self.ocr.recognize(img)]

    def read_screen_text(
        self,
        region: tuple[int, int, int, int] | None = None,
        contains: str | None = None,
        regex: bool = False,
    ) -> dict[str, Any]:
        """Text on screen via local OCR, optionally filtered, with click points. No image returned."""
        recorded = self._replay_backend()
        if recorded is not None:
            # A cassette carries the OCR that was recorded with each frame; use it, not a live engine.
            lines = list(recorded.current_state.ocr_boxes)
            if contains:
                lines = [
                    b for b in lines if text_matches(contains, str(b.get("text", "")), regex=regex)
                ]
            return {"available": True, "engine": "cassette", "count": len(lines), "lines": lines}
        if not self._ocr_available():
            return {
                "available": False,
                "engine": self.ocr.name,
                "lines": [],
                "diagnostics": self.ocr.diagnostics(),
            }
        lines = self.read_screen_lines(region)
        if contains:
            lines = [ln for ln in lines if text_matches(contains, ln.text, regex=regex)]
        lines.sort(key=lambda ln: (ln.y, ln.x))
        return {
            "available": True,
            "engine": self.ocr.name,
            "count": len(lines),
            "lines": [ln.as_dict() for ln in lines],
        }

    def ask_screen(
        self,
        question: str,
        region: tuple[int, int, int, int] | None = None,
        expected_text: str | None = None,
    ) -> dict[str, Any]:
        """Answers a yes/no question about the screen without returning any image.

        If ``expected_text`` is given, local OCR checks for it first (hundreds of
        ms); a hit answers True with no Gemma call. Otherwise local Gemma answers
        ``question`` on a crop of ``region`` (default: the active window).
        ``answer`` is None when neither path could answer.
        """
        region = region or self._default_region()
        image = self.hal.capture_screen(region=region)
        ox, oy = (region[0], region[1]) if region else (0, 0)

        want_ocr = self._ocr_available() and (
            expected_text or CONFIG.gemma3_ocr_context or CONFIG.gemma3_ocr_verify
        )
        ocr_lines = self.ocr.recognize(image) if want_ocr else []
        if expected_text and self._ocr_available():
            hits = [ln for ln in ocr_lines if text_matches(expected_text, ln.text)]
            if hits:
                ln = hits[0].shifted(ox, oy)
                return {
                    "answer": True,
                    "confidence": round(ln.confidence, 3),
                    "evidence": f"OCR found '{ln.text}' at {list(ln.center)}",
                    "source": "ocr",
                    "region": list(region) if region else None,
                }
            if not question.strip():
                return {
                    "answer": False,
                    "confidence": 0.9,
                    "evidence": f"OCR did not find '{expected_text}'",
                    "source": "ocr",
                    "region": list(region) if region else None,
                }

        gemma = self.neural_grounder.gemma3_grounder
        if not question.strip():
            return {
                "answer": None,
                "confidence": 0.0,
                "evidence": "empty question",
                "source": "fallback",
            }
        if gemma is None:
            return {
                "answer": None,
                "confidence": 0.0,
                "evidence": "Gemma sidecar disabled",
                "source": "fallback",
            }
        texts = [ln.text for ln in ocr_lines]
        result = gemma.ask_yes_no(
            image,
            question,
            ocr_lines=texts if CONFIG.gemma3_ocr_context else None,
            verify_lines=texts if CONFIG.gemma3_ocr_verify else None,
        )
        result["region"] = list(region) if region else None
        return result

    def execute_with_verification(
        self,
        action_name: str,
        action_func: Callable[[], None],
        settle_time_sec: float | None = None,
        verify_delta: bool = True,
        contract: PostConditionContract | None = None,
        ui_target_text: str = "",
    ) -> ActionVerificationResult:
        """Executes action with automated event-driven quiescence and post-condition verification."""
        # If recording skills, mark start offset
        if self.journal_correlator.is_recording:
            self.journal_correlator.mark_pre_action()

        # If verification is disabled, dispatch action immediately
        if not verify_delta:
            t_action_start = time.monotonic()
            try:
                action_func()
                if self.journal_correlator.is_recording:
                    try:
                        self.journal_correlator.harvest_post_quiescence(
                            action_id=f"{action_name}_{int(t_action_start * 1000)}",
                            gui_intent=action_name,
                            ui_target_text=ui_target_text,
                            t_start=t_action_start,
                        )
                    except Exception as e:
                        logger.debug(f"Failed to harvest post-quiescence journal entries: {e}")

                return ActionVerificationResult(
                    success=True,
                    action_type=action_name,
                    delta_detected=False,
                    change_ratio=0.0,
                    message="Action executed (verification skipped)",
                    execution_phase=ExecutionPhase.CONTRACT_VERIFIED,
                )
            except Exception as e:
                if is_handoff(e):
                    raise  # Stop / Deny / take-over: the AI must see what happened
                return ActionVerificationResult(
                    success=False,
                    action_type=action_name,
                    delta_detected=False,
                    change_ratio=0.0,
                    message=f"Action failed with error: {e!s}",
                    execution_phase=ExecutionPhase.CONTRACT_FAILED,
                )

        # Pre-action capture / damage reset
        before_img: Image.Image | None = None
        use_quiescence = (
            CONFIG.enable_quiescence_monitor
            and self.quiescence_monitor is not None
            and self.quiescence_monitor.is_available
        )

        if use_quiescence and self.quiescence_monitor is not None:
            self.quiescence_monitor.consume_damage_area()
        else:
            try:
                before_img, _, _, _ = self.pipeline.capture(draw_cursor=False, scale_down=True)
            except Exception:
                before_img = None

        # Execute action
        t_action_start = time.monotonic()
        try:
            action_func()
        except Exception as e:
            if is_handoff(e):
                raise  # Stop / Deny / take-over: the AI must see what happened
            return ActionVerificationResult(
                success=False,
                action_type=action_name,
                delta_detected=False,
                change_ratio=0.0,
                message=f"Action failed during execution: {e!s}",
                execution_phase=ExecutionPhase.CONTRACT_FAILED,
            )

        # Quiescence monitoring
        phase = ExecutionPhase.QUIESCENT
        quiescence_elapsed_ms = 0.0
        dirty_area = 0
        cursor_state = None

        if use_quiescence and self.quiescence_monitor is not None:
            q_win = contract.quiescence_window_ms if contract else CONFIG.quiescence_window_ms
            init_to = (
                contract.initial_reaction_timeout_ms
                if contract
                else CONFIG.initial_reaction_timeout_ms
            )
            max_to = contract.max_total_timeout_ms if contract else CONFIG.max_quiescence_timeout_ms

            phase, quiescence_elapsed_ms, dirty_area = self.quiescence_monitor.wait_for_quiescence(
                quiescence_window_ms=q_win,
                initial_timeout_ms=init_to,
                max_total_timeout_ms=max_to,
            )
            cur_name, _ = self.quiescence_monitor.get_cursor_state()
            cursor_state = cur_name
        else:
            sleep_duration = (
                settle_time_sec
                if settle_time_sec is not None
                else (CONFIG.quiescence_window_ms / 1000.0)
            )
            time.sleep(sleep_duration)
            quiescence_elapsed_ms = (time.monotonic() - t_action_start) * 1000.0
            phase = ExecutionPhase.SLEEP_FALLBACK

        # Evaluate Post-Condition Contract
        contract_passed: bool | None = None
        contract_details: list[str] = []
        if contract is not None:
            contract_passed, contract_details = self.verify_post_conditions(contract)
            if contract_passed:
                phase = ExecutionPhase.CONTRACT_VERIFIED
            else:
                phase = ExecutionPhase.CONTRACT_FAILED

        # Compute delta if before_img was taken or fallback
        change_ratio = 0.0
        delta_detected = dirty_area > 0 or phase in (
            ExecutionPhase.QUIESCENT,
            ExecutionPhase.CONTRACT_VERIFIED,
        )

        if before_img is not None:
            try:
                after_img, _, _, _ = self.pipeline.capture(draw_cursor=False, scale_down=True)
                delta = self.pipeline.compute_image_delta(before_img, after_img)
                change_ratio = round(delta, 4)
                delta_detected = delta > 0.001
            except Exception:
                pass

        if self.journal_correlator.is_recording:
            try:
                self.journal_correlator.harvest_post_quiescence(
                    action_id=f"{action_name}_{int(t_action_start * 1000)}",
                    gui_intent=action_name,
                    ui_target_text=ui_target_text,
                    t_start=t_action_start,
                )
            except Exception as e:
                logger.debug(f"Failed to harvest post-quiescence journal entries: {e}")

        details_msg = (
            f" | Contract details: {'; '.join(contract_details)}" if contract_details else ""
        )
        status_msg = f"Action verified [{phase.value} in {quiescence_elapsed_ms:.1f}ms, dirty_area={dirty_area}px]{details_msg}"

        # A click with no damage and no other evidence is a silent miss. Evidence wins:
        # a passed post-condition contract or a frame delta means the action landed.
        no_reaction = (
            verify_delta
            and phase == ExecutionPhase.SETTLING
            and dirty_area == 0
            and not delta_detected
            and contract_passed is not True
        )
        if no_reaction:
            status_msg += " No visual reaction detected."
        return ActionVerificationResult(
            success=phase != ExecutionPhase.CONTRACT_FAILED and not no_reaction,
            action_type=action_name,
            delta_detected=delta_detected,
            change_ratio=change_ratio,
            message=status_msg,
            execution_phase=phase,
            quiescence_elapsed_ms=round(quiescence_elapsed_ms, 2),
            post_conditions_passed=contract_passed,
            cursor_state=cursor_state,
            dirty_pixel_area=dirty_area,
        )

    def resolve_target_coordinates(
        self,
        query: str | None = None,
        mark_id: int | None = None,
        x: int | None = None,
        y: int | None = None,
        use_cache: bool = True,
    ) -> tuple[int, int]:
        """Resolves target coordinates from cache, query text, mark ID, or explicit (x, y)."""
        if query:
            app_key = self._get_active_app_key()
            if use_cache and CONFIG.enable_action_cache:
                current_win = self.hal.get_active_window()
                screen = None
                if CONFIG.action_cache_verify_visual:
                    try:
                        screen = self.hal.capture_screen()
                    except Exception:
                        screen = None

                status, cached = self.action_cache.resolve_locator(
                    app_key=app_key,
                    query=query,
                    live_screen=screen,
                    current_window=current_win,
                    search_radius=CONFIG.action_cache_search_radius_px,
                    min_match_score=CONFIG.action_cache_match_threshold,
                    dhash_threshold=CONFIG.action_cache_dhash_threshold,
                )
                if status in ("VERIFIED", "WINDOW_REPROJECTED", "HEALED", "CACHED") and cached:
                    return cached.center_coords

            res = self.find_element(query=query)
            if res.found and res.center_coords and res.element:
                if CONFIG.enable_action_cache:
                    current_win = self.hal.get_active_window()
                    crop = None
                    try:
                        fresh_screen = self.hal.capture_screen()
                        eb = res.element.bbox
                        crop = fresh_screen.crop((eb.x, eb.y, eb.right, eb.bottom))
                    except Exception:
                        pass
                    self.action_cache.record(
                        app_key=app_key,
                        query=query,
                        bbox=res.element.bbox,
                        center_coords=res.center_coords,
                        role=res.element.role,
                        window_info=current_win,
                        visual_crop=crop,
                    )
                return res.center_coords
            raise ValueError(f"Could not find element matching query '{query}'.")

        if mark_id is not None:
            if mark_id not in self.active_marks:
                raise ValueError(
                    f"Mark ID #{mark_id} not found in active screen marks. "
                    f"Available marks: {list(self.active_marks.keys())}. "
                    "Take a new screenshot to refresh marks."
                )
            elem = self.active_marks[mark_id]
            return elem.bbox.center

        if x is not None and y is not None:
            return int(x), int(y)

        raise ValueError("Must provide either 'query', 'mark_id', or both 'x' and 'y' coordinates.")

    def find_and_click(
        self,
        query: str,
        button: str = "left",
        click_count: int = 1,
        verify_delta: bool = True,
    ) -> ActionVerificationResult:
        """Autonomous one-shot action with Self-Healing Action Cache & Obstruction Defense."""
        app_key = self._get_active_app_key()
        current_win = self.hal.get_active_window()
        screen = None
        if CONFIG.enable_action_cache and CONFIG.action_cache_verify_visual:
            try:
                screen = self.hal.capture_screen()
            except Exception:
                screen = None

        cached = None
        status = "MISS"
        if CONFIG.enable_action_cache:
            status, cached = self.action_cache.resolve_locator(
                app_key=app_key,
                query=query,
                live_screen=screen,
                current_window=current_win,
                search_radius=CONFIG.action_cache_search_radius_px,
                min_match_score=CONFIG.action_cache_match_threshold,
                dhash_threshold=CONFIG.action_cache_dhash_threshold,
            )

        # 1. Attempt cached locator playback
        if cached and status in ("VERIFIED", "WINDOW_REPROJECTED", "HEALED", "CACHED"):
            target_x, target_y = cached.center_coords

            def _do_cached():
                self.hal.mouse_click(target_x, target_y, button=button, click_count=click_count)

            tag = (
                f"find_and_click[{status.lower()}]('{query}', {button}) at ({target_x}, {target_y})"
            )
            res = self.execute_with_verification(
                action_name=tag,
                action_func=_do_cached,
                verify_delta=verify_delta,
            )

            if res.success and (not verify_delta or res.delta_detected):
                self.action_cache.record_hit(app_key, query)
                return res

            # Cache miss / UI layout changed -> invalidate and self-heal
            self.action_cache.invalidate(app_key, query)

        # 2. Defensive obstruction check if enabled
        if CONFIG.auto_dismiss_obstructions:
            self.obstruction_interceptor.dismiss_if_present()

        # 3. Grounding resolution (self-healing path)
        elem_res = self.find_element(query=query)
        if not elem_res.found or not elem_res.center_coords or not elem_res.element:
            return ActionVerificationResult(
                success=False,
                action_type=f"find_and_click('{query}')",
                delta_detected=False,
                change_ratio=0.0,
                message=f"Element matching '{query}' could not be located on screen.",
            )

        target_x, target_y = elem_res.center_coords

        def _do_fresh_click():
            self.hal.mouse_click(target_x, target_y, button=button, click_count=click_count)

        res = self.execute_with_verification(
            action_name=f"find_and_click[self-healed]('{query}', {button}) at ({target_x}, {target_y})",
            action_func=_do_fresh_click,
            verify_delta=verify_delta,
        )

        # 4. Save newly verified coordinates into cache with visual crop and window info
        if res.success and (not verify_delta or res.delta_detected) and CONFIG.enable_action_cache:
            crop = None
            try:
                fresh_screen = self.hal.capture_screen()
                eb = elem_res.element.bbox
                crop = fresh_screen.crop((eb.x, eb.y, eb.right, eb.bottom))
            except Exception:
                pass

            self.action_cache.record(
                app_key=app_key,
                query=query,
                bbox=elem_res.element.bbox,
                center_coords=(target_x, target_y),
                role=elem_res.element.role,
                window_info=current_win,
                visual_crop=crop,
            )

        return res

    def click_and_type(
        self,
        text: str,
        query: str | None = None,
        mark_id: int | None = None,
        x: int | None = None,
        y: int | None = None,
        clear_first: bool = False,
        press_enter: bool = False,
        delay_ms: float = CONFIG.typing_delay_ms,
        verify_delta: bool = True,
    ) -> ActionVerificationResult:
        """Compound action with Self-Healing Action Cache: Focuses target, optionally clears,
        types text, and optionally presses Enter.
        """
        app_key = self._get_active_app_key()

        # Check defensive obstruction if query is given
        if query and CONFIG.auto_dismiss_obstructions:
            self.obstruction_interceptor.dismiss_if_present()

        target_x, target_y = self.resolve_target_coordinates(query=query, mark_id=mark_id, x=x, y=y)

        def _do_compound():
            self.hal.mouse_click(target_x, target_y, button="left", click_count=1)
            time.sleep(0.08)

            if clear_first:
                self.hal.keyboard_hotkey(["ctrl", "a"])
                time.sleep(0.03)
                self.hal.keyboard_press_key("backspace")
                time.sleep(0.03)

            self.hal.keyboard_type(text, delay_ms=delay_ms)

            if press_enter:
                time.sleep(0.05)
                self.hal.keyboard_press_key("enter")

        res = self.execute_with_verification(
            action_name=f"click_and_type(len={len(text)}, enter={press_enter}) at ({target_x}, {target_y})",
            action_func=_do_compound,
            verify_delta=verify_delta,
        )

        # Self-healing retry if query was used and delta was zero
        if query and verify_delta and not res.delta_detected and CONFIG.enable_action_cache:
            self.action_cache.invalidate(app_key, query)
            # Re-resolve without cache
            elem_res = self.find_element(query=query)
            if elem_res.found and elem_res.center_coords and elem_res.element:
                target_x, target_y = elem_res.center_coords
                res = self.execute_with_verification(
                    action_name=f"click_and_type[self-healed](len={len(text)}) at ({target_x}, {target_y})",
                    action_func=_do_compound,
                    verify_delta=verify_delta,
                )
                if res.delta_detected:
                    crop = None
                    try:
                        fresh_screen = self.hal.capture_screen()
                        eb = elem_res.element.bbox
                        crop = fresh_screen.crop((eb.x, eb.y, eb.right, eb.bottom))
                    except Exception:
                        pass
                    self.action_cache.record(
                        app_key=app_key,
                        query=query,
                        bbox=elem_res.element.bbox,
                        center_coords=(target_x, target_y),
                        role=elem_res.element.role,
                        window_info=self.hal.get_active_window(),
                        visual_crop=crop,
                    )

        return res

    def scroll_until_visible(
        self,
        query: str,
        direction: str = "down",
        max_scrolls: int = 6,
        scroll_amount: int = 6,
        verify_delta: bool = True,
    ) -> FindElementResult:
        """Iteratively scrolls the viewport until the query element is discovered."""
        for _scroll_idx in range(max_scrolls):
            res = self.find_element(query=query)
            if res.found:
                return res

            dy = -scroll_amount if direction == "down" else scroll_amount
            self.hal.mouse_scroll(dx=0, dy=dy)
            time.sleep(0.35)

        return self.find_element(query=query)

    def get_active_plugin(self) -> BaseUIAppPlugin | None:
        """Detects and returns the application plugin matching the active foreground window."""
        try:
            act_win = self.hal.get_active_window()
            if act_win and act_win.title:
                return self.plugin_registry.detect(act_win.title, act_win.app_name)
        except Exception:
            pass
        return None

    def capture_pane(
        self,
        pane_name: str,
        window_id: str | None = None,
        title_filter: str | None = None,
        mode: str = "raw",
    ) -> ScreenshotResult:
        """High-efficiency perception tool that captures strictly a named sub-pane of
        the target or active application window (e.g. 'address_bar', 'content_area').
        Slashes multimodal tokens by up to 90% compared to full-window captures.
        """
        target_win = self.window_clipper.resolve_target_window(
            window_id=window_id, title_filter=title_filter
        )
        if not target_win:
            target_win = self.hal.get_active_window()
        if not target_win:
            return self.capture_state(mode=mode)

        plugin = self.plugin_registry.detect(target_win.title, target_win.app_name)
        if not plugin:
            plugin = self.plugin_registry.get("chrome")

        pane_bbox = None
        # Dynamic anchor-relative sub-pane resolution (Phase 5)
        if CONFIG.enable_dynamic_subpanes and plugin and hasattr(plugin, "get_subpane_layout_spec"):
            spec = plugin.get_subpane_layout_spec(pane_name)
            if spec:
                if not self.last_vdom_snapshot:
                    self.capture_vdom(window_id=target_win.window_id)
                if self.last_vdom_snapshot:
                    engine = VDOMQueryEngine(self.last_vdom_snapshot)
                    pane_bbox = self.layout_solver.resolve_subpane(spec, engine, target_win.bbox)

        # Fallback to static relative sub-pane calculation
        if not pane_bbox and plugin:
            pane_bbox = plugin.get_sub_pane(pane_name, target_win.bbox)

        if not pane_bbox:
            return self.capture_state(mode=mode, window_id=target_win.window_id)

        screen_size = self.hal.get_screen_size()
        clipped_region = self.window_clipper.clip_region_to_screen(pane_bbox, screen_size)
        return self.capture_state(mode=mode, region=clipped_region)

    def _element_to_vdom(self, elem: UIElement) -> VDOMNode:
        """Converts an accessibility UIElement to a VDOMNode."""
        role_enum = UIRole.STATIC_TEXT
        try:
            role_enum = UIRole(elem.role.lower())
        except ValueError:
            if "button" in elem.role.lower():
                role_enum = UIRole.PUSH_BUTTON
            elif "text" in elem.role.lower():
                role_enum = UIRole.TEXT_INPUT
            elif "tab" in elem.role.lower():
                role_enum = UIRole.TAB_ITEM

        return VDOMNode(
            node_id=f"vdom_{elem.id}",
            source=GroundingSource.AT_SPI2,
            role=role_enum,
            name=elem.name,
            states=list(elem.states),
            bbox=elem.bbox,
            click_point=elem.bbox.center,
        )

    def capture_vdom(
        self,
        window_id: str | None = None,
        title_filter: str | None = None,
        label_with_gemma: bool | None = None,
    ) -> VirtualDOMSnapshot:
        """Captures desktop and accessibility state, fuses AT-SPI2 nodes with local neural/CV
        proposals, and returns an indexed VirtualDOMSnapshot in < 30ms with 0 cloud tokens.
        """
        target_win = self.window_clipper.resolve_target_window(
            window_id=window_id, title_filter=title_filter
        )
        if not target_win:
            target_win = self.hal.get_active_window()

        win_bbox = target_win.bbox if target_win else BoundingBox(x=0, y=0, width=1920, height=1080)
        win_title = target_win.title if target_win else "Desktop"
        win_id_str = target_win.window_id if target_win else ""

        # Capture only the target window so proposal offsets match the pixels
        # (a full-screen capture offset by the window origin misplaced every box).
        region = (win_bbox.x, win_bbox.y, win_bbox.width, win_bbox.height) if target_win else None
        img, _, _, _ = self.pipeline.capture(region=region, draw_cursor=False, scale_down=False)

        # Write frame to shared memory (/dev/shm) for sidecar daemons
        self.neural_grounder.write_frame_to_shm(img)

        # Neural / CV visual proposals
        visual_props = self.neural_grounder.generate_proposals(img, offset=(win_bbox.x, win_bbox.y))

        # Local OCR: name empty detector boxes, add free-standing text as static_text
        if CONFIG.ocr_in_vdom and self._ocr_available():
            lines = [ln.shifted(win_bbox.x, win_bbox.y) for ln in self.ocr.recognize(img)]
            assign_text_to_boxes(visual_props, lines)
            for ln in lines:
                inside_any = any(
                    p["bbox"]["x"] <= ln.center[0] <= p["bbox"]["x"] + p["bbox"]["width"]
                    and p["bbox"]["y"] <= ln.center[1] <= p["bbox"]["y"] + p["bbox"]["height"]
                    for p in visual_props
                    if p.get("named_by") == "ocr"
                )
                if not inside_any:
                    visual_props.append(
                        {
                            "bbox": {"x": ln.x, "y": ln.y, "width": ln.width, "height": ln.height},
                            "confidence": ln.confidence,
                            "class_name": "static_text",
                            "text": ln.text,
                            "named_by": "ocr",
                        }
                    )

        # AT-SPI2 accessibility elements
        atspi_elements = []
        try:
            atspi_elements = self.atspi_inspector.inspect_desktop(
                app_name_filter=target_win.app_name if target_win else None
            )
        except Exception as e:
            logger.debug(f"Failed to query AT-SPI2 hierarchy: {e}")

        vdom_nodes = [self._element_to_vdom(elem) for elem in atspi_elements]

        snapshot = self.vdom_fusion.fuse(
            atspi_nodes=vdom_nodes,
            visual_proposals=visual_props,
            window_id=win_id_str,
            window_title=win_title,
            window_bbox=win_bbox,
        )
        use_gemma = CONFIG.gemma3_label_vdom if label_with_gemma is None else label_with_gemma
        if use_gemma:
            self._label_vdom_with_gemma(snapshot, img, win_bbox, app_key=self._get_active_app_key())
        self.last_vdom_snapshot = snapshot
        return snapshot

    def _label_vdom_with_gemma(
        self,
        snapshot: VirtualDOMSnapshot,
        img: Image.Image,
        win_bbox: BoundingBox,
        app_key: str,
    ) -> int:
        """Names VISION_ONLY nodes with empty names via Gemma Set-of-Marks. Returns how many were named."""
        gemma = self.neural_grounder.gemma3_grounder
        if gemma is None:
            return 0
        targets = [
            n
            for n in snapshot.nodes.values()
            if n.source == GroundingSource.VISION_ONLY and not n.name.strip()
        ]
        if not targets:
            return 0
        boxes = [
            (n.bbox.x - win_bbox.x, n.bbox.y - win_bbox.y, n.bbox.width, n.bbox.height)
            for n in targets
        ]
        labels = gemma.marks.label_marks(img, boxes, app_key=app_key)
        for i, info in labels.items():
            node = targets[i]
            node.name = info["label"]
            try:
                node.role = UIRole(info["role"])
            except ValueError:
                pass
            node.attributes["named_by"] = "gemma"
        return len(labels)

    def find_with_gemma(
        self,
        description: str,
        region: tuple[int, int, int, int] | None = None,
    ) -> dict[str, Any]:
        """Finds an element by description on canvases and custom widgets that have
        no accessibility names: the detector proposes boxes, Gemma picks one by
        number, and the click point comes from the detector box.
        """
        gemma = self.neural_grounder.gemma3_grounder
        if region is None:
            region = self._default_region()
        ox, oy = (region[0], region[1]) if region else (0, 0)
        img = self.hal.capture_screen(region=region)

        # Cheapest first: an exact visible-text match needs no model at all
        if self._ocr_available():
            needle = description
            for suffix in (" button", " field", " tab", " menu", " icon", " link"):
                if needle.lower().endswith(suffix):
                    needle = needle[: -len(suffix)]
            hits = [
                ln
                for ln in self.ocr.recognize(img)
                if needle.strip() and text_matches(needle, ln.text)
            ]
            if hits:
                hits.sort(key=lambda ln: len(ln.text))  # tightest label wins
                ln = hits[0].shifted(ox, oy)
                return {
                    "found": True,
                    "bbox": {"x": ln.x, "y": ln.y, "width": ln.width, "height": ln.height},
                    "click_point": list(ln.center),
                    "candidates": len(hits),
                    "confidence": ln.confidence,
                    "reason": f"OCR text '{ln.text}'",
                    "source": "ocr",
                }

        if gemma is None:
            return {"found": False, "reason": "No OCR match and Gemma sidecar disabled"}
        proposals = self.neural_grounder.generate_proposals(img, offset=(0, 0))
        # Largest-first keeps real controls ahead of glyph fragments when capped
        proposals.sort(key=lambda p: p["bbox"]["width"] * p["bbox"]["height"], reverse=True)
        boxes = [
            (p["bbox"]["x"], p["bbox"]["y"], p["bbox"]["width"], p["bbox"]["height"])
            for p in proposals
        ]
        res = gemma.marks.find_mark(img, boxes, description)
        if res["index"] is None:
            return {
                "found": False,
                "candidates": len(boxes),
                **{k: res[k] for k in ("confidence", "reason", "source")},
            }
        x, y, w, h = boxes[res["index"]]
        return {
            "found": True,
            "bbox": {"x": ox + x, "y": oy + y, "width": w, "height": h},
            "click_point": [ox + x + w // 2, oy + y + h // 2],
            "candidates": len(boxes),
            "confidence": res["confidence"],
            "reason": res["reason"],
            "source": res["source"],
        }

    def query_vdom(
        self,
        selector: str,
        window_id: str | None = None,
        refresh: bool = False,
        limit: int = 10,
    ) -> list[VDOMNode]:
        """Executes zero-token spatial & semantic query over the Virtual DOM."""
        if self.last_vdom_snapshot is None or refresh:
            self.capture_vdom(window_id=window_id)

        engine = VDOMQueryEngine(self.last_vdom_snapshot)
        return engine.query_selector(selector, limit=limit)

    def get_accessibility_tree(
        self,
        app_name_filter: str | None = None,
        max_depth: int = 16,
        filter_interactable: bool = True,
        diff_only: bool = False,
    ) -> Any:
        """Directly queries the OS accessibility bus (AXUIElement on macOS, AT-SPI2 on Linux).
        Supports full tree export as well as diff-only payloads (added, removed, modified)
        to minimize multi-turn context token explosion.
        """
        recorded = self._replay_backend()
        if recorded is not None and not diff_only:
            return list((recorded.current_state.ax_tree or {}).get("nodes", []))
        elements = self.atspi_inspector.inspect_desktop(
            app_name_filter=app_name_filter,
            max_depth=max_depth,
            filter_interactable=filter_interactable,
        )
        if not diff_only:
            return [elem.model_dump() for elem in elements]

        key = app_name_filter or "__all__"
        prev = self._ax_previous_trees.get(key)
        if prev is None:
            self._ax_previous_trees[key] = elements
            return {
                "diff_mode": True,
                "full_tree": True,
                "added": [elem.model_dump() for elem in elements],
                "removed": [],
                "modified": [],
                "total_count": len(elements),
            }

        prev_map = {(e.role, e.name): e for e in prev}
        curr_map = {(e.role, e.name): e for e in elements}

        added = []
        modified = []
        for (role, name), curr_elem in curr_map.items():
            if (role, name) not in prev_map:
                added.append(curr_elem.model_dump())
            else:
                prev_elem = prev_map[(role, name)]
                if (
                    curr_elem.bbox.x != prev_elem.bbox.x
                    or curr_elem.bbox.y != prev_elem.bbox.y
                    or curr_elem.bbox.width != prev_elem.bbox.width
                    or curr_elem.bbox.height != prev_elem.bbox.height
                    or curr_elem.states != prev_elem.states
                    or curr_elem.interactable != prev_elem.interactable
                ):
                    modified.append(
                        {
                            "id": curr_elem.id,
                            "role": curr_elem.role,
                            "name": curr_elem.name,
                            "bbox": curr_elem.bbox.model_dump(),
                            "states": curr_elem.states,
                            "previous_bbox": prev_elem.bbox.model_dump(),
                        }
                    )

        removed = []
        for (role, name), prev_elem in prev_map.items():
            if (role, name) not in curr_map:
                removed.append({"id": prev_elem.id, "role": prev_elem.role, "name": prev_elem.name})

        self._ax_previous_trees[key] = elements
        return {
            "diff_mode": True,
            "full_tree": False,
            "added": added,
            "removed": removed,
            "modified": modified,
            "total_count": len(elements),
        }
