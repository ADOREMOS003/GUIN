"""MCP tool implementations (side-effect: registers tools on the FastMCP app)."""

from __future__ import annotations

import guin.mcp_server.tools.fmriprep  # noqa: F401
import guin.mcp_server.tools.mriqc  # noqa: F401
import guin.mcp_server.tools.factory  # noqa: F401

__all__ = []
