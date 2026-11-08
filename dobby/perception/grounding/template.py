"""Visual Template & Icon Grounding Provider using OpenCV Pattern Matching."""

import cv2
import numpy as np
from PIL import Image
from typing_extensions import override

from dobby.perception.grounding.base import BaseGroundingProvider
from dobby.types import BoundingBox, UIElement


class TemplateGroundingProvider(BaseGroundingProvider):
    """Grounding provider that finds known icons or UI snippets by template matching.

    Uses OpenCV normalised cross-correlation (``cv2.TM_CCOEFF_NORMED``).
    """

    def __init__(self, match_threshold: float = 0.80) -> None:
        self.match_threshold = match_threshold
        self._templates: dict[str, np.ndarray] = {}

    @property
    @override
    def name(self) -> str:
        return "template"

    @property
    @override
    def is_available(self) -> bool:
        return True

    def register_template(self, name: str, template_img: Image.Image) -> None:
        """Registers a named icon or button template image."""
        np_tpl = cv2.cvtColor(np.array(template_img.convert("RGB")), cv2.COLOR_RGB2BGR)
        self._templates[name] = np_tpl

    def detect(
        self,
        image: Image.Image,
        scale_factor: float = 1.0,
        app_filter: str | None = None,
        region: tuple[int, int, int, int] | None = None,
    ) -> list[UIElement]:
        """Matches all registered templates against the current screen image."""
        if not self._templates:
            return []

        screen_bgr = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)
        elements: list[UIElement] = []
        elem_id = 1

        for name, tpl in self._templates.items():
            th, tw = tpl.shape[:2]
            if screen_bgr.shape[0] < th or screen_bgr.shape[1] < tw:
                continue

            # Check for non-zero variance to prevent division by zero in TM_CCOEFF_NORMED
            if np.std(tpl) < 1e-3:
                # Fallback to normalized square difference for solid templates
                res = cv2.matchTemplate(screen_bgr, tpl, cv2.TM_SQDIFF_NORMED)
                min_val, _, min_loc, _ = cv2.minMaxLoc(res)
                if min_val <= (1.0 - self.match_threshold):
                    pt = min_loc
                    orig_x = int(pt[0] / scale_factor)
                    orig_y = int(pt[1] / scale_factor)
                    orig_w = int(tw / scale_factor)
                    orig_h = int(th / scale_factor)
                    elements.append(
                        UIElement(
                            id=elem_id,
                            role="template_match",
                            name=name,
                            bbox=BoundingBox(
                                x=orig_x,
                                y=orig_y,
                                width=orig_w,
                                height=orig_h,
                            ),
                            interactable=True,
                            states=["matched"],
                        )
                    )
                    elem_id += 1
                continue

            res = cv2.matchTemplate(screen_bgr, tpl, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
            if max_val >= self.match_threshold:
                pt = max_loc
                orig_x = int(pt[0] / scale_factor)
                orig_y = int(pt[1] / scale_factor)
                orig_w = int(tw / scale_factor)
                orig_h = int(th / scale_factor)

                elements.append(
                    UIElement(
                        id=elem_id,
                        role="template_match",
                        name=name,
                        bbox=BoundingBox(
                            x=orig_x,
                            y=orig_y,
                            width=orig_w,
                            height=orig_h,
                        ),
                        interactable=True,
                        states=["matched"],
                    )
                )
                elem_id += 1

        return elements
