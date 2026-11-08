"""
Unit and Latency Benchmark Tests for Phase 4 Neural Fast-Path (FastONNXUIDetector).
"""

import importlib.util
import os
import time
import pytest
import numpy as np
from PIL import Image, ImageDraw

from dobby.types import BoundingBox, VDOMNode, GroundingSource, UIRole
from dobby.vdom.fast_detector import (
    FastONNXUIDetector,
    UIClass,
    UI_CLASS_NAMES,
    letterbox,
    unletterbox_boxes,
    nms_vectorized,
    postprocess_yolo_detections,
    build_default_ui_detector_model,
)
from dobby.vdom.sidecar import LocalNeuralGrounder
from dobby.vdom.fusion import VDOMFusionEngine

# Building and running the bundled detector needs both onnx and onnxruntime.
needs_onnx = pytest.mark.skipif(
    not (importlib.util.find_spec("onnx") and importlib.util.find_spec("onnxruntime")),
    reason="needs onnx and onnxruntime (pip install onnx onnxruntime)",
)


def test_letterbox_and_unletterbox():
    """Validates letterboxing aspect ratio preservation and coordinate unpadding inversion."""
    orig_w, orig_h = 1920, 1080
    dummy = Image.new("RGB", (orig_w, orig_h), (255, 255, 255))

    tensor, scale, (pad_x, pad_y) = letterbox(dummy, target_size=(640, 640))

    # Tensor format validation
    assert tensor.shape == (1, 3, 640, 640)
    assert tensor.dtype == np.float32
    assert 0.0 <= tensor.min() and tensor.max() <= 1.0

    # 1920x1080 fits in 640x640: scale = 640/1920 = 1/3 ~ 0.3333
    expected_scale = 640.0 / 1920.0
    assert abs(scale - expected_scale) < 1e-4
    assert pad_x == 0
    # Expected height: 1080 * (1/3) = 360, pad_y = (640 - 360)//2 = 140
    assert pad_y == (640 - int(round(1080 * expected_scale))) // 2

    # Test coordinate inversion
    # Box in 640x640 space inside the valid area
    box_640 = np.array([[100.0, 140.0 + 50.0, 300.0, 140.0 + 150.0]], dtype=np.float32)
    unpadded = unletterbox_boxes(box_640, scale, (pad_x, pad_y), (orig_w, orig_h))

    assert unpadded.shape == (1, 4)
    # Expected unpadded x1 = 100 / (1/3) = 300
    assert abs(unpadded[0, 0] - 300.0) < 1.0
    # Expected unpadded y1 = 50 / (1/3) = 150
    assert abs(unpadded[0, 1] - 150.0) < 1.0
    # Expected unpadded x2 = 300 / (1/3) = 900
    assert abs(unpadded[0, 2] - 900.0) < 1.0
    # Expected unpadded y2 = 150 / (1/3) = 450
    assert abs(unpadded[0, 3] - 450.0) < 1.0


def test_nms_vectorized():
    """Validates vectorized Non-Maximum Suppression deduplication."""
    # 3 boxes: box0 and box1 overlap heavily (IoU > 0.8), box2 is distant
    boxes = np.array([
        [10.0, 10.0, 100.0, 100.0],   # box 0: score 0.90
        [12.0, 11.0, 102.0, 101.0],   # box 1: score 0.70 (duplicate)
        [300.0, 300.0, 400.0, 400.0], # box 2: score 0.85 (distinct)
    ], dtype=np.float32)
    scores = np.array([0.90, 0.70, 0.85], dtype=np.float32)

    kept = nms_vectorized(boxes, scores, iou_threshold=0.45)

    assert len(kept) == 2
    assert 0 in kept  # Highest score kept
    assert 2 in kept  # Distinct box kept
    assert 1 not in kept  # Duplicate suppressed


@needs_onnx
def test_build_default_ui_detector_model(tmp_path):
    """Validates building, ONNX checking, and IR version compatibility."""
    import onnx
    import onnxruntime as ort

    model_file = str(tmp_path / "custom_detector.onnx")
    out_path = build_default_ui_detector_model(model_file, target_size=(640, 640), num_classes=6)

    assert os.path.exists(out_path)
    assert os.path.getsize(out_path) > 1000

    # ONNX ModelProto checks
    model = onnx.load(out_path)
    onnx.checker.check_model(model)
    assert model.ir_version <= 13  # Required for ORT 1.31 compatibility

    # Load session and check input/output contract
    sess = ort.InferenceSession(out_path, providers=["CPUExecutionProvider"])
    assert len(sess.get_inputs()) == 1
    assert sess.get_inputs()[0].name == "images"
    assert len(sess.get_outputs()) == 1
    assert sess.get_outputs()[0].name == "output0"


@needs_onnx
def test_fast_onnx_ui_detector_detection():
    """Validates end-to-end detection on synthetic UI elements."""
    detector = FastONNXUIDetector()
    assert detector.is_available() is True

    # Create synthetic image with button and text field
    img = Image.new("RGB", (800, 600), (240, 240, 240))
    draw = ImageDraw.Draw(img)
    # Button
    draw.rectangle([100, 100, 260, 150], fill=(0, 120, 215), outline=(0, 80, 160), width=2)
    # Text input
    draw.rectangle([100, 200, 400, 245], fill=(255, 255, 255), outline=(120, 120, 120), width=2)

    offset = (50, 100)
    proposals = detector.detect(img, offset=offset, conf_thresh=0.05)

    # proposals should be a list of dicts
    assert isinstance(proposals, list)
    for p in proposals:
        assert "bbox" in p
        assert "x" in p["bbox"] and "y" in p["bbox"]
        assert "width" in p["bbox"] and "height" in p["bbox"]
        assert p["bbox"]["x"] >= offset[0]
        assert p["bbox"]["y"] >= offset[1]
        assert p["bbox"]["width"] > 0
        assert p["bbox"]["height"] > 0
        assert "confidence" in p
        assert "class_name" in p
        assert p["source"] == "VISION_FAST"


@needs_onnx
def test_fast_onnx_latency_benchmark():
    """Validates sub-20ms inference latency benchmark on CPU."""
    detector = FastONNXUIDetector()
    assert detector.is_available() is True

    metrics = detector.benchmark(num_runs=15, image_size=(1920, 1080))

    assert "total_latency_ms" in metrics
    assert "inference_ms" in metrics
    assert "letterbox_ms" in metrics
    assert "fps" in metrics

    # Must satisfy sub-20ms latency requirement on Zen 4 CPU. Shared CI runners
    # (3 vCPUs, noisy neighbours) set DOBBY_PERF_SCALE to widen the budget
    # while still catching a large regression.
    scale = max(1.0, float(os.environ.get("DOBBY_PERF_SCALE", "1") or 1))
    budget = 20.0 * scale
    assert metrics["total_latency_ms"] < budget, f"Latency {metrics['total_latency_ms']}ms exceeded {budget:g}ms threshold"
    assert metrics["fps"] >= 50.0 / scale


@needs_onnx
def test_local_neural_grounder_integration():
    """Validates LocalNeuralGrounder integration and fallback grace."""
    img = Image.new("RGB", (800, 600), (245, 245, 245))
    draw = ImageDraw.Draw(img)
    draw.rectangle([100, 100, 250, 140], fill=(0, 120, 215), outline=(0, 80, 160))

    # 1. Fast ONNX enabled
    grounder_fast = LocalNeuralGrounder(enable_fast_onnx=True, enable_gemma3=False)
    assert grounder_fast.fast_detector is not None
    assert grounder_fast.fast_detector.is_available() is True

    proposals = grounder_fast.generate_proposals(img)
    assert len(proposals) > 0

    # 2. Fast ONNX disabled -> fallback to OpenCV heuristics
    grounder_cv = LocalNeuralGrounder(enable_fast_onnx=False, enable_gemma3=False)
    assert grounder_cv.fast_detector is None

    cv_proposals = grounder_cv.generate_proposals(img)
    assert len(cv_proposals) > 0
    # CV proposals do not have VISION_FAST tag (source key is absent or different)
    assert cv_proposals[0].get("source") != "VISION_FAST"


def test_vdom_fusion_with_neural_proposals():
    """Validates Virtual DOM fusion engine handling of neural fast detector proposals."""
    fusion = VDOMFusionEngine(iou_threshold=0.60)

    atspi_root = VDOMNode(
        node_id="vdom_root",
        source=GroundingSource.AT_SPI2,
        role=UIRole.WINDOW,
        name="Main Window",
        bbox=BoundingBox(x=0, y=0, width=1920, height=1080),
        click_point=(960, 540),
    )
    atspi_btn = VDOMNode(
        node_id="vdom_btn",
        source=GroundingSource.AT_SPI2,
        role=UIRole.PUSH_BUTTON,
        name="Submit",
        bbox=BoundingBox(x=100, y=200, width=120, height=36),
        click_point=(160, 218),
        parent_id="vdom_root",
    )

    # Proposals from Fast ONNX detector
    fast_proposals = [
        # Proposal 1: Matches atspi_btn -> Upgrades to HYBRID
        {
            "bbox": {"x": 102, "y": 201, "width": 118, "height": 34},
            "confidence": 0.94,
            "class_name": "push_button",
            "text": "Submit",
            "source": "VISION_FAST",
        },
        # Proposal 2: Neural checkbox detection -> Unmatched, maps to UIRole.CHECK_BOX
        {
            "bbox": {"x": 100, "y": 300, "width": 24, "height": 24},
            "confidence": 0.89,
            "class_name": "checkbox",
            "text": "Enable Turbo",
            "source": "VISION_FAST",
        },
        # Proposal 3: Neural container viewport -> Maps to UIRole.PANEL
        {
            "bbox": {"x": 300, "y": 100, "width": 400, "height": 300},
            "confidence": 0.91,
            "class_name": "container_viewport",
            "text": "Waveform Viewport",
            "source": "VISION_FAST",
        },
    ]

    snapshot = fusion.fuse(
        atspi_nodes=[atspi_root, atspi_btn],
        visual_proposals=fast_proposals,
        window_id="0x301",
        window_title="Main Window",
        window_bbox=BoundingBox(x=0, y=0, width=1920, height=1080),
    )

    # 1. Verify Hybrid upgrade on button
    fused_btn = snapshot.nodes["vdom_btn"]
    assert fused_btn.source == GroundingSource.HYBRID
    assert fused_btn.click_point == (102 + 59, 201 + 17)

    # 2. Verify checkbox role normalization
    cb_node = next((n for n in snapshot.nodes.values() if n.name == "Enable Turbo"), None)
    assert cb_node is not None
    assert cb_node.role == UIRole.CHECK_BOX
    assert cb_node.source == GroundingSource.VISION_ONLY

    # 3. Verify container_viewport role normalization to PANEL
    panel_node = next((n for n in snapshot.nodes.values() if n.name == "Waveform Viewport"), None)
    assert panel_node is not None
    assert panel_node.role == UIRole.PANEL
    assert panel_node.source == GroundingSource.VISION_ONLY
