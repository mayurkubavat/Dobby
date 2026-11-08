"""Unified Virtual DOM (vDOM) Fusion Engine.

Reconciles ground-truth AT-SPI2 accessibility trees with neural vision proposals,
performing asymmetric IoU deduplication, containment nesting, and canvas role inference.
"""

import hashlib
import time
from typing import Any

from pinlet.config import CONFIG
from pinlet.types import (
    BoundingBox,
    GroundingSource,
    UIRole,
    VDOMNode,
    VirtualDOMSnapshot,
)


class VDOMFusionEngine:
    """Fuses AT-SPI2 semantic trees with Neural/CV visual proposals in < 4ms."""

    def __init__(
        self,
        iou_threshold: float = CONFIG.vdom_fusion_iou_threshold,
        containment_threshold: float = CONFIG.vdom_containment_ratio_threshold,
    ) -> None:
        self.iou_threshold = iou_threshold
        self.containment_threshold = containment_threshold

    def fuse(
        self,
        atspi_nodes: list[VDOMNode],
        visual_proposals: list[dict[str, Any]],
        window_id: str = "",
        window_title: str = "",
        window_bbox: BoundingBox | None = None,
        timestamp: float | None = None,
    ) -> VirtualDOMSnapshot:
        """Merges accessibility nodes and visual proposals into an indexed VirtualDOMSnapshot."""
        now = timestamp if timestamp is not None else time.monotonic()
        win_box = window_bbox or BoundingBox(x=0, y=0, width=1920, height=1080)

        # Fallback root node if empty
        if not atspi_nodes:
            root_id = "vdom_root"
            root_node = VDOMNode(
                node_id=root_id,
                source=GroundingSource.AT_SPI2,
                role=UIRole.WINDOW,
                name=window_title or "Desktop",
                bbox=win_box,
                click_point=win_box.center,
                states=["ENABLED", "VISIBLE", "FOCUSED"],
            )
            atspi_nodes = [root_node]

        nodes_map: dict[str, VDOMNode] = {n.node_id: n for n in atspi_nodes}
        root_node = atspi_nodes[0]

        # Identify leaf nodes and potential enclosing containers
        leaf_atspi = [n for n in atspi_nodes if not n.children_ids]
        containers = sorted(
            [
                n
                for n in atspi_nodes
                if n.role in {UIRole.CANVAS, UIRole.PANEL, UIRole.WINDOW, UIRole.DIALOG}
            ],
            key=lambda c: c.bbox.area,
        )

        for prop in visual_proposals:
            raw_box = prop.get("bbox")
            if not raw_box:
                continue

            if isinstance(raw_box, dict):
                v_bbox = BoundingBox(**raw_box)
            elif isinstance(raw_box, (list, tuple)) and len(raw_box) >= 4:
                v_bbox = BoundingBox(
                    x=int(raw_box[0]),
                    y=int(raw_box[1]),
                    width=int(raw_box[2]),
                    height=int(raw_box[3]),
                )
            elif isinstance(raw_box, BoundingBox):
                v_bbox = raw_box
            else:
                continue

            v_text = prop.get("text", "").strip()
            v_conf = float(prop.get("confidence", 0.85))
            v_raw_class = prop.get("class_name", "icon_button")

            # 1. Asymmetric IoU Deduplication against AT-SPI2 leaf nodes
            best_match: VDOMNode | None = None
            best_iou = 0.0
            for leaf in leaf_atspi:
                score = leaf.bbox.iou(v_bbox)
                if score > best_iou:
                    best_iou = score
                    best_match = leaf

            if best_match and best_iou >= self.iou_threshold:
                best_match.source = GroundingSource.HYBRID
                best_match.click_point = v_bbox.center
                if not best_match.name and v_text:
                    best_match.name = v_text
                continue

            # 2. Unmatched visual detection -> Find tightest enclosing AT-SPI2 container
            parent_node = next(
                (
                    c
                    for c in containers
                    if c.bbox.containment_ratio(v_bbox) >= self.containment_threshold
                ),
                root_node,
            )

            # 3. Context-Aware Role Inference for Canvas Elements
            inferred_role = self._infer_canvas_role(v_raw_class, v_text, v_bbox, parent_node)
            raw_sig = f"{parent_node.node_id}:{inferred_role.value}:{v_text}:{v_bbox.x // 8}:{v_bbox.y // 8}"
            node_id = "vdom_" + hashlib.md5(raw_sig.encode()).hexdigest()[:8]

            vision_node = VDOMNode(
                node_id=node_id,
                source=GroundingSource.VISION_ONLY,
                role=inferred_role,
                name=v_text or prop.get("caption", ""),
                states=["ENABLED", "VISIBLE"],
                bbox=v_bbox,
                click_point=v_bbox.center,
                confidence=v_conf,
                parent_id=parent_node.node_id,
            )
            nodes_map[node_id] = vision_node
            if node_id not in parent_node.children_ids:
                parent_node.children_ids.append(node_id)

        return VirtualDOMSnapshot(
            snapshot_id=f"snap_{int(now * 1000)}",
            timestamp=now,
            window_id=str(window_id),
            window_title=window_title,
            window_bbox=win_box,
            root_id=root_node.node_id,
            nodes=nodes_map,
        )

    def _infer_canvas_role(
        self,
        raw_class: str,
        text: str,
        bbox: BoundingBox,
        parent: VDOMNode,
    ) -> UIRole:
        """Infers domain-specific semantic role from visual shape, OCR text, and parent context."""
        parent_ctx = f"{parent.name} {parent.attributes.get('qt_name', '')}".lower()

        # Schematic viewer context
        if "schematic" in parent_ctx:
            if ("[" in text and "]" in text) or text.startswith(("w_", "net_")):
                return UIRole.SCHEMATIC_NET
            return UIRole.SCHEMATIC_PIN

        # Waveform viewer context
        if "wave" in parent_ctx or "xsim" in parent_ctx:
            return UIRole.WAVEFORM_SIGNAL

        # Sash dividers / splitters
        if (bbox.height <= 6 and bbox.width >= 50) or (bbox.width <= 6 and bbox.height >= 50):
            return UIRole.SASH_DIVIDER

        # Small icon buttons
        if bbox.width <= 36 and bbox.height <= 36 and not text:
            return UIRole.ICON_BUTTON

        # Check existing UIRole values
        try:
            raw_lower = raw_class.lower()
            if raw_lower == "checkbox":
                return UIRole.CHECK_BOX
            if raw_lower in {"container_viewport", "container"}:
                return UIRole.PANEL
            return UIRole(raw_lower)
        except ValueError:
            pass

        return UIRole.STATIC_TEXT if text else UIRole.ICON_BUTTON
