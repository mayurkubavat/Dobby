"""Pure Computer Vision Grounding Provider using Edge Contours and MSER."""

from PIL import Image
from typing_extensions import override

from pinlet.perception.cv_detector import CVDetector
from pinlet.perception.grounding.base import BaseGroundingProvider
from pinlet.types import UIElement


class CVGroundingProvider(BaseGroundingProvider):
    """Grounding provider leveraging pure Computer Vision edge and contour analysis."""

    def __init__(self, detector: CVDetector | None = None) -> None:
        self.detector = detector or CVDetector()

    @property
    @override
    def name(self) -> str:
        return "cv"

    @property
    @override
    def is_available(self) -> bool:
        return True

    def detect(
        self,
        image: Image.Image,
        scale_factor: float = 1.0,
        app_filter: str | None = None,
        region: tuple[int, int, int, int] | None = None,
    ) -> list[UIElement]:
        """Detects interactable regions directly from screenshot pixels."""
        elements = self.detector.detect(image, scale_factor=scale_factor)
        if region:
            rx, ry, rw, rh = region
            filtered = []
            for e in elements:
                bx, by, bw, bh = e.bbox.x, e.bbox.y, e.bbox.width, e.bbox.height
                if bx >= rx and by >= ry and (bx + bw) <= (rx + rw) and (by + bh) <= (ry + rh):
                    filtered.append(e)
            return filtered
        return elements
