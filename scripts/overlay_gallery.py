"""Screenshots of every overlay look on this Mac, for reviewing UI changes.

    python scripts/overlay_gallery.py --out /tmp/dobby-gallery [--style modern|classic]

Draws the overlay for each state (sleeping, hover, working, thinking, paused,
two Dobbys, tag at the screen edge, risky step) and saves a crop of the top
bar and of the pointer/glow area for each. It only draws: it never clicks,
types or moves the real mouse. Needs Screen Recording permission for the
terminal that runs it (screencapture).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--style", default="modern", choices=("modern", "classic"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    import AppKit
    from Foundation import NSDate, NSRunLoop
    from PIL import Image

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    from dobby.overlay import mac_helper

    lock = os.path.join(tempfile.mkdtemp(), "bar.lock")
    c = mac_helper.OverlayController(
        {"style": args.style, "pill_in_recordings": True, "bar_lock": lock, "speed": 0},
        out=open(os.devnull, "w"),
        interactive=False,
    )
    c._claim_bar()
    c.pill.animate = True  # shimmer and resize animations as in real use

    def pump(secs: float = 0.7) -> None:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(secs))

    screen = AppKit.NSScreen.screens()[0].frame()
    sw, sh = float(screen.size.width), float(screen.size.height)
    scene = (int(sw * 0.18), int(sh * 0.22), int(sw * 0.5), int(sh * 0.5))  # x, y, w, h (top-left)

    def shot(name: str, scene_too: bool = True) -> None:
        pump()
        path = os.path.join(args.out, f"_{name}.png")
        subprocess.run(["screencapture", "-x", "-m", path], check=True)
        img = Image.open(path)
        k = img.size[0] / sw  # pixels per point
        f = c.pill.window.frame()
        bx, bw = float(f.origin.x), float(f.size.width)
        by = sh - float(f.origin.y) - float(f.size.height)
        pad = 14
        img.crop(
            (
                int((bx - pad) * k),
                int(max(0, by - pad) * k),
                int((bx + bw + pad) * k),
                int((by + f.size.height + pad) * k),
            )
        ).save(os.path.join(args.out, f"{name}-bar.png"))
        if scene_too:
            x, y, w, h = scene
            img.crop(
                (int((x - 40) * k), int((y - 40) * k), int((x + w + 60) * k), int((y + h + 60) * k))
            ).save(os.path.join(args.out, f"{name}-scene.png"))
        os.remove(path)
        print("saved", name)

    def asleep() -> None:
        c.last_action = time.monotonic() - 3600
        c._refresh_pill()

    rect = (float(scene[0]), float(scene[1]), float(scene[2]), float(scene[3]))
    asleep()
    shot("1-sleeping", scene_too=False)
    c.on_pill_hover(True)
    shot("2-sleeping-hover", scene_too=False)
    c.on_pill_hover(False)
    c.pill_hovered = False

    # Working: glow around the scene rect, pointer, two clicks for the trail.
    c.glow_rect = rect
    c._glow_sig = None
    c._place_glow(rect, 0)
    for i, (px, py) in enumerate([(0.3, 0.4), (0.5, 0.55), (0.62, 0.7)]):
        pt = (rect[0] + rect[2] * px, rect[1] + rect[3] * py)
        # Like OverlayHAL inside step(): the intent names the step, the move has no verb.
        c.handle({"op": "cursor.move", "x": pt[0], "y": pt[1], "ms": 0})
        c.handle({"op": "intent", "text": "Click ‘Send’" if i == 2 else f"Click ‘Item {i + 1}’"})
        if i < 2:
            c.handle({"op": "cursor.click", "count": 1})
    shot("3-working")
    c.handle({"op": "cursor.state", "state": "thinking"})
    shot("4-thinking")
    c.handle({"op": "cursor.state", "state": "idle"})
    c.set_paused(True)
    shot("5-paused")
    c.resume()
    c.handle({"op": "risk", "level": "danger"})
    shot("6-risky")
    c.handle({"op": "risk", "level": "ok"})

    # Two Dobbys: this one becomes the second (teal) and the bar shows dots.
    c.server_pid = 222
    c._apply_agents(
        [
            {"pid": 111, "name": "antigravity", "started": 1, "color": "#7C5CFF"},
            {"pid": 222, "name": "claude-code", "started": 2, "color": "#14B8A6", "holding": True},
        ]
    )
    c._mark_active()
    c._refresh_pill()
    shot("7-two-dobbys")

    # Tag at the right edge flips to the left of the pointer.
    edge = (sw - 30, rect[1] + 40)
    c.handle({"op": "cursor.move", "x": edge[0], "y": edge[1], "ms": 0})
    c.handle({"op": "intent", "text": "Click ‘Close’"})
    pump()
    path = os.path.join(args.out, "_edge.png")
    subprocess.run(["screencapture", "-x", "-m", path], check=True)
    img = Image.open(path)
    k = img.size[0] / sw
    img.crop(
        (int((sw - 360) * k), int((edge[1] - 60) * k), int(sw * k), int((edge[1] + 80) * k))
    ).save(os.path.join(args.out, "8-tag-at-edge.png"))
    os.remove(path)
    print("saved 8-tag-at-edge")

    for p in c.panels:
        p.close()
    c.pill.close()
    c.bar_owner.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
