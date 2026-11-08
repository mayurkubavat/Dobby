"""``pinlet doctor``: check that Pinlet can run here, and say how to fix what is missing.

Each check prints one line: ``ok``, ``warn`` (works, but something is off) or ``fail``
(Pinlet cannot work this way), with a fix for anything not ok. ``--json`` prints the
same for Pinlet.app's Diagnostics. Exit code 1 when any check fails.

Stdlib only and import-light: packages are looked up, not imported, so it runs fast
and works when the desktop backend is broken.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from pinlet import __version__, agent_setup, runtime
from pinlet.hub import paths

REINSTALL = "Open Pinlet.app, or run the Pinlet installer again (scripts/install.sh)"


@dataclass
class Check:
    """One line of the report."""

    name: str
    status: str  # "ok", "warn" or "fail"
    detail: str
    fix: str = ""


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _packages(platform: str) -> list[Check]:
    required = {"mcp": "mcp", "PIL": "pillow", "pydantic": "pydantic", "cv2": "opencv"}
    optional: dict[str, str] = {}
    if platform == "darwin":
        required |= {
            "Quartz": "pyobjc Quartz",
            "ApplicationServices": "pyobjc ApplicationServices",
            "AppKit": "pyobjc Cocoa",
        }
        optional |= {"onnxruntime": "onnxruntime (UI detector)", "Vision": "pyobjc Vision (OCR)"}
    elif platform.startswith("linux"):
        required |= {"Xlib": "python-xlib", "mss": "mss", "pynput": "pynput"}
        optional |= {
            "onnxruntime": "onnxruntime (UI detector)",
            "rapidocr" if sys.version_info >= (3, 13) else "rapidocr_onnxruntime": "rapidocr (OCR)",
        }
        optional["gi"] = "PyGObject (overlay, accessibility tree; apt install python3-gi)"
    missing = [label for mod, label in required.items() if not _has(mod)]
    absent = [label for mod, label in optional.items() if not _has(mod)]
    extras = "macos,ocr" if platform == "darwin" else "linux,ocr"
    fix = REINSTALL if runtime.is_managed() else f"uv pip install -e '.[{extras}]' in the checkout"
    out = [
        Check(
            "Packages",
            "fail" if missing else "ok",
            f"missing: {', '.join(missing)}" if missing else "desktop backend installed",
            fix if missing else "",
        )
    ]
    if absent:
        out.append(Check("Optional packages", "warn", f"missing: {', '.join(absent)}", fix))
    return out


def _listening(path: Path) -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            sock.connect(str(path))
            return True
    except OSError:
        return False


def _app_config(platform: str) -> list[Check]:
    """The Python Pinlet.app starts, in the app's own order (see EngineConfig.resolve)."""
    if platform != "darwin":
        return []
    custom = os.environ.get("PINLET_PYTHON", "")
    if custom and os.access(os.path.expanduser(custom), os.X_OK):
        return [Check("Pinlet.app engine", "ok", f"{custom} (PINLET_PYTHON)")]
    own = runtime.runtime_python()
    if os.access(own, os.X_OK):
        return [Check("Pinlet.app engine", "ok", f"{own} (Pinlet's own runtime)")]
    path = paths.app_config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    python = str(data.get("python") or "")
    if python and not os.access(python, os.X_OK):
        return [
            Check(
                "Pinlet.app engine",
                "fail",
                f"{path} points at {python}, which is gone",
                "Run any AI tool once, or reinstall from Pinlet.app's Welcome window",
            )
        ]
    return [Check("Pinlet.app engine", "ok", python)]


def _ai_tools(home: Path) -> list[Check]:
    out: list[Check] = []
    for entry in agent_setup.tool_status(home):
        if not entry["installed"]:
            continue
        label, key, state = str(entry["label"]), str(entry["key"]), str(entry["state"])
        command = " ".join(entry["command"] or [])  # type: ignore[arg-type]
        if state == "connected":
            stable = entry["stable"]
            out.append(
                Check(
                    label,
                    "ok" if stable else "warn",
                    "connected" + ("" if stable else f" through {command}"),
                    "" if stable else "pinlet setup --repair (uses the stable launcher)",
                )
            )
        elif state == "broken":
            out.append(
                Check(
                    label, "fail", f"its Pinlet command is gone: {command}", "pinlet setup --repair"
                )
            )
        else:
            out.append(Check(label, "warn", "not set up", f"pinlet setup --only {key}"))
    return out


def checks(home: Path | None = None, platform: str = sys.platform) -> list[Check]:
    """Runs every check."""
    home = home or Path.home()
    py = sys.version_info
    where = "Pinlet's own runtime" if runtime.is_managed() else "a checkout or your own venv"
    out = [
        Check(
            "Python",
            "ok" if py >= (3, 10) else "fail",
            f"{py.major}.{py.minor}.{py.micro} at {sys.executable} ({where})",
            "" if py >= (3, 10) else "Pinlet needs Python 3.10 or newer",
        ),
        Check("Pinlet", "ok", f"{__version__} from {Path(__file__).resolve().parent}"),
    ]
    launcher = runtime.launcher_path()
    if platform.startswith("win"):
        pass
    elif runtime.launcher_works(launcher):
        targets = runtime.launcher_targets(launcher)
        live = next((t for t in targets if os.access(t, os.X_OK)), "")
        out.append(Check("Launcher", "ok", f"{launcher} runs {live}"))
    elif launcher.exists() and not runtime.launcher_targets(launcher):
        out.append(Check("Launcher", "warn", f"{launcher} was not written by Pinlet", ""))
    else:
        out.append(
            Check(
                "Launcher",
                "fail",
                f"{launcher} is missing or none of its Pythons exist",
                "pinlet setup",
            )
        )
    out += _packages(platform)
    model = Path(__file__).resolve().parent / "models" / "ui_detector_fast.onnx"
    out.append(
        Check("UI detector model", "ok", str(model))
        if model.is_file()
        else Check("UI detector model", "warn", f"{model} is missing", REINSTALL)
    )
    skills = agent_setup.skills_dir()
    out.append(
        Check("Skills", "ok", str(skills))
        if skills
        else Check("Skills", "warn", "no skills folder in this install", REINSTALL)
    )
    if platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        out.append(
            Check("Display", "warn", "no DISPLAY set", "run from a desktop session, or set DISPLAY")
        )
    sock = paths.socket_path()
    out.append(
        Check(
            "Shared Pinlet",
            "ok",
            f"running at {sock}" if _listening(sock) else "not running (it starts when needed)",
        )
    )
    out += _app_config(platform)
    out += _ai_tools(home)
    return out


_MARK = {"ok": "ok  ", "warn": "warn", "fail": "FAIL"}


def report(items: Sequence[Check]) -> str:
    """One line per check, with the fix indented under anything not ok."""
    lines = []
    for c in items:
        lines.append(f"{_MARK[c.status]}  {c.name:<28} {c.detail}")
        if c.fix:
            lines.append(f"{'':<35}fix: {c.fix}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Runs ``pinlet doctor``. Returns 1 when a check fails."""
    parser = argparse.ArgumentParser(prog="pinlet doctor", description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print the checks as JSON")
    parser.add_argument("--home", default="", help=argparse.SUPPRESS)
    args = parser.parse_args(list(argv or []))
    home = Path(args.home).expanduser() if args.home else None
    items = checks(home)
    failed = any(c.status == "fail" for c in items)
    if args.json:
        print(json.dumps({"ok": not failed, "checks": [asdict(c) for c in items]}, indent=2))
    else:
        print(report(items))
    return 1 if failed else 0
