"""Unit tests for Computer Vision (CV) interactable region detector."""

import numpy as np
from PIL import Image
import cv2
import pytest

from dobby.perception.cv_detector import CVDetector
from dobby.types import UIElement
import dobby.server as server


def test_cv_detector_synthetic_ui():
    """Verify CVDetector detects distinct button and input elements."""
    # Create dark-theme synthetic window (800x600)
    canvas = np.zeros((600, 800, 3), dtype=np.uint8) + 25

    # Button 1: Save (100, 80, 140, 45)
    cv2.rectangle(canvas, (100, 80), (240, 125), (40, 120, 220), -1)
    cv2.rectangle(canvas, (100, 80), (240, 125), (255, 255, 255), 1)

    # Button 2: Discard (260, 80, 140, 45)
    cv2.rectangle(canvas, (260, 80), (400, 125), (80, 80, 80), -1)
    cv2.rectangle(canvas, (260, 80), (400, 125), (180, 180, 180), 1)

    # Input Box: Search Field (100, 200, 450, 40)
    cv2.rectangle(canvas, (100, 200), (550, 240), (45, 45, 48), -1)
    cv2.rectangle(canvas, (100, 200), (550, 240), (100, 100, 100), 1)

    pil_img = Image.fromarray(canvas)

    detector = CVDetector(min_area=100, min_dimension=10)
    elements = detector.detect(pil_img, scale_factor=1.0)

    assert len(elements) >= 3
    
    # Check that bounding boxes roughly encompass our synthetic elements
    centers = [el.bbox.center for el in elements]

    # Save button center should be near (170, 102)
    assert any(150 <= c[0] <= 190 and 90 <= c[1] <= 120 for c in centers)

    # Discard button center should be near (330, 102)
    assert any(310 <= c[0] <= 350 and 90 <= c[1] <= 120 for c in centers)


def test_cv_detector_scaling_inversion():
    """Verify scale factor properly scales coordinates back to original physical screen."""
    canvas = np.zeros((300, 400, 3), dtype=np.uint8) + 20
    # Button at 100, 50, w=100, h=40 on half-scale image (meaning 200, 100, w=200, h=80 on physical screen)
    cv2.rectangle(canvas, (100, 50), (200, 90), (0, 150, 0), -1)
    cv2.rectangle(canvas, (100, 50), (200, 90), (255, 255, 255), 1)

    pil_img = Image.fromarray(canvas)
    detector = CVDetector(min_area=100, min_dimension=10)

    # scale_factor = 0.5 (half size)
    elements = detector.detect(pil_img, scale_factor=0.5)

    assert len(elements) >= 1
    target = elements[0]
    # Physical center should be around (300, 140)
    cx, cy = target.bbox.center
    assert 280 <= cx <= 320
    assert 120 <= cy <= 160


def test_server_take_screenshot_annotated_cv(live_screen):
    """Verify take_screenshot with mode='annotated_cv' works on live desktop."""
    from subprocess import CalledProcessError

    try:
        res = server.take_screenshot(mode="annotated_cv", include_image=True)
    except CalledProcessError:
        pytest.skip("screen capture is unavailable in this environment")
    assert "marks" in res
    assert isinstance(res["marks"], list)
    assert "image_base64" in res
    assert res["width"] > 0
