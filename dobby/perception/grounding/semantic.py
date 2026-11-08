"""Semantic Grounding Provider using OS Accessibility APIs (AT-SPI2 / UIA)."""

from PIL import Image
from typing_extensions import override

from dobby.perception.atspi import ATSPIInspector
from dobby.perception.ax import get_accessibility_inspector
from dobby.perception.grounding.base import BaseGroundingProvider
from dobby.types import UIElement


class SemanticGroundingProvider(BaseGroundingProvider):
    """Grounding provider leveraging OS accessibility trees (AT-SPI2 on Linux)."""

    def __init__(self, inspector: ATSPIInspector | None = None) -> None:
        self.inspector = inspector or get_accessibility_inspector()

    @property
    @override
    def name(self) -> str:
        return "semantic"

    @property
    @override
    def is_available(self) -> bool:
        return self.inspector.is_available

    def detect(
        self,
        image: Image.Image,
        scale_factor: float = 1.0,
        app_filter: str | None = None,
        region: tuple[int, int, int, int] | None = None,
    ) -> list[UIElement]:
        """Queries the OS accessibility hierarchy directly."""
        elements = self.inspector.inspect_desktop(app_name_filter=app_filter)
        if region:
            rx, ry, rw, rh = region
            filtered = []
            for e in elements:
                bx, by, bw, bh = e.bbox.x, e.bbox.y, e.bbox.width, e.bbox.height
                if bx >= rx and by >= ry and (bx + bw) <= (rx + rw) and (by + bh) <= (ry + rh):
                    filtered.append(e)
            return filtered
        return elements

    def find_by_text(
        self,
        query: str,
        image: Image.Image | None = None,
        scale_factor: float = 1.0,
        fuzzy: bool = True,
    ) -> list[UIElement]:
        """Fast semantic search across the OS accessibility tree without requiring screen image tokens."""
        all_elements = self.inspector.inspect_desktop()
        q = query.lower().strip()
        matches = []
        for elem in all_elements:
            name = (elem.name or "").lower()
            role = (elem.role or "").lower()
            if (fuzzy and (q in name or q in role)) or (not fuzzy and (q in (name, role))):
                matches.append(elem)
        return matches
