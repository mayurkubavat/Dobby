"""
Tests for the local OCR lane (RapidOCR on Linux/Windows, Apple Vision on macOS).
"""

from unittest.mock import MagicMock

import pytest
from PIL import Image, ImageDraw, ImageFont

from dobby.agent_loop.controller import FeedbackLoopController
from dobby.perception import ocr as ocr_mod
from dobby.perception.ocr import (
    AppleVisionOCR,
    CachedOCR,
    NullOCR,
    OCREngine,
    OCRLine,
    RapidOCREngine,
    assign_text_to_boxes,
    get_ocr_engine,
    normalize_text,
    text_matches,
    vision_observations_to_lines,
)
from dobby.types import (
    BoundingBox,
    PostConditionAssertion,
    PostConditionContract,
    StateAssertionType,
    WindowInfo,
)


def _text_image() -> Image.Image:
    img = Image.new("RGB", (600, 160), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
    except OSError:
        pytest.skip("DejaVu font not available")
    d.text((20, 20), "Run Simulation", fill="black", font=font)
    d.text((20, 90), "Status: Running", fill="black", font=font)
    return img


class FakeEngine(OCREngine):
    name = "fake"

    def __init__(self, lines):
        self.lines = lines
        self.calls = 0

    @property
    def is_available(self):
        return True

    def _recognize(self, image):
        self.calls += 1
        return list(self.lines)


# ------------------------------------------------------------------ helpers


def test_text_matching_ignores_spacing():
    assert normalize_text("Run Simulation") == "runsimulation"
    assert text_matches("run simulation", "RunSimulation")
    assert text_matches("clk_250m", "clk 250m =")
    assert text_matches(r"Running|Done", "Status: Running", regex=True)
    assert not text_matches("[bad", "x", regex=True)


def test_engine_selection_by_platform(monkeypatch):
    monkeypatch.setattr(ocr_mod.sys, "platform", "darwin")
    assert isinstance(get_ocr_engine("auto").inner, AppleVisionOCR)
    monkeypatch.setattr(ocr_mod.sys, "platform", "linux")
    assert isinstance(get_ocr_engine("auto").inner, RapidOCREngine)
    assert isinstance(get_ocr_engine("none").inner, NullOCR)
    assert get_ocr_engine("none").recognize(Image.new("RGB", (50, 50))) == []


def test_apple_vision_unavailable_off_mac(monkeypatch):
    monkeypatch.setattr(ocr_mod.sys, "platform", "linux")
    eng = AppleVisionOCR()
    assert not eng.is_available
    assert "macOS" in eng._error


def test_vision_observation_conversion_flips_y():
    class Cand:
        def string(self):
            return "Save"

        def confidence(self):
            return 1.0

    class Obs:
        def topCandidates_(self, n):
            return [Cand()]

        def boundingBox(self):
            # normalized, bottom-left origin: x=0.1, y=0.7, w=0.2, h=0.1
            return ((0.1, 0.7), (0.2, 0.1))

    (line,) = vision_observations_to_lines([Obs()], width=1000, height=500)
    assert (line.x, line.y, line.width, line.height) == (100, 100, 200, 50)
    assert line.text == "Save"


def test_cached_ocr_reads_unchanged_image_once():
    fake = FakeEngine([OCRLine("OK", 1, 2, 30, 10, 0.9), OCRLine("noise", 0, 0, 5, 5, 0.1)])
    cached = CachedOCR(fake)
    img = _text_image()
    first = cached.recognize(img)
    second = cached.recognize(img)
    assert [ln.text for ln in first] == ["OK"]  # low-confidence line dropped
    assert first == second
    assert fake.calls == 1 and cached.hits == 1
    assert cached.diagnostics()["engine"] == "fake"


def test_assign_text_to_boxes():
    props = [
        {"bbox": {"x": 0, "y": 0, "width": 100, "height": 40}, "text": ""},
        {"bbox": {"x": 200, "y": 0, "width": 100, "height": 40}, "text": "kept"},
    ]
    lines = [
        OCRLine("Run", 10, 10, 30, 20, 0.9),
        OCRLine("Sim", 50, 10, 30, 20, 0.9),
        OCRLine("x", 210, 10, 10, 10, 0.9),
    ]
    assert assign_text_to_boxes(props, lines) == 1
    assert props[0]["text"] == "Run Sim" and props[0]["named_by"] == "ocr"
    assert props[1]["text"] == "kept"


@pytest.mark.skipif(not RapidOCREngine().is_available, reason="rapidocr_onnxruntime not installed")
def test_rapidocr_reads_rendered_text():
    lines = RapidOCREngine().recognize(_text_image())
    texts = [normalize_text(ln.text) for ln in lines]
    assert any("runsimulation" in t for t in texts), texts
    run = next(ln for ln in lines if "runsimulation" in normalize_text(ln.text))
    assert 10 <= run.x <= 40 and 10 <= run.y <= 40


# --------------------------------------------------------------- controller


def _controller(lines):
    hal = MagicMock()
    hal.capture_screen.return_value = Image.new("RGB", (800, 600), "white")
    hal.get_active_window.return_value = WindowInfo(
        window_id="1", title="Vivado", bbox=BoundingBox(x=100, y=200, width=800, height=600)
    )
    ctrl = FeedbackLoopController.__new__(FeedbackLoopController)
    ctrl.hal = hal
    ctrl.ocr = CachedOCR(FakeEngine(lines))
    ctrl.neural_grounder = MagicMock()
    ctrl.atspi_inspector = MagicMock()
    ctrl.atspi_inspector.inspect.return_value = []
    ctrl.quiescence_monitor = None
    return ctrl


def test_read_screen_text_offsets_and_filters():
    ctrl = _controller(
        [
            OCRLine("Status: Running", 10, 20, 150, 18, 0.95),
            OCRLine("Run Simulation", 10, 60, 160, 18, 0.9),
        ]
    )
    res = ctrl.read_screen_text(contains="running")
    assert res["available"] and res["engine"] == "fake" and res["count"] == 1
    line = res["lines"][0]
    assert line["bbox"] == {"x": 110, "y": 220, "width": 150, "height": 18}
    assert line["click_point"] == [185, 229]
    ctrl.hal.capture_screen.assert_called_with(region=(100, 200, 800, 600))


def test_text_appeared_falls_back_to_ocr():
    ctrl = _controller([OCRLine("Simulation finished", 10, 20, 150, 18, 0.95)])
    contract = PostConditionContract(
        assertions=[
            PostConditionAssertion(
                assertion_type=StateAssertionType.TEXT_APPEARED, target_name_regex="finished"
            )
        ]
    )
    ok, details = ctrl.verify_post_conditions(contract)
    assert ok, details
    assert "OCR" in details[0]


def test_find_visual_element_uses_ocr_before_gemma():
    ctrl = _controller([OCRLine("RunSimulation", 30, 40, 120, 20, 0.97)])
    gemma = MagicMock()
    ctrl.neural_grounder.gemma3_grounder = gemma
    res = ctrl.find_with_gemma("Run Simulation button")
    assert res["found"] and res["source"] == "ocr"
    assert res["click_point"] == [190, 250]
    gemma.marks.find_mark.assert_not_called()


def test_read_screen_text_when_ocr_missing():
    ctrl = _controller([])
    ctrl.ocr = CachedOCR(NullOCR())
    res = ctrl.read_screen_text()
    assert res["available"] is False and res["lines"] == []


def test_capture_vdom_uses_ocr_for_names_and_free_text():
    from dobby.vdom.fusion import VDOMFusionEngine

    ctrl = _controller(
        [OCRLine("Run", 12, 12, 30, 16, 0.9), OCRLine("clk_250m", 300, 100, 80, 16, 0.9)]
    )
    win = ctrl.hal.get_active_window.return_value
    ctrl.window_clipper = MagicMock()
    ctrl.window_clipper.resolve_target_window.return_value = win
    ctrl.pipeline = MagicMock()
    ctrl.pipeline.capture.return_value = (
        Image.new("RGB", (800, 600), "white"),
        1.0,
        (800, 600),
        (0, 0),
    )
    ctrl.atspi_inspector.inspect_desktop.return_value = []
    ctrl.vdom_fusion = VDOMFusionEngine()
    # detector box in screen coords around the "Run" text (window origin 100, 200)
    ctrl.neural_grounder.generate_proposals.return_value = [
        {
            "bbox": {"x": 105, "y": 205, "width": 50, "height": 30},
            "confidence": 0.8,
            "class_name": "push_button",
            "text": "",
        }
    ]
    snap = ctrl.capture_vdom(label_with_gemma=False)
    names = sorted(n.name for n in snap.nodes.values() if n.name and n.name != "Vivado")
    assert names == ["Run", "clk_250m"]
