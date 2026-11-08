"""Tests for Round 5 Architecture enhancements.

Validates:
1. HAL Protocol segregation (IScreenCapture, IInputDriver, IWindowManager, IDesktopHALProtocol).
2. Deterministic Headless CI replay harness (MockReplayHAL & ScreenFrameState).
3. ToolSetManager dynamic capability gating & out-of-band state offloading.
4. Server tool manage_toolsets.
5. BicameralStepController (Tier 0 deterministic, Tier 1 local SLM, Tier 2 cloud planner escalation).
6. 8-Part Invariant COMPACT_PROMPT history compaction.
"""

import json
from pathlib import Path

import pinlet.server as server
from pinlet.agent_loop.bicameral import (
    BicameralStepController,
    VerificationTier,
)
from pinlet.hal.factory import get_desktop_hal
from pinlet.hal.mock_replay import MockReplayHAL, ScreenFrameState
from pinlet.hal.protocols import (
    IDesktopHALProtocol,
    IInputDriver,
    IScreenCapture,
    IWindowManager,
)
from pinlet.toolset import OutputMode, ToolDefinition, ToolSetManager
from pinlet.types import BoundingBox, WindowInfo


def test_hal_protocols_runtime_checkable():
    """Verify runtime HAL instance satisfies zero-dependency protocols."""
    hal = get_desktop_hal()
    assert isinstance(hal, IScreenCapture)
    assert isinstance(hal, IInputDriver)
    assert isinstance(hal, IWindowManager)
    assert isinstance(hal, IDesktopHALProtocol)


def test_mock_replay_hal_cassette_and_stepping():
    """Verify MockReplayHAL cassette replay, frame stepping, and action tracking."""
    frames = [
        ScreenFrameState(
            frame_id="frame_0",
            screen_size=(1920, 1080),
            cursor_pos=(100, 100),
            ocr_boxes=[{"text": "Login", "bbox": [100, 100, 200, 140]}],
            windows=[
                WindowInfo(
                    window_id="0x101",
                    title="Login Window",
                    bbox=BoundingBox(x=0, y=0, width=600, height=400),
                    is_active=True,
                )
            ],
            active_window=WindowInfo(
                window_id="0x101",
                title="Login Window",
                bbox=BoundingBox(x=0, y=0, width=600, height=400),
                is_active=True,
            ),
        ),
        ScreenFrameState(
            frame_id="frame_1",
            screen_size=(1920, 1080),
            cursor_pos=(200, 200),
            ocr_boxes=[
                {"text": "Dashboard", "bbox": [50, 50, 300, 100]},
                {"text": "Welcome User", "bbox": [50, 120, 250, 150]},
            ],
            ax_diff={"changed": True, "added": ["Welcome User"]},
            windows=[
                WindowInfo(
                    window_id="0x102",
                    title="Main Dashboard",
                    bbox=BoundingBox(x=0, y=0, width=1920, height=1080),
                    is_active=True,
                )
            ],
            active_window=WindowInfo(
                window_id="0x102",
                title="Main Dashboard",
                bbox=BoundingBox(x=0, y=0, width=1920, height=1080),
                is_active=True,
            ),
        ),
    ]

    mock_hal = MockReplayHAL(states=frames)
    assert isinstance(mock_hal, IDesktopHALProtocol)
    assert mock_hal.get_screen_size() == (1920, 1080)
    assert mock_hal.get_cursor_position() == (100, 100)

    # Frame 0 OCR text check
    res_login = mock_hal.read_screen_text(contains="Login")
    assert res_login["count"] == 1

    # Mouse click triggers automatic step advancement
    mock_hal.mouse_click(150, 120)
    assert len(mock_hal.actions_dispatched) == 1
    assert mock_hal.cursor == 1
    assert mock_hal.current_state.frame_id == "frame_1"

    # Frame 1 OCR text check
    res_dash = mock_hal.read_screen_text(contains="Dashboard")
    assert res_dash["count"] == 1
    diff = mock_hal.get_accessibility_tree(diff_only=True)
    assert diff["changed"] is True


def test_toolset_manager_profiles_and_capability_gating(tmp_path):
    """Verify toolset manager profiles, domain gating, and schema filtering."""
    mgr = ToolSetManager(
        initial_profile="minimal_computer_use",
        output_mode=OutputMode.INLINE,
        state_dir=tmp_path,
    )

    mgr.register_tool(ToolDefinition(name="click", domain="core", description="Click mouse"))
    mgr.register_tool(
        ToolDefinition(name="focus_window", domain="window_mgmt", description="Focus window")
    )
    mgr.register_tool(
        ToolDefinition(name="synthesize", domain="eda_vivado", description="Run synthesis")
    )

    # Initial profile minimal_computer_use has only 'core'
    schemas = mgr.get_active_mcp_schemas()
    assert len(schemas) == 1
    assert schemas[0]["name"] == "click"
    assert mgr.is_tool_active("click") is True
    assert mgr.is_tool_active("synthesize") is False

    # Escalate capabilities: enable 'eda_vivado'
    res = mgr.manage_toolsets(action="enable", domains=["eda_vivado"])
    assert res["success"] is True
    assert "eda_vivado" in res["active_domains"]
    assert mgr.is_tool_active("synthesize") is True

    schemas = mgr.get_active_mcp_schemas()
    names = {s["name"] for s in schemas}
    assert "synthesize" in names
    assert "click" in names
    assert "focus_window" not in names


def test_toolset_manager_out_of_band_file_offloading(tmp_path):
    """Verify heavy payloads are offloaded out-of-band to disk/tmpfs returning URI and preview."""
    mgr = ToolSetManager(
        initial_profile="full_developer",
        output_mode=OutputMode.FILE,
        max_inline_tokens=100,  # low threshold to trigger offloading
        state_dir=tmp_path,
    )

    # Small payload stays inline
    small_payload = {"status": "ok", "count": 2}
    res_small = mgr.format_output("small_tool", small_payload)
    assert res_small == small_payload

    # Large payload gets offloaded to disk
    large_payload = {
        "status": "success",
        "active_window": "Vivado 2025.1",
        "nodes": [{"id": i, "name": f"Wire_Net_{i}", "role": "AXNode"} for i in range(150)],
        "count": 150,
    }
    res_large = mgr.format_output("get_accessibility_tree", large_payload)

    assert res_large.get("offloaded") is True
    assert res_large.get("output_mode") == "file"
    assert "resource_uri" in res_large
    assert res_large["resource_uri"].startswith("mcp://pinlet/state/")
    assert "file_path" in res_large
    assert Path(res_large["file_path"]).exists()
    assert res_large["estimated_tokens_saved"] > 0
    assert "preview" in res_large
    assert res_large["preview"]["active_window"] == "Vivado 2025.1"
    assert res_large["preview"]["nodes_count"] == 150

    # Read back offloaded file
    saved_content = json.loads(Path(res_large["file_path"]).read_text())
    assert saved_content["count"] == 150


def test_server_manage_toolsets_tool():
    """Verify the manage_toolsets server tool works end-to-end."""
    # List domains
    res = server.manage_toolsets(action="list_domains")
    assert "active_domains" in res
    assert "available_domains" in res
    assert "core" in res["active_domains"]

    # Toggle output mode
    res_mode = server.manage_toolsets(action="set_output_mode", output_mode="file")
    assert res_mode["success"] is True
    assert res_mode["output_mode"] == "file"

    # Reset output mode to inline
    res_reset = server.manage_toolsets(action="set_output_mode", output_mode="inline")
    assert res_reset["output_mode"] == "inline"


def test_bicameral_step_controller_tier0_verification():
    """Verify Tier 0 deterministic verification (OCR assertion) returns zero tokens."""
    frame = ScreenFrameState(
        frame_id="t0",
        ocr_boxes=[{"text": "Build Succeeded", "bbox": [10, 10, 200, 40]}],
    )
    mock_hal = MockReplayHAL(states=[frame])
    controller = BicameralStepController(hal=mock_hal)

    verdict = controller.execute_step(
        action_name="press_key",
        action_args={"key": "enter"},
        expected_outcome={"assert_screen_text": "Build Succeeded"},
    )

    assert verdict.success is True
    assert verdict.tier_used == VerificationTier.TIER0_DETERMINISTIC
    assert verdict.tokens_consumed == 0
    assert verdict.confidence == 1.0
    assert "Tier 0 match" in verdict.reason


def test_bicameral_step_controller_tier1_local_slm():
    """Verify Tier 1 local SLM (Gemma 3) verification returns zero cloud tokens."""
    frame = ScreenFrameState(frame_id="t1", ocr_boxes=[])
    mock_hal = MockReplayHAL(states=[frame])

    class MockGemmaClient:
        def evaluate_step(self, action, args, expected, ax_diff):
            return {
                "verified": True,
                "confidence": 0.88,
                "reason": "Button state transitioned to disabled and spinner settled.",
            }

    controller = BicameralStepController(
        hal=mock_hal,
        local_gemma_client=MockGemmaClient(),
    )

    verdict = controller.execute_step(
        action_name="click",
        action_args={"x": 50, "y": 50},
        expected_outcome={"expect_settled": True},
    )

    assert verdict.success is True
    assert verdict.tier_used == VerificationTier.TIER1_LOCAL_SLM
    assert verdict.tokens_consumed == 0
    assert verdict.confidence == 0.88
    assert "Tier 1 SLM verified" in verdict.reason


def test_bicameral_step_controller_tier2_cloud_escalation():
    """Verify low local confidence triggers Tier 2 cloud escalation."""
    frame = ScreenFrameState(frame_id="t2", ocr_boxes=[])
    mock_hal = MockReplayHAL(states=[frame])

    class MockLowConfGemmaClient:
        def evaluate_step(self, action, args, expected, ax_diff):
            return {
                "verified": False,
                "confidence": 0.45,
                "reason": "Unexpected syntax error modal appeared on screen.",
            }

    class MockCloudPlanner:
        def diagnose_and_replan(self, action, args, expected, reason):
            return {
                "success": True,
                "confidence": 0.95,
                "reason": "Cloud planner diagnosed modal and identified corrective dismiss click.",
                "tokens_billed": 1420,
            }

    controller = BicameralStepController(
        hal=mock_hal,
        local_gemma_client=MockLowConfGemmaClient(),
        cloud_vlm_client=MockCloudPlanner(),
    )

    verdict = controller.execute_step(
        action_name="click",
        action_args={"x": 100, "y": 200},
        expected_outcome={"expect_settled": True},
    )

    assert verdict.success is True
    assert verdict.tier_used == VerificationTier.TIER2_CLOUD_VLM
    assert verdict.escalated_to_cloud is True
    assert verdict.tokens_consumed == 1420
    assert "Cloud planner diagnosed modal" in verdict.reason


def test_compact_trajectory_history_8_part_invariant():
    """Verify trajectory history compaction adheres strictly to the 8-part invariant schema."""
    # Generate mock 20-turn conversation history
    messages = [
        {"role": "system", "content": "You are a desktop control agent."},
    ]
    for i in range(1, 20):
        messages.append({"role": "user", "content": f"Turn {i}: Action executed: click button {i}"})
        messages.append(
            {"role": "assistant", "content": f"Turn {i}: Clicked button {i} successfully."}
        )

    # Total messages: 1 system + 38 turns = 39 messages
    compacted = BicameralStepController.compact_trajectory_history(
        messages=messages,
        max_tokens=50,  # Force compaction
        keep_last_n=2,
    )

    # Expect: 1 system message + 1 compacted state message + 2 recent turns = 4 messages
    assert len(compacted) == 4
    assert compacted[0]["role"] == "system"
    assert "[STATE COMPACTOR OUTPUT]" in compacted[1]["content"]

    body = compacted[1]["content"]
    assert "1. [USER_INSTRUCTIONS]:" in body
    assert "2. [TASK_TEMPLATE]:" in body
    assert "3. [CONSTRAINTS]:" in body
    assert "4. [ACTIONS_TAKEN]:" in body
    assert "5. [ERRORS_AND_FIXES]:" in body
    assert "6. [PROGRESS_METRICS]:" in body
    assert "7. [CURRENT_SCREEN_STATE]:" in body
    assert "8. [NEXT_IMMEDIATE_STEP]:" in body

    # Most recent raw turns are preserved verbatim
    assert compacted[-2] == messages[-2]
    assert compacted[-1] == messages[-1]


def test_cursor_state_checkpoint_schema_validation():
    """Verify StateCheckpoint Pydantic model validates structured fields."""
    from pinlet.agent_loop.bicameral import EscalationPredicate, StateCheckpoint

    checkpoint = StateCheckpoint(
        user_instructions="Synthesize Vivado top-level module",
        task_template=["[x] Open Project", "[/] Run Synthesis", "[ ] Check Timing"],
        constraints=["Do not close project", "Timeout 300s"],
        actions_taken=["T1: Launched project", "T2: Clicked Run Synthesis"],
        errors_and_fixes=["None"],
        progress_metrics="1/3 steps completed",
        current_screen_state="Window: Vivado 2025.1, Active Pane: Design Runs",
        next_immediate_step="Wait for synthesis run1 to finish",
        compressor="gemma3",
        covers_turns=(1, 4),
    )

    assert checkpoint.user_instructions == "Synthesize Vivado top-level module"
    assert len(checkpoint.task_template) == 3
    assert checkpoint.covers_turns == (1, 4)
    assert EscalationPredicate.P1_LOW_CONFIDENCE.value == "p1_confidence_below_threshold"
    assert EscalationPredicate.P2_UNEXPECTED_MODAL.value == "p2_unexpected_or_destructive_modal"


def test_hal_factory_desktop_hal_replay_override(monkeypatch):
    """Verify DESKTOP_HAL=replay environment override instantiates MockReplayHAL."""
    from pinlet.hal import factory
    from pinlet.hal.mock_replay import MockReplayHAL

    monkeypatch.setenv("DESKTOP_HAL", "replay")
    factory._HAL_INSTANCE = None  # Reset singleton

    hal = factory.get_desktop_hal()
    assert isinstance(getattr(hal, "backend", hal), MockReplayHAL)
    factory._HAL_INSTANCE = None  # Clean up
