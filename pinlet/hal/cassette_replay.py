"""Replay a recorded cassette and check its expectations.

A cassette folder holds ``index.json`` (frames + transitions) and may hold
``expect.json``:

    {
      "text_present": ["Synthesis Completed"],   # OCR text in the final frame
      "text_absent": ["Error"],
      "ax_names_present": ["Run Synthesis"],     # AX node names in the final frame
      "final_frame": "f004"
    }

Used by ``tests/test_cassette_replay.py`` (``pytest -m replay``) and
``scripts/replay_cassette.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pinlet.hal.errors import CassetteMismatch
from pinlet.hal.mock_replay import MockReplayHAL, load_cassette


def _expectations(path: Path) -> dict[str, Any]:
    folder = path if path.is_dir() else path.parent
    exp = folder / "expect.json"
    if exp.is_file():
        return json.loads(exp.read_text(encoding="utf-8"))
    return {}


def _ax_names(hal: MockReplayHAL) -> list[str]:
    tree = hal.get_accessibility_tree()
    names: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("name"):
                names.append(str(node["name"]))
            for key in ("nodes", "children"):
                for child in node.get(key) or []:
                    walk(child)

    walk(tree)
    return names


def replay_cassette(path: str | Path) -> dict[str, Any]:
    """Replays every transition through the HAL input API, then checks expect.json.

    Argument shapes are checked exactly as a live agent would call them.

    Returns {"ok", "steps", "final_frame", "failures": [...]}.
    """
    path = Path(path)
    hal = load_cassette(path)
    failures: list[str] = []
    steps = 0
    for t in list(hal.transitions):
        try:
            getattr(hal, t.action)(**t.args)
        except CassetteMismatch as exc:
            failures.append(f"step {steps} {t.action}: {exc}")
            break
        except (AttributeError, TypeError) as exc:
            failures.append(f"step {steps} {t.action}: not a HAL input call ({exc})")
            break
        steps += 1

    expect = _expectations(path)
    final = hal.current_state.frame_id
    if not failures:
        if expect.get("final_frame") and expect["final_frame"] != final:
            failures.append(f"final frame is {final}, expected {expect['final_frame']}")
        for text in expect.get("text_present", []):
            if not hal.read_screen_text(contains=text)["count"]:
                failures.append(f"text not on final frame: {text!r}")
        for text in expect.get("text_absent", []):
            if hal.read_screen_text(contains=text)["count"]:
                failures.append(f"text unexpectedly on final frame: {text!r}")
        names = [n.lower() for n in _ax_names(hal)]
        for want in expect.get("ax_names_present", []):
            if not any(want.lower() in n for n in names):
                failures.append(f"AX name not on final frame: {want!r}")
    return {
        "cassette": str(path),
        "ok": not failures,
        "steps": steps,
        "total_steps": len(hal.transitions),
        "final_frame": final,
        "failures": failures,
    }


def discover_cassettes(root: str | Path) -> list[Path]:
    """Folders under ``root`` that contain index.json, plus *.halpack files."""
    root = Path(root).expanduser()
    if not root.is_dir():
        return []
    found = sorted(p.parent for p in root.glob("*/index.json"))
    found += sorted(root.glob("*.halpack"))
    return found
