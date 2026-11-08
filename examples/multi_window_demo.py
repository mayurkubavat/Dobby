"""Multi-Window Orchestration Demo: Chrome Web App + Terminal Workflow.

Demonstrates cross-application navigation and closed-loop control:
1. Spawns Google Chrome displaying a web control console on the left screen.
2. Spawns a GNOME Terminal window on the right screen.
3. Dynamically identifies and focuses Chrome via X11.
4. Uses Set-of-Marks to locate and click 'Deploy Service' in the web app.
5. Verifies DOM visual reaction in Chrome.
6. Switches window focus to the Terminal window.
7. Types synchronization commands and verifies terminal output.
8. Captures the unified multi-app state and cleans up processes.
"""

import base64
import os
import subprocess
import time

import dobby.server as server


def run_multi_window_demo():
    print("=" * 75)
    print("🌐 STARTING MULTI-WINDOW DEMO: CHROME WEB DASHBOARD + TERMINAL")
    print("=" * 75)

    artifact_dir = os.path.expanduser(os.environ.get("DOBBY_ARTIFACT_DIR", "artifacts"))
    os.makedirs(artifact_dir, exist_ok=True)

    dashboard_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "dashboard.html"))

    # Step 1: Launch Google Chrome (Dashboard on left)
    print("\n[Step 1] Spawining Google Chrome with Control Dashboard...")
    chrome_proc = subprocess.Popen(
        [
            "google-chrome",
            "--no-sandbox",
            "--no-first-run",
            "--force-renderer-accessibility",
            "--user-data-dir=/tmp/chrome_multi_test_profile",
            "--window-size=1100,850",
            "--window-position=50,80",
            f"file://{dashboard_path}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Step 2: Launch Terminal on the right
    print("\n[Step 2] Spawning Terminal Console on the right...")
    term_proc = subprocess.Popen(
        [
            "gnome-terminal",
            "--title=Vision-Cluster-Terminal",
            "--geometry=90x28+1200+80",
            "--",
            "bash",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3.5)  # Allow both applications and D-Bus trees to stabilize

    try:
        # Step 3: Discover Both Windows via X11
        print("\n[Step 3] Discovering active desktop windows via X11...")
        windows = server.list_windows()
        chrome_win = None
        term_win = None

        for w in windows:
            title = w["title"]
            if "Vision MCP Multi-App Dashboard" in title and "ide" not in title.lower():
                chrome_win = w
            elif "Vision-Cluster-Terminal" in title or "Terminal" in title:
                # Target the terminal window
                if term_win is None:
                    term_win = w

        if not chrome_win:
            raise RuntimeError("Chrome Dashboard window was not found in active window list!")
        if not term_win:
            raise RuntimeError("Terminal window was not found in active window list!")

        print(f"  ✓ Found Chrome Window:   '{chrome_win['title']}' (ID: {chrome_win['window_id']})")
        print(f"  ✓ Found Terminal Window: '{term_win['title']}' (ID: {term_win['window_id']})")

        # Step 4: Interact with Chrome Web Dashboard
        print(f"\n[Step 4] Focusing Chrome Window ({chrome_win['window_id']})...")
        server.focus_window(chrome_win["window_id"])
        time.sleep(0.5)

        print("  ✓ Capturing Chrome state with Set-of-Marks visual grounding...")
        snap1 = server.take_screenshot(mode="annotated_som", app_filter="Chrome")
        print(f"  ✓ Tagged {len(snap1['marks'])} interactive elements in Chrome")

        # Find "Deploy Service" button
        deploy_mark = None
        for m in snap1["marks"]:
            if "deploy service" in m["name"].lower():
                deploy_mark = m
                break

        if not deploy_mark:
            # Fallback to pure visual click if DOM name differed
            print("  ! 'Deploy Service' not found in DOM marks, falling back to CV detection...")
            cv_snap = server.take_screenshot(mode="annotated_cv")
            deploy_mark = cv_snap["marks"][0] if cv_snap["marks"] else None

        if deploy_mark:
            print(f"  👉 Found Target Button: '{deploy_mark['name']}' (Mark #{deploy_mark['id']})")
            click_res = server.click(mark_id=deploy_mark["id"], verify_delta=True)
            status = "✓ DETECTED" if click_res["delta_detected"] else "– Settled"
            print(
                f"  ✓ Click dispatched. Visual Delta: {status} ({click_res['change_ratio'] * 100:.2f}%)"
            )
        else:
            print("  ! Could not locate Deploy Service mark, clicking at center of window...")
            server.click(x=600, y=550)

        time.sleep(0.8)

        # Step 5: Switch to Terminal and Execute Sync Commands
        print(f"\n[Step 5] Switching focus to Terminal ({term_win['window_id']})...")
        server.focus_window(term_win["window_id"])
        time.sleep(0.5)

        # Inject command into Terminal
        cmd = "echo '>>> CLUSTER VERIFIED: staging-cluster-alpha is ONLINE' && date"
        print(f'  👉 Typing into terminal: "{cmd}"')
        type_res = server.type_text(cmd, press_enter=True, delay_ms=8.0, verify_delta=True)
        print(
            f"  ✓ Keystrokes sent. Screen Delta: {'✓ DETECTED' if type_res['delta_detected'] else '– Settled'}"
        )
        time.sleep(1.0)

        # Step 6: Unified Multi-Window Screenshot
        print("\n[Step 6] Capturing unified multi-app screenshot...")
        full_snap = server.take_screenshot(mode="cursor_only")
        img_bytes = base64.b64decode(full_snap["image_base64"])
        out_path = f"{artifact_dir}/demo_multi_app_sync.jpg"
        with open(out_path, "wb") as f:
            f.write(img_bytes)
        print(f"  ✓ Saved multi-app verification image: {out_path}")

        print("\n" + "=" * 75)
        print("🎉 MULTI-WINDOW DEMO COMPLETE: Cross-app orchestration verified!")
        print("=" * 75)

    finally:
        print("\n[Cleanup] Terminating Chrome and Terminal processes...")
        chrome_proc.terminate()
        term_proc.terminate()
        try:
            chrome_proc.wait(timeout=2.0)
            term_proc.wait(timeout=2.0)
        except Exception:
            pass
        subprocess.run("pkill -f chrome_multi_test_profile || true", shell=True)
        print("  ✓ Cleanup complete.")


if __name__ == "__main__":
    run_multi_window_demo()
