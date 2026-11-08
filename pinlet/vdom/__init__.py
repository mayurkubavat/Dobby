"""Virtual DOM (vDOM), Grounding Sidecar, and Dynamic Layout Engine.

Provides unified perception fusing AT-SPI2 semantics with neural vision proposals,
zero-token CSS spatial querying, and dynamic anchor-relative layout resolution.
"""

from pinlet.vdom.fast_detector import (
    FastONNXUIDetector,
    UIClass,
    build_default_ui_detector_model,
    letterbox,
    nms_vectorized,
    postprocess_yolo_detections,
    unletterbox_boxes,
)
from pinlet.vdom.fusion import VDOMFusionEngine
from pinlet.vdom.gemma3_sidecar import Gemma3Grounder, SemanticROICache, compute_dhash
from pinlet.vdom.layout import SubPaneLayoutSolver
from pinlet.vdom.query import VDOMQueryEngine
from pinlet.vdom.sidecar import LocalNeuralGrounder

__all__ = [
    "FastONNXUIDetector",
    "Gemma3Grounder",
    "LocalNeuralGrounder",
    "SemanticROICache",
    "SubPaneLayoutSolver",
    "UIClass",
    "VDOMFusionEngine",
    "VDOMQueryEngine",
    "build_default_ui_detector_model",
    "compute_dhash",
    "letterbox",
    "nms_vectorized",
    "postprocess_yolo_detections",
    "unletterbox_boxes",
]
