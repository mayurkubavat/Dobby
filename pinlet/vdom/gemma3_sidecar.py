"""Local Gemma 3 Multimodal Sidecar Plugin for Phase 4 Grounding and Modal Triage.

Integrates local Google Gemma 3 models served by Ollama (gemma3:4b by default for both
vision sub-panes and text modal triage) with perceptual dHash caching and zero cloud
token overhead. Replies are constrained to JSON schemas via pinlet.gemma.client.
"""

from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from typing import Any

from PIL import Image

from pinlet.config import CONFIG
from pinlet.gemma.client import GemmaClient, GemmaError, encode_image
from pinlet.imagehash import compute_dhash, hamming_distance
from pinlet.types import (
    BoundingBox,
    GroundingSource,
    ModalRiskTier,
    UIRole,
    VDOMNode,
    VirtualDOMSnapshot,
)

logger = logging.getLogger(__name__)


class SemanticROICache:
    """Perceptual LRU cache for visual grounding proposals.

    Provides sub-millisecond retrieval on static or repeating EDA sub-panes.
    """

    def __init__(self, capacity: int = 256, threshold: int = 4) -> None:
        self.capacity = capacity
        self.threshold = threshold
        # Key: cache_key -> (dhash, timestamp, List[Dict[str, Any]])
        self._entries: OrderedDict[str, tuple[int, float, list[dict[str, Any]]]] = OrderedDict()

    def get(self, cache_key: str, dhash: int) -> list[dict[str, Any]] | None:
        """Retrieves cached proposals if key matches and Hamming distance <= threshold."""
        if cache_key in self._entries:
            entry_dhash, _, proposals = self._entries[cache_key]
            if hamming_distance(dhash, entry_dhash) <= self.threshold:
                self._entries.move_to_end(cache_key)
                logger.debug(
                    f"SemanticROICache hit for key '{cache_key}' (Hamming dist={hamming_distance(dhash, entry_dhash)})"
                )
                return proposals

        # Secondary search across all entries for identical perceptual match
        for k, (entry_dhash, _, proposals) in list(self._entries.items()):
            if hamming_distance(dhash, entry_dhash) <= self.threshold:
                self._entries.move_to_end(k)
                logger.debug(
                    f"SemanticROICache perceptual match for key '{k}' (Hamming dist={hamming_distance(dhash, entry_dhash)})"
                )
                return proposals

        return None

    def put(self, cache_key: str, dhash: int, proposals: list[dict[str, Any]]) -> None:
        """Stores proposals in the LRU cache."""
        if cache_key in self._entries:
            self._entries.move_to_end(cache_key)
        self._entries[cache_key] = (dhash, time.time(), proposals)
        if len(self._entries) > self.capacity:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        """Empties the cache."""
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


_GROUND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "elements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "box_2d": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 0, "maximum": 1000},
                        "minItems": 4,
                        "maxItems": 4,
                    },
                    "role": {
                        "type": "string",
                        "enum": [
                            "icon_button",
                            "push_button",
                            "text_input",
                            "dropdown",
                            "waveform_signal",
                            "schematic_pin",
                        ],
                    },
                    "label": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["box_2d", "role", "label", "confidence"],
            },
        }
    },
    "required": ["elements"],
}

_MODAL_TIERS = [t.value for t in ModalRiskTier]

_MODAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "risk_tier": {"type": "string", "enum": _MODAL_TIERS},
        "suggested_button": {"type": ["string", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
    "required": ["risk_tier", "suggested_button", "confidence", "reason"],
}


def _list_prompts():
    from pinlet.gemma.prompts import list_prompts

    return list_prompts()


def _is_blank(image: Image.Image, max_stddev: float = 3.0) -> bool:
    """True for a near-uniform crop (blank, solid fill): there is nothing for Gemma to read."""
    from PIL import ImageStat

    small = image.convert("L").resize((64, 64))
    return ImageStat.Stat(small).stddev[0] < max_stddev


def _fit_image(image: Image.Image, max_side: int) -> Image.Image:
    w, h = image.size
    scale = max_side / float(max(w, h))
    if scale >= 1.0:
        return image
    return image.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)


def _escalate(reason: str) -> dict[str, Any]:
    """Fail-closed modal verdict: never auto-click, hand the choice to the planner."""
    return {
        "risk_tier": ModalRiskTier.WORKFLOW_BRANCHING.value,
        "suggested_button": None,
        "confidence": 0.0,
        "reason": reason,
        "source": "fallback",
    }


class Gemma3Grounder:
    """On-demand local Gemma 3 sidecar (via Ollama) for sub-pane grounding and modal triage.

    All calls go through :class:`GemmaClient`, which constrains replies to a JSON schema and
    records call counts and latency.
    """

    def __init__(
        self,
        endpoint: str = CONFIG.gemma3_endpoint,
        vision_model: str | None = None,
        text_model: str | None = None,
        dhash_threshold: int = CONFIG.gemma3_dhash_threshold,
        cache_capacity: int = CONFIG.gemma3_cache_capacity,
        timeout: float = CONFIG.gemma3_timeout_seconds,
        client: GemmaClient | None = None,
    ) -> None:
        self.client = client or GemmaClient(
            base_url=endpoint,
            timeout=timeout,
            keep_alive=CONFIG.gemma3_keep_alive,
        )
        self.endpoint = self.client.base_url
        # None follows the role settings live (env, set_model_role, ~/.pinlet/models.json, CONFIG).
        self._vision_model = vision_model
        self._text_model = text_model
        self.timeout = timeout
        self.cache = SemanticROICache(capacity=cache_capacity, threshold=dhash_threshold)
        self.cache_hits = 0
        from pinlet.vdom.gemma_marks import GemmaMarkLabeler

        self.marks = GemmaMarkLabeler(self.client, vision_model=vision_model, timeout=timeout)

    @property
    def vision_model(self) -> str:
        """The Ollama model used for vision checks."""
        from pinlet.local_models import model_for

        return self._vision_model or model_for("vision")

    @vision_model.setter
    def vision_model(self, value: str | None) -> None:
        self._vision_model = value

    @property
    def text_model(self) -> str:
        """The Ollama model used for text triage."""
        from pinlet.local_models import model_for

        return self._text_model or model_for("triage")

    @text_model.setter
    def text_model(self, value: str | None) -> None:
        self._text_model = value

    def is_available(self) -> bool:
        """True when Ollama is reachable and both configured models are installed (exact names)."""
        return self.client.has_models({self.vision_model, self.text_model})

    def diagnostics(self) -> dict[str, Any]:
        """Configured models, which are installed, and the client's call stats."""
        installed = self.client.installed_models()
        return {
            "vision_model": self.vision_model,
            "text_model": self.text_model,
            "available": self.is_available(),
            "installed_models": sorted(installed),
            "roi_cache_entries": len(self.cache),
            "roi_cache_hits": self.cache_hits,
            "mark_label_cache_entries": len(self.marks.cache),
            "mark_label_cache_hits": self.marks.cache_hits,
            "prompts": {p["id"]: p["tag"] for p in _list_prompts()},
            **self.client.stats(),
        }

    def ground_roi(
        self,
        image: Image.Image,
        offset: tuple[int, int] = (0, 0),
        anchor_id: str = "default",
        task_intent: str = "ground_ui",
    ) -> list[dict[str, Any]]:
        """Deprecated: asks Gemma to regress box coordinates, which Gemma 3 does poorly.

        Prefer ``self.marks.label_marks`` / ``find_mark`` on detector boxes (see
        LocalNeuralGrounder.ground_subpane). Kept for compatibility.

        Grounds UI elements inside an ROI crop and returns proposals in absolute
        screen coordinates. Returns [] on any Gemma failure so callers fall back
        to the CV/ONNX proposals.
        """
        ox, oy = offset
        w, h = image.size
        if w < 10 or h < 10:
            return []

        img_dhash = compute_dhash(image)
        cache_key = f"{anchor_id}:{task_intent}"
        cached = self.cache.get(cache_key, img_dhash)
        if cached is not None:
            self.cache_hits += 1
            return [
                {
                    "bbox": {
                        "x": ox + item["rel_box"]["x"],
                        "y": oy + item["rel_box"]["y"],
                        "width": item["rel_box"]["width"],
                        "height": item["rel_box"]["height"],
                    },
                    "confidence": item["confidence"],
                    "class_name": item["class_name"],
                    "text": item.get("text", ""),
                    "source": GroundingSource.VISION_ONLY.value,
                }
                for item in cached
            ]

        from pinlet.gemma.prompts import get_prompt

        system_prompt = get_prompt("ground_roi").system
        user_prompt = (
            f"Ground the interactive targets in this '{anchor_id}' crop for '{task_intent}'."
        )

        try:
            data = self.client.chat_json(
                model=self.vision_model,
                system=system_prompt,
                user=user_prompt,
                schema=_GROUND_SCHEMA,
                images=[encode_image(image, quality=92)],
                timeout=self.timeout,
            )
        except GemmaError as e:
            logger.warning(f"Gemma 3 grounding failed: {e}")
            return []

        proposals: list[dict[str, Any]] = []
        raw_items_for_cache: list[dict[str, Any]] = []
        for el in data.get("elements", []) or []:
            box_2d = el.get("box_2d") if isinstance(el, dict) else None
            if not box_2d or len(box_2d) != 4:
                continue
            try:
                ymin, xmin, ymax, xmax = [float(v) for v in box_2d]
                conf = float(el.get("confidence", 0.5))
            except (TypeError, ValueError):
                continue

            ymin = max(0.0, min(1000.0, ymin))
            xmin = max(0.0, min(1000.0, xmin))
            ymax = max(ymin + 5.0, min(1000.0, ymax))
            xmax = max(xmin + 5.0, min(1000.0, xmax))

            rel_x = int(round(xmin * w / 1000.0))
            rel_y = int(round(ymin * h / 1000.0))
            rel_w = max(4, int(round((xmax - xmin) * w / 1000.0)))
            rel_h = max(4, int(round((ymax - ymin) * h / 1000.0)))

            role = el.get("role", "icon_button")
            label = el.get("label", "")
            conf = max(0.0, min(1.0, conf))

            raw_items_for_cache.append(
                {
                    "rel_box": {"x": rel_x, "y": rel_y, "width": rel_w, "height": rel_h},
                    "class_name": role,
                    "text": label,
                    "confidence": conf,
                }
            )
            proposals.append(
                {
                    "bbox": {"x": ox + rel_x, "y": oy + rel_y, "width": rel_w, "height": rel_h},
                    "confidence": conf,
                    "class_name": role,
                    "text": label,
                    "source": GroundingSource.VISION_ONLY.value,
                }
            )

        if raw_items_for_cache:
            self.cache.put(cache_key, img_dhash, raw_items_for_cache)
            logger.info(
                f"Gemma 3 grounded {len(proposals)} elements in '{anchor_id}' (dHash {hex(img_dhash)})"
            )
        return proposals

    def classify_modal(
        self,
        modal_text: str,
        modal_image: Image.Image | None = None,
        button_labels: list[str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Classifies a modal dialog into the same tiers as ``ModalRiskTier``.

        Returns ``{risk_tier, suggested_button, confidence, reason, source}``.
        Fails closed: on any error, unknown tier, or a suggested button that is
        not one of ``button_labels``, the verdict is WORKFLOW_BRANCHING with no
        button, so the dialog is escalated instead of clicked.
        """
        from pinlet.gemma.prompts import get_prompt

        spec = get_prompt("modal_triage")
        system_prompt = spec.system
        buttons_line = ", ".join(f'"{b}"' for b in button_labels) if button_labels else "(unknown)"
        user_prompt = f"Dialog text:\n{modal_text}\n\nButtons: {buttons_line}"

        model = self.text_model
        images = None
        if modal_image is not None:
            model = self.vision_model
            images = [encode_image(modal_image, quality=90)]

        try:
            data = self.client.chat_json(
                model=model,
                system=system_prompt,
                user=user_prompt,
                schema=_MODAL_SCHEMA,
                images=images,
                timeout=timeout or self.timeout,
                max_tokens=spec.max_tokens,
            )
        except GemmaError as e:
            logger.warning(f"Modal classification with Gemma 3 failed: {e}")
            return _escalate(f"Gemma unavailable: {e}")

        tier = data.get("risk_tier")
        if tier not in _MODAL_TIERS:
            return _escalate(f"Gemma returned unknown tier {tier!r}")

        try:
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0

        suggested = data.get("suggested_button")
        safe_tiers = {ModalRiskTier.BENIGN_DISMISSIBLE.value, ModalRiskTier.INFORMATIVE_ACK.value}
        if tier not in safe_tiers:
            suggested = None
        elif suggested is not None and button_labels is not None:
            match = next(
                (b for b in button_labels if b.strip().lower() == str(suggested).strip().lower()),
                None,
            )
            suggested = match
        if tier in safe_tiers and suggested is None:
            # A "safe" verdict with no usable button cannot be acted on: escalate.
            return _escalate(f"Gemma said {tier} but named no valid button")

        return {
            "risk_tier": tier,
            "suggested_button": suggested,
            "confidence": confidence,
            "reason": str(data.get("reason", "")),
            "source": "gemma",
        }

    def ask_yes_no(
        self,
        image: Image.Image,
        question: str,
        timeout: float | None = None,
        ocr_lines: list[str] | None = None,
        verify_lines: list[str] | None = None,
    ) -> dict[str, Any]:
        """Answers a yes/no question about a screen crop (prompt ``yes_no``).

        Returns ``{answer, confidence, evidence, evidence_type, evidence_verified,
        prompt, source}``. ``answer`` is None when Gemma said unsure or could not
        answer (unavailable, timeout, bad reply), so the caller falls back to a
        screenshot. ``ocr_lines`` (OCR text of the same crop) is shown to Gemma.
        Text evidence is checked against ``verify_lines`` (default: ``ocr_lines``);
        a quote OCR cannot find caps confidence at ``CONFIG.gemma3_unverified_text_cap``.
        """
        from pinlet.gemma.prompts import evidence_in_ocr, get_prompt, parse_answer, yes_no_user

        spec = get_prompt("yes_no")
        base = {"prompt": spec.tag, "evidence_type": "none", "evidence_verified": None}
        if image.size[0] < 4 or image.size[1] < 4:
            return {
                **base,
                "answer": None,
                "confidence": 0.0,
                "evidence": "crop too small",
                "source": "fallback",
            }
        if _is_blank(image):
            # Nothing on screen to judge: gemma3:4b answers a confident "no" here (eval_prompts blank.png).
            return {
                **base,
                "answer": None,
                "confidence": 0.0,
                "evidence": "blank crop",
                "source": "blank_crop",
            }
        crop = _fit_image(image, CONFIG.gemma3_visual_max_side)
        if CONFIG.gemma3_yes_no_logprobs:
            scored = self._yes_no_logprob(crop, question, ocr_lines, timeout)
            if scored is not None:
                return scored
        try:
            data = self.client.chat_json(
                model=self.vision_model,
                system=spec.system,
                user=yes_no_user(question, ocr_lines),
                schema=spec.schema,
                images=[encode_image(crop, quality=90)],
                timeout=timeout or self.timeout,
                max_tokens=spec.max_tokens,
            )
        except GemmaError as e:
            return {
                **base,
                "answer": None,
                "confidence": 0.0,
                "evidence": f"Gemma unavailable: {e}",
                "source": "fallback",
            }
        raw = data.get("answer")
        answer = parse_answer(raw)
        try:
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        evidence = str(data.get("evidence", ""))[:300]
        ev_type = data.get("evidence_type")
        if ev_type not in ("text", "visual", "none"):
            ev_type = "visual"  # v1 replies carry no type: nothing to check
        result = {
            **base,
            "answer": answer,
            "confidence": confidence,
            "evidence": evidence,
            "evidence_type": ev_type,
            "source": "gemma",
        }
        if answer is None:
            result["confidence"] = 0.0
            if not (isinstance(raw, str) and raw.strip().lower() == "unsure"):
                result["evidence"] = f"bad answer {raw!r}"
                result["source"] = "fallback"
            return result
        check = verify_lines if verify_lines is not None else ocr_lines
        if ev_type == "text" and check:
            verified = evidence_in_ocr(evidence, check)
            result["evidence_verified"] = verified
            if not verified:
                result["confidence"] = min(confidence, CONFIG.gemma3_unverified_text_cap)
        return result

    def decider(self, timeout: float | None = None, role: str = "vision"):
        """Returns a :class:`~pinlet.gemma.decide.GemmaDecider` for a model role.

        Roles are "text" (screen-state choices), "vision" (crop checks, the default) or
        "visual_pick" (Set-of-Marks).
        """
        from pinlet.gemma.decide import GemmaDecider
        from pinlet.local_models import model_for

        model = self._vision_model if (role == "vision" and self._vision_model) else model_for(role)
        return GemmaDecider(self.client, model=model, timeout=timeout or self.timeout)

    def _yes_no_logprob(
        self,
        crop: Image.Image,
        question: str,
        ocr_lines: list[str] | None,
        timeout: float | None,
    ) -> dict[str, Any] | None:
        """Yes/no from Gemma's token probabilities (prompt ``yes_no_logprob``).

        Returns None when this Ollama gives no logprobs or Gemma is unreachable,
        so :meth:`ask_yes_no` falls back to the generated JSON answer.
        """
        from pinlet.gemma.prompts import get_prompt

        spec = get_prompt("yes_no_logprob")
        lines = [ln.strip() for ln in (ocr_lines or []) if ln and ln.strip()][:40]
        state = "The screenshot crop is attached."
        if lines:
            state += "\nOCR text in this crop:\n" + "\n".join(f"- {ln}" for ln in lines)
        try:
            noul = self.decider(timeout).noul(
                state,
                question,
                images=[encode_image(crop, quality=90)],
                question_id="yes_no_logprob",
                system=spec.system,
            )
        except GemmaError:
            return None
        if not noul.calibrated:
            return None
        band = CONFIG.gemma3_yes_band
        answer = noul.answer(yes_at=band, no_at=1.0 - band)
        return {
            "prompt": spec.tag,
            "answer": answer,
            "confidence": round(noul.confidence, 4) if answer is not None else 0.0,
            "p_yes": round(noul.p_true, 4),
            "coverage": round(noul.coverage, 4),
            "evidence": f"p_yes={noul.p_true:.2f}",
            "evidence_type": "none" if answer is None else "visual",
            "evidence_verified": None,
            "source": "gemma_logprobs",
        }

    def evaluate_step(
        self,
        action: str,
        args: dict[str, Any] | None = None,
        expected: dict[str, Any] | None = None,
        ax_diff: dict[str, Any] | None = None,
        image: Image.Image | None = None,
    ) -> dict[str, Any]:
        """Tier-1 check. Confidence below 0.82 is not a pass."""
        expected = expected or {}
        question = expected.get("visual_question") or f"Did the action '{action}' succeed?"
        if image is None:
            return {
                "verified": False,
                "confidence": 0.0,
                "reason": "no crop for local verification",
            }
        result = self.ask_yes_no(image, str(question))
        confidence = float(result.get("confidence") or 0.0)
        verified = result.get("answer") is True and confidence >= 0.82
        return {
            "verified": verified,
            "confidence": confidence,
            "reason": str(result.get("evidence") or result.get("source") or ""),
        }

    def compress_history(self, prompt: str) -> str:
        """Asks local Gemma for a JSON StateCheckpoint and returns that JSON."""
        from pinlet.agent_loop.bicameral import StateCheckpoint
        from pinlet.gemma.prompts import get_prompt

        data = self.client.chat_json(
            model=self.text_model,
            system=get_prompt("compress_history").system,
            user=prompt,
            schema=StateCheckpoint.model_json_schema(),
            timeout=self.timeout,
            max_tokens=get_prompt("compress_history").max_tokens,
        )
        return json.dumps(StateCheckpoint(**data).model_dump())

    def fuse_with_vdom(
        self,
        vdom_snapshot: VirtualDOMSnapshot,
        proposals: list[dict[str, Any]],
        anchor_id: str | None = None,
    ) -> VirtualDOMSnapshot:
        """Integrates Gemma 3 visual proposals into a VirtualDOMSnapshot.

        Injects nodes with source=GroundingSource.VISION_ONLY into the snapshot nodes
        dictionary.
        """
        next_idx = len(vdom_snapshot.nodes) + 1

        for p in proposals:
            bbox_dict = p.get("bbox", {})
            bx = bbox_dict.get("x", 0)
            by = bbox_dict.get("y", 0)
            bw = bbox_dict.get("width", 10)
            bh = bbox_dict.get("height", 10)
            bbox = BoundingBox(
                x=bx,
                y=by,
                width=bw,
                height=bh,
            )
            role_name = p.get("class_name", "icon_button")
            try:
                role = UIRole(role_name)
            except ValueError:
                role = UIRole.PUSH_BUTTON

            node_id = f"gemma3_{next_idx}"
            attrs: dict[str, str] = {}
            if anchor_id:
                attrs["anchor_id"] = anchor_id

            node = VDOMNode(
                node_id=node_id,
                role=role,
                name=p.get("text", "") or role_name,
                bbox=bbox,
                click_point=(bx + bw // 2, by + bh // 2),
                source=GroundingSource.VISION_ONLY,
                confidence=float(p.get("confidence", 0.9)),
                attributes=attrs,
                parent_id=vdom_snapshot.root_id if vdom_snapshot.root_id else None,
            )
            vdom_snapshot.nodes[node_id] = node

            # If root exists, add child reference
            if vdom_snapshot.root_id and vdom_snapshot.root_id in vdom_snapshot.nodes:
                vdom_snapshot.nodes[vdom_snapshot.root_id].children_ids.append(node_id)

            next_idx += 1

        return vdom_snapshot
