"""Ensemble Grounding Provider fusing Semantic, Computer Vision, and Template detections with NMS."""

from PIL import Image
from typing_extensions import override

from pinlet.config import CONFIG
from pinlet.perception.grounding.base import BaseGroundingProvider
from pinlet.perception.grounding.cv import CVGroundingProvider
from pinlet.perception.grounding.semantic import SemanticGroundingProvider
from pinlet.perception.grounding.template import TemplateGroundingProvider
from pinlet.types import BoundingBox, UIElement


def compute_iou(boxA: BoundingBox, boxB: BoundingBox) -> float:
    """Computes Intersection-over-Union (IoU) between two bounding boxes."""
    xA = max(boxA.x, boxB.x)
    yA = max(boxA.y, boxB.y)
    xB = min(boxA.x + boxA.width, boxB.x + boxB.width)
    yB = min(boxA.y + boxA.height, boxB.y + boxB.height)

    inter_w = max(0, xB - xA)
    inter_h = max(0, yB - yA)
    inter_area = inter_w * inter_h

    boxA_area = boxA.width * boxA.height
    boxB_area = boxB.width * boxB.height
    union_area = float(boxA_area + boxB_area - inter_area)

    if union_area <= 0:
        return 0.0

    return inter_area / union_area


class EnsembleGroundingProvider(BaseGroundingProvider):
    """Fuses several grounding providers into one Set-of-Marks map.

    Combines the semantic hierarchy, visual edge contours and template matching, and
    de-duplicates overlapping boxes with Non-Maximum Suppression.
    """

    def __init__(
        self,
        semantic_provider: SemanticGroundingProvider | None = None,
        cv_provider: CVGroundingProvider | None = None,
        template_provider: TemplateGroundingProvider | None = None,
        iou_threshold: float = CONFIG.nms_iou_threshold,
    ) -> None:
        self.semantic = semantic_provider or SemanticGroundingProvider()
        self.cv = cv_provider or CVGroundingProvider()
        self.template = template_provider or TemplateGroundingProvider()
        self.iou_threshold = iou_threshold

    @property
    @override
    def name(self) -> str:
        return "ensemble"

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
        """Runs every provider and merges the results with Non-Maximum Suppression.

        Semantic attributes from the accessibility tree are kept on the surviving boxes.
        """
        raw_candidates: list[UIElement] = []

        # 1. Query semantic tree (highest priority for names and roles)
        if self.semantic.is_available:
            sem_elements = self.semantic.detect(
                image=image,
                scale_factor=scale_factor,
                app_filter=app_filter,
                region=region,
            )
            raw_candidates.extend(sem_elements)

        # 2. Query pure computer vision (detects buttons/inputs without AT-SPI metadata)
        cv_elements = self.cv.detect(
            image=image,
            scale_factor=scale_factor,
            app_filter=app_filter,
            region=region,
        )
        raw_candidates.extend(cv_elements)

        # 3. Query template matching if templates exist
        tpl_elements = self.template.detect(
            image=image,
            scale_factor=scale_factor,
            app_filter=app_filter,
            region=region,
        )
        raw_candidates.extend(tpl_elements)

        if not CONFIG.enable_nms_fusion:
            # Re-index mark IDs sequentially and return
            for idx, elem in enumerate(raw_candidates):
                elem.id = idx + 1
            return raw_candidates[: CONFIG.som_max_elements]

        # 4. Non-Maximum Suppression (NMS) with Semantic Prioritization
        # Semantic elements are preserved and enrich overlapping CV contours
        fused_elements: list[UIElement] = []

        for candidate in raw_candidates:
            # Check overlap against already selected elements
            merged = False
            for existing in fused_elements:
                iou = compute_iou(candidate.bbox, existing.bbox)
                if iou >= self.iou_threshold:
                    # Enrich existing element if candidate has better semantic name
                    if not existing.name and candidate.name:
                        existing.name = candidate.name
                    if existing.role == "unknown" and candidate.role != "unknown":
                        existing.role = candidate.role
                    merged = True
                    break

            if not merged:
                fused_elements.append(candidate.model_copy(deep=True))

        # Re-index marks sequentially
        for idx, elem in enumerate(fused_elements):
            elem.id = idx + 1

        return fused_elements[: CONFIG.som_max_elements]

    def find_by_text(
        self,
        query: str,
        image: Image.Image | None = None,
        scale_factor: float = 1.0,
        fuzzy: bool = True,
    ) -> list[UIElement]:
        """Searches across semantic tree first; falls back to full ensemble detection if needed."""
        # Fast path: search semantic tree without image overhead
        if self.semantic.is_available:
            sem_matches = self.semantic.find_by_text(query, fuzzy=fuzzy)
            if sem_matches:
                return sem_matches

        # Fallback to visual detection if image provided
        if image is not None:
            all_elements = self.detect(image, scale_factor=scale_factor)
            q = query.lower().strip()
            return [e for e in all_elements if q in (e.name or "").lower()]

        return []
