"""Compiles recorded GUI steps into a parameterizable macro the trajectory engine can run."""

from __future__ import annotations

import re
from typing import Any

from dobby.hal.errors import CassetteMismatch
from dobby.types import CorrelatedStep, CrystallizedMacro, TraceStep


class TraceCompiler:
    """Turns correlated GUI steps into TrajectoryEngine steps. Tcl stays on the native template."""

    def compile_steps(
        self,
        steps: list[CorrelatedStep],
        name: str,
        app_id: str,
        description: str = "",
        entities: dict[str, Any] | None = None,
        native_template: str = "",
        native_kind: str = "none",
    ) -> CrystallizedMacro:
        entities = entities or {}
        trace: list[TraceStep] = []
        params: dict[str, dict[str, Any]] = {}
        for step in steps:
            query = step.ui_target_text or ""
            for key, literal in sorted(entities.items(), key=lambda item: -len(str(item[1]))):
                lit = str(literal)
                if lit and lit in query:
                    query = query.replace(lit, "{{ " + key + " }}")
                    params[key] = {
                        "type": "number" if re.fullmatch(r"-?\d+(\.\d+)?", lit) else "string",
                        "description": f"Lifted from '{lit}'",
                        "default": lit,
                    }
            if step.ui_target_text:
                action = "find_and_click"
                selector = {"ocr_label": step.ui_target_text}
                args: dict[str, Any] = {"query": query or step.ui_target_text}
            else:
                action = step.gui_intent or "click"
                selector = {"unresolved": True}
                args = {}
            trace.append(TraceStep(action=action, selector=selector, args=args))
        kind = native_kind if native_template else "none"
        return CrystallizedMacro(
            name=name,
            app_id=app_id,
            description=description,
            params=params,
            steps=trace,
            native_template=native_template,
            native_kind=kind,
        )


def replay_macro(macro: CrystallizedMacro, hal: Any) -> None:
    """Replays macro steps against a cassette. A mismatch refuses registration."""
    for step in macro.steps:
        if step.action in ("click", "mouse_click") and "x" in step.args and "y" in step.args:
            hal.mouse_click(
                int(step.args["x"]),
                int(step.args["y"]),
                button=step.args.get("button", "left"),
                click_count=int(step.args.get("click_count", 1)),
            )
            continue
        label = step.args.get("query") or step.selector.get("ocr_label")
        if label and hasattr(hal, "read_screen_text"):
            # Strip jinja so the cassette can still see the recorded label.
            plain = re.sub(r"\{\{\s*\w+\s*\}\}", "", str(label)).strip() or str(
                step.selector.get("ocr_label") or ""
            )
            if plain:
                found = hal.read_screen_text(contains=plain)
                if found.get("count", 0) == 0:
                    raise CassetteMismatch(plain, found)
            continue
        if step.action in ("type_text", "keyboard_type"):
            hal.keyboard_type(str(step.args.get("text", "")))
            continue
        if step.action in ("press_key", "keyboard_press_key"):
            hal.keyboard_press_key(str(step.args.get("key", step.args.get("key_name", "enter"))))
            continue
        raise CassetteMismatch(step.action, "no replay handler")
