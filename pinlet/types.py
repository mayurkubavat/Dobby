"""Data types and schemas for Vision Control MCP."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ExecutionPhase(str, Enum):
    """Lifecycle phases of the adaptive event-driven quiescence loop."""

    DISPATCHED = "DISPATCHED"
    DAMAGE_ACCUMULATION = "DAMAGE_ACCUMULATION"
    SETTLING = "SETTLING"
    QUIESCENT = "QUIESCENT"
    ASYNC_PROMOTED = "ASYNC_PROMOTED"
    CONTRACT_VERIFIED = "CONTRACT_VERIFIED"
    OBSTRUCTED_BY_MODAL = "OBSTRUCTED_BY_MODAL"
    CONTRACT_FAILED = "CONTRACT_FAILED"
    SLEEP_FALLBACK = "SLEEP_FALLBACK"


class StateAssertionType(str, Enum):
    """Semantic post-condition assertion types."""

    AT_SPI_STATE_PRESENT = "at_spi_state_present"
    AT_SPI_STATE_ABSENT = "at_spi_state_absent"
    WINDOW_SPAWNED = "window_spawned"
    WINDOW_CLOSED = "window_closed"
    TEXT_APPEARED = "text_appeared"
    SUBPANE_SSIM_DROP = "subpane_ssim_drop"
    CURSOR_CHANGED = "cursor_changed"
    VISUAL_QUERY = "visual_query"


class PostConditionAssertion(BaseModel):
    """Single semantic post-condition assertion evaluated upon UI quiescence."""

    assertion_type: StateAssertionType = Field(description="Target assertion mechanism")
    target_role: str | None = Field(
        default=None, description="UI role to inspect (push_button, dialog, etc.)"
    )
    target_name_regex: str | None = Field(
        default=None, description="Regex pattern matching element name or window title"
    )
    target_subpane_id: str | None = Field(
        default=None, description="Optional sub-pane identifier for localized verification"
    )
    expected_state: str | None = Field(
        default=None, description="Expected accessible state (e.g., FOCUSED, CHECKED)"
    )
    min_ssim_delta: float = Field(
        default=0.08, ge=0.0, le=1.0, description="Minimum structural change required"
    )
    cursor_expected: str | None = Field(
        default=None, description="Expected cursor shape name (e.g., arrow, text)"
    )
    visual_question: str | None = Field(
        default=None,
        description="VISUAL_QUERY: yes/no question answered by local Gemma on a crop (e.g. 'Is the Save dialog closed?')",
    )
    visual_expected_text: str | None = Field(
        default=None,
        description="VISUAL_QUERY: text that proves the condition. Checked with local OCR first (ms); Gemma "
        "is only asked if the text is absent and visual_question is set.",
    )
    visual_region: list[int] | None = Field(
        default=None,
        description="VISUAL_QUERY: [x, y, width, height] crop; defaults to the active window",
    )


class PostConditionContract(BaseModel):
    """Contract declaring expected post-conditions following a GUI action."""

    assertions: list[PostConditionAssertion] = Field(
        default_factory=list, description="List of expected post-conditions"
    )
    require_all: bool = Field(
        default=True, description="True for AND evaluation across assertions; False for OR"
    )
    initial_reaction_timeout_ms: int = Field(
        default=1500, description="Max milliseconds to wait for initial reaction/damage"
    )
    quiescence_window_ms: int = Field(
        default=180, description="Required milliseconds of continuous quiet state"
    )
    max_total_timeout_ms: int = Field(
        default=15000, description="Maximum ceiling before escalating or timing out"
    )
    allow_async_promotion: bool = Field(
        default=True, description="Whether to promote long-running actions to async jobs"
    )


class ModalRiskTier(str, Enum):
    """Risk tiers for modal dialog classification and triage."""

    BENIGN_DISMISSIBLE = "BENIGN_DISMISSIBLE"
    INFORMATIVE_ACK = "INFORMATIVE_ACK"
    WORKFLOW_BRANCHING = "WORKFLOW_BRANCHING"
    DESTRUCTIVE_OR_SYSTEM = "DESTRUCTIVE_OR_SYSTEM"


class ModalButtonOption(BaseModel):
    """Button option discovered inside a modal dialog."""

    button_id: str = Field(description="Unique button identifier (e.g. 'btn_0', 'btn_ok')")
    label: str = Field(description="Button display label text")
    bbox: BoundingBox = Field(description="Physical pixel bounding box")
    is_default_focused: bool = Field(
        default=False, description="Whether button is the default focused option"
    )
    inferred_consequence: str = Field(
        default="State mutation",
        description="Heuristic inference: 'Safe dismissal', 'Workflow branching decision', 'Destructive mutation'",
    )


class ModalEscalationPayload(BaseModel):
    """Structured payload returned to the orchestrator when an ambiguous or dangerous modal is encountered."""

    status: str = Field(
        default="ACTION_BLOCKED_BY_MODAL", description="Escalation status indicator"
    )
    window_title: str = Field(description="Title bar of the blocking modal window")
    dialog_body_text: str = Field(description="Text extracted from the dialog body")
    risk_tier: ModalRiskTier = Field(description="Assessed risk classification tier")
    detected_buttons: list[ModalButtonOption] = Field(
        default_factory=list, description="Available action buttons"
    )
    recommended_action_tool: str = Field(
        default="resolve_modal_dialog",
        description="Tool name to invoke with chosen button_id",
    )


class ModalTriageResult(BaseModel):
    """Outcome of an autonomous modal inspection and triage cycle."""

    detected: bool = Field(default=False, description="Whether a modal or popup was detected")
    window_title: str = Field(default="", description="Title of the detected window")
    body_text: str = Field(default="", description="Body text content of the dialog")
    risk_tier: ModalRiskTier | None = Field(default=None, description="Assessed risk tier")
    classified_by: str | None = Field(
        default=None, description="'rules' or 'gemma' (local model tie-breaker)"
    )
    auto_dismissed: bool = Field(
        default=False, description="Whether the modal was safely auto-dismissed"
    )
    action_taken: str = Field(
        default="", description="Description of the action taken (or escalation reason)"
    )
    extracted_summary: str | None = Field(
        default=None, description="Information extracted from informative dialogs"
    )
    escalation: ModalEscalationPayload | None = Field(
        default=None, description="Escalation details if blocked"
    )


class CorrelatedStep(BaseModel):
    """Single GUI action correlated with underlying journal stream commands."""

    action_id: str = Field(description="Unique action execution identifier")
    gui_intent: str = Field(
        description="High-level description of what the GUI action was intended to perform"
    )
    ui_target_text: str = Field(
        default="", description="Target text label or accessible name clicked/addressed"
    )
    timestamp_start: float = Field(default=0.0, description="Action start time (monotonic)")
    timestamp_end: float = Field(
        default=0.0, description="Action completion & UI quiescence time (monotonic)"
    )
    journal_lines: list[str] = Field(
        default_factory=list, description="Echoed journal/CLI command lines captured"
    )


class CrystallizedSkillSpec(BaseModel):
    """Specification of a reusable parameterized skill compiled from GUI exploration."""

    skill_name: str = Field(
        description="Unique identifier for the skill (e.g. 'vivado_create_custom_clock')"
    )
    description: str = Field(
        description="Human-readable description of what this parameterized skill does"
    )
    app_target: str = Field(default="vivado_2025.1", description="Target application identifier")
    tcl_template: str = Field(description="Parameterized Jinja2 script template")
    parameters: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="JSON Schema property definitions for parameterized arguments",
    )
    verification_cmd: str = Field(
        default="",
        description="Command to execute for verifying post-execution status",
    )
    source_steps_count: int = Field(
        default=0, description="Number of GUI steps crystallized into this skill"
    )


class SkillExecutionResult(BaseModel):
    """Outcome of executing a crystallized skill via direct API / Tcl RPC."""

    success: bool = Field(description="Whether the skill script executed without error")
    skill_name: str = Field(description="Name of the skill executed")
    rendered_script: str = Field(
        description="Exact rendered script dispatched to target application"
    )
    output: str = Field(default="", description="Execution response / log output")
    verification: str = Field(default="", description="Verification command outcome")
    elapsed_ms: float = Field(default=0.0, description="Execution duration in milliseconds")


class BoundingBox(BaseModel):
    """2D Bounding Box in physical desktop pixel coordinates."""

    x: int = Field(description="X coordinate of top-left corner")
    y: int = Field(description="Y coordinate of top-left corner")
    width: int = Field(description="Width in pixels")
    height: int = Field(description="Height in pixels")

    @property
    def center(self) -> tuple[int, int]:
        """Returns the center (x, y) coordinates."""
        return (self.x + self.width // 2, self.y + self.height // 2)

    @property
    def right(self) -> int:
        """Returns the right edge x coordinate."""
        return self.x + self.width

    @property
    def bottom(self) -> int:
        """Returns the bottom edge y coordinate."""
        return self.y + self.height

    @property
    def area(self) -> int:
        """Returns pixel area."""
        return max(0, self.width) * max(0, self.height)

    def iou(self, other: BoundingBox) -> float:
        """Computes Intersection-over-Union with another bounding box."""
        ix1, iy1 = max(self.x, other.x), max(self.y, other.y)
        ix2, iy2 = min(self.right, other.right), min(self.bottom, other.bottom)
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0

    def containment_ratio(self, child: BoundingBox) -> float:
        """Fraction of child bounding box area contained within self."""
        if child.area == 0:
            return 0.0
        ix1, iy1 = max(self.x, child.x), max(self.y, child.y)
        ix2, iy2 = min(self.right, child.right), min(self.bottom, child.bottom)
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        return inter / child.area


class UIElement(BaseModel):
    """Semantic or visual UI element."""

    id: int = Field(description="Numeric Set-of-Marks identifier or index")
    role: str = Field(
        default="unknown", description="Semantic role (button, text_entry, menu_item, etc.)"
    )
    name: str = Field(default="", description="Text label, accessible name, or description")
    bbox: BoundingBox = Field(description="Physical screen location and dimensions")
    interactable: bool = Field(
        default=True, description="Whether element responds to clicks or keyboard input"
    )
    states: list[str] = Field(
        default_factory=list, description="Active states (e.g., focused, selected, enabled)"
    )


class WindowInfo(BaseModel):
    """Desktop window metadata."""

    window_id: str = Field(description="Hexadecimal or integer window identifier")
    title: str = Field(description="Window title bar text")
    bbox: BoundingBox = Field(description="Window position and dimensions")
    is_active: bool = Field(
        default=False, description="Whether this window currently has keyboard/mouse focus"
    )
    app_name: str | None = Field(default=None, description="Application binary name or WM_CLASS")


class ScreenshotResult(BaseModel):
    """Output of a screen capture with optional Set-of-Marks tags."""

    image_base64: str = Field(description="Base64 encoded JPEG or WebP image string")
    mime_type: str = Field(default="image/jpeg", description="MIME format of the image")
    width: int = Field(description="Captured image width (post-scaling)")
    height: int = Field(description="Captured image height (post-scaling)")
    original_width: int = Field(description="Physical desktop screen width")
    original_height: int = Field(description="Physical desktop screen height")
    scale_factor: float = Field(
        default=1.0, description="Scale ratio applied (e.g. 0.5 means scaled to 50%)"
    )
    zoom_factor: float = Field(
        default=1.0, description="Zoom magnification factor applied to capture"
    )
    origin_x: int = Field(
        default=0, description="Screen x of the image's top-left corner (click coordinates)"
    )
    origin_y: int = Field(
        default=0, description="Screen y of the image's top-left corner (click coordinates)"
    )
    area_width: int = Field(
        default=0, description="Width of the captured area in screen (click) units"
    )
    area_height: int = Field(
        default=0, description="Height of the captured area in screen (click) units"
    )
    marks: list[UIElement] = Field(
        default_factory=list, description="List of interactable elements tagged with marks"
    )
    cursor_position: tuple[int, int] | None = Field(
        default=None, description="Pointer (x, y) at time of capture"
    )


class ActionVerificationResult(BaseModel):
    """Outcome of an action execution with observation verification."""

    success: bool = Field(description="Whether the action was physically injected successfully")
    action_type: str = Field(description="Name of the action executed")
    delta_detected: bool = Field(
        default=False, description="Whether a visual screen change was observed post-action"
    )
    change_ratio: float = Field(
        default=0.0, description="Fraction of pixels altered in region of interest"
    )
    message: str = Field(default="", description="Status or error message")
    execution_phase: ExecutionPhase = Field(
        default=ExecutionPhase.CONTRACT_VERIFIED,
        description="Quiescence lifecycle phase reached (QUIESCENT, CONTRACT_VERIFIED, CONTRACT_FAILED, etc.)",
    )
    quiescence_elapsed_ms: float = Field(
        default=0.0,
        description="Time in milliseconds elapsed from action injection to UI quiescence",
    )
    post_conditions_passed: bool | None = Field(
        default=None,
        description="Whether explicit post-condition contract assertions succeeded (None if no contract provided)",
    )
    cursor_state: str | None = Field(
        default=None,
        description="Observed mouse cursor shape name post-action (e.g. 'arrow', 'text', 'watch')",
    )
    dirty_pixel_area: int = Field(
        default=0,
        description="Total cumulative dirty pixel area observed during damage accumulation",
    )


class FindElementResult(BaseModel):
    """Result of an autonomous element search by text, role, or label."""

    found: bool = Field(description="Whether a matching element was discovered")
    query: str = Field(description="Search query string or pattern")
    element: UIElement | None = Field(default=None, description="Best matching UI element if found")
    center_coords: tuple[int, int] | None = Field(
        default=None, description="Exact physical (x, y) coordinates for clicking"
    )
    confidence: float = Field(default=1.0, description="Match confidence score (0.0 to 1.0)")
    candidates_count: int = Field(default=0, description="Total matching candidates discovered")


class DiagnosticsResult(BaseModel):
    """Diagnostic health check of the Vision MCP server and perception backends."""

    platform: str = Field(description="Host OS platform (linux, win32, darwin)")
    display_server: str = Field(description="Display environment (X11, Wayland, Windows GDI/DXGI)")
    primary_screen_size: tuple[int, int] = Field(description="Primary display resolution (w, h)")
    active_grounding_strategy: str = Field(description="Current grounding engine mode")
    atspi_available: bool = Field(description="Whether Linux AT-SPI2 accessibility bus is active")
    opencv_available: bool = Field(description="Whether OpenCV contour detection is active")
    supported_grounding_providers: list[str] = Field(
        description="List of registered grounding providers"
    )
    active_window: str | None = Field(default=None, description="Currently focused window title")
    config_summary: dict[str, Any] = Field(description="Active configuration parameters")
    gemma: dict[str, Any] | None = Field(
        default=None,
        description="Local Gemma sidecar status: models, availability, call counts, latency, cache hits",
    )
    ocr: dict[str, Any] | None = Field(
        default=None, description="Local OCR engine status and call stats"
    )


class SubPaneROI(BaseModel):
    """Defines a functional sub-pane within an application window."""

    name: str = Field(
        description="Identifier for the pane (e.g. address_bar, tab_strip, waveform_canvas)"
    )
    description: str = Field(default="", description="Semantic role or description of the sub-pane")
    relative_x: float = Field(
        default=0.0, description="Horizontal start offset as fraction of window width [0.0..1.0]"
    )
    relative_y: float = Field(
        default=0.0, description="Vertical start offset as fraction of window height [0.0..1.0]"
    )
    relative_width: float = Field(
        default=1.0, description="Pane width as fraction of window width [0.0..1.0]"
    )
    relative_height: float = Field(
        default=1.0, description="Pane height as fraction of window height [0.0..1.0]"
    )
    offset_x: int = Field(default=0, description="Fixed pixel offset added to X")
    offset_y: int = Field(default=0, description="Fixed pixel offset added to Y")
    fixed_width: int | None = Field(
        default=None, description="Optional fixed pixel width overriding relative_width"
    )
    fixed_height: int | None = Field(
        default=None, description="Optional fixed pixel height overriding relative_height"
    )

    def compute_bounds(self, window_bbox: BoundingBox) -> BoundingBox:
        """Computes absolute physical screen bounding box relative to host window."""
        x = window_bbox.x + int(window_bbox.width * self.relative_x) + self.offset_x
        y = window_bbox.y + int(window_bbox.height * self.relative_y) + self.offset_y

        if self.fixed_width is not None:
            w = min(self.fixed_width, max(1, window_bbox.width - (x - window_bbox.x)))
        else:
            w = int(window_bbox.width * self.relative_width) - self.offset_x

        if self.fixed_height is not None:
            h = min(self.fixed_height, max(1, window_bbox.height - (y - window_bbox.y)))
        else:
            h = int(window_bbox.height * self.relative_height) - self.offset_y

        return BoundingBox(x=x, y=y, width=max(1, w), height=max(1, h))


class FeatureDescriptor(BaseModel):
    """Declarative capability supported by an application plugin."""

    feature_id: str = Field(
        description="Unique feature identifier (e.g. 'browser.navigate', 'verdi.trace_driver')"
    )
    name: str = Field(description="Human-readable feature name")
    description: str = Field(description="Description of what this feature accomplishes")
    category: str = Field(
        default="general", description="Category: navigation, editing, view, devtools, macro"
    )
    hotkey: str | None = Field(
        default=None, description="Direct keyboard shortcut if applicable (e.g. 'ctrl+l')"
    )
    menu_path: list[str] | None = Field(
        default=None, description="Menu navigation path if applicable"
    )
    target_pane: str | None = Field(
        default=None, description="Name of sub-pane where feature executes"
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Expected parameter schemas"
    )


class PluginSummary(BaseModel):
    """Metadata summary of a registered UI application plugin."""

    app_id: str = Field(description="Unique application identifier")
    name: str = Field(description="Display name")
    description: str = Field(description="Plugin description")
    is_active: bool = Field(
        default=False,
        description="Whether this plugin matches the current active foreground window",
    )
    supported_panes: list[str] = Field(
        default_factory=list, description="Names of available sub-panes"
    )
    features_count: int = Field(default=0, description="Total number of registered features")


# =============================================================================
# Phase 4: Local Neural Grounding Sidecar & Unified Virtual DOM (vDOM)
# =============================================================================


class GroundingSource(str, Enum):
    """Source origin of a Virtual DOM node."""

    AT_SPI2 = "AT_SPI2"  # Pure OS accessibility node
    VISION_ONLY = "VISION_ONLY"  # Neural detector + OCR (OpenGL/Canvas/Custom Qt)
    HYBRID = "HYBRID"  # Fused AT-SPI2 + Neural visual verification


class UIRole(str, Enum):
    """Normalized semantic UI roles across accessibility and visual grounding."""

    WINDOW = "window"
    DIALOG = "dialog"
    PANEL = "panel"
    CANVAS = "canvas"
    TAB_ITEM = "tab_item"
    PUSH_BUTTON = "push_button"
    TOGGLE_BUTTON = "toggle_button"
    CHECK_BOX = "check_box"
    RADIO_BUTTON = "radio_button"
    TEXT_INPUT = "text_input"
    COMBO_BOX = "combo_box"
    MENU_ITEM = "menu_item"
    TREE_NODE = "tree_node"
    TABLE_CELL = "table_cell"
    SCHEMATIC_NET = "schematic_net"
    SCHEMATIC_PIN = "schematic_pin"
    WAVEFORM_SIGNAL = "waveform_signal"
    ICON_BUTTON = "icon_button"
    SASH_DIVIDER = "sash_divider"
    STATUS_BAR = "status_bar"
    STATIC_TEXT = "static_text"


class VDOMNode(BaseModel):
    """Unified Virtual DOM node fusing accessibility semantics and neural vision proposals."""

    node_id: str = Field(description="Unique spatial/semantic identifier (e.g. 'vdom_8f3a')")
    source: GroundingSource = Field(
        default=GroundingSource.AT_SPI2, description="Grounding source origin"
    )
    role: UIRole = Field(default=UIRole.STATIC_TEXT, description="Normalized semantic UI role")
    name: str = Field(default="", description="Name, label, or OCR text string")
    value: str | None = Field(default=None, description="Optional dynamic value or input content")
    states: list[str] = Field(
        default_factory=list,
        description="Active accessible states (ENABLED, VISIBLE, FOCUSED, etc.)",
    )
    bbox: BoundingBox = Field(description="Bounding box in physical screen pixel coordinates")
    click_point: tuple[int, int] = Field(
        default=(0, 0), description="Optimal verified click coordinate (x, y)"
    )
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Detection / grounding confidence"
    )
    parent_id: str | None = Field(default=None, description="Parent container node identifier")
    children_ids: list[str] = Field(default_factory=list, description="Child node identifiers")
    attributes: dict[str, str] = Field(
        default_factory=dict, description="Custom application attributes (e.g. qt_name, net_name)"
    )


class VirtualDOMSnapshot(BaseModel):
    """Immutable hierarchical snapshot of the desktop UI state."""

    snapshot_id: str = Field(description="Unique snapshot identifier (e.g. 'snap_1729000000')")
    timestamp: float = Field(description="Capture timestamp (monotonic)")
    window_id: str = Field(default="", description="Window identifier if localized")
    window_title: str = Field(default="", description="Target window title")
    window_bbox: BoundingBox = Field(description="Window bounding box")
    root_id: str = Field(default="", description="Root container node identifier")
    nodes: dict[str, VDOMNode] = Field(
        default_factory=dict, description="Lookup dictionary of all nodes by node_id"
    )


# =============================================================================
# Phase 5: Dynamic Anchor-Relative Sub-Pane Resolution
# =============================================================================


class AnchorEdge(str, Enum):
    """Edge direction for dynamic topological boundary anchoring."""

    TOP = "TOP"
    BOTTOM = "BOTTOM"
    LEFT = "LEFT"
    RIGHT = "RIGHT"


class BoundaryConstraint(BaseModel):
    """Topological anchor constraint targeting a persistent vDOM element or layout edge."""

    anchor_selector: str = Field(
        description="CSS/vDOM selector matching the anchor element (e.g. 'sash_divider', 'status_bar')"
    )
    edge: AnchorEdge = Field(description="Which edge of the anchor element serves as the boundary")
    offset: int = Field(
        default=0, description="Pixel offset from the anchor edge (positive moves down/right)"
    )


class SubPaneLayoutSpec(BaseModel):
    """Dynamic topological sub-pane specification resilient to window resize and sash movement."""

    pane_id: str = Field(
        description="Unique sub-pane identifier (e.g. 'waveform_canvas', 'tcl_console')"
    )
    top_constraint: BoundaryConstraint | None = Field(
        default=None, description="Top boundary constraint"
    )
    bottom_constraint: BoundaryConstraint | None = Field(
        default=None, description="Bottom boundary constraint"
    )
    left_constraint: BoundaryConstraint | None = Field(
        default=None, description="Left boundary constraint"
    )
    right_constraint: BoundaryConstraint | None = Field(
        default=None, description="Right boundary constraint"
    )
    fallback_roi: SubPaneROI | None = Field(
        default=None, description="Fallback static ROI if dynamic resolution fails"
    )


# =============================================================================
# Option 4: Local Macro & Trajectory Dispatcher
# =============================================================================


class TrajectoryStep(BaseModel):
    """Declarative specification for a single action step within a batch trajectory."""

    step_id: str = Field(
        description="Unique identifier for the step (e.g. 'focus_window', 'click_tab')"
    )
    action: str = Field(
        description="Action primitive: focus_window, click, find_and_click, click_and_type, type_text, "
        "press_key, hotkey, scroll, wait, wait_for_ui_change, capture_pane, capture_vdom, "
        "query_vdom, execute_crystallized_skill, run_terminal_command, dismiss_obstructions"
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Action-specific parameters"
    )
    description: str = Field(default="", description="Human-readable description of intent")
    verify_delta: bool = Field(default=True, description="Whether to verify UI delta after step")
    assertions: list[dict[str, Any]] = Field(
        default_factory=list, description="Optional post-step assertions"
    )
    retry_count: int = Field(
        default=1, ge=1, le=5, description="Number of retry attempts if step fails"
    )
    timeout_sec: float = Field(
        default=15.0, description="Max execution timeout in seconds for this step"
    )
    continue_on_failure: bool = Field(
        default=False,
        description="Whether to continue executing subsequent steps if this step fails",
    )


class TrajectorySpec(BaseModel):
    """Specification of a multi-step declarative trajectory executed locally at native speeds."""

    trajectory_name: str = Field(description="Unique name or title of the trajectory")
    description: str = Field(default="", description="High-level description of the workflow")
    variables: dict[str, Any] = Field(
        default_factory=dict, description="Template variables for parameter interpolation"
    )
    steps: list[TrajectoryStep] = Field(description="Ordered list of steps to execute")
    stop_on_failure: bool = Field(
        default=True, description="Whether to abort remaining steps if a step fails"
    )
    capture_final_screenshot: bool = Field(
        default=False, description="Whether to return a final base64 screenshot"
    )


class StepExecutionResult(BaseModel):
    """Outcome of an individual trajectory step execution."""

    step_id: str = Field(description="Identifier of the executed step")
    action: str = Field(description="Action executed")
    success: bool = Field(
        description="Whether the step executed without error and met all assertions"
    )
    elapsed_ms: float = Field(default=0.0, description="Execution duration in milliseconds")
    message: str = Field(default="", description="Execution status or error message")
    delta_detected: bool = Field(
        default=False, description="Whether visual delta was detected post-step"
    )
    output_data: dict[str, Any] = Field(
        default_factory=dict, description="Structured outputs produced by the action"
    )
    error: str | None = Field(default=None, description="Error details if failed")


class TrajectoryExecutionResult(BaseModel):
    """Comprehensive outcome of executing a declarative multi-step trajectory."""

    success: bool = Field(description="Whether all required steps executed successfully")
    is_error: bool = Field(
        default=False, description="Whether an unhandled step failure or error occurred"
    )
    trajectory_name: str = Field(description="Name of the executed trajectory")
    total_steps: int = Field(default=0, description="Total steps in the trajectory")
    executed_steps: int = Field(default=0, description="Number of steps actually executed")
    successful_steps: int = Field(default=0, description="Number of steps that succeeded")
    failed_steps: int = Field(default=0, description="Number of steps that failed")
    elapsed_ms: float = Field(
        default=0.0, description="Total trajectory execution duration in milliseconds"
    )
    estimated_tokens_saved: int = Field(
        default=0, description="Estimated multimodal LLM tokens saved by local batching"
    )
    step_results: list[StepExecutionResult] = Field(
        default_factory=list, description="Per-step execution outcomes"
    )
    final_screenshot_b64: str | None = Field(
        default=None, description="Optional final screen capture"
    )
    message: str = Field(default="", description="Overall trajectory status message")


class DisplayMetrics(BaseModel):
    """One monitor in logical virtual-desktop space."""

    display_id: str
    origin: tuple[int, int]
    size: tuple[int, int]
    scale: float = 1.0
    rotation_deg: int = 0


class DisplayTopology(BaseModel):
    """Virtual desktop: logical points, origins may be negative."""

    displays: list[DisplayMetrics] = Field(default_factory=list)
    primary_id: str = "primary"


def logical_to_backing(point: tuple[int, int], display: DisplayMetrics) -> tuple[int, int]:
    """Converts a logical global point into that display's backing pixels."""
    lx = point[0] - display.origin[0]
    ly = point[1] - display.origin[1]
    return (round(lx * display.scale), round(ly * display.scale))


class QuiescenceReport(BaseModel):
    """Result of an event-driven settle wait."""

    phase: ExecutionPhase
    elapsed_ms: float = 0.0
    dirty_area: int = 0
    cursor: str = "unknown"
    ax_changed: bool = False


class RelationalAnchor(BaseModel):
    """Icon located by an OCR label plus a pixel offset."""

    label: str
    dx: int = -24
    dy: int = 0
    box: tuple[int, int] = (24, 24)
    magnitude_sha: str = ""


class StateCheckpoint(BaseModel):
    """Eight-part invariant compaction of a desktop trajectory."""

    user_instructions: str
    task_template: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    actions_taken: list[str] = Field(default_factory=list)
    errors_and_fixes: list[str] = Field(default_factory=list)
    progress_metrics: str = ""
    current_screen_state: str = ""
    next_immediate_step: str = ""
    compressor: str = "heuristic"
    covers_turns: tuple[int, int] = (0, 0)


class TraceStep(BaseModel):
    """One recorded action with the selector the cascade actually used."""

    action: str
    selector: dict[str, Any] = Field(default_factory=dict)
    args: dict[str, Any] = Field(default_factory=dict)
    post: PostConditionContract | None = None


class CrystallizedMacro(BaseModel):
    """Parameterizable macro. Steps run locally; native_template is optional."""

    name: str
    app_id: str = ""
    description: str = ""
    params: dict[str, dict[str, Any]] = Field(default_factory=dict)
    steps: list[TraceStep] = Field(default_factory=list)
    native_template: str = ""
    native_kind: str = "none"
