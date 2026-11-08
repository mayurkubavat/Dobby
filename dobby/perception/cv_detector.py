"""Computer Vision (CV) interactable UI region detector.

Provides pure visual grounding fallback for applications that lack AT-SPI2
accessibility trees (e.g. canvas web apps, games, legacy X11 utilities).
"""

import cv2
import numpy as np
from PIL import Image

from dobby.config import CONFIG
from dobby.types import BoundingBox, UIElement


class CVDetector:
    """Detects interactable UI components (buttons, input fields, tabs, icons) directly from pixels."""

    def __init__(
        self,
        min_area: int = 120,
        max_area_ratio: float = 0.70,
        min_dimension: int = 14,
        iou_threshold: float = 0.45,
    ) -> None:
        self.min_area = min_area
        self.max_area_ratio = max_area_ratio
        self.min_dimension = min_dimension
        self.iou_threshold = iou_threshold

    def detect(
        self,
        image: Image.Image,
        scale_factor: float = 1.0,
        max_elements: int = CONFIG.som_max_elements,
    ) -> list[UIElement]:
        """Detects interactable regions in the image.

        Args:
            image: PIL Image (may be scaled down).
            scale_factor: Ratio of current image size relative to original physical display.
            max_elements: Maximum number of elements to return.

        Returns:
            List of UIElement models with bounding boxes in original physical desktop coordinates.
        """
        # Convert PIL to BGR numpy array
        np_img = np.array(image.convert("RGB"))
        img_h, img_w = np_img.shape[:2]
        max_allowed_area = int(img_w * img_h * self.max_area_ratio)

        # 1. Edge & Contour Detection
        boxes: list[tuple[int, int, int, int]] = []
        gray = cv2.cvtColor(np_img, cv2.COLOR_RGB2GRAY)

        # Contrast enhancement & noise reduction
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)

        # Multi-threshold Canny edge detection
        edges_high = cv2.Canny(blurred, 50, 150)
        edges_low = cv2.Canny(blurred, 20, 60)
        edges = cv2.bitwise_or(edges_high, edges_low)

        # Dilate edges to close button borders
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        closed_edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=1)

        # Find external and nested contours
        contours, _ = cv2.findContours(closed_edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            area = w * h
            if (
                w >= self.min_dimension
                and h >= self.min_dimension
                and area >= self.min_area
                and area <= max_allowed_area
            ):
                aspect_ratio = float(w) / float(h)
                # Filter out extreme horizontal or vertical divider lines
                if 0.08 <= aspect_ratio <= 12.0:
                    boxes.append((x, y, w, h))

        # 2. Text Region Detection (MSER)
        try:
            mser = cv2.MSER_create(
                min_area=self.min_area // 2,
                max_area=max_allowed_area // 2,
            )
            regions, _ = mser.detectRegions(gray)
            for p in regions:
                x, y, w, h = cv2.boundingRect(p)
                area = w * h
                if (
                    w >= self.min_dimension
                    and h >= self.min_dimension
                    and area >= self.min_area
                    and area <= max_allowed_area
                ):
                    boxes.append((x, y, w, h))
        except Exception:
            pass

        # 3. Non-Maximum Suppression (NMS) & Containment Filter
        filtered_boxes = self._suppress_overlapping_boxes(boxes, self.iou_threshold)

        # 4. Convert to UIElement with physical screen coordinates
        elements: list[UIElement] = []
        inv_scale = 1.0 / scale_factor if scale_factor > 0 else 1.0

        for idx, (x, y, w, h) in enumerate(filtered_boxes[:max_elements]):
            # Infer role by geometry
            aspect = float(w) / float(h)
            if aspect > 2.8 and h < 55:
                role = "text_entry"
            elif 0.8 <= aspect <= 2.8 and h < 70:
                role = "button"
            elif aspect < 0.8:
                role = "vertical_control"
            else:
                role = "ui_container"

            # Map to physical screen coordinates
            phys_x = int(x * inv_scale)
            phys_y = int(y * inv_scale)
            phys_w = int(w * inv_scale)
            phys_h = int(h * inv_scale)

            elements.append(
                UIElement(
                    id=idx + 1,
                    role=role,
                    name=f"Visual Element #{idx + 1}",
                    bbox=BoundingBox(x=phys_x, y=phys_y, width=phys_w, height=phys_h),
                    interactable=True,
                )
            )

        return elements

    def _suppress_overlapping_boxes(
        self, boxes: list[tuple[int, int, int, int]], iou_thresh: float
    ) -> list[tuple[int, int, int, int]]:
        """Suppresses redundant, heavily overlapping, or duplicate bounding boxes."""
        if not boxes:
            return []

        # Sort boxes by area ascending (favor compact, specific buttons over giant outer panels)
        sorted_boxes = sorted(boxes, key=lambda b: b[2] * b[3])
        keep: list[tuple[int, int, int, int]] = []

        for b1 in sorted_boxes:
            x1, y1, w1, h1 = b1
            area1 = w1 * h1
            suppress = False

            for b2 in keep:
                x2, y2, w2, h2 = b2
                area2 = w2 * h2

                # Intersection
                xx1 = max(x1, x2)
                yy1 = max(y1, y2)
                xx2 = min(x1 + w1, x2 + w2)
                yy2 = min(y1 + h1, y2 + h2)

                inter_w = max(0, xx2 - xx1)
                inter_h = max(0, yy2 - yy1)
                inter_area = inter_w * inter_h

                if inter_area > 0:
                    union_area = area1 + area2 - inter_area
                    iou = inter_area / float(union_area) if union_area > 0 else 0

                    # If high IoU or one box is mostly contained in another with similar size
                    if iou > iou_thresh or (
                        inter_area / float(area1) > 0.85 and area2 / float(area1) < 1.4
                    ):
                        suppress = True
                        break

            if not suppress:
                keep.append(b1)

        # Return sorted by top-to-bottom, left-to-right reading order
        return sorted(keep, key=lambda b: (b[1] // 40, b[0]))
