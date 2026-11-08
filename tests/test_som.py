"""Tests for Set-of-Marks (SoM) overlay generator."""

from PIL import Image

from dobby.perception.som import SetOfMarksGenerator
from dobby.types import BoundingBox, UIElement


def test_som_annotation_and_mapping():
    """Verify badges are drawn and mapping returns correct UIElement."""
    generator = SetOfMarksGenerator(font_size=12)
    img = Image.new("RGB", (800, 600), color=(20, 20, 20))

    elements = [
        UIElement(
            id=1,
            role="push button",
            name="Submit",
            bbox=BoundingBox(x=100, y=100, width=80, height=30),
            interactable=True,
        ),
        UIElement(
            id=2,
            role="text entry",
            name="Search Query",
            bbox=BoundingBox(x=250, y=100, width=200, height=35),
            interactable=True,
        ),
    ]

    annotated, mapping = generator.annotate(img, elements, scale_factor=1.0)

    assert annotated.size == (800, 600)
    assert len(mapping) == 2
    assert 1 in mapping
    assert 2 in mapping
    assert mapping[1].name == "Submit"
    assert mapping[1].bbox.center == (140, 115)
    assert mapping[2].name == "Search Query"
    assert mapping[2].bbox.center == (350, 117)


def test_som_scaled_coordinates():
    """Verify scaling factor properly scales element bounding boxes without altering original center."""
    generator = SetOfMarksGenerator()
    img = Image.new("RGB", (400, 300), color=(20, 20, 20))

    elements = [
        UIElement(
            id=1,
            role="button",
            name="OK",
            bbox=BoundingBox(x=200, y=100, width=100, height=50),
            interactable=True,
        )
    ]

    # Half scale: original was 800x600, current image is 400x300
    annotated, mapping = generator.annotate(img, elements, scale_factor=0.5)

    assert annotated.size == (400, 300)
    assert mapping[1].bbox.center == (250, 125)  # Original center preserved for physical click
