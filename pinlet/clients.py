"""What Pinlet knows about each AI client, kept in one table instead of name checks.

Clients differ in ways that change how Pinlet must answer:

* ``tool_timeout_s``: many MCP clients give up on a tool call after about 60
  seconds (the MCP TypeScript SDK's default request timeout), but
  ``wait_for_wake`` can wait for 30 minutes. With a known limit Pinlet returns
  just before it ("nobody woke Pinlet, call again") instead of the client
  cutting the call off with an error.
* ``sees_list_changed``: Antigravity ignores ``notifications/tools/list_changed``
  (it writes one schema file per tool once), so tools enabled later stay
  invisible to it.

The limits are the clients' documented or observed defaults. A person who
raised a client's limit sets ``PINLET_TOOL_TIMEOUT_S`` in that client's Pinlet
entry (``0`` = no limit); it travels in the connector's hello.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ClientProfile:
    """How one kind of AI client behaves, as far as Pinlet cares."""

    key: str
    label: str
    tool_timeout_s: float | None = None  # None: no limit known
    sees_list_changed: bool = True


# Matched against the lower-cased MCP clientInfo.name (or the launcher's name),
# first match wins. Keep specific patterns above general ones.
_TABLE: tuple[tuple[tuple[str, ...], ClientProfile], ...] = (
    (("claude-code", "claude code"), ClientProfile("claude-code", "Claude Code")),
    (("claude-ai", "claude desktop", "claude"), ClientProfile("claude-desktop", "Claude", 60.0)),
    (("codex",), ClientProfile("codex", "Codex", 60.0)),  # tool_timeout_sec defaults to 60
    (("gemini-cli", "gemini cli"), ClientProfile("gemini-cli", "Gemini CLI", 600.0)),
    (
        ("antigravity",),
        ClientProfile("antigravity", "Antigravity", 60.0, sees_list_changed=False),
    ),
    (("cursor",), ClientProfile("cursor", "Cursor", 60.0)),
    (("copilot",), ClientProfile("copilot", "Copilot", 60.0)),
    (("visual studio code", "vscode"), ClientProfile("vscode", "VS Code", 60.0)),
    (("opencode",), ClientProfile("opencode", "OpenCode")),
)
UNKNOWN = ClientProfile("unknown", "AI client")
# Return this long before the client's own limit, for the reply to travel.
MARGIN_S = 5.0


def parse_timeout(raw: object) -> float | None:
    """``PINLET_TOOL_TIMEOUT_S`` as seconds; None when unset or invalid, 0.0 for "no limit"."""
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = float(str(raw).strip())
    except ValueError:
        return None
    return max(0.0, value)


def profile_for(name: str | None, timeout_override: float | None = None) -> ClientProfile:
    """The profile for a client called ``name``, with the person's timeout override applied."""
    low = (name or "").strip().lower()
    found = UNKNOWN
    for patterns, profile in _TABLE:
        if any(p in low for p in patterns):
            found = profile
            break
    if timeout_override is not None:
        found = replace(found, tool_timeout_s=timeout_override or None)
    return found


def current() -> ClientProfile:
    """The calling client's profile: its shared-Pinlet session, else this process's client."""
    from pinlet.hub import sessions

    info = sessions.CURRENT.get()
    if info is not None:
        return profile_for(info.client or info.name, info.tool_timeout_s)
    from pinlet.control import get_controller

    ctl = get_controller(create=False)
    name = ctl.agent.name if ctl is not None else None
    return profile_for(name, parse_timeout(os.environ.get("PINLET_TOOL_TIMEOUT_S")))


def blocking_budget(wanted_s: float, profile: ClientProfile | None = None) -> float:
    """How long a blocking tool may wait so the client does not cut the call off."""
    profile = current() if profile is None else profile
    limit = profile.tool_timeout_s
    if not limit:
        return wanted_s
    return max(1.0, min(wanted_s, limit - MARGIN_S))
