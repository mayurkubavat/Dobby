"""Record live sessions as replayable cassettes.

A ``LiveCassetteRecorder`` is attached to a ``DesktopSession``. Every input call
on the session (click, type, key, hotkey, drag, scroll, move, focus) is
followed by a settle delay and a snapshot of what the screen looked like:
AX nodes, OCR lines, windows, cursor, and optionally a JPEG frame.

``stop()`` writes ``<out_dir>/<name>/index.json`` in the format that
``MockReplayHAL.load_cassette`` reads, so the recording can be replayed in CI
(``pytest -m replay``), used as ``DESKTOP_HAL=replay``, or passed to
``crystallize_skill(cassette_path=...)``.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat

# Snapshot callback: returns a frame dict (without frame_id) and an optional image.
SnapshotFn = Callable[[], "tuple[dict[str, Any], Image.Image | None]"]

INPUT_ACTIONS = (
    "mouse_move",
    "mouse_click",
    "mouse_drag",
    "mouse_scroll",
    "keyboard_type",
    "keyboard_press_key",
    "keyboard_hotkey",
    "focus_window",
)


def sanitize_name(name: str) -> str:
    """Cassette folder names are limited to [a-z0-9_-]."""
    clean = re.sub(r"[^a-z0-9_-]+", "_", (name or "").strip().lower()).strip("_")
    return clean[:64] or "cassette"


def pixel_delta(a: Image.Image | None, b: Image.Image | None) -> float:
    """Fraction of pixels (0..1, coarse) that changed between two frames."""
    if a is None or b is None:
        return 0.0
    if a.size != b.size:
        return 1.0
    small = (max(1, a.width // 8), max(1, a.height // 8))
    diff = ImageChops.difference(a.convert("L").resize(small), b.convert("L").resize(small))
    mask = diff.point(lambda v: 255 if v > 16 else 0)
    return round(ImageStat.Stat(mask).mean[0] / 255.0, 4)


class LiveCassetteRecorder:
    """Collects (pre frame, action, args, post frame) transitions from a live session."""

    def __init__(
        self,
        name: str,
        out_dir: str | Path,
        snapshot_fn: SnapshotFn,
        capture_images: bool = False,
        settle_ms: int = 400,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.name = sanitize_name(name)
        self.root = Path(out_dir).expanduser() / self.name
        self.snapshot_fn = snapshot_fn
        self.capture_images = capture_images
        self.settle_ms = max(0, int(settle_ms))
        self._sleep = sleep
        self.frames: list[dict[str, Any]] = []
        self.transitions: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self._images: dict[str, Image.Image] = {}
        self._last_image: Image.Image | None = None
        self.started_at: float | None = None
        self.active = False

    # ------------------------------------------------------------------ frames

    def _snapshot(self) -> str:
        frame_id = f"f{len(self.frames):03d}"
        try:
            frame, image = self.snapshot_fn()
        except Exception as exc:  # a broken snapshot must not break the user's action
            self.errors.append(f"{frame_id}: {type(exc).__name__}: {exc}")
            frame, image = {}, None
        frame = dict(frame)
        frame["frame_id"] = frame_id
        frame.setdefault("phase", "QUIESCENT")
        frame["pixel_delta_from_prev"] = (
            pixel_delta(self._last_image, image) if self.frames else 0.0
        )
        if image is not None:
            self._last_image = image
            if self.capture_images:
                self._images[frame_id] = image
                frame["image_path"] = f"frames/{frame_id}.jpg"
        self.frames.append(frame)
        return frame_id

    # ----------------------------------------------------------------- control

    def start(self) -> dict[str, Any]:
        if self.root.exists() and (self.root / "index.json").exists():
            raise FileExistsError(f"cassette '{self.name}' already exists at {self.root}")
        self.started_at = time.time()
        self.active = True
        first = self._snapshot()
        return {"name": self.name, "path": str(self.root), "first_frame": first}

    def after_action(self, action: str, args: dict[str, Any]) -> str | None:
        """Called by the session after an input action has been sent."""
        if not self.active or not self.frames:
            return None
        pre = self.frames[-1]["frame_id"]
        if self.settle_ms:
            self._sleep(self.settle_ms / 1000.0)
        post = self._snapshot()
        self.transitions.append(
            {"pre_frame_id": pre, "action": action, "args": dict(args), "post_frame_id": post}
        )
        return post

    def payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "recorded_at": self.started_at,
            "frames": self.frames,
            "transitions": self.transitions,
        }

    def stop(self) -> dict[str, Any]:
        """Writes the cassette folder and returns a summary."""
        self.active = False
        self.root.mkdir(parents=True, exist_ok=True)
        if self._images:
            frames_dir = self.root / "frames"
            frames_dir.mkdir(exist_ok=True)
            for frame_id, img in self._images.items():
                img.convert("RGB").save(frames_dir / f"{frame_id}.jpg", quality=70)
        index = self.root / "index.json"
        index.write_text(json.dumps(self.payload(), indent=1, default=str), encoding="utf-8")
        return {
            "name": self.name,
            "path": str(self.root),
            "index": str(index),
            "frames": len(self.frames),
            "transitions": len(self.transitions),
            "images": len(self._images),
            "errors": self.errors,
        }
