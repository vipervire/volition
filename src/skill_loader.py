"""
Skill discovery, loading, and activation for Volition agents.

Skills are .skill.md files with YAML-style frontmatter defining:
- Metadata (name, description, tier, activation mode, keywords)
- Optional tool definitions that delegate to existing handlers
- Prompt text injected into the agent's context when the skill is active

System skills live in <repo>/skills/, agent-authored skills in ~/skills/.
User skills override system skills with the same name.
"""

import hashlib
import json
import logging
import re
import shlex
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger("guppi.skills")

MAX_ACTIVE_SKILLS = 3
SKILL_PROMPT_MAX_CHARS = 800


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """
    Parse YAML-style frontmatter from a markdown file.
    Returns (metadata_dict, body_text). Returns ({}, text) if no frontmatter.
    Supports: string, bool, list (comma-separated on one line or block list).
    """
    match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)$', text, re.DOTALL)
    if not match:
        return {}, text

    raw_fm, body = match.group(1), match.group(2)
    meta: dict = {}

    # Parse line by line
    lines = raw_fm.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        kv = re.match(r'^(\w+)\s*:\s*(.*)$', line)
        if not kv:
            i += 1
            continue
        key, val = kv.group(1), kv.group(2).strip()

        # Block list (next lines start with "  -")
        if val == '' and i + 1 < len(lines) and re.match(r'^\s+-', lines[i + 1]):
            items = []
            i += 1
            while i < len(lines) and re.match(r'^\s+-\s*(.*)', lines[i]):
                item_match = re.match(r'^\s+-\s*(.*)', lines[i])
                items.append(item_match.group(1).strip())
                i += 1
            meta[key] = items
            continue

        # Boolean
        if val.lower() in ('true', 'yes'):
            meta[key] = True
        elif val.lower() in ('false', 'no'):
            meta[key] = False
        # Inline list (comma-separated)
        elif ',' in val and not val.startswith('"'):
            meta[key] = [v.strip() for v in val.split(',') if v.strip()]
        else:
            meta[key] = val.strip('"').strip("'")

        i += 1

    return meta, body.strip()


def _parse_tool_block(raw: str) -> List[dict]:
    """
    Parse a simple tool definition block from frontmatter.
    Format:
      tools:
        - name: tool_name
          description: ...
          handler: shell
          template: "cmd {param}"
          parameters:
            param: {type: string, description: ..., required: true}

    This is a best-effort parser for the subset used in skill files.
    Returns a list of tool dicts.
    """
    tools = []
    # Split into per-tool blocks by "  - name:"
    blocks = re.split(r'\n\s{2,4}-\s+(?=\w+\s*:)', '\n' + raw.strip())
    for block in blocks:
        if not block.strip():
            continue
        tool: dict = {}
        params: dict = {}

        for line in block.split('\n'):
            # Top-level field
            m = re.match(r'^\s{2,4}(\w+)\s*:\s*(.+)$', line)
            if m and not re.match(r'^\s{6,}', line):
                tool[m.group(1)] = m.group(2).strip().strip('"').strip("'")
                continue
            # Parameter definition: "    param: {type: ..., description: ..., required: ...}"
            pm = re.match(r'^\s{6,}(\w+)\s*:\s*\{(.+)\}', line)
            if pm:
                pname = pm.group(1)
                pdef: dict = {}
                for part in pm.group(2).split(','):
                    kv = re.match(r'\s*(\w+)\s*:\s*(.+)', part.strip())
                    if kv:
                        k, v = kv.group(1), kv.group(2).strip()
                        if k == 'required':
                            pdef[k] = v.lower() in ('true', 'yes')
                        else:
                            pdef[k] = v.strip('"').strip("'")
                params[pname] = pdef

        if 'name' in tool:
            tool['parameters'] = params
            tools.append(tool)

    return tools


class Skill:
    """Parsed representation of a single .skill.md file."""

    __slots__ = (
        'name', 'description', 'tier', 'activation_mode',
        'keywords', 'tools', 'flash_forbidden',
        'prompt_text', 'source_path', 'checksum'
    )

    def __init__(self):
        self.name = ''
        self.description = ''
        self.tier = 'both'
        self.activation_mode = 'trigger'
        self.keywords: List[str] = []
        self.tools: List[dict] = []
        self.flash_forbidden = False
        self.prompt_text = ''
        self.source_path: Optional[Path] = None
        self.checksum = ''

    @classmethod
    def from_file(cls, path: Path) -> Optional['Skill']:
        """Parse a .skill.md file. Returns None if invalid."""
        try:
            text = path.read_text(encoding='utf-8')
            checksum = hashlib.md5(text.encode()).hexdigest()
            meta, body = _parse_frontmatter(text)

            if not meta.get('name'):
                logger.warning(f"Skill file missing 'name': {path}")
                return None

            skill = cls()
            skill.source_path = path
            skill.checksum = checksum
            skill.name = str(meta['name'])
            skill.description = str(meta.get('description', ''))
            skill.tier = str(meta.get('tier', 'both')).lower()
            skill.activation_mode = str(meta.get('activation', 'trigger')).lower()
            skill.flash_forbidden = bool(meta.get('flash_forbidden', False))
            skill.prompt_text = body[:SKILL_PROMPT_MAX_CHARS]

            # Keywords: list or comma-string already normalized by frontmatter parser
            kw = meta.get('keywords', [])
            if isinstance(kw, list):
                skill.keywords = kw
            elif isinstance(kw, str):
                skill.keywords = [k.strip() for k in kw.split(',') if k.strip()]

            # Tools: parse raw frontmatter section if present
            # The frontmatter parser stores 'tools' as a list of strings (block list items)
            # We need to re-parse from the raw frontmatter for structured tool defs
            raw_tools = meta.get('tools', [])
            if isinstance(raw_tools, list) and raw_tools and isinstance(raw_tools[0], str):
                # Re-extract the tools block from raw frontmatter for structured parsing
                fm_match = re.match(r'^---\s*\n(.*?)\n---', text, re.DOTALL)
                if fm_match:
                    tools_block_match = re.search(
                        r'^tools\s*:\s*\n((?:\s{2,}.+\n?)+)',
                        fm_match.group(1),
                        re.MULTILINE
                    )
                    if tools_block_match:
                        skill.tools = _parse_tool_block(tools_block_match.group(1))

            return skill
        except Exception as e:
            logger.warning(f"Failed to parse skill file {path}: {e}")
            return None

    def allows_tier(self, is_flash: bool) -> bool:
        """Returns True if this skill should be available for the given tier."""
        if not is_flash:
            return True
        return self.tier in ('flash', 'both')

    def keyword_score(self, text: str) -> int:
        """Count how many of this skill's keywords appear in the text (case-insensitive)."""
        text_lower = text.lower()
        return sum(1 for kw in self.keywords if kw.lower() in text_lower)


class SkillRegistry:
    """Discovers, caches, and queries skills from one or two directories."""

    def __init__(self, system_dir: Optional[Path], user_dir: Optional[Path]):
        self._system_dir = system_dir
        self._user_dir = user_dir
        self._cache: Dict[str, Skill] = {}         # name -> Skill
        self._checksums: Dict[str, str] = {}        # str(path) -> checksum
        self._last_scan: float = 0.0
        self._scan_interval = 60.0  # seconds between auto-rescans

    def scan(self, force: bool = False) -> int:
        """
        Scan skill directories for .skill.md files.
        Uses checksum comparison to skip unchanged files.
        System skills are loaded first; user skills override by name.
        Returns count of loaded skills.
        """
        now = time.monotonic()
        if not force and (now - self._last_scan) < self._scan_interval:
            return len(self._cache)

        self._last_scan = now
        found: Dict[str, Skill] = {}

        for directory in [self._system_dir, self._user_dir]:
            if not directory or not directory.exists():
                continue
            for path in sorted(directory.glob('*.skill.md')):
                path_key = str(path)
                text_hash = ''
                try:
                    content = path.read_bytes()
                    text_hash = hashlib.md5(content).hexdigest()
                except Exception:
                    continue

                # Use cached version if unchanged
                if (path_key in self._checksums and
                        self._checksums[path_key] == text_hash and
                        path_key in [str(s.source_path) for s in self._cache.values()]):
                    cached = next(
                        (s for s in self._cache.values() if str(s.source_path) == path_key),
                        None
                    )
                    if cached:
                        found[cached.name] = cached
                        continue

                skill = Skill.from_file(path)
                if skill:
                    found[skill.name] = skill
                    self._checksums[path_key] = text_hash
                    logger.debug(f"Loaded skill: {skill.name} from {path}")

        self._cache = found
        return len(self._cache)

    def get_active_skills(
        self,
        is_flash: bool,
        context_text: str,
        explicit_activations: Set[str]
    ) -> List[Skill]:
        """
        Determine which skills to activate for this think cycle.

        Priority:
        1. 'always' mode skills (tier-filtered)
        2. 'manual' mode skills in explicit_activations (tier-filtered)
        3. 'trigger' mode skills ranked by keyword score (tier-filtered)

        Hard cap: MAX_ACTIVE_SKILLS total.
        """
        active: List[Skill] = []
        trigger_candidates: List[tuple[int, Skill]] = []

        for skill in self._cache.values():
            if not skill.allows_tier(is_flash):
                continue

            if skill.activation_mode == 'always':
                active.append(skill)
            elif skill.activation_mode == 'manual' and skill.name in explicit_activations:
                active.append(skill)
            elif skill.activation_mode == 'trigger':
                score = skill.keyword_score(context_text)
                if score > 0:
                    trigger_candidates.append((score, skill))

        # Add trigger skills ranked by score, up to the cap
        trigger_candidates.sort(key=lambda x: x[0], reverse=True)
        for _, skill in trigger_candidates:
            if len(active) >= MAX_ACTIVE_SKILLS:
                break
            active.append(skill)

        return active[:MAX_ACTIVE_SKILLS]

    def get_tool_schemas(self, active_skills: List[Skill]) -> List[dict]:
        """
        Generate OpenAI function-calling schemas from active skills' tool definitions.
        Tool names are namespaced as 'skillname:toolname' to avoid collisions.
        """
        schemas = []
        for skill in active_skills:
            for tool_def in skill.tools:
                name = tool_def.get('name', '')
                description = tool_def.get('description', '')
                params_raw = tool_def.get('parameters', {})

                # Build JSON Schema properties from parameter definitions
                properties = {}
                required = []
                for pname, pdef in params_raw.items():
                    prop = {'type': pdef.get('type', 'string')}
                    if 'description' in pdef:
                        prop['description'] = pdef['description']
                    properties[pname] = prop
                    if pdef.get('required', False):
                        required.append(pname)

                schemas.append({
                    'type': 'function',
                    'function': {
                        'name': f'{skill.name}:{name}',
                        'description': description,
                        'parameters': {
                            'type': 'object',
                            'properties': properties,
                            'required': required
                        }
                    }
                })
        return schemas

    def get_skill(self, name: str) -> Optional[Skill]:
        return self._cache.get(name)

    def list_skills(self) -> List[dict]:
        """Return a summary list of all loaded skills."""
        return [
            {
                'name': s.name,
                'description': s.description,
                'tier': s.tier,
                'activation': s.activation_mode,
                'keywords': s.keywords,
                'tools': [t.get('name') for t in s.tools],
                'flash_forbidden': s.flash_forbidden,
                'source': str(s.source_path)
            }
            for s in sorted(self._cache.values(), key=lambda x: x.name)
        ]

    def execute_skill_tool(self, skill_name: str, tool_name: str, params: dict) -> Optional[str]:
        """
        Build a shell command from a skill tool template with safely-quoted parameters.
        Returns the command string, or None if the tool/skill is not found.
        """
        skill = self._cache.get(skill_name)
        if not skill:
            return None
        tool_def = next((t for t in skill.tools if t.get('name') == tool_name), None)
        if not tool_def:
            return None
        if tool_def.get('handler') != 'shell':
            return None

        template = tool_def.get('template', '')
        try:
            # Quote each parameter value before substitution to prevent injection
            quoted = {k: shlex.quote(str(v)) for k, v in params.items()}
            return template.format(**quoted)
        except KeyError as e:
            logger.warning(f"Skill tool '{skill_name}:{tool_name}' missing parameter: {e}")
            return None
