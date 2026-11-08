"""Unit and integration tests for enhanced hotswappable grounding and compound tools."""

from PIL import Image, ImageDraw

from dobby.agent_loop.controller import FeedbackLoopController
from dobby.perception.grounding.engine import GroundingEngine
from dobby.perception.grounding.ensemble import compute_iou
from dobby.perception.grounding.template import TemplateGroundingProvider
from dobby.perception.token_optimizer import TokenOptimizer
from dobby.server import (
    click_and_type,
    configure_vision,
    get_active_window,
    get_vision_diagnostics,
)
from dobby.types import BoundingBox


def test_iou_computation():
    """Verifies IoU bounding box calculations for NMS fusion."""
    boxA = BoundingBox(x=10, y=10, width=50, height=50)
    # Identical box should have IoU = 1.0
    assert compute_iou(boxA, boxA) == 1.0

    # Non-overlapping box should have IoU = 0.0
    boxB = BoundingBox(x=100, y=100, width=50, height=50)
    assert compute_iou(boxA, boxB) == 0.0

    # Partial overlap
    boxC = BoundingBox(x=35, y=10, width=50, height=50)
    iou = compute_iou(boxA, boxC)
    assert 0.0 < iou < 1.0


def test_grounding_engine_hotswapping():
    """Verifies runtime strategy switching."""
    engine = GroundingEngine()
    assert "ensemble" in engine.list_strategies()
    assert "semantic" in engine.list_strategies()
    assert "cv" in engine.list_strategies()
    assert "template" in engine.list_strategies()

    # Hotswap to CV
    ok = engine.set_strategy("cv")
    assert ok is True
    assert engine.active_strategy == "cv"
    assert engine.current_provider.name == "cv"

    # Hotswap to Semantic
    ok = engine.set_strategy("semantic")
    assert ok is True
    assert engine.active_strategy == "semantic"

    # Invalid strategy
    ok = engine.set_strategy("non_existent")
    assert ok is False


def test_template_grounding_matching():
    """Verifies OpenCV template icon matching provider."""
    provider = TemplateGroundingProvider(match_threshold=0.85)

    # Create a synthetic target image with a distinctive patterned icon
    screen = Image.new("RGB", (300, 300), color="white")
    draw = ImageDraw.Draw(screen)
    draw.rectangle([50, 50, 90, 90], fill="black")
    draw.ellipse([60, 60, 80, 80], fill="red")

    # Register the icon template
    template = screen.crop((50, 50, 90, 90))
    provider.register_template("custom_icon", template)

    matches = provider.detect(screen, scale_factor=1.0)
    assert len(matches) >= 1
    best_match = matches[0]
    assert best_match.name == "custom_icon"
    # Should be located at (50, 50)
    assert abs(best_match.bbox.x - 50) <= 2
    assert abs(best_match.bbox.y - 50) <= 2


def test_token_optimizer_delta_patch():
    """Verifies delta patch bounding extraction for token efficiency."""
    before = Image.new("RGB", (800, 600), color="white")
    after = before.copy()
    draw = ImageDraw.Draw(after)
    # Simulate a prompt text input change
    draw.rectangle([200, 400, 450, 480], fill="blue")

    res = TokenOptimizer.compute_changed_patch(before, after, padding=10)
    assert res is not None
    patch_img, patch_box = res
    assert patch_img.size[0] > 0
    assert patch_img.size[1] > 0
    # Patch box should surround (200, 400)
    assert patch_box.x <= 200
    assert patch_box.y <= 400
    assert patch_box.x + patch_box.width >= 450
    assert patch_box.y + patch_box.height >= 480


def test_controller_click_and_type(mock_hal):
    """Verifies atomic compound action click_and_type."""
    controller = FeedbackLoopController(mock_hal)
    res = controller.click_and_type(
        text="Hello AI Vision",
        x=250,
        y=350,
        clear_first=True,
        press_enter=True,
        verify_delta=False,
    )
    assert res.success is True
    assert mock_hal.cursor_x == 250
    assert mock_hal.cursor_y == 350
    assert "Hello AI Vision" in mock_hal.typed_text
    assert "<enter>" in mock_hal.typed_text


def test_mcp_new_server_tools(mock_hal, monkeypatch):
    """Verifies all new enhanced MCP tools execute successfully."""
    import dobby.server as server

    # Never drive the real desktop: point the server at the mock HAL.
    monkeypatch.setattr(server, "hal", mock_hal)
    monkeypatch.setattr(server, "controller", FeedbackLoopController(mock_hal))
    # 1. configure_vision
    cfg_res = configure_vision(setting="grounding_strategy", value="cv")
    assert cfg_res["success"] is True
    assert cfg_res["value"] == "cv"

    # Reset back to ensemble
    configure_vision(setting="grounding_strategy", value="ensemble")

    # 2. get_vision_diagnostics
    diag = get_vision_diagnostics()
    assert "platform" in diag
    assert "supported_grounding_providers" in diag
    assert "ensemble" in diag["supported_grounding_providers"]

    # 3. get_active_window
    act_win = get_active_window()
    assert act_win is not None
    assert "title" in act_win

    # 4. click_and_type via server
    cat_res = click_and_type(text="Autonomous Prompt", x=150, y=150, verify_delta=False)
    assert cat_res["success"] is True
