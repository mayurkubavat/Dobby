"""Tests for Agent Feedback Loop Controller and verification."""

import pytest
from PIL import Image

from pinlet.agent_loop.controller import FeedbackLoopController
from pinlet.types import BoundingBox, UIElement


def test_feedback_loop_coordinate_resolution(mock_hal):
    """Test resolution between mark_id and explicit coordinates."""
    controller = FeedbackLoopController(mock_hal)
    controller.active_marks = {
        5: UIElement(
            id=5,
            role="button",
            name="Run Test",
            bbox=BoundingBox(x=300, y=400, width=100, height=50),
            interactable=True,
        )
    }

    # Resolution by mark ID
    cx, cy = controller.resolve_target_coordinates(mark_id=5)
    assert cx == 350
    assert cy == 425

    # Resolution by explicit (x, y)
    ex, ey = controller.resolve_target_coordinates(x=120, y=240)
    assert ex == 120
    assert ey == 240

    # Non-existent mark ID should raise ValueError
    with pytest.raises(ValueError, match="Mark ID #99 not found"):
        controller.resolve_target_coordinates(mark_id=99)


def test_feedback_loop_delta_computation():
    """Verify image delta computation detects pixel alterations."""
    img1 = Image.new("RGB", (100, 100), color=(0, 0, 0))
    img2 = Image.new("RGB", (100, 100), color=(0, 0, 0))

    delta_identical = FeedbackLoopController(None).pipeline.compute_image_delta(img1, img2)
    assert delta_identical == 0.0

    # Change 20x20 quadrant to white (400 pixels out of 10,000 = 4% = 0.04)
    for x in range(20):
        for y in range(20):
            img2.putpixel((x, y), (255, 255, 255))

    delta_changed = FeedbackLoopController(None).pipeline.compute_image_delta(img1, img2)
    assert 0.035 <= delta_changed <= 0.045
