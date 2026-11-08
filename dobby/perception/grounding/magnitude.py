"""Theme-invariant icon matching on normalized gradient magnitude."""

from __future__ import annotations

import hashlib

import cv2
import numpy as np
from PIL import Image

from dobby.types import BoundingBox, RelationalAnchor, UIElement


def magnitude_map(bgr: np.ndarray) -> np.ndarray:
    """Polarity-invariant edges. A dark icon matches the same icon inverted."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    return cv2.normalize(mag, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)


def _to_bgr(image: Image.Image | np.ndarray) -> np.ndarray:
    if isinstance(image, np.ndarray):
        if image.ndim == 2:
            return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        return image
    return cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)


def match_magnitude(
    search: Image.Image | np.ndarray,
    template: Image.Image | np.ndarray,
    threshold: float = 0.72,
    ambiguity: float = 0.05,
) -> tuple[float, tuple[int, int], tuple[int, int]] | None:
    """Returns (score, top_left, template_size) or None when the peak is weak or ambiguous."""
    search_mag = magnitude_map(_to_bgr(search))
    tpl_mag = magnitude_map(_to_bgr(template))
    th, tw = tpl_mag.shape[:2]
    if search_mag.shape[0] < th or search_mag.shape[1] < tw:
        return None
    if float(np.std(tpl_mag)) < 1e-3:
        return None
    scores = cv2.matchTemplate(search_mag, tpl_mag, cv2.TM_CCOEFF_NORMED)
    _min, max_val, _min_loc, max_loc = cv2.minMaxLoc(scores)
    # Suppress the winning peak and look at the next one.
    x, y = max_loc
    x0, y0 = max(0, x - tw // 2), max(0, y - th // 2)
    x1, y1 = min(scores.shape[1], x + tw // 2), min(scores.shape[0], y + th // 2)
    suppressed = scores.copy()
    suppressed[y0:y1, x0:x1] = 0
    second = float(suppressed.max()) if suppressed.size else 0.0
    if float(max_val) < threshold or float(max_val) - second < ambiguity:
        return None
    return float(max_val), (int(x), int(y)), (int(tw), int(th))


def anchor_window(
    label_box: tuple[int, int, int, int], anchor: RelationalAnchor
) -> tuple[int, int, int, int]:
    """Pixel window placed dx, dy from the label box's top-left."""
    lx, ly, _lw, _lh = label_box
    bw, bh = anchor.box
    return (lx + anchor.dx, ly + anchor.dy, bw, bh)


def ground_magnitude_icon(
    image: Image.Image,
    template: Image.Image,
    anchor: RelationalAnchor | None = None,
    ocr_lines: list | None = None,
    scale_factor: float = 1.0,
) -> UIElement | None:
    """Matches a magnitude template, restricted to an OCR offset window when a label is present."""
    search: Image.Image | np.ndarray = image
    origin_x, origin_y = 0, 0
    if anchor is not None and ocr_lines:
        label = anchor.label.lower()
        hit = next((line for line in ocr_lines if label in str(line.get("text", "")).lower()), None)
        if hit is not None:
            box = hit.get("bbox") or [
                hit.get("x", 0),
                hit.get("y", 0),
                hit.get("width", 0),
                hit.get("height", 0),
            ]
            wx, wy, ww, wh = anchor_window(
                (int(box[0]), int(box[1]), int(box[2]), int(box[3])), anchor
            )
            search = image.crop((wx, wy, wx + ww, wy + wh))
            origin_x, origin_y = wx, wy
    found = match_magnitude(search, template)
    if found is None:
        return None
    score, (x, y), (tw, th) = found
    digest = hashlib.sha256(template.tobytes()).hexdigest()[:12]
    if anchor is not None:
        anchor.magnitude_sha = digest
    return UIElement(
        id=1,
        role="icon_button",
        name=anchor.label if anchor else "magnitude_icon",
        bbox=BoundingBox(
            x=int((origin_x + x) / scale_factor),
            y=int((origin_y + y) / scale_factor),
            width=int(tw / scale_factor),
            height=int(th / scale_factor),
        ),
        interactable=True,
        states=["magnitude_match"],
    )
