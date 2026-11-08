"""
Tests for Gemma Set-of-Marks labeling and selection (Gemma never returns coordinates).
"""

import base64
import io
from unittest.mock import MagicMock

from PIL import Image, ImageDraw
from test_gemma3_sidecar import FakeOllama

from pinlet.agent_loop.controller import FeedbackLoopController
from pinlet.gemma.client import GemmaClient
from pinlet.types import (
    BoundingBox,
    GroundingSource,
    UIRole,
    VDOMNode,
    VirtualDOMSnapshot,
    WindowInfo,
)
from pinlet.vdom.gemma3_sidecar import Gemma3Grounder
from pinlet.vdom.gemma_marks import GemmaMarkLabeler, draw_marks


def _toolbar(w=1200, h=400) -> Image.Image:
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, 60, 60], fill="blue")
    d.ellipse([100, 20, 140, 60], fill="red")
    d.rectangle([200, 20, 300, 50], outline="black")
    return img


BOXES = [(20, 20, 40, 40), (100, 20, 40, 40), (200, 20, 100, 30)]


def _labeler(fake, **kw):
    return GemmaMarkLabeler(GemmaClient(transport=fake), **kw)


def _sent_image(fake, i=0) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(fake.chat_calls[i]["messages"][1]["images"][0])))


def test_draw_marks_downscales_and_keeps_size_cap():
    out = draw_marks(_toolbar(), BOXES, max_side=896)
    assert max(out.size) == 896
    # Badge 1 is drawn in the first palette color at the scaled box corner
    x0 = y0 = int(20 * 896 / 1200)
    assert out.getpixel((x0 + 1, y0 + 1)) == (0xE6, 0x19, 0x4B)


def test_label_marks_validates_and_caches():
    fake = FakeOllama(
        replies=[
            {
                "labels": [
                    {"mark": 1, "label": "Run", "role": "icon_button"},
                    {"mark": 2, "label": "Stop", "role": "icon_button"},
                    {"mark": 9, "label": "Ghost", "role": "icon_button"},  # out of range
                    {"mark": 3, "label": "Search", "role": "not_a_role"},  # bad role
                ]
            }
        ]
    )
    lab = _labeler(fake)
    img = _toolbar()
    res = lab.label_marks(img, BOXES, app_key="vivado")
    assert res == {
        0: {"label": "Run", "role": "icon_button"},
        1: {"label": "Stop", "role": "icon_button"},
    }
    assert max(_sent_image(fake).size) == 896
    assert fake.chat_calls[0]["format"]["required"] == ["labels"]

    # Second pass: the two named patches come from cache; only box 3 is sent again
    fake.replies = [{"labels": [{"mark": 1, "label": "Search", "role": "text_input"}]}]
    res2 = lab.label_marks(img, BOXES, app_key="vivado")
    assert res2[2] == {"label": "Search", "role": "text_input"}
    assert res2[0]["label"] == "Run"
    assert lab.cache_hits == 2
    assert fake.chat_calls[1]["messages"][1]["content"] == "Name boxes 1 to 1."

    # Cache is per app
    fake.replies = [{"labels": []}]
    lab.label_marks(img, BOXES[:1], app_key="verdi")
    assert len(fake.chat_calls) == 3


def test_label_marks_batches_and_survives_failure():
    fake = FakeOllama(
        replies=[{"labels": [{"mark": 1, "label": "A", "role": "icon_button"}]}, {"labels": []}]
    )
    lab = _labeler(fake, max_marks=2)
    lab.label_marks(_toolbar(), BOXES)
    assert len(fake.chat_calls) == 2

    broken = _labeler(FakeOllama(fail=TimeoutError("slow")))
    assert broken.label_marks(_toolbar(), BOXES) == {}


def test_find_mark():
    fake = FakeOllama(replies=[{"mark": 2, "confidence": 0.9, "reason": "red stop icon"}])
    res = _labeler(fake).find_mark(_toolbar(), BOXES, "stop button")
    assert res["index"] == 1 and res["source"] == "gemma"

    none = _labeler(FakeOllama(replies=[{"mark": None, "confidence": 0.2, "reason": "absent"}]))
    assert none.find_mark(_toolbar(), BOXES, "save")["index"] is None
    bad = _labeler(FakeOllama(replies=[{"mark": 7, "confidence": 0.9, "reason": ""}]))
    assert bad.find_mark(_toolbar(), BOXES, "save")["index"] is None
    down = _labeler(FakeOllama(fail=OSError("refused")))
    assert down.find_mark(_toolbar(), BOXES, "save")["source"] == "fallback"


def _controller(fake, proposals):
    hal = MagicMock()
    hal.capture_screen.return_value = _toolbar()
    hal.get_active_window.return_value = WindowInfo(
        window_id="1", title="Vivado", bbox=BoundingBox(x=500, y=300, width=1200, height=400)
    )
    ctrl = FeedbackLoopController.__new__(FeedbackLoopController)
    ctrl.hal = hal
    ctrl.neural_grounder = MagicMock()
    ctrl.neural_grounder.gemma3_grounder = Gemma3Grounder(client=GemmaClient(transport=fake))
    ctrl.neural_grounder.generate_proposals.return_value = proposals
    return ctrl


def _prop(x, y, w, h):
    return {
        "bbox": {"x": x, "y": y, "width": w, "height": h},
        "confidence": 0.8,
        "class_name": "push_button",
        "text": "",
    }


def test_find_with_gemma_clicks_detector_box_in_screen_coords():
    fake = FakeOllama(replies=[{"mark": 1, "confidence": 0.88, "reason": "matches"}])
    # Sorted largest-first, so the 100x30 box becomes mark 1
    ctrl = _controller(fake, [_prop(20, 20, 40, 40), _prop(200, 20, 100, 30)])
    res = ctrl.find_with_gemma("search field")
    assert res["found"]
    assert res["bbox"] == {"x": 700, "y": 320, "width": 100, "height": 30}
    assert res["click_point"] == [750, 335]
    ctrl.hal.capture_screen.assert_called_once_with(region=(500, 300, 1200, 400))


def test_label_vdom_names_only_unnamed_visual_nodes():
    fake = FakeOllama(
        replies=[{"labels": [{"mark": 1, "label": "Zoom Fit", "role": "icon_button"}]}]
    )
    ctrl = _controller(fake, [])
    win = BoundingBox(x=500, y=300, width=1200, height=400)
    nodes = {
        "a": VDOMNode(
            node_id="a",
            source=GroundingSource.VISION_ONLY,
            role=UIRole.PUSH_BUTTON,
            name="",
            bbox=BoundingBox(x=520, y=320, width=40, height=40),
        ),
        "b": VDOMNode(
            node_id="b",
            source=GroundingSource.VISION_ONLY,
            role=UIRole.STATIC_TEXT,
            name="clk",
            bbox=BoundingBox(x=600, y=320, width=40, height=40),
        ),
        "c": VDOMNode(
            node_id="c",
            source=GroundingSource.AT_SPI2,
            role=UIRole.PUSH_BUTTON,
            name="",
            bbox=BoundingBox(x=700, y=320, width=40, height=40),
        ),
    }
    snap = VirtualDOMSnapshot(
        snapshot_id="s", timestamp=0.0, window_bbox=win, root_id="a", nodes=nodes
    )
    named = ctrl._label_vdom_with_gemma(snap, _toolbar(), win, app_key="vivado")
    assert named == 1
    assert snap.nodes["a"].name == "Zoom Fit" and snap.nodes["a"].role == UIRole.ICON_BUTTON
    assert snap.nodes["a"].attributes["named_by"] == "gemma"
    assert snap.nodes["b"].name == "clk" and snap.nodes["c"].name == ""
    assert fake.chat_calls[0]["messages"][1]["content"] == "Name boxes 1 to 1."


def test_capture_vdom_captures_window_region():
    ctrl = _controller(FakeOllama(), [])
    win = ctrl.hal.get_active_window.return_value
    ctrl.window_clipper = MagicMock()
    ctrl.window_clipper.resolve_target_window.return_value = win
    ctrl.pipeline = MagicMock()
    ctrl.pipeline.capture.return_value = (_toolbar(), 1.0, (1200, 400), (0, 0))
    ctrl.atspi_inspector = MagicMock()
    ctrl.atspi_inspector.inspect_desktop.return_value = []
    from pinlet.vdom.fusion import VDOMFusionEngine

    ctrl.vdom_fusion = VDOMFusionEngine()
    ctrl.capture_vdom(label_with_gemma=False)
    assert ctrl.pipeline.capture.call_args.kwargs["region"] == (500, 300, 1200, 400)
    ctrl.neural_grounder.generate_proposals.assert_called_once()
    assert ctrl.neural_grounder.generate_proposals.call_args.kwargs["offset"] == (500, 300)
