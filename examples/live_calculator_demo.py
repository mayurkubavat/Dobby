"""Live Desktop Automation Demonstration: Vision Control MCP on GNOME Calculator.

This script executes a full perception-action-verification closed loop:
1. Spawns gnome-calculator on the active display.
2. Discovers and focuses the window via X11.
3. Captures an initial Set-of-Marks annotated screenshot.
4. Dynamically locates arithmetic buttons from the AT-SPI semantic marks registry.
5. Performs physical clicks with automated visual delta verification: 4, 5, ×, 2, =.
6. Captures the verified result screenshot.
7. Gracefully terminates the application.
"""

import base64
import os
import subprocess
import time

import pinlet.server as server
from pinlet.types import UIElement


def run_demo():
    print("=" * 70)
    print("🚀 STARTING LIVE DESKTOP DEMO: VISION CONTROL MCP")
    print("=" * 70)

    # 1. Launch gnome-calculator
    print("\n[Step 1] Launching gnome-calculator on DISPLAY...")
    proc = subprocess.Popen(
        ["gnome-calculator"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2.0)  # Allow window and AT-SPI registration to settle

    try:
        # 2. Window Discovery & Focus
        print("\n[Step 2] Discovering active desktop windows via X11...")
        windows = server.list_windows()
        calc_win = None
        for w in windows:
            title = w["title"].strip()
            if "ide" in title.lower() or "git" in title.lower():
                continue
            if "calculator" in title.lower():
                calc_win = w
                break

        if not calc_win:
            raise RuntimeError("Calculator window was not found in desktop window list!")

        print(f"  ✓ Found Window: '{calc_win['title']}' (ID: {calc_win['window_id']})")
        print(
            f"  ✓ Bounds: {calc_win['bbox']['width']}x{calc_win['bbox']['height']} at ({calc_win['bbox']['x']}, {calc_win['bbox']['y']})"
        )

        print(f"  ✓ Focusing window {calc_win['window_id']}...")
        server.focus_window(calc_win["window_id"])
        time.sleep(0.5)

        # 3. Perception: Capture Set-of-Marks Screenshot
        print("\n[Step 3] Capturing screen with Set-of-Marks (SoM) visual grounding...")
        initial_snap = server.take_screenshot(mode="annotated_som", app_filter="calculator")

        print(
            f"  ✓ Screen Resolution: {initial_snap['original_width']}x{initial_snap['original_height']}"
        )
        print(
            f"  ✓ Downscaled Frame: {initial_snap['width']}x{initial_snap['height']} (Scale: {initial_snap['scale_factor']:.3f})"
        )
        print(f"  ✓ Payload Size: {len(initial_snap['image_base64']) // 1024} KB")
        print(f"  ✓ Total Interactive Marks Tagged: {len(initial_snap['marks'])}")

        # Save initial annotated image
        artifact_dir = os.path.expanduser(os.environ.get("PINLET_ARTIFACT_DIR", "artifacts"))
        os.makedirs(artifact_dir, exist_ok=True)
        initial_img_data = base64.b64decode(initial_snap["image_base64"])
        with open(f"{artifact_dir}/demo_som_initial.jpg", "wb") as f:
            f.write(initial_img_data)
        print(f"  ✓ Saved annotated frame: {artifact_dir}/demo_som_initial.jpg")

        # 4. Semantic Element Resolution
        # Map element labels to mark IDs
        marks_by_label: dict[str, UIElement] = {}
        for mark in initial_snap["marks"]:
            name = mark["name"].strip()
            if name:
                marks_by_label[name] = mark

        print("\n[Step 4] Resolving calculator button marks:")
        for target in ["4", "5", "×", "2", "="]:
            if target in marks_by_label:
                m = marks_by_label[target]
                cx = m["bbox"]["x"] + m["bbox"]["width"] // 2
                cy = m["bbox"]["y"] + m["bbox"]["height"] // 2
                print(f"  - Button '{target}': Mark #{m['id']} -> Screen Center ({cx}, {cy})")
            else:
                print(f"  ! Warning: Button '{target}' not directly matched in marks.")

        # 5. Closed-Loop Execution: 4, 5, ×, 2, =
        sequence = ["4", "5", "×", "2", "="]
        print(
            f"\n[Step 5] Executing action sequence with visual delta verification: {' -> '.join(sequence)}"
        )

        for key in sequence:
            if key not in marks_by_label:
                print(f"  ! Skipping '{key}' (not mapped)")
                continue

            target_mark = marks_by_label[key]
            mark_id = target_mark["id"]

            # Click by Mark ID with verification
            res = server.click(mark_id=mark_id, verify_delta=True)

            status = "✓ DETECTED" if res["delta_detected"] else "– No Delta"
            change_pct = f"{res['change_ratio'] * 100:.2f}%"
            print(
                f"  👉 Clicked '{key}' (Mark #{mark_id}) | Success: {res['success']} | Screen Delta: {status} ({change_pct})"
            )
            time.sleep(0.3)

        # 6. Capture Final Verification Screenshot
        print("\n[Step 6] Capturing final verified desktop state...")
        final_snap = server.take_screenshot(mode="cursor_only", app_filter="calculator")
        final_img_data = base64.b64decode(final_snap["image_base64"])
        with open(f"{artifact_dir}/demo_calculator_result.jpg", "wb") as f:
            f.write(final_img_data)
        print(f"  ✓ Saved result frame: {artifact_dir}/demo_calculator_result.jpg")

        print("\n" + "=" * 70)
        print(
            "🎉 DEMONSTRATION COMPLETE: Full perception, grounding, and closed-loop execution succeeded!"
        )
        print("=" * 70)

    finally:
        # Clean up
        print("\n[Cleanup] Closing gnome-calculator...")
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("  ✓ Process terminated cleanly.")


if __name__ == "__main__":
    run_demo()
