"""How the runner reaches Pinlet: over MCP (default) or in this process."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from typing import Any, Protocol


class DesktopBackend(Protocol):
    """Anything that can run a Pinlet tool by name for the plan runner."""

    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        """Runs ``tool`` with ``args`` and returns its result."""
        ...


class InProcessBackend:
    """Calls the server's tool functions directly. Use with DESKTOP_HAL=replay for cassettes."""

    def __init__(self, server_module: Any = None) -> None:
        if server_module is None:
            import pinlet.server as server_module  # loads the HAL chosen by DESKTOP_HAL

        self.server = server_module

    async def enable(self, domains: list[str]) -> None:
        """Turns on the tool domains (``manage_toolsets``) that the plan needs."""
        if domains:
            self.server.manage_toolsets(action="enable", domains=domains)

    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        """Calls the server's tool function in a worker thread and returns its result."""
        fn = getattr(self.server, tool)
        return await asyncio.to_thread(fn, **args)


class McpBackend:
    """Starts Pinlet as an MCP server over stdio and calls its tools."""

    def __init__(self, command: list[str] | None = None, env: dict[str, str] | None = None) -> None:
        self.command = command or [sys.executable, "-m", "pinlet.server"]
        self.env = env
        self._stack: AsyncExitStack | None = None
        self.session: Any = None

    async def __aenter__(self) -> McpBackend:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=self.command[0], args=self.command[1:], env={**os.environ, **(self.env or {})}
        )
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._stack is not None:
            await self._stack.aclose()

    async def enable(self, domains: list[str]) -> None:
        """Turns on the tool domains (``manage_toolsets``) that the plan needs."""
        if domains:
            await self.call("manage_toolsets", {"action": "enable", "domains": domains})

    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        """Calls ``tool`` over the MCP session and returns its decoded result.

        Raises:
            RuntimeError: The server reported the call as an error.
        """
        result = await self.session.call_tool(tool, args)
        if getattr(result, "is_error", False):
            text = " ".join(getattr(c, "text", "") for c in result.content or [])
            return {"success": False, "message": text or "tool error"}
        structured = getattr(result, "structured_content", None)
        if structured is not None:
            # FastMCP wraps non-dict returns as {"result": ...}
            return (
                structured.get("result", structured)
                if set(structured) == {"result"}
                else structured
            )
        texts = [getattr(c, "text", "") for c in result.content or []]
        if len(texts) == 1:
            try:
                return json.loads(texts[0])
            except (TypeError, ValueError):
                return texts[0]
        return texts
