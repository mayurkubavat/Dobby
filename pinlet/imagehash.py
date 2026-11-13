"""Perceptual image hashes used to recognise a screen region seen before.

Shared by the action cache, the Gemma sidecar's region cache, the OCR cache
and the mark cache, so a hash made in one place compares with one made in
another.
"""

from __future__ import annotations

import logging

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


def compute_dhash(image: Image.Image) -> int:
    """Computes a 64-bit difference hash (dHash) for an image crop in < 0.6ms.

    Captures structural gradient morphology invariant to slight brightness/contrast shifts.

    Returns:
        The hash as an integer, or 0 when the image could not be hashed.
    """
    try:
        # Resize to 9x8 grayscale, then compare adjacent columns.
        resized = image.convert("L").resize((9, 8), Image.Resampling.BILINEAR)
        arr = np.array(resized)
        diff = arr[:, 1:] > arr[:, :-1]
        return int.from_bytes(np.packbits(diff.flatten()), "big")
    except Exception as e:
        logger.debug(f"Error computing dHash: {e}")
        return 0


def hamming_distance(h1: int, h2: int) -> int:
    """Computes the Hamming distance (number of differing bits) between two 64-bit hashes."""
    return (h1 ^ h2).bit_count()
