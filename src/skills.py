"""
Skills System for GUPPI v8.0+
Skills are composable capability packages:
  - context.md  : prompt augmentation injected into build_abe_context
  - skill.json  : manifest declaring MCP servers, flash policy, custom tools
  - tools.py    : optional custom tool handlers (register_tools(registry, daemon))

Active skills are persisted to ~/.abe-skills.json for restart recovery.

Skill directory: ~/skills/<skill_name>/
"""
from __future__ import annotations
import importlib.util
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mcp_client import MCPManager
    from tool_registry import ToolRegistry

logger = logging.getLogger("guppi.skills")

CONTEXT_MAX_CHARS = 2000


class SkillManager:
    def __init__(self, registry, mcp_manager, abe_root: Path, abe_name: str):
        self.registry = registry
        self.mcp = mcp_manager
        self.abe_root = abe_root
        self.abe_name = abe_name
        self.skills_dir = abe_root / "skills"
        self._persistence_file = abe_root / ".abe-skills.json"
        # skill_name -> manifest dict
        self.loaded_skills: Dict[str, Dict[str, Any]] = {}

    async def load_skill(self, skill_name: str, daemon=None) -> Dict[str, Any]:
        """Load a skill from ~/skills/<skill_name>/skill.json."""
        skill_dir = self.skills_dir / skill_name
        manifest_path = skill_dir / "skill.json"

        if not manifest_path.exists():
            return {"status": "error", "message": f"Skill '{skill_name}' not found at {skill_dir}"}

        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception as e:
            return {"status": "error", "message": f"Failed to parse skill manifest: {e}"}

        if skill_name in self.loaded_skills:
            return {"status": "already_loaded", "skill": skill_name}

        # 1. Queue declared MCP servers for lazy connection on first tool use
        for server_alias, server_cfg in manifest.get("mcp_servers", {}).items():
            qualified_name = f"{skill_name}.{server_alias}"
            url = server_cfg.get("url")
            if not url:
                logger.warning(f"Skill '{skill_name}' MCP server '{server_alias}' missing url, skipping")
                continue
            headers = server_cfg.get("headers", {})
            flash_allowed = server_cfg.get("flash_allowed_tools", [])
            self.mcp.queue_server(qualified_name, url, headers, flash_allowed_tools=flash_allowed)

        # 2. Apply flash_policy overrides on already-registered tools
        flash_policy = manifest.get("flash_policy", {})
        for tool_name in flash_policy.get("allow", []):
            tool = self.registry.get(tool_name)
            if tool:
                tool.flash_allowed = True
                logger.info(f"Skill '{skill_name}' granted Flash access to: {tool_name}")

        # 3. Load custom tools.py via importlib
        custom_tools_filename = manifest.get("custom_tools")
        if custom_tools_filename and daemon is not None:
            tools_path = skill_dir / custom_tools_filename
            if tools_path.exists():
                try:
                    spec = importlib.util.spec_from_file_location(
                        f"skill_{skill_name}_tools", str(tools_path)
                    )
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    if hasattr(module, "register_tools"):
                        module.register_tools(self.registry, daemon)
                        logger.info(f"Skill '{skill_name}': loaded custom tools from {tools_path}")
                    else:
                        logger.warning(f"Skill '{skill_name}': tools.py missing register_tools(registry, daemon)")
                except Exception as e:
                    logger.error(f"Skill '{skill_name}': failed to load tools.py: {e}")

        self.loaded_skills[skill_name] = manifest
        self._persist()
        mcp_servers = list(manifest.get("mcp_servers", {}).keys())
        return {"status": "loaded", "skill": skill_name, "mcp_servers": mcp_servers}

    async def unload_skill(self, skill_name: str) -> Dict[str, Any]:
        """Unload a skill: disconnect its MCP servers, unregister its tools."""
        if skill_name not in self.loaded_skills:
            return {"status": "not_loaded", "skill": skill_name}

        manifest = self.loaded_skills[skill_name]

        # Disconnect or dequeue MCP servers registered by this skill
        for server_alias in manifest.get("mcp_servers", {}):
            qualified_name = f"{skill_name}.{server_alias}"
            self.mcp._pending_configs.pop(qualified_name, None)
            await self.mcp.disconnect(qualified_name)

        # Unregister any tools sourced from this skill
        to_remove = [
            name for name, t in list(self.registry._tools.items())
            if t.source == f"skill:{skill_name}"
        ]
        for name in to_remove:
            self.registry.unregister(name)

        del self.loaded_skills[skill_name]
        self._persist()
        return {"status": "unloaded", "skill": skill_name}

    def list_skills(self) -> Dict[str, Any]:
        """List skills: available on disk vs currently loaded."""
        available = []
        if self.skills_dir.exists():
            for d in self.skills_dir.iterdir():
                if d.is_dir() and (d / "skill.json").exists():
                    available.append(d.name)
        return {
            "available": sorted(available),
            "loaded": sorted(self.loaded_skills.keys()),
        }

    def get_context_blocks(self, event_type: Optional[str] = None) -> str:
        """Return context.md content from skills relevant to the current event type.

        Skills may declare a ``relevant_events`` list in their manifest. If set,
        the skill's context block is only injected when the current event_type
        matches one of the declared values. Skills without ``relevant_events``
        are always included (backward-compatible default).
        """
        blocks = []
        for skill_name, manifest in self.loaded_skills.items():
            relevant_events = manifest.get("relevant_events", [])
            if relevant_events and event_type and event_type not in relevant_events:
                logger.debug(
                    f"Skill '{skill_name}': skipping context (event_type='{event_type}' "
                    f"not in relevant_events={relevant_events})"
                )
                continue

            ctx_filename = manifest.get("context_file", "context.md")
            ctx_path = self.skills_dir / skill_name / ctx_filename
            if ctx_path.exists():
                try:
                    content = ctx_path.read_text(encoding="utf-8")
                    if len(content) > CONTEXT_MAX_CHARS:
                        content = content[:CONTEXT_MAX_CHARS] + f"\n... [TRUNCATED: {len(content) - CONTEXT_MAX_CHARS} chars removed]"
                    blocks.append(f"[SKILL: {skill_name}]\n{content}")
                except Exception as e:
                    logger.warning(f"Skill '{skill_name}': failed to read context.md: {e}")
        return "\n".join(blocks)

    async def restore_from_persistence(self, daemon=None) -> None:
        """On startup, reload previously active skills from ~/.abe-skills.json."""
        if not self._persistence_file.exists():
            return
        try:
            active = json.loads(self._persistence_file.read_text())
        except Exception as e:
            logger.warning(f"Failed to parse skills persistence file: {e}")
            return

        for skill_name in active:
            result = await self.load_skill(skill_name, daemon=daemon)
            logger.info(f"Restored skill '{skill_name}': {result.get('status')}")

    def _persist(self) -> None:
        """Write the list of active skill names to disk."""
        try:
            self._persistence_file.write_text(json.dumps(sorted(self.loaded_skills.keys())))
        except Exception as e:
            logger.warning(f"Failed to persist skills list: {e}")
