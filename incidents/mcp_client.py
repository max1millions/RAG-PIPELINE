"""MCP client for fetching recent errors from a configured MCP server."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.sse import sse_client

# Resolved at call time from incidents config or ORION_MCP_SERVER env var.
_FALLBACK_SERVER = "my_mcp_server"
_FALLBACK_TOOL = "get_recent_errors"
_DEFAULT_TIMEOUT_S = 30.0


def _resolve_server_and_tool() -> tuple[str, str]:
    """Return (server_name, tool_name) from config or env vars."""
    server = os.environ.get("ORION_MCP_SERVER")
    tool = os.environ.get("ORION_MCP_TOOL")
    if not server or not tool:
        try:
            from incidents.settings import load_incidents_config

            cfg = load_incidents_config()
            server = server or str(cfg.get("mcp_server") or _FALLBACK_SERVER)
            tool = tool or str(cfg.get("mcp_tool") or _FALLBACK_TOOL)
        except Exception:
            server = server or _FALLBACK_SERVER
            tool = tool or _FALLBACK_TOOL
    return server, tool


def load_openclaw_config(config_path: Path | None = None) -> tuple[Path, dict[str, Any]]:
    state_dir = Path(os.environ.get("OPENCLAW_STATE_DIR", Path.home() / ".openclaw"))
    path = config_path or (state_dir / "openclaw.json")
    if not path.is_file():
        raise FileNotFoundError(f"Config not found: {path}")
    return path, json.loads(path.read_text(encoding="utf-8"))


def resolve_mcp_server(cfg: dict[str, Any], server_name: str) -> str:
    servers = (cfg.get("mcp") or {}).get("servers")
    if not isinstance(servers, dict):
        raise ValueError("No mcp.servers block in openclaw.json")
    server = servers.get(server_name)
    if not server:
        names = ", ".join(sorted(servers.keys())) or "(none)"
        raise ValueError(f'Unknown MCP server "{server_name}". Configured: {names}')
    url = server.get("url")
    if not url:
        raise ValueError(f"mcp.servers.{server_name} has no url")
    transport = str(server.get("transport") or "sse").lower()
    if transport not in ("sse",):
        raise ValueError(f'Unsupported transport "{transport}" (only sse is implemented)')
    return str(url)


def _extract_json_from_tool_result(result: Any) -> dict[str, Any]:
    sc = getattr(result, "structuredContent", None)
    if isinstance(sc, dict):
        return sc
    if getattr(result, "isError", False):
        content = getattr(result, "content", None) or []
        texts = [
            getattr(b, "text", "") if hasattr(b, "text") else str(b)
            for b in content
        ]
        raise ValueError("MCP tool error: " + " ".join(texts).strip() or "(no detail)")
    content = getattr(result, "content", None) or []
    texts: list[str] = []
    for block in content:
        if hasattr(block, "text"):
            texts.append(block.text)
        elif isinstance(block, dict) and block.get("type") == "text":
            texts.append(str(block.get("text") or ""))
    raw = "\n".join(texts).strip()
    if not raw:
        raise ValueError("Empty MCP tool result")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("MCP tool result is not a JSON object")
    return parsed


async def fetch_recent_errors(
    *,
    limit: int,
    server_name: str | None = None,
    tool_name: str | None = None,
    config_path: Path | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    resolved_server, resolved_tool = _resolve_server_and_tool()
    server_name = server_name or resolved_server
    tool_name = tool_name or resolved_tool

    _, cfg = load_openclaw_config(config_path)
    url = resolve_mcp_server(cfg, server_name)

    async def _run() -> dict[str, Any]:
        async with sse_client(url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, {"limit": limit})
                return _extract_json_from_tool_result(result)

    return await asyncio.wait_for(_run(), timeout=timeout_s)


def fetch_recent_errors_sync(
    *,
    limit: int,
    server_name: str | None = None,
    tool_name: str | None = None,
    config_path: Path | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    return asyncio.run(
        fetch_recent_errors(
            limit=limit,
            server_name=server_name,
            tool_name=tool_name,
            config_path=config_path,
            timeout_s=timeout_s,
        )
    )
