"""Set-of-Marks labeling with local Gemma.

Geometry comes from AT-SPI, the ONNX detector or CV contours. Gemma only sees
the crop with numbered boxes drawn on it and answers with mark numbers:

* ``label_marks``: name and role for each numbered box (fills empty vDOM names).
* ``find_mark``: which numbered box matches a description ("Run Simulation").

Gemma never emits coordinates, so Retina/DPI scaling and VLM coordinate drift
cannot move a click. Labels are cached per app by a dHash of each element patch,
so a toolbar icon is labeled once per app version.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from collections.abc import Sequence
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from pinlet.config import CONFIG
from pinlet.gemma.client import GemmaClient, GemmaError, encode_image
from pinlet.gemma.prompts import get_prompt
from pinlet.types import UIRole

logger = logging.getLogger(__name__)

# Relative box inside the crop: (x, y, w, h)
Box = tuple[int, int, int, int]

LABEL_ROLES = [
    UIRole.ICON_BUTTON.value,
    UIRole.PUSH_BUTTON.value,
    UIRole.TOGGLE_BUTTON.value,
    UIRole.TEXT_INPUT.value,
    UIRole.COMBO_BOX.value,
    UIRole.TAB_ITEM.value,
    UIRole.CHECK_BOX.value,
    UIRole.MENU_ITEM.value,
    UIRole.TREE_NODE.value,
    UIRole.STATIC_TEXT.value,
    UIRole.WAVEFORM_SIGNAL.value,
    UIRole.SCHEMATIC_PIN.value,
    UIRole.SCHEMATIC_NET.value,
]

_LABEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "mark": {"type": "integer", "minimum": 1},
                    "label": {"type": "string"},
                    "role": {"type": "string", "enum": LABEL_ROLES},
                },
                "required": ["mark", "label", "role"],
            },
        }
    },
    "required": ["labels"],
}

_FIND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mark": {"type": ["integer", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string", "maxLength": 120},
    },
    "required": ["mark", "confidence", "reason"],
}

_COLORS = ["#E6194B", "#3CB44B", "#4363D8", "#F58231", "#911EB4", "#008080", "#9A6324", "#800000"]


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_marks(
    image: Image.Image, boxes: Sequence[Box], max_side: int, labels: Sequence[str] | None = None
) -> Image.Image:
    """Downscales ``image`` to ``max_side`` and draws numbered boxes on it.

    Boxes are numbered from 1 in ``boxes`` order, or take the given ``labels`` (e.g.
    letters for a logprob choice).

    Badges are drawn after the downscale so they stay legible.
    """
    w, h = image.size
    scale = min(1.0, max_side / float(max(w, h)))
    out = image.convert("RGB")
    if scale < 1.0:
        out = out.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    draw = ImageDraw.Draw(out)
    font = _font(14)
    for idx, (bx, by, bw, bh) in enumerate(boxes):
        color = _COLORS[idx % len(_COLORS)]
        x0, y0 = int(bx * scale), int(by * scale)
        x1, y1 = int((bx + bw) * scale), int((by + bh) * scale)
        draw.rectangle([x0, y0, x1, y1], outline=color, width=2)
        label = str(labels[idx]) if labels is not None and idx < len(labels) else str(idx + 1)
        tb = draw.textbbox((0, 0), label, font=font)
        tw, th = tb[2] - tb[0] + 6, tb[3] - tb[1] + 6
        ty = y0 - th if y0 >= th else y0
        draw.rectangle([x0, ty, x0 + tw, ty + th], fill=color)
        draw.text((x0 + 3, ty + 2), label, fill="white", font=font)
    return out


def _patch_dhash(image: Image.Image, box: Box) -> int:
    from pinlet.vdom.gemma3_sidecar import compute_dhash

    x, y, w, h = box
    pad = 3
    crop = image.crop(
        (
            max(0, x - pad),
            max(0, y - pad),
            min(image.width, x + w + pad),
            min(image.height, y + h + pad),
        )
    )
    return compute_dhash(crop)


class MarkLabelCache:
    """Per-app LRU of element-patch dHash -> (label, role), matched within a Hamming threshold."""

    def __init__(self, capacity: int = 2048, threshold: int = 4) -> None:
        self.capacity = capacity
        self.threshold = threshold
        self._entries: OrderedDict[tuple[str, int], tuple[str, str]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, app_key: str, dhash: int) -> tuple[str, str] | None:
        """The cached (label, role) for a patch hash, matching within the Hamming threshold."""
        with self._lock:
            exact = self._entries.get((app_key, dhash))
            if exact is not None:
                self._entries.move_to_end((app_key, dhash))
                return exact
            for (app, h), value in self._entries.items():
                if app == app_key and bin(h ^ dhash).count("1") <= self.threshold:
                    return value
        return None

    def put(self, app_key: str, dhash: int, label: str, role: str) -> None:
        """Caches the label and role for a patch hash, evicting the oldest past capacity."""
        with self._lock:
            self._entries[(app_key, dhash)] = (label, role)
            self._entries.move_to_end((app_key, dhash))
            while len(self._entries) > self.capacity:
                self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)


class GemmaMarkLabeler:
    """Labels and selects detector boxes by mark number using a local Gemma vision model."""

    def __init__(
        self,
        client: GemmaClient,
        vision_model: str | None = None,
        max_side: int = CONFIG.gemma3_visual_max_side,
        max_marks: int = CONFIG.gemma3_max_marks,
        timeout: float | None = None,
    ) -> None:
        self.client = client
        self._vision_model = vision_model
        self.max_side = max_side
        self.max_marks = max_marks
        self.timeout = timeout
        self.cache = MarkLabelCache(threshold=CONFIG.gemma3_dhash_threshold)
        self.cache_hits = 0

    @property
    def vision_model(self) -> str:
        """The Ollama model used to label marks."""
        from pinlet.local_models import model_for

        return self._vision_model or model_for("vision")

    def label_marks(
        self, image: Image.Image, boxes: Sequence[Box], app_key: str = "default"
    ) -> dict[int, dict[str, str]]:
        """Returns ``{index_in_boxes: {"label", "role"}}`` for boxes Gemma could name.

        Cached patches are answered without a call; at most ``max_marks`` uncached boxes are
        sent per call. Returns what it has on failure.
        """
        results: dict[int, dict[str, str]] = {}
        pending: list[tuple[int, Box, int]] = []
        for i, box in enumerate(boxes):
            if box[2] < 4 or box[3] < 4:
                continue
            dh = _patch_dhash(image, box)
            hit = self.cache.get(app_key, dh)
            if hit is not None:
                self.cache_hits += 1
                results[i] = {"label": hit[0], "role": hit[1]}
            else:
                pending.append((i, box, dh))

        for start in range(0, len(pending), self.max_marks):
            batch = pending[start : start + self.max_marks]
            marked = draw_marks(image, [b for _, b, _ in batch], self.max_side)
            spec = get_prompt("mark_label")
            system = spec.system
            try:
                data = self.client.chat_json(
                    model=self.vision_model,
                    system=system,
                    user=f"Name boxes 1 to {len(batch)}.",
                    schema=_LABEL_SCHEMA,
                    images=[encode_image(marked, quality=90)],
                    timeout=self.timeout,
                    max_tokens=spec.max_tokens * len(batch) + 20,
                )
            except GemmaError as e:
                logger.warning(f"Gemma mark labeling failed: {e}")
                break
            for item in data.get("labels", []) or []:
                try:
                    mark = int(item.get("mark"))
                except (TypeError, ValueError):
                    continue
                label = str(item.get("label", "")).strip()[:80]
                role = item.get("role")
                if not (1 <= mark <= len(batch)) or not label or role not in LABEL_ROLES:
                    continue
                idx, _, dh = batch[mark - 1]
                results[idx] = {"label": label, "role": role}
                self.cache.put(app_key, dh, label, role)
        return results

    def find_mark(
        self, image: Image.Image, boxes: Sequence[Box], description: str
    ) -> dict[str, Any]:
        """Picks the box matching ``description``.

        Returns ``{"index": int|None, "confidence", "reason", "source"}`` where index is into
        ``boxes``; None when nothing matches or Gemma fails.
        """
        boxes = list(boxes)[: self.max_marks]
        if not boxes or not description.strip():
            return {
                "index": None,
                "confidence": 0.0,
                "reason": "no candidates",
                "source": "fallback",
            }
        marked = draw_marks(image, boxes, self.max_side)
        spec = get_prompt("mark_find")
        system = spec.system
        try:
            data = self.client.chat_json(
                model=self.vision_model,
                system=system,
                user=f"Description: {description}\nCandidates: 1 to {len(boxes)}.",
                schema=_FIND_SCHEMA,
                images=[encode_image(marked, quality=90)],
                timeout=self.timeout,
                max_tokens=spec.max_tokens,
            )
        except GemmaError as e:
            return {
                "index": None,
                "confidence": 0.0,
                "reason": f"Gemma unavailable: {e}",
                "source": "fallback",
            }
        mark = data.get("mark")
        try:
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        if not isinstance(mark, int) or not (1 <= mark <= len(boxes)):
            return {
                "index": None,
                "confidence": confidence,
                "reason": str(data.get("reason", "")),
                "source": "gemma",
            }
        return {
            "index": mark - 1,
            "confidence": confidence,
            "reason": str(data.get("reason", "")),
            "source": "gemma",
        }
