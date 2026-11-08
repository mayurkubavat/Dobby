"""
Tests for Local Gemma 3 Multimodal Sidecar and Perceptual dHash Cache.
"""

import json
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw

from dobby.gemma.client import GemmaClient, GemmaError, normalize_base_url
from dobby.types import (
    BoundingBox,
    GroundingSource,
    ModalRiskTier,
    UIRole,
    VDOMNode,
    VirtualDOMSnapshot,
)
from dobby.vdom.gemma3_sidecar import (
    Gemma3Grounder,
    SemanticROICache,
    compute_dhash,
    hamming_distance,
)
from dobby.vdom.sidecar import LocalNeuralGrounder


def _create_sample_image(color="white", draw_button=True) -> Image.Image:
    img = Image.new("RGB", (200, 100), color=color)
    if draw_button:
        draw = ImageDraw.Draw(img)
        draw.rectangle([20, 20, 80, 60], fill="blue", outline="black")
        draw.rectangle([100, 20, 180, 60], fill="gray", outline="black")
    return img


def test_dhash_and_hamming_distance():
    img1 = _create_sample_image("white", draw_button=True)
    img2 = _create_sample_image("white", draw_button=True)
    img3 = _create_sample_image("black", draw_button=False)

    h1 = compute_dhash(img1)
    h2 = compute_dhash(img2)
    h3 = compute_dhash(img3)

    assert h1 == h2
    assert hamming_distance(h1, h2) == 0
    assert hamming_distance(h1, h3) > 10


def test_semantic_roi_cache():
    cache = SemanticROICache(capacity=2, threshold=4)
    proposals = [
        {
            "rel_box": {"x": 10, "y": 10, "width": 50, "height": 30},
            "class_name": "push_button",
            "confidence": 0.95,
        }
    ]

    h1 = 0x123456789ABCDEF0
    cache.put("anchor_a:intent_1", h1, proposals)

    # Exact hit
    assert cache.get("anchor_a:intent_1", h1) == proposals

    # Perceptual hit (Hamming distance = 1 bit flipped)
    h1_drift = h1 ^ 0x01
    assert hamming_distance(h1, h1_drift) == 1
    assert cache.get("anchor_a:intent_1", h1_drift) == proposals

    # Miss due to large distance
    h_distant = h1 ^ 0xFFFFFFFFFF
    assert cache.get("anchor_a:intent_1", h_distant) is None

    # Test LRU eviction
    cache.put("anchor_b:intent_1", 0x1111, proposals)
    cache.put("anchor_c:intent_1", 0x2222, proposals)
    assert len(cache) == 2
    assert cache.get("anchor_a:intent_1", h1) is None


class FakeOllama:
    """Fake transport for GemmaClient: serves /api/tags and canned /api/chat replies."""

    def __init__(self, replies=None, models=("gemma3:4b", "gemma3:12b"), fail=None):
        self.replies = list(replies or [])
        self.models = list(models)
        self.fail = fail
        self.chat_calls = []

    def __call__(self, method, url, body, timeout):
        if url.endswith("/api/tags"):
            return {"models": [{"name": m} for m in self.models]}
        assert url.endswith("/api/chat"), url
        self.chat_calls.append(body)
        if self.fail is not None:
            raise self.fail
        reply = self.replies.pop(0)
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return {"message": {"role": "assistant", "content": content}}


def _grounder(fake, **kw):
    return Gemma3Grounder(client=GemmaClient(transport=fake), **kw)


def test_gemma3_grounder_schema_call_and_cache():
    fake = FakeOllama(
        replies=[
            {
                "elements": [
                    {
                        "box_2d": [100, 200, 500, 600],
                        "role": "icon_button",
                        "label": "Zoom Fit",
                        "confidence": 0.96,
                    },
                    {
                        "box_2d": [600, 100, 900, 800],
                        "role": "waveform_signal",
                        "label": "clk_250m",
                        "confidence": 0.91,
                    },
                ]
            }
        ]
    )
    grounder = _grounder(fake)
    img = _create_sample_image()

    proposals = grounder.ground_roi(
        image=img, offset=(50, 100), anchor_id="waveform_canvas", task_intent="find_zoom_fit"
    )

    assert len(proposals) == 2
    # box_2d [100, 200, 500, 600] on a 200x100 crop -> rel (40, 10, 80, 40), plus offset (50, 100)
    p1 = proposals[0]
    assert (p1["bbox"]["x"], p1["bbox"]["y"], p1["bbox"]["width"], p1["bbox"]["height"]) == (
        90,
        110,
        80,
        40,
    )
    assert p1["class_name"] == "icon_button"
    assert p1["text"] == "Zoom Fit"

    body = fake.chat_calls[0]
    assert body["model"] == "gemma3:4b"
    assert body["format"]["required"] == ["elements"]  # a JSON schema, not just "json"
    assert body["messages"][1]["images"]

    # Same crop again: served from the dHash cache with no Gemma call
    again = grounder.ground_roi(
        image=img, offset=(50, 100), anchor_id="waveform_canvas", task_intent="find_zoom_fit"
    )
    assert len(again) == 2
    assert len(fake.chat_calls) == 1
    assert grounder.diagnostics()["roi_cache_hits"] == 1


def test_gemma3_grounder_returns_empty_on_failure():
    fake = FakeOllama(fail=TimeoutError("timed out"))
    grounder = _grounder(fake)
    assert grounder.ground_roi(_create_sample_image()) == []
    stats = grounder.diagnostics()
    assert stats["errors"] == 1 and "timed out" in stats["last_error"]


def test_gemma3_modal_classification_aligned_tiers():
    fake = FakeOllama(
        replies=[
            {
                "risk_tier": "INFORMATIVE_ACK",
                "suggested_button": "ok",
                "confidence": 0.9,
                "reason": "Synthesis finished notice.",
            }
        ]
    )
    grounder = _grounder(fake)
    result = grounder.classify_modal("Synthesis completed successfully.", button_labels=["OK"])
    assert result["risk_tier"] == ModalRiskTier.INFORMATIVE_ACK.value
    assert result["suggested_button"] == "OK"  # normalized to the real label
    assert result["source"] == "gemma"
    assert fake.chat_calls[0]["format"]["properties"]["risk_tier"]["enum"] == [
        t.value for t in ModalRiskTier
    ]


def test_gemma3_modal_destructive_never_suggests_button():
    fake = FakeOllama(
        replies=[
            {
                "risk_tier": "DESTRUCTIVE_OR_SYSTEM",
                "suggested_button": "Discard",
                "confidence": 0.98,
                "reason": "Drops waveforms.",
            }
        ]
    )
    result = _grounder(fake).classify_modal(
        "Reset simulation and discard waveforms?", button_labels=["Discard", "Cancel"]
    )
    assert result["risk_tier"] == ModalRiskTier.DESTRUCTIVE_OR_SYSTEM.value
    assert result["suggested_button"] is None


@pytest.mark.parametrize(
    "fake",
    [
        FakeOllama(fail=OSError("connection refused")),
        FakeOllama(replies=["not json"]),
        FakeOllama(
            replies=[
                {
                    "risk_tier": "PASSIVE_NOTIFICATION",
                    "suggested_button": "OK",
                    "confidence": 0.9,
                    "reason": "",
                }
            ]
        ),
        FakeOllama(
            replies=[
                {
                    "risk_tier": "BENIGN_DISMISSIBLE",
                    "suggested_button": "Delete All",
                    "confidence": 0.9,
                    "reason": "",
                }
            ]
        ),
        FakeOllama(models=("gemma3:12b",)),  # configured model not installed
    ],
)
def test_gemma3_modal_fails_closed(fake):
    result = _grounder(fake).classify_modal("Some dialog", button_labels=["OK", "Cancel"])
    assert result["risk_tier"] == ModalRiskTier.WORKFLOW_BRANCHING.value
    assert result["suggested_button"] is None
    assert result["source"] == "fallback"


def test_gemma3_defaults_use_installed_model():
    grounder = Gemma3Grounder(client=GemmaClient(transport=FakeOllama()))
    assert grounder.text_model == "gemma3:4b"
    assert grounder.is_available()
    assert not Gemma3Grounder(
        client=GemmaClient(transport=FakeOllama(models=("gemma3:4b-x",)))
    ).is_available()


def test_gemma_client_url_normalization_and_stats():
    assert normalize_base_url("http://127.0.0.1:11434/api/generate") == "http://127.0.0.1:11434"
    assert normalize_base_url("http://host:11434/") == "http://host:11434"

    fake = FakeOllama(replies=[{"ok": True}, "[1, 2]"])
    client = GemmaClient(transport=fake, keep_alive="5m")
    assert client.chat_json("gemma3:4b", "s", "u", {"type": "object"}) == {"ok": True}
    assert fake.chat_calls[0]["keep_alive"] == "5m"
    assert fake.chat_calls[0]["options"]["temperature"] == 0.0
    with pytest.raises(GemmaError):
        client.chat_json("gemma3:4b", "s", "u", {"type": "object"})  # array, not object
    stats = client.stats()
    assert stats["calls"] == 2 and stats["errors"] == 1
    assert stats["per_model"]["gemma3:4b"]["calls"] == 2


def test_gemma_client_untagged_model_matches_latest():
    client = GemmaClient(transport=FakeOllama(models=("gemma3:latest",)))
    assert client.has_model("gemma3")
    assert not client.has_model("gemma3:4b")


def test_gemma3_vdom_fusion():
    grounder = Gemma3Grounder()
    root = VDOMNode(
        node_id="root_1",
        role=UIRole.WINDOW,
        name="Main Window",
        bbox=BoundingBox(x=0, y=0, width=1920, height=1080),
        source=GroundingSource.AT_SPI2,
    )
    vdom = VirtualDOMSnapshot(
        snapshot_id="snap_test_1",
        timestamp=1.0,
        window_title="Vivado 2025.1",
        window_bbox=BoundingBox(x=0, y=0, width=1920, height=1080),
        root_id="root_1",
        nodes={"root_1": root},
    )

    proposals = [
        {
            "bbox": {"x": 200, "y": 300, "width": 80, "height": 30},
            "class_name": "push_button",
            "text": "Run Simulation",
            "confidence": 0.94,
        }
    ]

    fused_vdom = grounder.fuse_with_vdom(vdom, proposals, anchor_id="tcl_console")
    assert len(fused_vdom.nodes) == 2
    assert "gemma3_2" in fused_vdom.nodes
    gemma_node = fused_vdom.nodes["gemma3_2"]
    assert gemma_node.node_id == "gemma3_2"
    assert gemma_node.role == UIRole.PUSH_BUTTON
    assert gemma_node.name == "Run Simulation"
    assert gemma_node.source == GroundingSource.VISION_ONLY
    assert gemma_node.attributes.get("anchor_id") == "tcl_console"
    assert gemma_node.click_point == (240, 315)
    assert "gemma3_2" in fused_vdom.nodes["root_1"].children_ids


def test_local_neural_grounder_delegation():
    """ground_subpane keeps detector geometry and only takes names from Gemma."""
    grounder = LocalNeuralGrounder(enable_gemma3=True)
    img = _create_sample_image()
    detector_box = {
        "bbox": {"x": 30, "y": 40, "width": 50, "height": 20},
        "confidence": 0.82,
        "class_name": "push_button",
        "text": "",
    }

    with (
        patch.object(grounder, "generate_proposals", return_value=[dict(detector_box)]),
        patch.object(
            grounder.gemma3_grounder.marks,
            "label_marks",
            return_value={0: {"label": "Step", "role": "icon_button"}},
        ) as mock_label,
    ):
        proposals = grounder.ground_subpane(img, offset=(10, 20), anchor_id="sim_toolbar")

    assert len(proposals) == 1
    assert proposals[0]["bbox"] == {"x": 40, "y": 60, "width": 50, "height": 20}
    assert proposals[0]["class_name"] == "icon_button"
    assert proposals[0]["text"] == "Step"
    assert proposals[0]["named_by"] == "gemma"
    mock_label.assert_called_once()
    assert mock_label.call_args.kwargs["app_key"] == "sim_toolbar"
