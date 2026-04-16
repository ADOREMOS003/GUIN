"""MCP server integration (FastMCP)."""

from __future__ import annotations

import asyncio
from typing import Any

from guin.mcp_server.server import mcp


def call_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Sync helper for calling a registered MCP tool from generated code."""
    result = asyncio.run(mcp.call_tool(name, arguments))
    if isinstance(result, tuple) and len(result) == 2:
        _content, structured = result
        return structured
    return result


__all__ = ["call_tool"]
