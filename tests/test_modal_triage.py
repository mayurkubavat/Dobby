"""
Unit and integration tests for Phase 2: Semantic Modal Dialog Triage & Policy Router.
Tests SemanticModalRouter, ObstructionInterceptor, risk classification tiers, and resolve_modal_dialog.
"""

from dobby.agent_loop.obstruction import (
    ObstructionInterceptor,
    SemanticModalRouter,
)
from dobby.hal.base import IDesktopHAL
from dobby.server import (
    dismiss_obstructions,
    resolve_modal_dialog,
)
from dobby.types import (
    BoundingBox,
    ModalButtonOption,
    ModalRiskTier,
)


class MockHAL(IDesktopHAL):
    """Mock Desktop HAL for deterministic unit testing."""

    def get_screen_size(self):
        return (1920, 1080)

    def capture_screen(self, region=None):
        from PIL import Image

        return Image.new("RGB", (100, 100), color="white")

    def get_cursor_position(self):
        return (500, 500)

    def mouse_move(self, x, y):
        pass

    def mouse_click(self, x, y, button="left", click_count=1):
        pass

    def mouse_drag(self, start_x, start_y, end_x, end_y, duration_sec=0.3):
        pass

    def mouse_scroll(self, dx, dy):
        pass

    def keyboard_type(self, text, delay_ms=12.0):
        pass

    def keyboard_press_key(self, key_name: str) -> None:
        pass

    def keyboard_hotkey(self, keys):
        pass

    def list_windows(self):
        return []

    def get_active_window(self):
        return None

    def focus_window(self, window_id):
        return True


def test_semantic_router_benign_dismissible():
    """Verifies that benign popups (tips, cookies) are classified as Tier 0 with safe auto-dismissal."""
    router = SemanticModalRouter()
    buttons = [
        ModalButtonOption(
            button_id="btn_close",
            label="Close",
            bbox=BoundingBox(x=100, y=100, width=80, height=30),
        )
    ]
    tier, safe_btn, escalation = router.classify_and_route(
        window_title="Tip of the Day",
        body_text="You can use Ctrl+Q to quickly search commands in Vivado 2025.1.",
        buttons=buttons,
    )
    assert tier == ModalRiskTier.BENIGN_DISMISSIBLE
    assert safe_btn is not None
    assert safe_btn.label == "Close"
    assert safe_btn.inferred_consequence == "Safe dismissal"
    assert escalation is None


def test_semantic_router_informative_ack():
    """Verifies that completion notices (e.g. Synthesis Completed) are Tier 1 with auto-acknowledgment."""
    router = SemanticModalRouter()
    buttons = [
        ModalButtonOption(
            button_id="btn_ok",
            label="OK",
            bbox=BoundingBox(x=500, y=500, width=90, height=35),
        )
    ]
    tier, safe_btn, escalation = router.classify_and_route(
        window_title="Synthesis Completed Successfully",
        body_text="Synthesis completed with 0 errors, 1 critical warning, and 12 warnings.",
        buttons=buttons,
    )
    assert tier == ModalRiskTier.INFORMATIVE_ACK
    assert safe_btn is not None
    assert safe_btn.label == "OK"
    assert safe_btn.inferred_consequence == "Safe acknowledgment"
    assert escalation is None


def test_semantic_router_workflow_branching():
    """Verifies that multi-choice workflow prompts are Tier 2 and halt with an escalation payload."""
    router = SemanticModalRouter()
    buttons = [
        ModalButtonOption(
            button_id="btn_0",
            label="Save & Re-run",
            bbox=BoundingBox(x=400, y=300, width=120, height=35),
        ),
        ModalButtonOption(
            button_id="btn_1",
            label="Cancel",
            bbox=BoundingBox(x=540, y=300, width=80, height=35),
        ),
    ]
    tier, safe_btn, escalation = router.classify_and_route(
        window_title="Synthesis Out-of-Date",
        body_text="Source files modified. Would you like to re-run synthesis before running implementation?",
        buttons=buttons,
    )
    assert tier == ModalRiskTier.WORKFLOW_BRANCHING
    assert safe_btn is None
    assert escalation is not None
    assert escalation.risk_tier == ModalRiskTier.WORKFLOW_BRANCHING
    assert len(escalation.detected_buttons) == 2
    assert escalation.detected_buttons[0].inferred_consequence == "Mutate workflow path"
    assert escalation.detected_buttons[1].inferred_consequence == "Preserve current state"


def test_semantic_router_destructive_confirmation():
    """Verifies that file overwrite and license failure dialogs are Tier 3 and strictly halt execution."""
    router = SemanticModalRouter()
    buttons = [
        ModalButtonOption(
            button_id="btn_overwrite",
            label="Overwrite",
            bbox=BoundingBox(x=300, y=200, width=100, height=35),
        ),
        ModalButtonOption(
            button_id="btn_cancel",
            label="Cancel",
            bbox=BoundingBox(x=420, y=200, width=90, height=35),
        ),
    ]
    tier, safe_btn, escalation = router.classify_and_route(
        window_title="Confirm Overwrite",
        body_text="File 'constraints.xdc' already exists. Do you want to overwrite it?",
        buttons=buttons,
    )
    assert tier == ModalRiskTier.DESTRUCTIVE_OR_SYSTEM
    assert safe_btn is None
    assert escalation is not None
    assert escalation.risk_tier == ModalRiskTier.DESTRUCTIVE_OR_SYSTEM
    assert escalation.detected_buttons[0].inferred_consequence == "Destructive state mutation"
    assert escalation.detected_buttons[1].inferred_consequence == "Safe abort"


def test_semantic_router_license_failure():
    """Verifies that FlexLM license expiration dialogs trigger Tier 3 alerts."""
    router = SemanticModalRouter()
    buttons = [
        ModalButtonOption(
            button_id="btn_retry",
            label="Retry",
            bbox=BoundingBox(x=200, y=200, width=90, height=35),
        ),
        ModalButtonOption(
            button_id="btn_exit",
            label="Exit Vivado",
            bbox=BoundingBox(x=310, y=200, width=110, height=35),
        ),
    ]
    tier, safe_btn, escalation = router.classify_and_route(
        window_title="FlexLM License Server Error",
        body_text="License checkout failed. FlexLM server connection lost or license expired.",
        buttons=buttons,
    )
    assert tier == ModalRiskTier.DESTRUCTIVE_OR_SYSTEM
    assert safe_btn is None
    assert escalation is not None


def test_interceptor_benign_auto_dismissal():
    """Verifies ObstructionInterceptor auto-dismissing benign dialogs."""
    mock_hal = MockHAL()
    clicked_coords = []
    mock_hal.mouse_click = lambda x, y, button="left", click_count=1: clicked_coords.append((x, y))

    interceptor = ObstructionInterceptor(mock_hal)
    # Mock active dialog inspection
    interceptor.inspect_active_dialog = lambda: (
        "Tip of the Day",
        "Tip text",
        [
            ModalButtonOption(
                button_id="btn_close",
                label="Close",
                bbox=BoundingBox(x=200, y=300, width=100, height=40),
            )
        ],
    )

    result = interceptor.triage_and_resolve()
    assert result.detected is True
    assert result.auto_dismissed is True
    assert result.risk_tier == ModalRiskTier.BENIGN_DISMISSIBLE
    assert len(clicked_coords) == 1
    assert clicked_coords[0] == (250, 320)  # Center of (200, 300, 100, 40)


def test_interceptor_informative_auto_acknowledgment():
    """Verifies ObstructionInterceptor extracting text and auto-acknowledging informative notices."""
    mock_hal = MockHAL()
    clicked_coords = []
    mock_hal.mouse_click = lambda x, y, button="left", click_count=1: clicked_coords.append((x, y))

    interceptor = ObstructionInterceptor(mock_hal)
    interceptor.inspect_active_dialog = lambda: (
        "Synthesis Completed Successfully",
        "Design meets timing with 0 errors.",
        [
            ModalButtonOption(
                button_id="btn_ok", label="OK", bbox=BoundingBox(x=400, y=400, width=80, height=30)
            )
        ],
    )

    result = interceptor.triage_and_resolve()
    assert result.detected is True
    assert result.auto_dismissed is True
    assert result.risk_tier == ModalRiskTier.INFORMATIVE_ACK
    assert result.extracted_summary == "Design meets timing with 0 errors."
    assert len(clicked_coords) == 1
    assert clicked_coords[0] == (440, 415)


def test_interceptor_destructive_blocks_and_resolves():
    """Verifies that destructive dialogs halt without clicking and can be resolved via resolve_pending_modal."""
    mock_hal = MockHAL()
    clicked_coords = []
    mock_hal.mouse_click = lambda x, y, button="left", click_count=1: clicked_coords.append((x, y))

    interceptor = ObstructionInterceptor(mock_hal)
    interceptor.inspect_active_dialog = lambda: (
        "Confirm Delete Netlist",
        "Are you sure you want to delete synthesized netlist?",
        [
            ModalButtonOption(
                button_id="btn_del",
                label="Delete",
                bbox=BoundingBox(x=100, y=100, width=80, height=30),
            ),
            ModalButtonOption(
                button_id="btn_cancel",
                label="Cancel",
                bbox=BoundingBox(x=200, y=100, width=80, height=30),
            ),
        ],
    )

    # 1. Triage must NOT auto-click
    result = interceptor.triage_and_resolve()
    assert result.detected is True
    assert result.auto_dismissed is False
    assert result.risk_tier == ModalRiskTier.DESTRUCTIVE_OR_SYSTEM
    assert result.escalation is not None
    assert len(clicked_coords) == 0  # CRITICAL: No click dispatched!

    # 2. Resolve via orchestrator choice (e.g. choose "Cancel")
    res = interceptor.resolve_pending_modal(button_label="Cancel")
    assert res["success"] is True
    assert res["clicked_button"] == "Cancel"
    assert len(clicked_coords) == 1
    assert clicked_coords[0] == (240, 115)
    assert interceptor.pending_escalation is None


def test_server_tools_dismiss_and_resolve():
    """Verifies MCP server tools dismiss_obstructions and resolve_modal_dialog."""
    res_dismiss = dismiss_obstructions()
    assert isinstance(res_dismiss, dict)
    assert "detected" in res_dismiss

    res_resolve = resolve_modal_dialog(button_label="OK")
    assert isinstance(res_resolve, dict)
    # Since no modal was pending, should return error message gracefully
    assert res_resolve["success"] is False
    assert "No escalated modal" in res_resolve["error"]
