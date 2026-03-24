"""
Tool Registry for GUPPI v8.0+
Replaces the hardcoded if/elif dispatch chain in execute_action().
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ToolDef:
    name: str
    description: str
    handler: Callable        # async def(daemon, turn_id, action) -> dict | None
    source: str = "builtin"  # "builtin" | "mcp:<server>" | "skill:<name>"
    flash_allowed: bool = True
    quiet: bool = False      # True = suppress success notification on non-error
    returns_async: bool = False  # True = handler manages its own patch_abe_outcome


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, ToolDef] = {}

    def register(self, tool_def: ToolDef):
        self._tools[tool_def.name] = tool_def

    def unregister(self, name: str):
        self._tools.pop(name, None)

    def get(self, name: str) -> Optional[ToolDef]:
        return self._tools.get(name)

    def is_flash_forbidden(self, name: str) -> bool:
        tool = self._tools.get(name)
        # Unknown tools are also forbidden for Flash
        return tool is None or not tool.flash_allowed

    def get_help(self, tool_name: Optional[str] = None):
        if tool_name:
            t = self._tools.get(tool_name)
            return t.description if t else "Unknown tool"
        return {n: t.description for n, t in self._tools.items()}

    def list_for_context(self) -> List[Dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "source": t.source}
            for t in self._tools.values()
        ]
