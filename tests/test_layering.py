"""Keeps the package layering from drifting.

Each area of ``pinlet`` may import, at module top level, only the areas listed
for it in ``ALLOWED``. A new edge has to be added here on purpose (and to the
"Layers" section of CONTRIBUTING.md), or made a function-local import with a
comment saying why, which is how the existing upward references are written.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "pinlet"

# Leaf modules: stdlib (and Pillow/numpy for imagehash and the icons) only, so
# they can be imported before any desktop backend, and by everything else.
LEAVES = {
    "envflags": set(),
    "imagehash": set(),
    "skills": set(),
    "cli": set(),
    "runtime": set(),
    "overlay.text": set(),
    "overlay.palette": set(),
    "overlay.geometry": set(),
    "overlay.protocol": set(),
    "overlay.menu": {"overlay.text"},
    "overlay.topbar_icons": {"overlay.palette"},
}

# area -> areas it may import at top level. Single-file modules are their own area.
ALLOWED = {
    "types": set(),
    "config": set(),
    "envflags": set(),
    "imagehash": set(),
    "skills": set(),
    "runtime": set(),
    "cli": set(),
    "personality": set(),
    "handoff": set(),
    "journal": set(),
    "clients": set(),
    "triage": set(),
    "agent_browser": set(),
    "model_bench": set(),
    "__main__": {"cli"},
    "mode": {"envflags"},
    "policy": {"envflags"},
    "local_models": {"config"},
    "toolset": set(),
    "parentwatch": {"control"},
    "agent_setup": {"runtime", "skills", "pinlet"},
    "doctor": {"hub", "pinlet"},
    "server_support": set(),
    "hal": {"config", "types"},
    "gemma": {"config"},
    "screen_state": {"config"},
    "plugins": {"types"},
    "perception": {"hal", "config", "types"},
    "crystallization": {"hal", "config", "types"},
    "vdom": {"gemma", "config", "imagehash", "types"},
    "control": {"envflags"},
    "hub": {"envflags"},
    "overlay": {"envflags", "mode", "personality"},
    "adk": {"gemma", "perception", "config"},
    # hub is here for hub.per_session only (per-agent state for the shared Pinlet).
    "agent_loop": {
        "crystallization",
        "hal",
        "hub",
        "perception",
        "plugins",
        "vdom",
        "config",
        "handoff",
        "imagehash",
        "types",
    },
    "server": {
        "agent_loop",
        "control",
        "hal",
        "config",
        "mode",
        "server_support",
        "toolset",
        "types",
    },
}


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _top_level_imports(path: Path) -> set[str]:
    """Dotted names under ``pinlet`` that this file imports at module top level."""
    package = list(path.relative_to(ROOT).parent.parts)
    found: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                prefix = ".".join(["pinlet", *base])
                module = f"{prefix}.{node.module}" if node.module else prefix
            else:
                module = node.module or ""
            # "from . import a, b" names modules; "from .a import x" names things in a module
            names = [f"{module}.{a.name}" for a in node.names] if not node.module else [module]
        else:
            continue
        for name in names:
            if name == "pinlet" or name.startswith("pinlet."):
                found.add(name.removeprefix("pinlet").lstrip(".") or "pinlet")
    return found


def _area(module: str) -> str:
    return module.split(".")[0] if module else "pinlet"


def test_top_level_imports_follow_the_layers():
    problems = []
    for path in sorted(ROOT.rglob("*.py")):
        module = _module_name(path)
        if not module:
            continue  # pinlet/__init__.py
        area = _area(module)
        assert area in ALLOWED, f"new area pinlet.{area}: add it to ALLOWED in {__file__}"
        for target in _top_level_imports(path):
            if _area(target) not in ALLOWED[area] | {area}:
                problems.append(f"pinlet.{module} imports pinlet.{target} at top level")
    assert not problems, "\n".join(problems)


def test_leaf_modules_stay_leaves():
    problems = []
    for leaf, allowed in LEAVES.items():
        path = ROOT / (leaf.replace(".", "/") + ".py")
        for target in _top_level_imports(path) - allowed:
            problems.append(f"pinlet.{leaf} imports pinlet.{target} at top level")
    assert not problems, "\n".join(problems)
