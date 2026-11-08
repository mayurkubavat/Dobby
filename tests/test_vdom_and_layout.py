"""
Unit tests for Phase 4 (Unified vDOM & Neural Sidecar) and Phase 5 (Dynamic Anchor-Relative Sub-Pane Resolution).
"""

from PIL import Image

from pinlet.agent_loop.controller import FeedbackLoopController
from pinlet.types import (
    AnchorEdge,
    BoundaryConstraint,
    BoundingBox,
    GroundingSource,
    SubPaneLayoutSpec,
    SubPaneROI,
    UIRole,
    VDOMNode,
    VirtualDOMSnapshot,
    WindowInfo,
)
from pinlet.vdom import (
    SubPaneLayoutSolver,
    VDOMFusionEngine,
    VDOMQueryEngine,
)


class MockHAL:
    def get_screen_size(self):
        return 1920, 1080

    def capture_screen(self, region=None):
        return Image.new("RGB", (1920, 1080), color="white")

    def mouse_click(self, x, y, button="left", click_count=1):
        pass

    def mouse_move(self, x, y):
        pass

    def mouse_down(self, button="left"):
        pass

    def mouse_up(self, button="left"):
        pass

    def mouse_scroll(self, dx, dy):
        pass

    def get_cursor_position(self):
        return (100, 100)

    def keyboard_type(self, text, delay_ms=10.0):
        pass

    def keyboard_press_key(self, key):
        pass

    def keyboard_release_key(self, key):
        pass

    def keyboard_hotkey(self, keys):
        pass

    def list_windows(self):
        return []

    def get_active_window(self):
        return WindowInfo(
            window_id="0x4001",
            title="Vivado 2025.1 - fifo_design",
            app_name="vivado",
            bbox=BoundingBox(x=0, y=0, width=1920, height=1080),
            is_active=True,
        )

    def focus_window(self, window_id):
        return True


def test_bounding_box_iou_and_containment():
    box1 = BoundingBox(x=10, y=10, width=100, height=100)
    box2 = BoundingBox(x=10, y=10, width=100, height=100)
    assert box1.iou(box2) == 1.0
    assert box1.containment_ratio(box2) == 1.0

    # Disjoint
    box3 = BoundingBox(x=200, y=200, width=50, height=50)
    assert box1.iou(box3) == 0.0
    assert box1.containment_ratio(box3) == 0.0

    # Child half inside
    parent = BoundingBox(x=0, y=0, width=100, height=100)
    child = BoundingBox(x=50, y=0, width=100, height=100)
    # Child total area = 10000, intersection area = 50 * 100 = 5000
    assert parent.containment_ratio(child) == 0.5


def test_vdom_fusion_engine():
    fusion = VDOMFusionEngine(iou_threshold=0.65, containment_threshold=0.85)

    atspi_root = VDOMNode(
        node_id="vdom_root",
        source=GroundingSource.AT_SPI2,
        role=UIRole.WINDOW,
        name="Vivado 2025.1",
        bbox=BoundingBox(x=0, y=0, width=1920, height=1080),
        click_point=(960, 540),
        children_ids=["vdom_canvas", "vdom_btn"],
    )
    atspi_canvas = VDOMNode(
        node_id="vdom_canvas",
        source=GroundingSource.AT_SPI2,
        role=UIRole.CANVAS,
        name="Waveform Viewer Canvas",
        bbox=BoundingBox(x=250, y=80, width=1400, height=600),
        click_point=(950, 380),
        parent_id="vdom_root",
    )
    atspi_btn = VDOMNode(
        node_id="vdom_btn",
        source=GroundingSource.AT_SPI2,
        role=UIRole.PUSH_BUTTON,
        name="Run Simulation",
        bbox=BoundingBox(x=10, y=60, width=120, height=30),
        click_point=(70, 75),
        parent_id="vdom_root",
    )

    visual_proposals = [
        # Proposal 1: Matches atspi_btn with high IoU -> Hybrid upgrade
        {
            "bbox": {"x": 12, "y": 61, "width": 118, "height": 29},
            "confidence": 0.95,
            "class_name": "push_button",
            "text": "Run Simulation",
        },
        # Proposal 2: Waveform signal line inside atspi_canvas -> VISION_ONLY child
        {
            "bbox": {"x": 260, "y": 120, "width": 300, "height": 18},
            "confidence": 0.88,
            "class_name": "waveform_signal",
            "text": "clk_100mhz",
        },
        # Proposal 3: Sash divider
        {
            "bbox": {"x": 248, "y": 0, "width": 4, "height": 1080},
            "confidence": 0.90,
            "class_name": "sash_divider",
            "text": "",
        },
    ]

    snapshot = fusion.fuse(
        atspi_nodes=[atspi_root, atspi_canvas, atspi_btn],
        visual_proposals=visual_proposals,
        window_id="0x4001",
        window_title="Vivado 2025.1",
        window_bbox=BoundingBox(x=0, y=0, width=1920, height=1080),
    )

    # 1. Verification of Hybrid deduplication
    fused_btn = snapshot.nodes["vdom_btn"]
    assert fused_btn.source == GroundingSource.HYBRID
    # Click point upgraded to visual proposal center (12+59, 61+14) = (71, 75)
    assert fused_btn.click_point == (71, 75)

    # 2. Verification of canvas child nesting and role inference
    signal_node = next(
        (n for n in snapshot.nodes.values() if n.name == "clk_100mhz"),
        None,
    )
    assert signal_node is not None
    assert signal_node.source == GroundingSource.VISION_ONLY
    assert signal_node.role == UIRole.WAVEFORM_SIGNAL
    assert signal_node.parent_id == "vdom_canvas"
    assert signal_node.node_id in snapshot.nodes["vdom_canvas"].children_ids

    # 3. Verification of sash divider role inference
    sash_node = next(
        (n for n in snapshot.nodes.values() if n.role == UIRole.SASH_DIVIDER),
        None,
    )
    assert sash_node is not None


def test_vdom_query_engine():
    root_bbox = BoundingBox(x=0, y=0, width=1920, height=1080)
    btn_run = VDOMNode(
        node_id="btn_run",
        source=GroundingSource.HYBRID,
        role=UIRole.PUSH_BUTTON,
        name="Run Synthesis",
        states=["ENABLED", "VISIBLE"],
        bbox=BoundingBox(x=100, y=200, width=150, height=40),
        click_point=(175, 220),
    )
    input_period = VDOMNode(
        node_id="txt_period",
        source=GroundingSource.AT_SPI2,
        role=UIRole.TEXT_INPUT,
        name="Period (ns)",
        value="10.0",
        states=["ENABLED", "VISIBLE"],
        bbox=BoundingBox(x=260, y=200, width=120, height=40),
        click_point=(320, 220),
    )
    label_status = VDOMNode(
        node_id="lbl_status",
        source=GroundingSource.AT_SPI2,
        role=UIRole.STATIC_TEXT,
        name="Synthesis Out of Date",
        states=["VISIBLE"],
        bbox=BoundingBox(x=100, y=140, width=280, height=30),
        click_point=(240, 155),
    )

    snapshot = VirtualDOMSnapshot(
        snapshot_id="snap_test",
        timestamp=100.0,
        window_bbox=root_bbox,
        nodes={
            "btn_run": btn_run,
            "txt_period": input_period,
            "lbl_status": label_status,
        },
    )

    engine = VDOMQueryEngine(snapshot)

    # 1. Role query
    res_btn = engine.query_selector("push_button")
    assert len(res_btn) == 1
    assert res_btn[0].node_id == "btn_run"

    # 2. Text regex query
    res_name = engine.query_selector('push_button[name*="Synthesis"]')
    assert len(res_name) == 1

    # 3. Spatial: right_of("Run Synthesis")
    res_right = engine.query_selector('text_input:right_of("Run Synthesis")')
    assert len(res_right) == 1
    assert res_right[0].node_id == "txt_period"

    # 4. Spatial: below("Synthesis Out of Date")
    res_below = engine.query_selector('push_button:below("Synthesis Out of Date")')
    assert len(res_below) == 1
    assert res_below[0].node_id == "btn_run"

    # 5. ID lookup
    res_id = engine.query_selector("#txt_period")
    assert len(res_id) == 1
    assert res_id[0].node_id == "txt_period"


def test_subpane_layout_solver():
    win_bbox = BoundingBox(x=0, y=40, width=1920, height=1040)
    solver = SubPaneLayoutSolver()

    # Anchor elements
    tab_console = VDOMNode(
        node_id="tab_console",
        source=GroundingSource.AT_SPI2,
        role=UIRole.TAB_ITEM,
        name="Tcl Console",
        bbox=BoundingBox(x=250, y=720, width=120, height=26),
        click_point=(310, 733),
    )
    sash_vertical = VDOMNode(
        node_id="sash_v",
        source=GroundingSource.VISION_ONLY,
        role=UIRole.SASH_DIVIDER,
        name="",
        bbox=BoundingBox(x=246, y=40, width=4, height=1040),
        click_point=(248, 560),
    )

    snapshot = VirtualDOMSnapshot(
        snapshot_id="snap_layout",
        timestamp=100.0,
        window_bbox=win_bbox,
        nodes={
            "tab_console": tab_console,
            "sash_v": sash_vertical,
        },
    )
    engine = VDOMQueryEngine(snapshot)

    # 1. Dynamic Tcl Console layout spec
    tcl_spec = SubPaneLayoutSpec(
        pane_id="tcl_console",
        top_constraint=BoundaryConstraint(
            anchor_selector='tab_item[name*="Tcl Console"]',
            edge=AnchorEdge.TOP,
            offset=-4,
        ),
        left_constraint=BoundaryConstraint(
            anchor_selector=":sash_divider",
            edge=AnchorEdge.RIGHT,
            offset=0,
        ),
        fallback_roi=SubPaneROI(name="tcl_console", relative_x=0.15, relative_y=0.7),
    )

    tcl_box = solver.resolve_subpane(tcl_spec, engine, win_bbox)
    # top_y should be tab_console.bbox.y - 4 = 720 - 4 = 716
    assert tcl_box.y == 716
    # left_x should be sash_v.bbox.right + 0 = 250
    assert tcl_box.x == 250
    # bottom should reach window bottom = 1080
    assert tcl_box.bottom == win_bbox.bottom
    assert tcl_box.width == 1920 - 250

    # 2. Fallback when anchor missing
    missing_spec = SubPaneLayoutSpec(
        pane_id="missing_pane",
        top_constraint=BoundaryConstraint(
            anchor_selector="non_existent_anchor",
            edge=AnchorEdge.TOP,
        ),
        bottom_constraint=BoundaryConstraint(
            anchor_selector="non_existent_anchor",
            edge=AnchorEdge.BOTTOM,
        ),
        fallback_roi=SubPaneROI(
            name="fallback", offset_x=100, offset_y=100, fixed_width=400, fixed_height=300
        ),
    )
    fb_box = solver.resolve_subpane(missing_spec, engine, win_bbox)
    assert fb_box.width > 0
    assert fb_box.height > 0


def test_controller_vdom_integration(tmp_path):
    hal = MockHAL()
    controller = FeedbackLoopController(hal)

    # Test vDOM capture
    snapshot = controller.capture_vdom()
    assert snapshot is not None
    assert len(snapshot.nodes) > 0

    # Test vDOM query
    results = controller.query_vdom(selector="window")
    assert len(results) >= 1
