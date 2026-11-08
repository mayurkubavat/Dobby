"""Dynamic Anchor-Relative Sub-Pane Resolution Engine.

Calculates exact sub-pane bounding boxes dynamically relative to persistent UI anchors
(e.g., sash dividers, tab headers, status bar) rather than static pixel coordinates.
Resilient to window resizing, display scaling, and docking panel shifts.
"""

import logging

from dobby.types import (
    AnchorEdge,
    BoundaryConstraint,
    BoundingBox,
    SubPaneLayoutSpec,
)
from dobby.vdom.query import VDOMQueryEngine

logger = logging.getLogger(__name__)


class SubPaneLayoutSolver:
    """Solves sub-pane bounding geometries dynamically from vDOM topological anchors."""

    def resolve_edge(
        self,
        constraint: BoundaryConstraint | None,
        vdom_engine: VDOMQueryEngine,
        default_val: int,
        axis: str,
        win_bbox: BoundingBox,
    ) -> int:
        """Resolves a single spatial boundary coordinate from a topological constraint."""
        if not constraint:
            return default_val

        matches = vdom_engine.query_selector(constraint.anchor_selector, limit=1)
        if not matches:
            logger.debug(
                f"Anchor selector '{constraint.anchor_selector}' not found in vDOM; using default {default_val}"
            )
            return default_val

        anchor = matches[0]

        if constraint.edge == AnchorEdge.TOP:
            val = anchor.bbox.y + constraint.offset
        elif constraint.edge == AnchorEdge.BOTTOM:
            val = anchor.bbox.bottom + constraint.offset
        elif constraint.edge == AnchorEdge.LEFT:
            val = anchor.bbox.x + constraint.offset
        elif constraint.edge == AnchorEdge.RIGHT:
            val = anchor.bbox.right + constraint.offset
        else:
            val = default_val

        # Clamp within window boundaries
        if axis == "x":
            return max(win_bbox.x, min(win_bbox.right, val))
        else:
            return max(win_bbox.y, min(win_bbox.bottom, val))

    def resolve_subpane(
        self,
        spec: SubPaneLayoutSpec,
        vdom_engine: VDOMQueryEngine,
        win_bbox: BoundingBox,
    ) -> BoundingBox:
        """Resolves full 2D bounding box for the target sub-pane relative to active window and vDOM."""
        top_y = self.resolve_edge(
            spec.top_constraint,
            vdom_engine,
            default_val=win_bbox.y,
            axis="y",
            win_bbox=win_bbox,
        )
        bottom_y = self.resolve_edge(
            spec.bottom_constraint,
            vdom_engine,
            default_val=win_bbox.bottom,
            axis="y",
            win_bbox=win_bbox,
        )
        left_x = self.resolve_edge(
            spec.left_constraint,
            vdom_engine,
            default_val=win_bbox.x,
            axis="x",
            win_bbox=win_bbox,
        )
        right_x = self.resolve_edge(
            spec.right_constraint,
            vdom_engine,
            default_val=win_bbox.right,
            axis="x",
            win_bbox=win_bbox,
        )

        width = right_x - left_x
        height = bottom_y - top_y

        # If constraints yielded degenerate geometry, use fallback ROI if available
        if (width <= 10 or height <= 10) and spec.fallback_roi is not None:
            logger.debug(
                f"Dynamic resolution for '{spec.pane_id}' produced degenerate box ({width}x{height}); falling back to static ROI"
            )
            return spec.fallback_roi.compute_bbox(win_bbox)

        return BoundingBox(
            x=left_x,
            y=top_y,
            width=max(1, width),
            height=max(1, height),
        )
