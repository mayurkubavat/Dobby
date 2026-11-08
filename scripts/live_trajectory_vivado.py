#!/usr/bin/env python3
"""
Live Trajectory Execution on Vivado 2025.1 Simulation GUI (DISPLAY=:20).
Executes a multi-step autonomous trajectory locally at native OS speeds,
verifying deltas, sub-pane captures, vDOM queries, and journal synchronization.
"""

import base64
import os
import sys
import time

# Run from a clone without installing: put the repo root on the path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pinlet.agent_loop.controller import FeedbackLoopController
from pinlet.agent_loop.trajectory import TrajectoryEngine
from pinlet.hal.factory import get_desktop_hal
from pinlet.types import TrajectorySpec, TrajectoryStep

# The Vivado project folder (holding vivado.log and vivado.jou) and where to save results.
VIVADO_DIR = os.path.expanduser(os.environ.get("PINLET_VIVADO_DIR", "~/vivado_fifo"))
ARTIFACT_DIR = os.path.expanduser(os.environ.get("PINLET_ARTIFACT_DIR", "artifacts"))


def main():
    os.environ["DISPLAY"] = ":20"

    print("=================================================================")
    print("Option 4: Live Trajectory Dispatcher on Vivado 2025.1 (DISPLAY=:20)")
    print("=================================================================")

    hal = get_desktop_hal()
    controller = FeedbackLoopController(hal)
    engine = TrajectoryEngine(controller)

    spec = TrajectorySpec(
        trajectory_name="vivado_cdc_fifo_autonomous_regression",
        description="Autonomous multi-step CDC FIFO verification, waveform capture, and journal sync",
        variables={
            "target_app": "Vivado 2025.1",
            "sim_time": "300ns",
            "radix_mode": "hex",
        },
        steps=[
            TrajectoryStep(
                step_id="1_focus_vivado",
                action="focus_window",
                parameters={"title": "{{target_app}}"},
                verify_delta=False,
            ),
            TrajectoryStep(
                step_id="2_capture_initial_waves",
                action="capture_pane",
                parameters={"pane_name": "waveform_canvas"},
                verify_delta=False,
            ),
            TrajectoryStep(
                step_id="3_audit_vdom",
                action="capture_vdom",
                parameters={"query_selector": "push_button"},
                verify_delta=False,
            ),
            TrajectoryStep(
                step_id="4_verify_log_pre",
                action="run_terminal_command",
                parameters={
                    "command": "python3 -c \"lines = open('"
                    + os.path.join(VIVADO_DIR, "vivado.log")
                    + "').readlines(); print(f'LOG_LINES={len(lines)}')\""
                },
                assertions=[{"assert_exit_code": 0}],
            ),
            TrajectoryStep(
                step_id="5_vivado_zoom_fit",
                action="invoke_plugin_feature",
                parameters={"feature_id": "vivado.zoom_fit"},
                verify_delta=False,
            ),
            TrajectoryStep(
                step_id="6_execute_crystallized_skill",
                action="execute_crystallized_skill",
                parameters={
                    "skill_name": "cdc_fifo_regression_step",
                    "parameters": {"step_time": "{{sim_time}}"},
                },
                verify_delta=False,
            ),
            TrajectoryStep(
                step_id="7_quiesce_delay",
                action="wait",
                parameters={"duration_sec": 0.8},
                verify_delta=False,
            ),
            TrajectoryStep(
                step_id="8_capture_final_waves",
                action="capture_pane",
                parameters={"pane_name": "waveform_canvas"},
                verify_delta=False,
            ),
            TrajectoryStep(
                step_id="9_sync_tcl_journal",
                action="run_terminal_command",
                parameters={"command": "tail -n 15 " + os.path.join(VIVADO_DIR, "vivado.jou")},
                assertions=[{"assert_exit_code": 0}],
            ),
        ],
        stop_on_failure=True,
        capture_final_screenshot=True,
    )

    t0 = time.monotonic()
    result = engine.execute_trajectory(spec)
    (time.monotonic() - t0) * 1000.0

    print(f"\nTrajectory Name: {result.trajectory_name}")
    print(f"Overall Success: {result.success}")
    print(f"Total Steps: {result.total_steps}")
    print(f"Executed Steps: {result.executed_steps}")
    print(f"Successful Steps: {result.successful_steps}")
    print(f"Failed Steps: {result.failed_steps}")
    print(f"Elapsed Time: {result.elapsed_ms:.2f} ms ({result.elapsed_ms / 1000.0:.2f} s)")
    print(f"Tokens Saved: {result.estimated_tokens_saved:,} tokens (0 LLM round-trips)")
    print(f"Message: {result.message}\n")

    print("Step Details:")
    print("-" * 65)
    for r in result.step_results:
        print(
            f"[{r.step_id}] {r.action:<24} | Success: {r.success!s:<5} | {r.elapsed_ms:>7.2f} ms | {r.message}"
        )

    # Save artifacts
    brain_dir = ARTIFACT_DIR
    os.makedirs(brain_dir, exist_ok=True)
    if result.final_screenshot_b64:
        screen_bytes = base64.b64decode(result.final_screenshot_b64)
        out_path = os.path.join(brain_dir, "live_trajectory_final_screen.png")
        with open(out_path, "wb") as f:
            f.write(screen_bytes)
        print(f"\nFinal screen saved: {out_path}")

    # Save post-simulation waveform pane
    post_wave_step = next(
        (r for r in result.step_results if r.step_id == "8_capture_final_waves"), None
    )
    if post_wave_step and post_wave_step.success:
        hal.capture_screen()
        # Sub-pane bounding box
        roi = controller.get_active_plugin().get_sub_pane(
            "waveform_canvas", hal.get_active_window().bbox
        )
        if roi:
            crop = hal.capture_screen(region=(roi.x, roi.y, roi.width, roi.height))
            wave_path = os.path.join(brain_dir, "live_trajectory_waveform.png")
            crop.save(wave_path)
            print(f"Waveform sub-pane saved: {wave_path}")

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
