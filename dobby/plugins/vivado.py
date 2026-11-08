"""AMD/Xilinx Vivado EDA Application Plugin for Vision MCP.

Implements specialized EDA tool sub-pane bounding box routing,
simulation control macros (launch, step, restart, zoom fit),
waveform canvas focus management, and deterministic Tcl automation.
"""

import time
from typing import Any

from dobby.plugins.base import BaseUIAppPlugin
from dobby.types import (
    AnchorEdge,
    BoundaryConstraint,
    FeatureDescriptor,
    SubPaneLayoutSpec,
    SubPaneROI,
)


def _send_hotkey(hal, combo: str) -> None:
    """Helper to parse hotkey combo and dispatch to HAL."""
    parts = [p.strip().lower() for p in combo.split("+")]
    hal.keyboard_hotkey(parts)


class VivadoPlugin(BaseUIAppPlugin):
    """Specialized UI plugin for AMD/Xilinx Vivado Design Suite."""

    app_id = "vivado"
    name = "AMD/Xilinx Vivado EDA Plugin"
    description = (
        "Automates Vivado project navigation, xsim behavioral simulation, "
        "waveform viewer inspection, zoom fit, and deterministic Tcl console execution"
    )

    def _register_panes(self) -> None:
        """Registers standard Vivado 2025.1 sub-panes."""
        self.sub_panes = {
            "flow_navigator": SubPaneROI(
                name="flow_navigator",
                description="Left workflow panel for Project Manager, Simulation, Synthesis, Implementation",
                relative_x=0.0,
                relative_y=0.0,
                offset_y=55,
                fixed_width=250,
                relative_height=0.92,
            ),
            "workspace": SubPaneROI(
                name="workspace",
                description="Central workspace area hosting Project Summary, text editors, schematics, and waveforms",
                relative_x=0.0,
                offset_x=250,
                relative_y=0.0,
                offset_y=55,
                relative_width=1.0,
                relative_height=0.68,
            ),
            "waveform_canvas": SubPaneROI(
                name="waveform_canvas",
                description="Primary waveform viewer canvas and signal trace area",
                relative_x=0.22,
                relative_y=0.0,
                offset_y=80,
                relative_width=0.78,
                relative_height=0.64,
            ),
            "scope_objects": SubPaneROI(
                name="scope_objects",
                description="Hierarchy scope tree and objects / signals list dock during simulation",
                relative_x=0.0,
                offset_x=250,
                relative_y=0.0,
                offset_y=55,
                fixed_width=320,
                relative_height=0.68,
            ),
            "tcl_console": SubPaneROI(
                name="tcl_console",
                description="Bottom dock containing Tcl Console, Messages, Log, and Reports tabs",
                relative_x=0.0,
                offset_x=250,
                relative_y=0.68,
                offset_y=55,
                relative_width=1.0,
                relative_height=0.28,
            ),
        }

        # Dynamic topological anchor specifications (Phase 5)
        self.subpane_layouts = {
            "tcl_console": SubPaneLayoutSpec(
                pane_id="tcl_console",
                top_constraint=BoundaryConstraint(
                    anchor_selector='tab_item[name*="Tcl Console"]',
                    edge=AnchorEdge.TOP,
                    offset=-6,
                ),
                bottom_constraint=None,  # Window bottom
                fallback_roi=self.sub_panes["tcl_console"],
            ),
            "flow_navigator": SubPaneLayoutSpec(
                pane_id="flow_navigator",
                right_constraint=BoundaryConstraint(
                    anchor_selector=":sash_divider",
                    edge=AnchorEdge.LEFT,
                    offset=0,
                ),
                fallback_roi=self.sub_panes["flow_navigator"],
            ),
            "waveform_canvas": SubPaneLayoutSpec(
                pane_id="waveform_canvas",
                fallback_roi=self.sub_panes["waveform_canvas"],
            ),
            "workspace": SubPaneLayoutSpec(
                pane_id="workspace",
                fallback_roi=self.sub_panes["workspace"],
            ),
        }

    def _register_features(self) -> None:
        """Registers declarative Vivado capabilities and keyboard shortcuts."""
        self.feature_map = {
            "vivado.launch_simulation": FeatureDescriptor(
                feature_id="vivado.launch_simulation",
                name="Launch Behavioral Simulation",
                description="Triggers behavioral simulation compile/elaboration via Tcl console or Flow Navigator",
                category="simulation",
                target_pane="flow_navigator",
            ),
            "vivado.zoom_fit": FeatureDescriptor(
                feature_id="vivado.zoom_fit",
                name="Zoom Fit Waveform",
                description="Focuses the waveform canvas and presses 'F' to fit the full simulation timeline into the view",
                category="waveform",
                hotkey="f",
                target_pane="waveform_canvas",
            ),
            "vivado.zoom_in": FeatureDescriptor(
                feature_id="vivado.zoom_in",
                name="Zoom In Waveform",
                description="Zooms in horizontally on the waveform timeline",
                category="waveform",
                hotkey="ctrl+=",
                target_pane="waveform_canvas",
            ),
            "vivado.zoom_out": FeatureDescriptor(
                feature_id="vivado.zoom_out",
                name="Zoom Out Waveform",
                description="Zooms out horizontally on the waveform timeline",
                category="waveform",
                hotkey="ctrl+-",
                target_pane="waveform_canvas",
            ),
            "vivado.run_all": FeatureDescriptor(
                feature_id="vivado.run_all",
                name="Run All Simulation",
                description="Runs simulation indefinitely until $finish or $stop breakpoint",
                category="simulation",
                hotkey="f5",
                target_pane="workspace",
            ),
            "vivado.run_for": FeatureDescriptor(
                feature_id="vivado.run_for",
                name="Run Simulation For Time",
                description="Steps simulation forward by a designated time duration (e.g. '500ns', '10us')",
                category="simulation",
                hotkey="shift+f2",
                target_pane="tcl_console",
                parameters={
                    "time": {
                        "type": "string",
                        "default": "500ns",
                        "description": "Simulation time increment",
                    },
                },
            ),
            "vivado.restart": FeatureDescriptor(
                feature_id="vivado.restart",
                name="Restart Simulation",
                description="Resets simulation time to 0 ns while preserving waveform configuration",
                category="simulation",
                hotkey="ctrl+shift+f5",
                target_pane="workspace",
            ),
            "vivado.add_wave": FeatureDescriptor(
                feature_id="vivado.add_wave",
                name="Add Waveform Signal",
                description="Adds design hierarchy or signal path to active waveform viewer",
                category="waveform",
                target_pane="tcl_console",
                parameters={
                    "signal": {
                        "type": "string",
                        "required": True,
                        "description": "Signal path (e.g. '/tb_sync_fifo/u_fifo/*')",
                    },
                },
            ),
            "vivado.set_radix": FeatureDescriptor(
                feature_id="vivado.set_radix",
                name="Set Waveform Radix",
                description="Configures display radix (hex, bin, dec, ascii, unsigned) for waveform signals",
                category="waveform",
                target_pane="tcl_console",
                parameters={
                    "radix": {
                        "type": "string",
                        "default": "hex",
                        "description": "Radix format: 'hex', 'bin', 'unsigned', 'dec'",
                    },
                    "signals": {
                        "type": "string",
                        "default": "[get_waves *]",
                        "description": "Tcl signal query expression",
                    },
                },
            ),
            "vivado.tcl_exec": FeatureDescriptor(
                feature_id="vivado.tcl_exec",
                name="Execute Tcl Command",
                description="Dispatches deterministic Tcl command string to the Vivado Tcl Console",
                category="tcl",
                target_pane="tcl_console",
                parameters={
                    "command": {
                        "type": "string",
                        "required": True,
                        "description": "Tcl command to execute",
                    },
                },
            ),
            "vivado.toggle_flow_navigator": FeatureDescriptor(
                feature_id="vivado.toggle_flow_navigator",
                name="Toggle Flow Navigator",
                description="Toggles or collapses the left Flow Navigator panel to reclaim horizontal workspace for waveforms",
                category="view",
                hotkey="ctrl+q",
                target_pane="flow_navigator",
            ),
        }

    def detect(self, window_title: str, app_name: str | None = None) -> bool:
        """Matches AMD/Xilinx Vivado desktop windows."""
        lower_title = window_title.lower() if window_title else ""
        lower_app = app_name.lower() if app_name else ""
        return (
            "vivado" in lower_title
            or "vivado" in lower_app
            or "librdi_vivado" in lower_app
            or "xsim" in lower_title
            or "xsim" in lower_app
        )

    def execute_feature(
        self,
        feature_id: str,
        controller: Any,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Executes a Vivado feature with closed-loop verification."""
        args = args or {}
        feature = self.get_feature(feature_id)
        if not feature:
            return {
                "success": False,
                "feature_id": feature_id,
                "message": f"Feature '{feature_id}' is not supported by {self.name}.",
            }

        hal = controller.hal

        # 1. Zoom Fit (Waveform)
        if feature_id == "vivado.zoom_fit":
            # Focus waveform canvas first by clicking its center ROI
            active_win = hal.get_active_window()
            if active_win:
                wave_box = self.get_sub_pane("waveform_canvas", active_win.bbox)
                if wave_box:
                    cx = wave_box.x + wave_box.width // 2
                    cy = wave_box.y + wave_box.height // 2
                    hal.mouse_click(cx, cy)
                    time.sleep(0.1)

            hal.keyboard_press_key("f")
            res = controller.execute_with_verification(
                action_name="vivado.zoom_fit",
                action_func=lambda: None,
                settle_time_sec=0.3,
            )
            return {
                "success": res.success,
                "feature_id": feature_id,
                "delta_detected": res.delta_detected,
                "change_ratio": res.change_ratio,
                "message": "Waveform zoomed to fit entire simulation duration.",
            }

        # 2. Zoom In
        if feature_id == "vivado.zoom_in":
            _send_hotkey(hal, "ctrl+equal")
            res = controller.execute_with_verification(
                action_name="vivado.zoom_in",
                action_func=lambda: None,
                settle_time_sec=0.2,
            )
            return {
                "success": res.success,
                "feature_id": feature_id,
                "delta_detected": res.delta_detected,
                "message": "Waveform zoomed in.",
            }

        # 3. Zoom Out
        if feature_id == "vivado.zoom_out":
            _send_hotkey(hal, "ctrl+minus")
            res = controller.execute_with_verification(
                action_name="vivado.zoom_out",
                action_func=lambda: None,
                settle_time_sec=0.2,
            )
            return {
                "success": res.success,
                "feature_id": feature_id,
                "delta_detected": res.delta_detected,
                "message": "Waveform zoomed out.",
            }

        # 4. Run All
        if feature_id == "vivado.run_all":
            hal.keyboard_press_key("f5")
            res = controller.execute_with_verification(
                action_name="vivado.run_all",
                action_func=lambda: None,
                settle_time_sec=0.5,
            )
            return {
                "success": res.success,
                "feature_id": feature_id,
                "delta_detected": res.delta_detected,
                "message": "Simulation run all dispatched (F5).",
            }

        # 5. Restart
        if feature_id == "vivado.restart":
            _send_hotkey(hal, "ctrl+shift+f5")
            res = controller.execute_with_verification(
                action_name="vivado.restart",
                action_func=lambda: None,
                settle_time_sec=0.4,
            )
            return {
                "success": res.success,
                "feature_id": feature_id,
                "delta_detected": res.delta_detected,
                "message": "Simulation restarted to 0ns.",
            }

        # 6. Execute Tcl Command
        if feature_id == "vivado.tcl_exec":
            cmd = args.get("command", "")
            if not cmd:
                return {
                    "success": False,
                    "feature_id": feature_id,
                    "message": "Missing 'command' argument.",
                }

            # Focus Tcl Console pane input line
            active_win = hal.get_active_window()
            if active_win:
                tcl_box = self.get_sub_pane("tcl_console", active_win.bbox)
                if tcl_box:
                    # Bottom-ish line of Tcl pane is the input line
                    cx = tcl_box.x + 100
                    cy = tcl_box.y + tcl_box.height - 18
                    hal.mouse_click(cx, cy)
                    time.sleep(0.1)

            hal.keyboard_type(cmd)
            time.sleep(0.05)
            hal.keyboard_press_key("enter")

            res = controller.execute_with_verification(
                action_name=f"vivado.tcl_exec({cmd})",
                action_func=lambda: None,
                settle_time_sec=0.5,
            )
            return {
                "success": res.success,
                "feature_id": feature_id,
                "delta_detected": res.delta_detected,
                "change_ratio": res.change_ratio,
                "command": cmd,
                "message": f"Dispatched Tcl command: {cmd}",
            }

        # 7. Run For
        if feature_id == "vivado.run_for":
            time_val = args.get("time", "500ns")
            return self.execute_feature(
                "vivado.tcl_exec", controller, {"command": f"run {time_val}"}
            )

        # 8. Add Wave
        if feature_id == "vivado.add_wave":
            sig = args.get("signal", "")
            if not sig:
                return {
                    "success": False,
                    "feature_id": feature_id,
                    "message": "Missing 'signal' argument.",
                }
            return self.execute_feature(
                "vivado.tcl_exec", controller, {"command": f"add_wave {sig}"}
            )

        # 9. Set Radix
        if feature_id == "vivado.set_radix":
            radix = args.get("radix", "hex")
            signals = args.get("signals", "[get_waves *]")
            return self.execute_feature(
                "vivado.tcl_exec", controller, {"command": f"set_property radix {radix} {signals}"}
            )

        # 10. Launch Simulation
        if feature_id == "vivado.launch_simulation":
            return self.execute_feature(
                "vivado.tcl_exec", controller, {"command": "launch_simulation"}
            )

        # 11. Toggle Flow Navigator
        if feature_id == "vivado.toggle_flow_navigator":
            _send_hotkey(hal, "ctrl+q")
            res = controller.execute_with_verification(
                action_name="vivado.toggle_flow_navigator",
                action_func=lambda: None,
                settle_time_sec=0.3,
            )
            return {
                "success": res.success,
                "feature_id": feature_id,
                "delta_detected": res.delta_detected,
                "change_ratio": res.change_ratio,
                "message": "Flow Navigator toggled (Ctrl+Q).",
            }

        return {
            "success": False,
            "feature_id": feature_id,
            "message": f"Execution logic not implemented for '{feature_id}'.",
        }
