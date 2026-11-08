"""Dynamic Skill Registry and Storage.

Persists crystallized parameterized skills and renders their execution templates via Jinja2.
"""

import json
import logging
import os
from typing import Any

import jinja2

from dobby.config import CONFIG
from dobby.types import CrystallizedMacro, CrystallizedSkillSpec

logger = logging.getLogger(__name__)


class DynamicSkillRegistry:
    """Manages registration, disk persistence, and template rendering for crystallized skills."""

    def __init__(self, storage_path: str | None = None) -> None:
        raw_path = storage_path or CONFIG.crystallized_skills_path
        self.storage_path = os.path.expanduser(raw_path)
        self.skills: dict[str, CrystallizedSkillSpec] = {}
        self.macros: dict[str, CrystallizedMacro] = {}
        self._jinja = jinja2.Environment(autoescape=False)
        self.load_from_disk()

    def register_skill(self, spec: CrystallizedSkillSpec) -> bool:
        """Registers and persists a new crystallized skill."""
        self.skills[spec.skill_name] = spec
        self.save_to_disk()
        logger.info(
            f"Registered crystallized skill '{spec.skill_name}' with {len(spec.parameters)} parameters"
        )
        return True

    def register_macro(self, macro: CrystallizedMacro, hal: Any | None = None) -> bool:
        """Registers a macro. When a cassette HAL is passed, replay must succeed first."""
        if hal is not None:
            from dobby.crystallization.trace_compiler import replay_macro

            replay_macro(macro, hal)
        self.macros[macro.name] = macro
        self.save_to_disk()
        return True

    def get_macro(self, name: str) -> CrystallizedMacro | None:
        return self.macros.get(name)

    def get_skill(self, name: str) -> CrystallizedSkillSpec | None:
        """Retrieves skill specification by name."""
        return self.skills.get(name)

    def list_skills(self) -> list[CrystallizedSkillSpec]:
        """Returns all registered crystallized skills."""
        return list(self.skills.values())

    def delete_skill(self, name: str) -> bool:
        """Removes a skill from registry and persists changes."""
        if name in self.skills:
            del self.skills[name]
            self.save_to_disk()
            return True
        return False

    def render_script(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        """Renders parameterized template into concrete executable script."""
        spec = self.get_skill(name)
        if not spec:
            raise KeyError(f"Skill '{name}' not found in registry")

        args = arguments or {}
        # Apply defaults from parameter schema if missing
        for param_name, schema in spec.parameters.items():
            if param_name not in args and "default" in schema:
                args[param_name] = schema["default"]

        template = self._jinja.from_string(spec.tcl_template)
        return template.render(**args)

    def save_to_disk(self) -> bool:
        """Persists all skills to JSON file."""
        try:
            os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
            payload = {name: spec.model_dump() for name, spec in self.skills.items()}
            if self.macros:
                payload["_macros"] = {
                    name: macro.model_dump() for name, macro in self.macros.items()
                }
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            return True
        except Exception as e:
            logger.warning(f"Failed to persist skills to '{self.storage_path}': {e}")
            return False

    def load_from_disk(self) -> bool:
        """Loads skills from JSON storage if present."""
        if not os.path.exists(self.storage_path):
            return False

        try:
            with open(self.storage_path, encoding="utf-8") as f:
                data = json.load(f)
            for name, spec_dict in data.items():
                if name == "_macros":
                    for macro_name, macro_dict in spec_dict.items():
                        self.macros[macro_name] = CrystallizedMacro(**macro_dict)
                    continue
                self.skills[name] = CrystallizedSkillSpec(**spec_dict)
            logger.info(f"Loaded {len(self.skills)} crystallized skills from '{self.storage_path}'")
            return True
        except Exception as e:
            logger.warning(f"Failed to load skills from '{self.storage_path}': {e}")
            return False
