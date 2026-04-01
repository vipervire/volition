# Volition Skills

Skills are `.skill.md` files that inject additional context and tools into an agent's think cycle. They activate based on keyword matching, explicit agent activation, or always-on mode.

## File Format

```markdown
---
name: skill-name
description: One-line description shown in skill_list
tier: pro | flash | both
activation: trigger | manual | always
keywords: comma, separated, trigger, words
flash_forbidden: true | false
tools:
  - name: tool_name
    description: What this tool does
    handler: shell
    template: "command with {param_name} substitution"
    parameters:
      param_name: {type: string, description: Parameter description, required: true}
---

Prompt text injected into [ACTIVE_SKILLS] block when this skill is active.
Capped at 800 characters in context.
```

## Fields

| Field | Values | Description |
|-------|--------|-------------|
| `name` | string | Unique identifier (used in skill_manage tool) |
| `description` | string | Shown in skill_list output |
| `tier` | `pro` / `flash` / `both` | Which model tier can use this skill |
| `activation` | `trigger` / `manual` / `always` | How the skill activates |
| `keywords` | comma list | Triggers activation when matched in event context |
| `flash_forbidden` | bool | Whether skill-defined tools are blocked for Flash tier |
| `tools` | list | Optional tool definitions (shell-template based) |

## Activation Modes

- **`trigger`**: Activates automatically when keywords appear in the current event. Up to 3 trigger skills can be active simultaneously.
- **`manual`**: Only activates when explicitly turned on via `skill_manage` → `activate`. Persists across restarts.
- **`always`**: Always injected every think cycle. Use sparingly — costs tokens every call.

## Tool Templates

Skill tools delegate to the `shell` handler. Template parameters are substituted with `shlex.quote()` values for safety:

```
template: "cd {repo_path} && git status"
```

Tool names are namespaced as `skillname:toolname` to avoid collisions with core GUPPI tools.

## Locations

- `<repo>/skills/` — System/shared skills (deployed with code, available to all agents)
- `~/skills/` — Agent-authored skills (personal, override system skills by name)
- `~/.abe-active-skills` — Persisted manual activations (JSON array of skill names)

## Self-Authoring

Agents can create their own skills using the `write_file` tool:

```
write_file ~/skills/my-skill.skill.md
```

After writing, use `skill_manage` with `action: "refresh"` to reload, then `action: "activate"` if it's a manual-mode skill.
