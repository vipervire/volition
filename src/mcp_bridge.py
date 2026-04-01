"""
MCP (Model Context Protocol) bridge for GUPPI.

Manages connections to external MCP servers, discovers their tools,
converts them to OpenAI-compatible schemas, and routes tool calls.

Config: $ABE_ROOT/.mcp-servers.json
  {
    "servers": [
      {"name": "filesystem", "transport": "stdio",
       "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/home/abe"],
       "env": {}, "active": true},
      {"name": "github", "transport": "sse",
       "url": "http://localhost:3001/sse", "active": false}
    ]
  }

Dependency: pip install mcp
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.sse import sse_client
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False


@dataclass
class MCPServerSession:
    name: str
    transport: str          # "stdio" or "sse"
    config: dict
    session: Optional[Any] = None
    _ctx_exit: Optional[Any] = None  # stored __aexit__ for cleanup
    tools: List = field(default_factory=list)  # raw MCP tool objects (all servers)
    connected: bool = False


class MCPBridge:
    """Manages MCP server connections and exposes their tools as OpenAI-format schemas."""

    def __init__(self, config_path: Path, logger: logging.Logger):
        self.config_path = config_path
        self.logger = logger
        self.sessions: Dict[str, MCPServerSession] = {}
        self._tool_cache: List[dict] = []                        # OpenAI schemas for active servers
        self._tool_routing: Dict[str, Tuple[str, str]] = {}      # namespaced -> (server, original_name)
        self._all_sessions: Dict[str, MCPServerSession] = {}     # includes inactive (for help)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def connect_all(self) -> None:
        """Connect to all active MCP servers and discover their tools."""
        if not MCP_AVAILABLE:
            self.logger.warning("MCP SDK not installed. Run: pip install mcp")
            return

        servers = self._load_config()
        for srv_cfg in servers:
            name = srv_cfg.get("name")
            if not name:
                continue
            active = srv_cfg.get("active", False)
            sess = MCPServerSession(name=name, transport=srv_cfg.get("transport", "stdio"), config=srv_cfg)
            self._all_sessions[name] = sess
            if active:
                await self._connect_server(sess)
                if sess.connected:
                    self.sessions[name] = sess

        self._rebuild_tool_cache()
        self.logger.info(f"MCP: {len(self.sessions)} active server(s), {len(self._tool_cache)} tool(s)")

    async def disconnect_all(self) -> None:
        """Close all active server sessions cleanly."""
        for name, sess in list(self.sessions.items()):
            await self._disconnect_server(sess)
        self.sessions.clear()
        self._tool_cache.clear()
        self._tool_routing.clear()

    async def reload(self, activate: str = None, deactivate: str = None) -> Tuple[int, int]:
        """
        Re-read config, optionally toggle server active flags, reconnect as needed.
        Returns (active_server_count, tool_count).
        """
        if not MCP_AVAILABLE:
            return 0, 0

        # Persist active/deactivate changes to config file
        if activate or deactivate:
            self._toggle_active_in_config(activate, deactivate)

        new_cfg = {s["name"]: s for s in self._load_config() if "name" in s}

        # Disconnect removed/deactivated servers
        for name in list(self.sessions.keys()):
            cfg = new_cfg.get(name, {})
            if not cfg.get("active", False):
                await self._disconnect_server(self.sessions[name])
                del self.sessions[name]

        # Connect newly active servers
        for name, cfg in new_cfg.items():
            if cfg.get("active", False) and name not in self.sessions:
                sess = MCPServerSession(name=name, transport=cfg.get("transport", "stdio"), config=cfg)
                self._all_sessions[name] = sess
                await self._connect_server(sess)
                if sess.connected:
                    self.sessions[name] = sess

        # Re-discover tools for servers still connected (config may have changed)
        for name, sess in list(self.sessions.items()):
            if name in new_cfg:
                sess.config = new_cfg[name]
                await self._discover_tools(sess)

        self._rebuild_tool_cache()
        return len(self.sessions), len(self._tool_cache)

    async def call_tool(self, namespaced_name: str, arguments: dict) -> dict:
        """
        Call an MCP tool by its namespaced name (mcp_{server}__{tool}).
        Returns a dict compatible with patch_abe_outcome.
        """
        routing = self._tool_routing.get(namespaced_name)
        if not routing:
            return {"status": "error", "message": f"Unknown MCP tool: {namespaced_name}"}

        server_name, original_name = routing
        sess = self.sessions.get(server_name)
        if not sess or not sess.connected:
            return {"status": "error", "message": f"MCP server '{server_name}' is not connected"}

        try:
            result = await sess.session.call_tool(original_name, arguments or {})
            return self._mcp_result_to_dict(result)
        except Exception as e:
            # One reconnect attempt
            self.logger.warning(f"MCP call failed, attempting reconnect: {e}")
            try:
                await self._disconnect_server(sess)
                await self._connect_server(sess)
                if sess.connected:
                    result = await sess.session.call_tool(original_name, arguments or {})
                    return self._mcp_result_to_dict(result)
            except Exception as e2:
                pass
            return {"status": "error", "message": f"MCP tool call failed: {e}"}

    def get_openai_schemas(self) -> List[dict]:
        """Return OpenAI-format tool schemas for all active MCP tools."""
        return list(self._tool_cache)

    def has_tool(self, namespaced_name: str) -> bool:
        """Return True if this namespaced name belongs to a known MCP tool."""
        return namespaced_name in self._tool_routing

    def get_tool_help(self) -> dict:
        """
        Return {server_name: {"active": bool, "tools": {tool_name: description}}}
        for ALL servers (active and inactive), for use by help(tool_name="mcp_tools").
        """
        result = {}
        for name, sess in self._all_sessions.items():
            result[name] = {
                "active": sess.connected,
                "tools": {t.name: (t.description or "") for t in sess.tools}
            }
        return result

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _load_config(self) -> List[dict]:
        if not self.config_path.exists():
            return []
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            return data.get("servers", [])
        except Exception as e:
            self.logger.warning(f"MCP config parse error: {e}")
            return []

    def _toggle_active_in_config(self, activate: str, deactivate: str) -> None:
        """Update active flags in .mcp-servers.json and save."""
        try:
            if self.config_path.exists():
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
            else:
                data = {"servers": []}
            for srv in data.get("servers", []):
                if activate and srv.get("name") == activate:
                    srv["active"] = True
                if deactivate and srv.get("name") == deactivate:
                    srv["active"] = False
            self.config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            self.logger.warning(f"MCP config update failed: {e}")

    async def _connect_server(self, sess: MCPServerSession) -> None:
        """Open transport and session for a single server config."""
        try:
            if sess.transport == "stdio":
                await self._connect_stdio(sess)
            elif sess.transport == "sse":
                await self._connect_sse(sess)
            else:
                self.logger.warning(f"MCP: Unknown transport '{sess.transport}' for server '{sess.name}'")
                return

            if sess.session:
                await self._discover_tools(sess)
                sess.connected = True
                self.logger.info(f"MCP: Connected '{sess.name}' ({len(sess.tools)} tools)")
        except Exception as e:
            self.logger.warning(f"MCP: Failed to connect '{sess.name}': {e}")
            sess.connected = False

    async def _connect_stdio(self, sess: MCPServerSession) -> None:
        cfg = sess.config
        cmd = cfg.get("command", [])
        if not cmd:
            raise ValueError(f"Server '{sess.name}' has no 'command' defined")
        env = cfg.get("env") or None

        params = StdioServerParameters(command=cmd[0], args=cmd[1:], env=env)
        ctx = stdio_client(params)
        read, write = await ctx.__aenter__()
        sess._ctx_exit = ctx.__aexit__
        client_session = ClientSession(read, write)
        await client_session.__aenter__()
        await client_session.initialize()
        sess.session = client_session

    async def _connect_sse(self, sess: MCPServerSession) -> None:
        url = sess.config.get("url")
        if not url:
            raise ValueError(f"Server '{sess.name}' has no 'url' defined")
        ctx = sse_client(url)
        read, write = await ctx.__aenter__()
        sess._ctx_exit = ctx.__aexit__
        client_session = ClientSession(read, write)
        await client_session.__aenter__()
        await client_session.initialize()
        sess.session = client_session

    async def _disconnect_server(self, sess: MCPServerSession) -> None:
        """Close session and transport cleanly."""
        try:
            if sess.session:
                await sess.session.__aexit__(None, None, None)
                sess.session = None
        except Exception:
            pass
        try:
            if sess._ctx_exit:
                await sess._ctx_exit(None, None, None)
                sess._ctx_exit = None
        except Exception:
            pass
        sess.connected = False

    async def _discover_tools(self, sess: MCPServerSession) -> None:
        """Call list_tools() and cache results on the session."""
        response = await sess.session.list_tools()
        sess.tools = response.tools if hasattr(response, "tools") else []

    def _rebuild_tool_cache(self) -> None:
        """Rebuild OpenAI schema cache from all currently connected sessions."""
        self._tool_cache = []
        self._tool_routing = {}
        for name, sess in self.sessions.items():
            if sess.connected:
                for tool in sess.tools:
                    schema = self._convert_mcp_to_openai(name, tool)
                    self._tool_cache.append(schema)

    def _convert_mcp_to_openai(self, server_name: str, mcp_tool) -> dict:
        """Convert a single MCP tool to OpenAI function-calling format."""
        namespaced = f"mcp_{server_name}__{mcp_tool.name}"
        self._tool_routing[namespaced] = (server_name, mcp_tool.name)
        desc = mcp_tool.description or ""
        return {
            "type": "function",
            "function": {
                "name": namespaced,
                "description": f"[{server_name}] {desc[:200]}",
                "parameters": mcp_tool.inputSchema or {"type": "object", "properties": {}, "required": []}
            }
        }

    def _mcp_result_to_dict(self, result) -> dict:
        """Convert MCP CallToolResult to a plain dict for patch_abe_outcome."""
        if result is None:
            return {"status": "success", "content": None}
        # MCP result has a .content list of content blocks
        content = getattr(result, "content", None)
        is_error = getattr(result, "isError", False)
        if content:
            # Flatten text blocks into a string
            parts = []
            for block in content:
                if hasattr(block, "text"):
                    parts.append(block.text)
                elif hasattr(block, "data"):
                    parts.append(f"[binary data {len(block.data)} bytes]")
                else:
                    parts.append(str(block))
            text = "\n".join(parts)
        else:
            text = str(result)

        return {
            "status": "error" if is_error else "success",
            "content": text
        }
