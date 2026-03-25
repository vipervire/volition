"""
MCP Client for GUPPI v8.0+
Connects to MCP servers over streamable HTTP, discovers their tools,
and registers them into the ToolRegistry.

Config file: ~/.abe-mcp.json
{
  "servers": {
    "myserver": {
      "url": "http://localhost:3001/mcp",
      "headers": {"Authorization": "Bearer ${TOKEN}"},
      "flash_allowed_tools": ["safe_tool_name"]
    }
  }
}
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from tool_registry import ToolDef, ToolRegistry

logger = logging.getLogger("guppi.mcp")


def _expand_env_vars(value):
    """Replace ${VAR} patterns in strings with environment variable values."""
    if not isinstance(value, str):
        return value
    return re.sub(r'\$\{(\w+)\}', lambda m: os.environ.get(m.group(1), ''), value)


def _expand_headers(headers: Dict[str, str]) -> Dict[str, str]:
    return {k: _expand_env_vars(v) for k, v in (headers or {}).items()}


class MCPManager:
    def __init__(self, registry, redis_client, abe_name: str):
        self.registry = registry
        self.r = redis_client
        self.abe_name = abe_name
        # server_name -> {"client": client_obj, "session": session_obj, "config": {...}}
        self._connections: Dict[str, Dict] = {}
        # server_name -> {"url": ..., "headers": ..., "flash_allowed_tools": [...]}
        self._pending_configs: Dict[str, Dict] = {}

    def queue_server(self, server_name: str, url: str, headers: Optional[Dict[str, str]] = None, flash_allowed_tools: Optional[List[str]] = None):
        """Store server config for lazy connection on first tool use."""
        self._pending_configs[server_name] = {
            "url": url,
            "headers": headers or {},
            "flash_allowed_tools": flash_allowed_tools or [],
        }
        logger.info(f"MCP queued (lazy): {server_name} @ {url}")

    async def connect_pending(self, server_name: str) -> bool:
        """Connect a queued server on demand. Returns True if tools were registered."""
        cfg = self._pending_configs.pop(server_name, None)
        if not cfg:
            return False
        await self.connect(server_name, cfg["url"], cfg["headers"])
        if server_name in self._connections:
            await self.discover_and_register(server_name, flash_allowed_tools=cfg["flash_allowed_tools"])
            return True
        return False

    def list_pending_servers(self) -> Dict[str, str]:
        """Return names and URLs of servers queued but not yet connected."""
        return {name: cfg["url"] for name, cfg in self._pending_configs.items()}

    async def connect(self, server_name: str, url: str, headers: Optional[Dict[str, str]] = None):
        """Connect to an MCP server over streamable HTTP."""
        try:
            from mcp.client.streamable_http import streamablehttp_client
            from mcp import ClientSession
        except ImportError:
            logger.error("mcp SDK not installed. Run: pip install mcp")
            return

        resolved_headers = _expand_headers(headers or {})
        try:
            client_cm = streamablehttp_client(url, headers=resolved_headers)
            read, write, _ = await client_cm.__aenter__()
            session = ClientSession(read, write)
            await session.__aenter__()
            await session.initialize()
            self._connections[server_name] = {
                "session": session,
                "client_cm": client_cm,
                "url": url,
            }
            logger.info(f"MCP connected: {server_name} @ {url}")
        except Exception as e:
            logger.error(f"MCP connect failed for '{server_name}': {e}")

    async def discover_and_register(self, server_name: str, flash_allowed_tools: Optional[List[str]] = None):
        """List tools from a connected MCP server and register them in the ToolRegistry."""
        from tool_registry import ToolDef

        conn = self._connections.get(server_name)
        if not conn:
            logger.warning(f"discover_and_register: no connection for '{server_name}'")
            return

        session = conn["session"]
        try:
            tools_result = await session.list_tools()
        except Exception as e:
            logger.error(f"MCP list_tools failed for '{server_name}': {e}")
            return

        allowed_set = set(flash_allowed_tools or [])

        for tool in tools_result.tools:
            namespaced = f"mcp.{server_name}.{tool.name}"
            tool_def = ToolDef(
                name=namespaced,
                description=f"[MCP:{server_name}] {tool.description or tool.name}",
                handler=self._make_handler(server_name, tool.name),
                source=f"mcp:{server_name}",
                flash_allowed=(tool.name in allowed_set),
                quiet=False,
            )
            self.registry.register(tool_def)
            logger.info(f"Registered MCP tool: {namespaced} (flash_allowed={tool.name in allowed_set})")

    def _make_handler(self, server_name: str, mcp_tool_name: str):
        """Factory: returns a handler closure for a specific MCP tool."""
        async def handler(daemon, turn_id, action):
            conn = self._connections.get(server_name)
            if not conn:
                return {"status": "error", "message": f"MCP server '{server_name}' not connected"}

            session = conn["session"]
            # Collect all action keys except "tool" as MCP arguments
            params = {k: v for k, v in action.items() if k != "tool"}

            try:
                result = await session.call_tool(mcp_tool_name, arguments=params)
            except Exception as e:
                return {"status": "error", "message": f"MCP call failed: {e}"}

            # Normalize MCP content list to a single string
            content_parts = []
            for c in result.content:
                if hasattr(c, "text"):
                    content_parts.append(c.text)
                elif hasattr(c, "data"):
                    content_parts.append(str(c.data))

            content_text = "\n".join(content_parts)

            # Publish to action_log so audit trail covers MCP calls
            try:
                import json as _json
                from datetime import datetime
                log_entry = {
                    "agent": daemon.abe_name,
                    "source": f"mcp:{server_name}",
                    "tool": mcp_tool_name,
                    "params": _json.dumps(params),
                    "status": "error" if result.isError else "success",
                    "timestamp": datetime.utcnow().isoformat(),
                }
                await daemon.r.xadd("volition:action_log", {"entry": _json.dumps(log_entry)})
            except Exception:
                pass

            if result.isError:
                return {"status": "error", "message": content_text}
            return {"status": "success", "content": content_text}

        return handler

    async def fetch_resources(self, server_name: str) -> List[Dict[str, Any]]:
        """Fetch MCP resources for optional context injection."""
        conn = self._connections.get(server_name)
        if not conn:
            return []
        session = conn["session"]
        try:
            resources_result = await session.list_resources()
            out = []
            for resource in resources_result.resources:
                try:
                    content = await session.read_resource(resource.uri)
                    out.append({
                        "uri": str(resource.uri),
                        "name": resource.name,
                        "content": content,
                    })
                except Exception as e:
                    logger.warning(f"MCP read_resource failed ({resource.uri}): {e}")
            return out
        except Exception as e:
            logger.error(f"MCP list_resources failed for '{server_name}': {e}")
            return []

    async def disconnect(self, server_name: str):
        """Disconnect an MCP server and unregister its tools from the registry."""
        conn = self._connections.pop(server_name, None)
        if not conn:
            return
        # Unregister all tools from this server
        to_remove = [
            name for name, t in list(self.registry._tools.items())
            if t.source == f"mcp:{server_name}"
        ]
        for name in to_remove:
            self.registry.unregister(name)

        try:
            await conn["session"].__aexit__(None, None, None)
            await conn["client_cm"].__aexit__(None, None, None)
        except Exception as e:
            logger.warning(f"MCP disconnect error for '{server_name}': {e}")

        logger.info(f"MCP disconnected: {server_name} ({len(to_remove)} tools removed)")

    async def disconnect_all(self):
        for name in list(self._connections):
            await self.disconnect(name)

    def list_connections(self) -> Dict[str, str]:
        return {name: conn["url"] for name, conn in self._connections.items()}


async def load_mcp_config(mcp_manager: MCPManager, config_path) -> None:
    """Load ~/.abe-mcp.json and queue all configured servers for lazy connection."""
    from pathlib import Path
    p = Path(config_path)
    if not p.exists():
        return
    try:
        config = json.loads(p.read_text())
    except Exception as e:
        logger.error(f"Failed to parse MCP config {p}: {e}")
        return

    for server_name, server_cfg in config.get("servers", {}).items():
        url = server_cfg.get("url")
        if not url:
            logger.warning(f"MCP server '{server_name}' missing url, skipping")
            continue
        headers = server_cfg.get("headers", {})
        flash_allowed = server_cfg.get("flash_allowed_tools", [])

        mcp_manager.queue_server(server_name, url, headers, flash_allowed_tools=flash_allowed)
