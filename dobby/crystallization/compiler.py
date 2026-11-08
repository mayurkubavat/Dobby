"""Trajectory Compiler and AST Parameterizer.

Cleanses raw echoed journal commands, deduplicates redundant selections,
lifts entity literals into Jinja2 {{param}} templates, and injects synchronization barriers.
"""

import re
import time
from typing import Any

from dobby.types import CorrelatedStep, CrystallizedSkillSpec


class TclTrajectoryCompiler:
    """Compiles raw multi-step GUI trajectories into clean, parameterized Tcl/CLI scripts."""

    NOISE_PATTERNS = [
        r"^highlight_objects\b.*",
        r"^unhighlight_objects\b.*",
        r"^show_objects\b.*-name\s+find_\d+",
        r"^wvSetCursor\b.*",
        r"^set_property\s+location\s+.*",
        r"^gui_.*",
    ]

    def _collapse_redundant_selections(self, cmds: list[str]) -> list[str]:
        """Collapses consecutive select_objects calls, keeping only the final selection."""
        result: list[str] = []
        for i, cmd in enumerate(cmds):
            if (
                cmd.startswith("select_objects")
                and i + 1 < len(cmds)
                and cmds[i + 1].startswith("select_objects")
            ):
                continue
            result.append(cmd)
        return result

    def _inject_sync_barriers(self, script: str) -> str:
        """Injects deterministic blocking wait barriers after asynchronous jobs."""
        # Vivado launch_runs -> wait_on_run
        pattern = r"(launch_runs\s+([^\s\n;]+)[^\n]*)"

        def _add_wait(match):
            full_cmd = match.group(1)
            run_name = match.group(2)
            if f"wait_on_run {run_name}" in script:
                return full_cmd
            return f"{full_cmd}\nwait_on_run {run_name}"

        return re.sub(pattern, _add_wait, script)

    def _parameterize_entities(
        self,
        script_body: str,
        entities: dict[str, Any],
        task_prompt: str,
    ) -> tuple[str, dict[str, dict[str, Any]]]:
        """Replaces concrete entity literals with {{param}} placeholders.

        Sorts entities by length descending to prevent substring collisions.
        """
        param_schema: dict[str, dict[str, Any]] = {}

        for param_key, literal_val in sorted(entities.items(), key=lambda x: -len(str(x[1]))):
            lit_str = str(literal_val).strip()
            if not lit_str:
                continue

            escaped_val = re.escape(lit_str)
            if re.search(escaped_val, script_body):
                script_body = re.sub(escaped_val, f"{{{{{param_key}}}}}", script_body)

                is_num = lit_str.replace(".", "", 1).isdigit()
                param_schema[param_key] = {
                    "type": "number" if is_num else "string",
                    "description": f"Parameterized from '{lit_str}' in task: {task_prompt}",
                    "default": float(lit_str)
                    if is_num and "." in lit_str
                    else (int(lit_str) if is_num else lit_str),
                }

        return script_body, param_schema

    def compile_trajectory(
        self,
        steps: list[CorrelatedStep],
        task_prompt: str,
        extracted_entities: dict[str, Any] | None = None,
        skill_name: str | None = None,
        app_target: str = "vivado_2025.1",
        description: str | None = None,
        verification_cmd: str | None = None,
    ) -> CrystallizedSkillSpec:
        """Compiles captured trajectory into a structured, parameterized skill specification."""
        raw_commands: list[str] = []
        for step in steps:
            for line in step.journal_lines:
                cleaned = line.strip()
                if not any(re.match(pat, cleaned) for pat in self.NOISE_PATTERNS):
                    raw_commands.append(cleaned)

        # 1. Deduplicate sequential selections
        deduped_commands = self._collapse_redundant_selections(raw_commands)
        script_body = "\n".join(deduped_commands)

        # 2. Lift literal entities into {{parameters}}
        entities = extracted_entities or {}
        script_body, param_schema = self._parameterize_entities(script_body, entities, task_prompt)

        # 3. Inject synchronization barriers
        script_body = self._inject_sync_barriers(script_body)

        # 4. Generate metadata
        name = skill_name or f"skill_{app_target}_{int(time.time())}"
        desc = (
            description or f"Crystallized deterministic skill compiled from task: '{task_prompt}'"
        )
        v_cmd = verification_cmd or "puts [get_property STATUS [get_runs -quiet current_run]]"

        return CrystallizedSkillSpec(
            skill_name=name,
            description=desc,
            app_target=app_target,
            tcl_template=script_body,
            parameters=param_schema,
            verification_cmd=v_cmd,
            source_steps_count=len(steps),
        )
