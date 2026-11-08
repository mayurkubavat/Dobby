"""Deterministic Mock & Replay HAL for Headless CI and Cassette Testing.

Cassettes are a directory (or zip) with index.json plus optional WebP frames,
or a single JSON document. When transitions are present, an input call must
match the next (action, args) pair or the replay fails.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from dobby.hal.errors import CassetteMismatch
from dobby.types import BoundingBox, DisplayTopology, ExecutionPhase, WindowInfo


@dataclass
class Transition:
    """One recorded (pre state, action, post state) step."""

    pre_frame_id: str
    action: str
    args: dict[str, Any]
    post_frame_id: str


@dataclass
class ScreenFrameState:
    """A discrete recorded snapshot in a deterministic state cassette."""

    frame_id: str
    ocr_boxes: list[dict[str, Any]] = field(default_factory=list)
    ax_tree: dict[str, Any] = field(default_factory=dict)
    ax_diff: dict[str, Any] = field(default_factory=dict)
    screen_size: tuple[int, int] = (1920, 1080)
    cursor_pos: tuple[int, int] = (100, 100)
    windows: list[WindowInfo] = field(default_factory=list)
    active_window: WindowInfo | None = None
    pixel_delta_from_prev: float = 0.0
    image_path: str | None = None
    phase: str = "QUIESCENT"


def _default_frame() -> ScreenFrameState:
    window = WindowInfo(
        window_id="0x1000",
        title="Mock Terminal",
        bbox=BoundingBox(x=50, y=50, width=800, height=600),
        is_active=True,
    )
    return ScreenFrameState(
        frame_id="init",
        screen_size=(1920, 1080),
        cursor_pos=(0, 0),
        windows=[window],
        active_window=window,
    )


class MockReplayHAL:
    """Cassette-driven Desktop HAL.
    Implements the capture, input, window, AX, and quiescence protocols.
    """

    is_available = True

    def __init__(
        self,
        states: list[ScreenFrameState] | None = None,
        transitions: list[Transition] | None = None,
        topology: DisplayTopology | None = None,
    ) -> None:
        if states is None:
            states = [_default_frame()]
        if len(states) == 0:
            raise ValueError("cassette has no frames")
        self.states: list[ScreenFrameState] = states
        self.transitions: list[Transition] = list(transitions or [])
        self.topology = topology
        self.cursor: int = 0
        self._transition_i: int = 0
        self.actions_dispatched: list[tuple[str, dict[str, Any]]] = []
        self._auto_advance: bool = not self.transitions

    @property
    def current_state(self) -> ScreenFrameState:
        return self.states[self.cursor]

    def set_auto_advance(self, auto_advance: bool) -> None:
        """Enable frame advance on every action. Ignored while transitions are bound."""
        if not self.transitions:
            self._auto_advance = auto_advance

    def advance_frame(self) -> None:
        """Manually advance to the next recorded state frame if available."""
        if self.cursor < len(self.states) - 1:
            self.cursor += 1

    def _goto(self, frame_id: str) -> None:
        for i, frame in enumerate(self.states):
            if frame.frame_id == frame_id:
                self.cursor = i
                return
        raise CassetteMismatch(frame_id, [f.frame_id for f in self.states])

    def _note(self, action: str, args: dict[str, Any]) -> None:
        self.actions_dispatched.append((action, args))
        if self.transitions:
            if self._transition_i >= len(self.transitions):
                raise CassetteMismatch("end of cassette", (action, args))
            expected = self.transitions[self._transition_i]
            if self.current_state.frame_id != expected.pre_frame_id:
                raise CassetteMismatch(expected.pre_frame_id, self.current_state.frame_id)
            if expected.action != action or expected.args != args:
                raise CassetteMismatch((expected.action, expected.args), (action, args))
            self._goto(expected.post_frame_id)
            self._transition_i += 1
            return
        if "x" in args and "y" in args:
            self.current_state.cursor_pos = (int(args["x"]), int(args["y"]))
        elif "end_x" in args:
            self.current_state.cursor_pos = (int(args["end_x"]), int(args["end_y"]))
        if self._auto_advance:
            self.advance_frame()

    def get_screen_size(self) -> tuple[int, int]:
        return self.current_state.screen_size

    def capture_screen(self, region: tuple[int, int, int, int] | None = None) -> Image.Image:
        path = self.current_state.image_path
        if path and Path(path).is_file():
            img = Image.open(path).convert("RGB")
        else:
            w, h = self.current_state.screen_size
            img = Image.new("RGB", (w, h), color=(30, 30, 30))
        if region:
            x, y, rw, rh = region
            return img.crop((x, y, x + rw, y + rh))
        return img

    def get_cursor_position(self) -> tuple[int, int]:
        return self.current_state.cursor_pos

    def mouse_move(self, x: int, y: int) -> None:
        self._note("mouse_move", {"x": x, "y": y})

    def mouse_click(self, x: int, y: int, button: str = "left", click_count: int = 1) -> None:
        self._note(
            "mouse_click",
            {"x": x, "y": y, "button": button, "click_count": click_count},
        )

    def mouse_drag(
        self, start_x: int, start_y: int, end_x: int, end_y: int, duration_sec: float = 0.3
    ) -> None:
        self._note(
            "mouse_drag",
            {
                "start_x": start_x,
                "start_y": start_y,
                "end_x": end_x,
                "end_y": end_y,
                "duration_sec": duration_sec,
            },
        )

    def mouse_scroll(self, dx: int, dy: int) -> None:
        self._note("mouse_scroll", {"dx": dx, "dy": dy})

    def keyboard_type(self, text: str, delay_ms: float = 12.0) -> None:
        self._note("keyboard_type", {"text": text, "delay_ms": delay_ms})

    def keyboard_press_key(self, key_name: str) -> None:
        self._note("keyboard_press_key", {"key_name": key_name})

    def keyboard_hotkey(self, keys: list[str]) -> None:
        self._note("keyboard_hotkey", {"keys": list(keys)})

    def list_windows(self) -> list[WindowInfo]:
        return self.current_state.windows

    def get_active_window(self) -> WindowInfo | None:
        return self.current_state.active_window

    def focus_window(self, window_id: str) -> bool:
        if self.transitions:
            self._note("focus_window", {"window_id": window_id})
        else:
            self.actions_dispatched.append(("focus_window", {"window_id": window_id}))
        for win in self.current_state.windows:
            if win.window_id == window_id:
                win.is_active = True
                self.current_state.active_window = win
                return True
        return False

    def get_accessibility_tree(
        self,
        app_filter: str | None = None,
        max_depth: int | None = None,
        diff_only: bool = False,
    ) -> dict[str, Any]:
        if diff_only:
            return self.current_state.ax_diff or {"changed": False, "nodes": []}
        return self.current_state.ax_tree or {"role": "AXApplication", "children": []}

    def read_screen_text(self, contains: str | None = None) -> dict[str, Any]:
        """Queries recorded OCR boxes in the active frame."""
        boxes = self.current_state.ocr_boxes
        if contains:
            matched = [b for b in boxes if contains.lower() in str(b.get("text", "")).lower()]
            return {"count": len(matched), "lines": matched}
        return {"count": len(boxes), "lines": boxes}

    def arm(self) -> None:
        return None

    def consume_damage_area(self) -> int:
        return int(self.current_state.pixel_delta_from_prev)

    def get_cursor_state(self) -> tuple[str, int]:
        return ("arrow", 0)

    def wait_for_quiescence(
        self,
        quiescence_window_ms: int = 180,
        initial_timeout_ms: int = 1500,
        max_total_timeout_ms: int = 15000,
        poll_interval_sec: float = 0.025,
    ) -> tuple[ExecutionPhase, float, int]:
        """Returns the recorded phase and pixel delta. Does not sleep."""
        try:
            phase = ExecutionPhase(self.current_state.phase)
        except ValueError:
            phase = ExecutionPhase.QUIESCENT
        return (phase, 0.0, int(self.current_state.pixel_delta_from_prev))

    @classmethod
    def load_cassette(cls, path_or_json: str | Path) -> MockReplayHAL:
        """Loads a state cassette from a directory, zip, JSON file, or JSON string."""
        if isinstance(path_or_json, Path) or (
            isinstance(path_or_json, str)
            and (path_or_json.endswith((".json", ".halpack")) or Path(path_or_json).exists())
        ):
            path = Path(path_or_json)
            if path.is_dir():
                return cls._from_payload(
                    json.loads((path / "index.json").read_text(encoding="utf-8")), path
                )
            if zipfile.is_zipfile(path):
                import tempfile

                dest = Path(tempfile.mkdtemp(prefix="halpack-"))
                with zipfile.ZipFile(path) as zf:
                    zf.extractall(dest)
                index = dest / "index.json"
                return cls._from_payload(json.loads(index.read_text(encoding="utf-8")), dest)
            content = path.read_text(encoding="utf-8")
            base = path.parent
        else:
            content = str(path_or_json)
            base = Path(".")
        return cls._from_payload(json.loads(content), base)

    @classmethod
    def _from_payload(cls, data: dict[str, Any], base: Path) -> MockReplayHAL:
        raw_frames = data.get("frames", [])
        frames: list[ScreenFrameState] = []
        if isinstance(raw_frames, dict):
            items = list(raw_frames.values())
        else:
            items = list(raw_frames)
        for f in items:
            wins = [WindowInfo(**w) for w in f.get("windows", [])]
            act = WindowInfo(**f["active_window"]) if f.get("active_window") else None
            image_path = f.get("image_path")
            if image_path and not Path(image_path).is_absolute():
                image_path = str((base / image_path).resolve())
            frames.append(
                ScreenFrameState(
                    frame_id=f.get("frame_id", "f"),
                    ocr_boxes=f.get("ocr_boxes", []),
                    ax_tree=f.get("ax_tree", {}),
                    ax_diff=f.get("ax_diff", {}),
                    screen_size=tuple(f.get("screen_size", (1920, 1080))),
                    cursor_pos=tuple(f.get("cursor_pos", (0, 0))),
                    windows=wins,
                    active_window=act,
                    pixel_delta_from_prev=f.get("pixel_delta_from_prev", 0.0),
                    image_path=image_path,
                    phase=f.get("phase", "QUIESCENT"),
                )
            )
        transitions = [
            Transition(
                pre_frame_id=t["pre_frame_id"],
                action=t["action"],
                args=dict(t.get("args") or {}),
                post_frame_id=t["post_frame_id"],
            )
            for t in data.get("transitions", [])
        ]
        topology = None
        if data.get("topology"):
            topology = DisplayTopology(**data["topology"])
        return cls(states=frames, transitions=transitions, topology=topology)


def load_cassette(path_or_json: str | Path) -> MockReplayHAL:
    return MockReplayHAL.load_cassette(path_or_json)


class CassetteRecorder:
    """Wraps a live session and records (pre, action, post) transitions in memory."""

    def __init__(self, session: Any) -> None:
        self.session = session
        self.frames: list[dict[str, Any]] = []
        self.transitions: list[Transition] = []
        self._seq = 0

    def _snapshot(self) -> str:
        frame_id = f"f{self._seq}"
        self._seq += 1
        ax: dict[str, Any] = {}
        if hasattr(self.session, "get_accessibility_tree"):
            try:
                ax = self.session.get_accessibility_tree(diff_only=False)
            except Exception:
                ax = {}
        size = (0, 0)
        if hasattr(self.session, "get_screen_size"):
            try:
                size = tuple(self.session.get_screen_size())
            except Exception:
                size = (0, 0)
        self.frames.append({"frame_id": frame_id, "ax_tree": ax, "screen_size": list(size)})
        return frame_id

    def run(self, action: str, args: dict[str, Any]) -> str:
        pre = self._snapshot()
        method = getattr(self.session, action)
        method(**args)
        post = self._snapshot()
        self.transitions.append(Transition(pre, action, dict(args), post))
        return post
