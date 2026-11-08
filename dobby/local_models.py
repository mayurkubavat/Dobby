"""Which local model does which job: plug-and-play model roles.

Dobby talks to any model served by Ollama. Instead of one "Gemma" setting,
each job has a role, and each role can point at a different model:

    text         choices and yes/no questions over the text screen state
    vision       yes/no checks on screenshot crops, region grounding, mark labels
    visual_pick  picking a lettered box on a window screenshot (Set-of-Marks)
    triage       classifying dialog text (safe / risky)

A role's model is resolved in this order, first hit wins:

1. environment variable ``DOBBY_MODEL_<ROLE>`` (e.g. ``DOBBY_MODEL_VISION=qwen2.5vl:7b``)
2. runtime overrides from :func:`set_role` (the ``set_model_role`` MCP tool)
3. ``~/.dobby/models.json`` (``{"vision": "qwen2.5vl:7b", "visual_pick": "qwen3-vl:8b"}``),
   re-read when the file changes, so a swap needs no restart
4. ``CONFIG.model_<role>`` when set, else the legacy ``CONFIG.gemma3_*`` defaults

``KNOWN_MODELS`` lists candidates with what Dobby needs to know about them. It
is advice for the benchmark and the docs, never a download list: pull models
yourself after checking ``~/LocalAI/MODELS.md``.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from dobby.config import CONFIG

ROLES = ("text", "vision", "visual_pick", "triage")
ROLE_HELP = {
    "text": "choices and yes/no over the text screen state (decide_next_element, choose_option)",
    "vision": "yes/no on screenshot crops, region grounding, mark labels",
    "visual_pick": "lettered-box picks on a window screenshot (decide_visual_element)",
    "triage": "dialog text classification (safe / risky)",
}
MODELS_FILE = Path(os.path.expanduser("~/.dobby/models.json"))


@dataclass(frozen=True)
class ModelProfile:
    tag: str  # Ollama tag
    family: str
    vision: bool
    approx_gb: float  # download size, roughly
    good_for: str
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


KNOWN_MODELS: list[ModelProfile] = [
    ModelProfile(
        "gemma3:4b",
        "Gemma 3",
        True,
        3.3,
        "current default for every role",
        "overconfident on Set-of-Marks picks; rarely answers 'none'",
    ),
    ModelProfile(
        "gemma3:12b", "Gemma 3", True, 8.1, "harder text decisions", "slow on a 16 GB Mac"
    ),
    ModelProfile(
        "qwen2.5vl:3b",
        "Qwen2.5-VL",
        True,
        3.2,
        "vision checks, small and fast",
        "trained on UI screenshots and grounding",
    ),
    ModelProfile("qwen2.5vl:7b", "Qwen2.5-VL", True, 6.0, "vision checks, visual_pick"),
    ModelProfile(
        "qwen3-vl:4b", "Qwen3-VL", True, 3.3, "vision, visual_pick", "needs a recent Ollama"
    ),
    ModelProfile(
        "qwen3-vl:8b",
        "Qwen3-VL",
        True,
        6.1,
        "visual_pick, strongest open UI reader here",
        "needs a recent Ollama",
    ),
    ModelProfile("minicpm-v:8b", "MiniCPM-V", True, 5.5, "vision checks, OCR-heavy crops"),
    ModelProfile("granite3.2-vision:2b", "Granite Vision", True, 2.4, "document/table crops, tiny"),
    ModelProfile(
        "moondream:1.8b", "Moondream", True, 1.7, "fast yes/no on crops", "weak at multi-choice"
    ),
    ModelProfile(
        "llama3.2-vision:11b", "Llama 3.2 Vision", True, 7.9, "vision checks", "heavy for 16 GB"
    ),
    ModelProfile("qwen3:4b", "Qwen3", False, 2.6, "text role", "text only"),
    ModelProfile(
        "ui-tars-1.5-7b",
        "UI-TARS",
        True,
        5.0,
        "visual_pick (GUI agent model)",
        "not in the Ollama library: import a GGUF with a Modelfile and use your tag",
    ),
]

_lock = threading.Lock()
_overrides: dict[str, str] = {}
_file_cache: dict[str, Any] = {"mtime": None, "data": {}}


def _check(role: str) -> str:
    role = (role or "").strip().lower()
    if role not in ROLES:
        raise ValueError(f"unknown model role '{role}' (roles: {', '.join(ROLES)})")
    return role


def _file_roles(path: Path | None = None) -> dict[str, str]:
    path = path or MODELS_FILE
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    with _lock:
        if _file_cache["mtime"] != (str(path), mtime):
            try:
                data = json.loads(path.read_text())
                data = {
                    k: str(v)
                    for k, v in data.items()
                    if k in ROLES and isinstance(v, str) and v.strip()
                }
            except (OSError, ValueError, AttributeError):
                data = {}
            _file_cache.update(mtime=(str(path), mtime), data=data)
        return dict(_file_cache["data"])


def _config_default(role: str) -> str:
    explicit = getattr(CONFIG, f"model_{role}", "") or ""
    if explicit:
        return explicit
    if role in ("text", "triage"):
        return CONFIG.gemma3_text_model
    return CONFIG.gemma3_vision_model


def model_for(role: str, models_file: Path | None = None) -> str:
    """The Ollama model tag for ``role`` (see the module docstring for precedence)."""
    role = _check(role)
    env = os.environ.get(f"DOBBY_MODEL_{role.upper()}", "").strip()
    if env:
        return env
    with _lock:
        if role in _overrides:
            return _overrides[role]
    from_file = _file_roles(models_file).get(role)
    if from_file:
        return from_file
    return _config_default(role)


def source_of(role: str, models_file: Path | None = None) -> str:
    role = _check(role)
    if os.environ.get(f"DOBBY_MODEL_{role.upper()}", "").strip():
        return "env"
    with _lock:
        if role in _overrides:
            return "runtime"
    if _file_roles(models_file).get(role):
        return "models.json"
    return "config" if getattr(CONFIG, f"model_{role}", "") else "default"


def set_role(role: str, model: str | None) -> str:
    """Runtime override for ``role`` (None or "" clears it). Returns the role's model now."""
    role = _check(role)
    with _lock:
        if model and str(model).strip():
            _overrides[role] = str(model).strip()
        else:
            _overrides.pop(role, None)
    return model_for(role)


def roles_table(installed: set | None = None) -> list[dict[str, Any]]:
    """Each role with its model, where the choice came from, and whether it is installed."""
    rows = []
    for role in ROLES:
        model = model_for(role)
        row = {"role": role, "model": model, "source": source_of(role), "job": ROLE_HELP[role]}
        if installed is not None:
            names = set(installed)
            row["installed"] = model in names or (":" not in model and f"{model}:latest" in names)
        rows.append(row)
    return rows


def profile(tag: str) -> ModelProfile | None:
    return next((p for p in KNOWN_MODELS if p.tag == tag), None)
