"""Local OCR lane: screen pixels to text lines with boxes, at zero model-token cost.

Backends:

* ``AppleVisionOCR`` (macOS): Apple's built-in Vision framework
  (VNRecognizeTextRequest). Nothing to download; needs pyobjc-framework-Vision.
* ``RapidOCREngine`` (Linux/Windows): PP-OCRv4 ONNX models that ship inside the
  ``rapidocr_onnxruntime`` wheel (~16 MB). Note its default recognizer is the
  Chinese/English PP-OCR model, which often drops spaces between English words;
  use :func:`normalize_text` when matching.

``get_ocr_engine()`` picks one from ``CONFIG.ocr_engine`` ("auto" chooses by
platform). Every engine returns :class:`OCRLine` in the coordinates of the
image it was given; callers add the capture offset.
"""

from __future__ import annotations

import io
import logging
import re
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from PIL import Image

from dobby.config import CONFIG

logger = logging.getLogger(__name__)


@dataclass
class OCRLine:
    text: str
    x: int
    y: int
    width: int
    height: int
    confidence: float

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    def shifted(self, dx: int, dy: int) -> OCRLine:
        return OCRLine(
            self.text, self.x + dx, self.y + dy, self.width, self.height, self.confidence
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "bbox": {"x": self.x, "y": self.y, "width": self.width, "height": self.height},
            "click_point": list(self.center),
            "confidence": round(self.confidence, 3),
        }


_WS = re.compile(r"[\s_]+")


def normalize_text(text: str) -> str:
    """Lower-case and drop whitespace/underscores so 'Run Simulation' matches 'RunSimulation'."""
    return _WS.sub("", text or "").lower()


def text_matches(needle: str, haystack: str, regex: bool = False) -> bool:
    if regex:
        try:
            return re.search(needle, haystack, re.IGNORECASE) is not None
        except re.error:
            return False
    return normalize_text(needle) in normalize_text(haystack)


class OCREngine:
    name = "none"

    @property
    def is_available(self) -> bool:
        return False

    def _recognize(self, image: Image.Image) -> list[OCRLine]:  # pragma: no cover - abstract
        raise NotImplementedError

    def recognize(self, image: Image.Image) -> list[OCRLine]:
        """Text lines in ``image`` coordinates. Returns [] on any backend error."""
        if not self.is_available or image.width < 8 or image.height < 8:
            return []
        try:
            lines = self._recognize(image)
        except Exception as e:
            logger.warning(f"OCR ({self.name}) failed: {e}")
            return []
        min_conf = CONFIG.ocr_min_confidence
        return [ln for ln in lines if ln.text.strip() and ln.confidence >= min_conf]


class NullOCR(OCREngine):
    name = "none"


class RapidOCREngine(OCREngine):
    """PP-OCR text recognition through RapidOCR.

    Uses rapidocr_onnxruntime on Python <= 3.12 (models bundled in the wheel), or
    rapidocr 3.x with onnxruntime on Python 3.13+ (models fetched once on first use).
    """

    name = "rapidocr"

    def __init__(self) -> None:
        self._engine = None
        self._error: str | None = None
        self._lock = threading.Lock()

    def _load(self):
        if self._engine is None and self._error is None:
            try:
                try:
                    from rapidocr_onnxruntime import RapidOCR  # type: ignore
                except ImportError:
                    from rapidocr import RapidOCR  # type: ignore

                self._engine = RapidOCR()
            except Exception as e:  # not installed, or onnxruntime missing
                self._error = f"{type(e).__name__}: {e}"
        return self._engine

    @property
    def is_available(self) -> bool:
        return self._load() is not None

    def _recognize(self, image: Image.Image) -> list[OCRLine]:
        import numpy as np

        engine = self._load()
        with self._lock:  # onnxruntime sessions are shared; keep calls serialized
            out = engine(np.array(image.convert("RGB")))
        if isinstance(out, tuple):  # rapidocr_onnxruntime: ([[box, text, score], ...], timings)
            result = out[0]
        else:  # rapidocr 3.x: RapidOCROutput with parallel boxes / txts / scores
            result = zip(out.boxes, out.txts, out.scores) if out.boxes is not None else []
        lines: list[OCRLine] = []
        for box, text, score in result or []:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            x0, y0 = int(min(xs)), int(min(ys))
            lines.append(
                OCRLine(str(text), x0, y0, int(max(xs)) - x0, int(max(ys)) - y0, float(score))
            )
        return lines


class AppleVisionOCR(OCREngine):
    """macOS Vision framework (VNRecognizeTextRequest, accurate level)."""

    name = "apple_vision"

    def __init__(self) -> None:
        self._mods = None
        self._error: str | None = None

    def _load(self):
        if self._mods is None and self._error is None:
            if sys.platform != "darwin":
                self._error = "Apple Vision is only available on macOS"
                return None
            try:
                import Vision  # type: ignore
                from Foundation import NSData  # type: ignore

                self._mods = (Vision, NSData)
            except Exception as e:
                self._error = f"{type(e).__name__}: {e} (pip install pyobjc-framework-Vision)"
        return self._mods

    @property
    def is_available(self) -> bool:
        return self._load() is not None

    def _recognize(self, image: Image.Image) -> list[OCRLine]:
        Vision, NSData = self._load()
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="PNG")
        data = NSData.dataWithBytes_length_(buf.getvalue(), len(buf.getvalue()))

        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(False)  # UI labels and net names are not prose
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
        ok, err = handler.performRequests_error_([request], None)
        if not ok:
            raise RuntimeError(f"VNImageRequestHandler failed: {err}")
        return vision_observations_to_lines(request.results() or [], image.width, image.height)


def vision_observations_to_lines(observations, width: int, height: int) -> list[OCRLine]:
    """Converts VNRecognizedTextObservation results to pixel lines.

    Vision boxes are normalized 0..1 with the origin at the bottom-left, so y is flipped.
    """
    lines: list[OCRLine] = []
    for obs in observations:
        candidates = obs.topCandidates_(1)
        if not candidates:
            continue
        cand = candidates[0]
        (nx, ny), (nw, nh) = obs.boundingBox()
        x = int(round(nx * width))
        w = int(round(nw * width))
        h = int(round(nh * height))
        y = int(round((1.0 - ny - nh) * height))
        lines.append(OCRLine(str(cand.string()), x, y, w, h, float(cand.confidence())))
    return lines


class CachedOCR(OCREngine):
    """Wraps an engine with a small dHash LRU so an unchanged pane is read once."""

    def __init__(self, inner: OCREngine, capacity: int = 32) -> None:
        self.inner = inner
        self.name = inner.name
        self.capacity = capacity
        self._cache: OrderedDict[tuple[int, int, int], list[OCRLine]] = OrderedDict()
        self.hits = 0
        self.calls = 0
        self.total_ms = 0.0

    @property
    def is_available(self) -> bool:
        return self.inner.is_available

    def recognize(self, image: Image.Image) -> list[OCRLine]:
        from dobby.vdom.gemma3_sidecar import compute_dhash

        if image.width < 8 or image.height < 8:
            return []
        key = (image.width, image.height, compute_dhash(image))
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            self.hits += 1
            return list(cached)
        start = time.perf_counter()
        lines = self.inner.recognize(image)
        self.calls += 1
        self.total_ms += (time.perf_counter() - start) * 1000.0
        self._cache[key] = lines
        while len(self._cache) > self.capacity:
            self._cache.popitem(last=False)
        return list(lines)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "engine": self.name,
            "available": self.is_available,
            "error": getattr(self.inner, "_error", None),
            "calls": self.calls,
            "cache_hits": self.hits,
            "mean_ms": round(self.total_ms / self.calls, 1) if self.calls else None,
        }


def get_ocr_engine(kind: str | None = None) -> CachedOCR:
    """Builds the configured OCR engine. "auto": Apple Vision on macOS, else RapidOCR."""
    kind = (kind or CONFIG.ocr_engine or "auto").lower()
    if kind == "auto":
        kind = "apple_vision" if sys.platform == "darwin" else "rapidocr"
    engine: OCREngine
    if kind == "apple_vision":
        engine = AppleVisionOCR()
    elif kind == "rapidocr":
        engine = RapidOCREngine()
    else:
        engine = NullOCR()
    return CachedOCR(engine)


def assign_text_to_boxes(
    proposals: list[dict[str, Any]],
    lines: list[OCRLine],
) -> int:
    """Labels detector proposals that have no text with OCR lines.

    Each one gets the OCR line whose center lies inside it (largest overlap first).

    Returns how many proposals were named. Both lists must be in the same coordinate space.
    """
    named = 0
    for p in proposals:
        if (p.get("text") or "").strip():
            continue
        b = p["bbox"]
        inside = [
            ln
            for ln in lines
            if b["x"] <= ln.center[0] <= b["x"] + b["width"]
            and b["y"] <= ln.center[1] <= b["y"] + b["height"]
        ]
        if inside:
            inside.sort(key=lambda ln: (ln.y, ln.x))
            p["text"] = " ".join(ln.text for ln in inside)[:120]
            p["named_by"] = "ocr"
            named += 1
    return named
