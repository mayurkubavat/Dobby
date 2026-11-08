"""Configuration settings for Vision Control MCP Server."""

from pydantic import BaseModel, Field


class VisionConfig(BaseModel):
    """Runtime configuration for screen capture, input simulation, and grounding."""

    # Screen Capture & Image Optimization
    max_image_dimension: int = Field(
        default=1600,
        description="Max width or height for screenshots sent to AI (prevents token bloat).",
    )
    image_quality: int = Field(
        default=85,
        description="JPEG/WebP compression quality (1-100).",
    )
    draw_cursor: bool = Field(
        default=True,
        description="Whether to draw a virtual cursor overlay on captured screenshots.",
    )
    token_optimization_mode: str = Field(
        default="standard",
        description="Token optimization mode: 'standard', 'delta_patch', 'thumbnail_roi'.",
    )
    clip_to_active_window: bool = Field(
        default=False,
        description="Whether to automatically crop screenshots to the currently active window bounds.",
    )

    # Event-Driven Quiescence & Verification
    enable_quiescence_monitor: bool = Field(
        default=True,
        description="Whether to use XDamage & XFixes sub-millisecond quiescence monitoring instead of static sleep.",
    )
    quiescence_window_ms: int = Field(
        default=180,
        description="Continuous quiet period (ms) required before declaring UI settled.",
    )
    initial_reaction_timeout_ms: int = Field(
        default=1500,
        description="Maximum milliseconds to wait for initial screen damage or cursor busy signal post-action.",
    )
    max_quiescence_timeout_ms: int = Field(
        default=15000,
        description="Maximum total milliseconds to wait for UI settling before timeout or async job promotion.",
    )
    min_damage_area: int = Field(
        default=64,
        description="Pixel area threshold to filter out tiny caret blinks during damage accumulation.",
    )

    # GUI-to-Script Skill Crystallization
    enable_skill_crystallization: bool = Field(
        default=True,
        description="Whether to capture and correlate underlying application journal streams into reusable Tcl/CLI scripts.",
    )
    crystallized_skills_path: str = Field(
        default="~/.gemini/antigravity-cli/skills/crystallized_skills.json",
        description="Persistent file path for storing crystallized parameterized skills.",
    )

    # Local Neural Grounding Sidecar & Unified vDOM
    enable_vdom: bool = Field(
        default=True,
        description="Whether to generate and maintain a unified hierarchical Virtual DOM (vDOM).",
    )
    vdom_sidecar_shm_path: str = Field(
        default="/dev/shm/vision_mcp_fb",
        description="Shared memory path for zero-copy IPC between MCP server and local neural daemon.",
    )
    vdom_fusion_iou_threshold: float = Field(
        default=0.65,
        description="Asymmetric IoU threshold for merging neural proposals with AT-SPI2 leaf nodes into HYBRID nodes.",
    )
    vdom_containment_ratio_threshold: float = Field(
        default=0.85,
        description="Containment threshold for nesting unmatched visual elements under parent containers.",
    )

    # Fast-Path Neural ONNX UI Detector
    enable_fast_onnx_detector: bool = Field(
        default=True,
        description="Whether to use sub-20ms ONNX UI detector for fast zero-token vDOM proposals.",
    )
    fast_onnx_model_path: str = Field(
        default="dobby/models/ui_detector_fast.onnx",
        description="File path to the ONNX UI detection model.",
    )
    fast_onnx_conf_threshold: float = Field(
        default=0.25,
        description="Confidence threshold for neural UI proposals.",
    )
    fast_onnx_iou_threshold: float = Field(
        default=0.45,
        description="Non-Maximum Suppression IoU threshold for neural proposals.",
    )
    fast_onnx_num_threads: int = Field(
        default=4,
        description="Number of intra-op threads for ONNX Runtime CPU inference.",
    )

    # Local OCR lane
    ocr_engine: str = Field(
        default="auto",
        description="OCR backend: 'auto' (Apple Vision on macOS, RapidOCR elsewhere), 'apple_vision', 'rapidocr', or 'none'.",
    )
    ocr_min_confidence: float = Field(
        default=0.5,
        description="Drop OCR lines below this recognition confidence.",
    )
    ocr_in_vdom: bool = Field(
        default=True,
        description="Run OCR during capture_vdom: name empty detector boxes and add free text as static_text nodes.",
    )
    ax_enable_web_content: bool = Field(
        default=True,
        description=(
            "macOS: set AXManualAccessibility on each inspected app once; Linux: set org.a11y.Status.IsEnabled. "
            "Either way Electron/Chromium apps "
            "(Claude, VS Code, Slack) expose their web content to the AX tree."
        ),
    )
    settle_timeout_s: float = Field(
        default=2.5,
        description="Screen state: longest wait for the window to stop changing before reading it.",
    )
    settle_after_action_s: float = Field(
        default=3.0,
        description='Screen state: settle="auto" waits only if the last action was this recent.',
    )
    settle_interval_s: float = Field(
        default=0.12, description="Screen state: time between settle samples."
    )
    settle_max_delta: float = Field(
        default=1.5,
        description="Screen state: mean grayscale difference (0-255) of 64 px thumbnails that still counts as still.",
    )
    screen_state_max_icons: int = Field(
        default=20,
        description="Screen state: most unnamed icon-detector boxes added to an observation.",
    )
    ax_include_menu_bar: bool = Field(
        default=True,
        description="Include the frontmost app's top-level menu bar items in the macOS AX tree.",
    )
    ax_web_content_settle_seconds: float = Field(
        default=0.4,
        description="After first enabling web-content AX on an app, wait this long for Chromium to build its tree.",
    )
    ax_web_content_retry_seconds: float = Field(
        default=1.5,
        description="If that first read is still near-empty, keep re-reading for up to this long.",
    )

    # Local Gemma 3 Multimodal Sidecar Plugin
    enable_gemma3_sidecar: bool = Field(
        default=True,
        description="Whether to enable on-demand Gemma 3 multimodal grounding for opaque sub-panes and modal triage.",
    )
    gemma3_endpoint: str = Field(
        default="http://127.0.0.1:11434",
        description="Ollama base URL for local Gemma 3 inference (a legacy .../api/generate URL is also accepted).",
    )
    gemma3_vision_model: str = Field(
        default="gemma3:4b",
        description="Model identifier for multimodal visual grounding (Gemma 3 4B-IT).",
    )
    gemma3_text_model: str = Field(
        default="gemma3:4b",
        description="Model identifier for text modal risk triage. gemma3:4b avoids loading a second model.",
    )
    model_text: str = Field(
        default="",
        description="Model for the 'text' role (empty: gemma3_text_model). See dobby/local_models.py.",
    )
    model_vision: str = Field(
        default="", description="Model for the 'vision' role (empty: gemma3_vision_model)."
    )
    model_visual_pick: str = Field(
        default="", description="Model for the 'visual_pick' role (empty: gemma3_vision_model)."
    )
    model_triage: str = Field(
        default="", description="Model for the 'triage' role (empty: gemma3_text_model)."
    )
    icon_detector: str = Field(
        default="auto",
        description="Icon boxes for screen state: 'auto' (neural, contours when it misbehaves), 'onnx', or 'contours'.",
    )
    gemma3_dhash_threshold: int = Field(
        default=4,
        description="Perceptual hash Hamming distance threshold for semantic ROI cache hits.",
    )
    gemma3_cache_capacity: int = Field(
        default=256,
        description="Maximum entries in the local Gemma 3 semantic ROI cache.",
    )
    gemma3_keep_alive: str = Field(
        default="10m",
        description="How long Ollama keeps the Gemma model loaded after a call.",
    )
    gemma3_modal_tiebreak: bool = Field(
        default=True,
        description="Ask Gemma about dialogs the regex rules cannot classify. Gemma can only make the verdict "
        "stricter, or approve a known-safe dismiss button on a dialog with no risky keywords.",
    )
    gemma3_modal_min_confidence: float = Field(
        default=0.8,
        description="Minimum Gemma confidence before a dialog may be auto-dismissed on its verdict.",
    )
    gemma3_modal_timeout_seconds: float = Field(
        default=10.0,
        description="Timeout for a Gemma dialog triage call; on timeout the dialog is escalated.",
    )
    gemma3_visual_min_confidence: float = Field(
        default=0.6,
        description="Minimum Gemma confidence for a VISUAL_QUERY post-condition to pass.",
    )
    gemma3_visual_max_side: int = Field(
        default=896,
        description="Long-side pixel cap for crops sent to Gemma (896 is Gemma 3's native image size).",
    )
    gemma3_max_marks: int = Field(
        default=30,
        description="Most numbered boxes drawn on one crop for Gemma Set-of-Marks labeling or selection.",
    )
    gemma3_label_vdom: bool = Field(
        default=False,
        description="Name unlabeled VISION_ONLY vDOM nodes with Gemma during capture_vdom (adds seconds per uncached batch).",
    )
    gemma3_warmup_on_start: bool = Field(
        default=True,
        description="Load the Gemma vision model in the background when the server starts, so the first check is warm.",
    )
    gemma3_prompt_dir: str | None = Field(
        default="~/.dobby/prompts",
        description="Folder of local prompt overrides: <prompt_id>.txt replaces that prompt's system text.",
    )
    gemma3_ocr_context: bool = Field(
        default=False,
        description="Also show Gemma the crop's OCR text with yes/no questions. Off by default: on gemma3:4b it "
        "caused a wrong answer in eval_prompts (see the perception skill). Evidence is OCR-checked either way.",
    )
    gemma3_ocr_verify: bool = Field(
        default=True,
        description="Check Gemma's quoted text evidence against local OCR of the same crop.",
    )
    gemma3_unverified_text_cap: float = Field(
        default=0.5,
        description="Confidence cap when Gemma quotes text as evidence that OCR cannot find in the crop.",
    )
    gemma3_yes_no_logprobs: bool = Field(
        default=False,
        description="Answer yes/no checks by reading Gemma's yes/no token probabilities (one token, calibrated "
        "p_yes) instead of a generated JSON answer. Needs an Ollama that returns logprobs; falls back otherwise.",
    )
    gemma3_yes_band: float = Field(
        default=0.7,
        description="Logprob yes/no: p_yes at or above this is yes, at or below 1 - this is no, between is unsure.",
    )
    gemma3_timeout_seconds: float = Field(
        default=25.0,
        description="Maximum timeout in seconds for local Gemma 3 inference requests.",
    )

    # Dynamic Anchor-Relative Sub-Pane Resolution
    enable_dynamic_subpanes: bool = Field(
        default=True,
        description="Whether to resolve sub-pane boundaries dynamically relative to persistent UI anchors.",
    )

    # Self-Healing Action Cache
    enable_action_cache: bool = Field(
        default=True,
        description="Whether to use the self-healing locator cache for zero-token repeat actions.",
    )
    action_cache_verify_visual: bool = Field(
        default=True,
        description="Whether to perform pre-flight dHash and template visual verification before clicking cached coordinates.",
    )
    action_cache_dhash_threshold: int = Field(
        default=6,
        description="Maximum perceptual Hamming distance for pre-flight visual verification hit.",
    )
    action_cache_search_radius_px: int = Field(
        default=60,
        description="Pixel search radius around cached coordinates for auto-reanchoring drift recovery.",
    )
    action_cache_match_threshold: float = Field(
        default=0.78,
        description="Minimum normalized cross-correlation score for neighborhood template auto-reanchoring.",
    )
    action_cache_window_relative: bool = Field(
        default=True,
        description="Whether to dynamically compensate for window movements and resizing.",
    )

    # Autonomous Obstruction Interception
    auto_dismiss_obstructions: bool = Field(
        default=True,
        description="Whether to automatically detect and dismiss blocking modal dialogs/permission prompts.",
    )

    # Set-of-Marks (SoM) & Grounding Strategy
    grounding_strategy: str = Field(
        default="ensemble",
        description="Active grounding strategy: 'ensemble', 'semantic', 'cv', or 'template'.",
    )
    enable_nms_fusion: bool = Field(
        default=True,
        description="Whether to use Non-Maximum Suppression to deduplicate fused detections.",
    )
    nms_iou_threshold: float = Field(
        default=0.40,
        description="Intersection-over-Union threshold for fusing overlapping marks.",
    )
    som_badge_font_size: int = Field(
        default=14,
        description="Font size for SoM numeric badge overlays.",
    )
    som_max_elements: int = Field(
        default=150,
        description="Max number of interactable elements to tag with SoM badges.",
    )
    som_min_element_area: int = Field(
        default=100,
        description="Minimum pixel area for an element to be tagged with a mark.",
    )

    # Input Simulation & Safety
    typing_delay_ms: float = Field(
        default=12.0,
        description="Natural typing delay between keystrokes in milliseconds.",
    )
    action_cooldown_ms: float = Field(
        default=50.0,
        description="Minimum delay after mouse or keyboard actions to allow UI update.",
    )
    failsafe_corner: bool = Field(
        default=True,
        description="Emergency abort if mouse cursor is forced to top-left (0, 0).",
    )

    # Terminal Integration
    allow_terminal_commands: bool = Field(
        default=False,
        description=(
            "Allow the AI to run shell commands (run_terminal_command). Off by default; the person opts in "
            "with DOBBY_ALLOW_SHELL=1, and an admin policy (dobby.policy) overrides both."
        ),
    )
    command_timeout_seconds: int = Field(
        default=30,
        description="Max runtime in seconds for terminal commands.",
    )

    # Tool Catalog & Out-of-Band State Offloading
    output_mode: str = Field(
        default="inline",
        description="Output mode for MCP payloads: 'inline', 'file', or 'resource'.",
    )
    output_file_threshold_tokens: int = Field(
        default=350,
        description="Estimated token threshold above which outputs are offloaded out-of-band to disk/tmpfs.",
    )
    toolset_profile: str = Field(
        default="minimal_computer_use",
        description="Default active toolset profile: 'minimal_computer_use', 'window_mgmt', 'full_developer', 'eda_automation'.",
    )
    cassette_dir: str = Field(
        default="~/.dobby/cassettes",
        description="Where start_cassette_recording writes recorded cassettes (one folder per name).",
    )
    state_dir: str | None = Field(
        default=None,
        description="Directory for RAM-backed state payload offloading (defaults to tmpfs /tmp/dc_state).",
    )


# Singleton default config instance
CONFIG = VisionConfig()
