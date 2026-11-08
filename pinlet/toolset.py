"""Tool Catalog Modularization, Capability Gating, and Out-of-Band State Offloading.

Reduces cold-start prompt prefix overhead from ~5,400 tokens down to <900 tokens by:
1. Dynamically exposing domain-gated tool subsets (minimal_computer_use, full_developer, eda_automation).
2. Offloading dense accessibility trees, OCR bounding boxes, and VDOM snapshots to RAM-backed
   tmpfs files (/tmp/dc_state/ or /dev/shm/dc_state/), returning compact 80-token summary headers.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

# Notify callbacks scheduled on the running loop; held so they finish.
_PENDING_NOTIFIES: set[Any] = set()


class OutputMode(str, Enum):
    """Payload output mode for MCP tools."""

    INLINE = "inline"  # Return full dictionary directly inside MCP response
    FILE = "file"  # Offload heavy payloads to tmpfs file, return URI & preview
    RESOURCE = "resource"  # Expose as mcp://pinlet/state/<id> pointer


class ToolDefinition(BaseModel):
    """Definition and metadata for a registered MCP tool."""

    name: str
    domain: str = Field(
        default="core",
        description="Domain group: 'core', 'window_mgmt', 'developer', 'plugins', 'skills', 'vdom', 'advanced_vision', 'eda_vivado', 'eda_verdi'",
    )
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    max_inline_tokens: int = 350


class ManageToolsetsInput(BaseModel):
    """Input parameters for manage_toolsets tool."""

    action: str = Field(
        pattern="^(list_domains|enable|disable|set_output_mode)$",
        description="Action to perform: 'list_domains', 'enable', 'disable', or 'set_output_mode'",
    )
    domains: list[str] = Field(
        default_factory=list,
        description="List of domain names to enable or disable",
    )
    output_mode: OutputMode | None = Field(
        default=None,
        description="Target output mode when action is 'set_output_mode'",
    )


class ToolSetManager:
    """Manages tool profiles and offloads large tool outputs.

    Gates which tool domains are active, and writes large payloads to disk or tmpfs
    instead of returning them inline.
    """

    PROFILES: dict[str, list[str]] = {
        "minimal_computer_use": ["core"],
        "window_mgmt": ["core", "window_mgmt"],
        "full_developer": [
            "core",
            "developer",
            "window_mgmt",
            "plugins",
            "skills",
            "vdom",
            "advanced_vision",
        ],
        "eda_automation": [
            "core",
            "developer",
            "window_mgmt",
            "plugins",
            "skills",
            "vdom",
            "advanced_vision",
            "eda_vivado",
            "eda_verdi",
        ],
    }

    def __init__(
        self,
        initial_profile: str = "full_developer",
        output_mode: OutputMode = OutputMode.INLINE,
        max_inline_tokens: int = 350,
        state_dir: Path | str | None = None,
        notify_tools_changed_cb: Callable[[], Any] | None = None,
    ) -> None:
        self._registry: dict[str, ToolDefinition] = {}
        domains = self.PROFILES.get(initial_profile, ["core"])
        # Each client of the shared Pinlet has its own profile: one agent's
        # manage_toolsets must not change (or shrink) another agent's tools.
        self._initial_domains: frozenset[str] = frozenset(domains)
        self._domains_by_session: dict[int, set[str]] = {}
        try:
            from pinlet.hub import sessions

            sessions.on_close(lambda sid: self._domains_by_session.pop(sid, None))
        except ImportError:  # pragma: no cover
            pass
        self._output_mode: OutputMode = output_mode
        self._max_inline_tokens: int = max_inline_tokens
        self._notify_cb = notify_tools_changed_cb

        # Resolve state directory (prefers /dev/shm on Linux, tempdir on macOS/Windows)
        if state_dir:
            self._state_dir = Path(state_dir)
        elif os.path.exists("/dev/shm") and os.access("/dev/shm", os.W_OK):
            self._state_dir = Path("/dev/shm/dc_state")
        else:
            self._state_dir = Path(tempfile.gettempdir()) / "dc_state"
        self._state_dir.mkdir(parents=True, exist_ok=True)

    @property
    def _active_domains(self) -> set[str]:
        """The current session's enabled domains (see :mod:`pinlet.hub.per_session`)."""
        from pinlet.hub.sessions import current_id

        key = current_id()
        domains = self._domains_by_session.get(key)
        if domains is None:
            domains = self._domains_by_session.setdefault(key, set(self._initial_domains))
        return domains

    @_active_domains.setter
    def _active_domains(self, domains: set[str]) -> None:
        from pinlet.hub.sessions import current_id

        self._domains_by_session[current_id()] = set(domains)

    @property
    def output_mode(self) -> OutputMode:
        """Where large tool outputs go: inline, a file, or an MCP resource."""
        return self._output_mode

    @output_mode.setter
    def output_mode(self, mode: OutputMode | str) -> None:
        self._output_mode = OutputMode(mode)

    @property
    def state_dir(self) -> Path:
        """Directory for offloaded payloads and toolset state."""
        return self._state_dir

    def register_tool(self, tool: ToolDefinition) -> None:
        """Registers a tool definition and its domain categorization."""
        self._registry[tool.name] = tool

    def get_tool(self, name: str) -> ToolDefinition | None:
        """Retrieves tool definition by name."""
        return self._registry.get(name)

    def list_all_tools(self) -> list[str]:
        """Lists all registered tool names."""
        return sorted(self._registry.keys())

    def get_active_domains(self) -> list[str]:
        """Returns sorted list of currently enabled domain names."""
        return sorted(self._active_domains)

    def get_available_domains(self) -> list[str]:
        """Returns sorted list of all registered domain names."""
        domains = {tool.domain for tool in self._registry.values()}
        domains.add("core")
        return sorted(domains)

    def is_tool_active(self, tool_name: str) -> bool:
        """Checks if a tool belongs to an active domain."""
        tool = self._registry.get(tool_name)
        if not tool:
            return True  # Unknown tools default to active for compatibility
        return tool.domain in self._active_domains

    def get_active_mcp_schemas(self) -> list[dict[str, Any]]:
        """Returns active MCP tool schemas matching enabled domains."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
            }
            for tool in self._registry.values()
            if tool.domain in self._active_domains
        ]

    def manage_toolsets(
        self,
        action: str,
        domains: list[str] | None = None,
        output_mode: OutputMode | str | None = None,
    ) -> dict[str, Any]:
        """Manages domain activation and output mode preferences.

        Args:
            action: 'list_domains', 'enable', 'disable', or 'set_output_mode'
            domains: Domain list to enable or disable
            output_mode: 'inline', 'file', or 'resource'
        """
        domains = domains or []
        available = self.get_available_domains()

        if action == "list_domains":
            return {
                "active_domains": self.get_active_domains(),
                "available_domains": available,
                "output_mode": self._output_mode.value,
                "active_tools_count": len(
                    [t for t in self._registry.values() if t.domain in self._active_domains]
                ),
                "total_tools_count": len(self._registry),
            }

        if action == "set_output_mode":
            if output_mode:
                self._output_mode = OutputMode(output_mode)
            return {
                "success": True,
                "output_mode": self._output_mode.value,
            }

        mutated = False
        for dom in domains:
            if action == "enable" and dom in available and dom not in self._active_domains:
                self._active_domains.add(dom)
                mutated = True
            elif action == "disable" and dom in self._active_domains and dom != "core":
                self._active_domains.discard(dom)
                mutated = True

        if mutated and self._notify_cb is not None:
            try:
                res = self._notify_cb()
                if hasattr(res, "__await__"):
                    import asyncio

                    try:
                        loop = asyncio.get_running_loop()
                        task = loop.create_task(res)
                        # Keep a reference so the task is not garbage-collected mid-flight.
                        _PENDING_NOTIFIES.add(task)
                        task.add_done_callback(_PENDING_NOTIFIES.discard)
                    except RuntimeError:
                        asyncio.run(res)
            except Exception:
                pass

        return {
            "success": True,
            "mutated": mutated,
            "action": action,
            "active_domains": self.get_active_domains(),
            "active_tools_count": len(
                [t for t in self._registry.values() if t.domain in self._active_domains]
            ),
            "output_mode": self._output_mode.value,
        }

    def format_output(
        self,
        tool_name: str,
        payload: dict[str, Any],
        output_mode_override: OutputMode | str | None = None,
        force_offload: bool = False,
    ) -> dict[str, Any]:
        """Intercepts tool payloads and offloads heavy structures out-of-band when configured.

        Args:
            tool_name: Name of executing tool
            payload: Tool result dictionary
            output_mode_override: Optional per-call override ('inline', 'file', 'resource')
            force_offload: Offload the payload even when it is small.

        Returns:
            Raw payload if inline or small; summary preview and file URI if offloaded.
        """
        effective_mode = (
            OutputMode(output_mode_override) if output_mode_override else self._output_mode
        )

        serialized = json.dumps(payload, default=str, indent=2)
        est_tokens = len(serialized) // 4
        tool = self._registry.get(tool_name)
        threshold = tool.max_inline_tokens if tool else self._max_inline_tokens

        if not force_offload and (effective_mode == OutputMode.INLINE or est_tokens <= threshold):
            return payload

        h = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]
        filename = f"{tool_name}_{h}.json"
        artifact_path = self._state_dir / filename
        artifact_path.write_text(serialized, encoding="utf-8")

        preview_fields = [
            "status",
            "active_window",
            "is_error",
            "modal_state",
            "count",
            "lines_count",
            "nodes_count",
            "elements_count",
            "title",
            "resolution",
        ]
        compact_preview: dict[str, Any] = {k: payload[k] for k in preview_fields if k in payload}
        sample_lines: list[str] = []
        if "lines" in payload and isinstance(payload["lines"], list):
            sample_lines = [str(item.get("text", item))[:60] for item in payload["lines"][:3]]
            compact_preview["sample_lines"] = sample_lines
        if "nodes" in payload and isinstance(payload["nodes"], list):
            compact_preview["nodes_count"] = len(payload["nodes"])
            sample_lines = sample_lines or [
                str(item.get("name", item))[:60] for item in payload["nodes"][:3]
            ]

        uri = f"mcp://pinlet/state/{filename}"
        header = {
            "ok": True,
            "window": payload.get("active_window") or payload.get("window_title") or "",
            "kind": tool_name,
            "counts": {
                "nodes": compact_preview.get("nodes_count", payload.get("count")),
            },
            "preview": sample_lines,
            "uri": uri,
            "bytes": len(serialized.encode("utf-8")),
        }
        result: dict[str, Any] = {
            "offloaded": True,
            "output_mode": effective_mode.value,
            "estimated_tokens_saved": max(0, est_tokens - 80),
            "original_estimated_tokens": est_tokens,
            "resource_uri": uri,
            "uri": uri,
            "preview": compact_preview,
            "header": header,
            "bytes": header["bytes"],
        }
        if effective_mode == OutputMode.RESOURCE:
            result["read_hint"] = f"resources/read {uri}"
        else:
            result["file_path"] = str(artifact_path)
            result["read_hint"] = (
                f"Call read_file(path='{artifact_path}') or grep target keys on demand."
            )
        return result

    def resolve_state_file(self, name: str) -> Path:
        """Returns a state file inside the state dir. Rejects paths that escape it."""
        if not name or "/" in name or "\\" in name or name.startswith("."):
            raise ValueError(f"invalid state name: {name}")
        path = (self._state_dir / name).resolve()
        root = self._state_dir.resolve()
        if path.parent != root:
            raise ValueError(f"state path escapes {root}")
        if not path.is_file():
            raise FileNotFoundError(name)
        return path

    def cleanup_old_state(self, max_age_seconds: int = 3600) -> int:
        """Deletes state payload files older than max_age_seconds."""
        now = time.time()
        removed = 0
        if not self._state_dir.exists():
            return 0
        for f in self._state_dir.glob("*.json"):
            try:
                if now - f.stat().st_mtime > max_age_seconds:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
        return removed
