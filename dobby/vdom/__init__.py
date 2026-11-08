"""Virtual DOM (vDOM), Grounding Sidecar, and Dynamic Layout Engine.

Provides unified perception fusing AT-SPI2 semantics with neural vision proposals,
zero-token CSS spatial querying, and dynamic anchor-relative layout resolution.
"""

from dobby.vdom.fast_detector import (
    FastONNXUIDetector,
    UIClass,
    build_default_ui_detector_model,
    letterbox,
    nms_vectorized,
    postprocess_yolo_detections,
    unletterbox_boxes,
)
from dobby.vdom.fusion import VDOMFusionEngine
from dobby.vdom.gemma3_sidecar import Gemma3Grounder, SemanticROICache, compute_dhash
from dobby.vdom.layout import SubPaneLayoutSolver
from dobby.vdom.query import VDOMQueryEngine
from dobby.vdom.sidecar import LocalNeuralGrounder

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
