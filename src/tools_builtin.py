"""
Built-in tool handlers for GUPPI v8.0+
Each function is async def handler(daemon, turn_id, action) -> dict | None.
Returning None means the handler managed its own patch_abe_outcome call.
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from tool_registry import ToolDef, ToolRegistry


# ---------------------------------------------------------------------------
# Helpers (re-use daemon internals via the passed daemon reference)
# ---------------------------------------------------------------------------

async def _retry(daemon, *args, **kwargs):
    """Shortcut to daemon's retry_async utility."""
    # Import retry_async from the guppi module at runtime to avoid circular import
    from guppi import retry_async
    return await retry_async(*args, **kwargs)


# ---------------------------------------------------------------------------
# Built-in handlers
# ---------------------------------------------------------------------------

async def tool_help(daemon, turn_id, action):
    return daemon.registry.get_help(action.get("tool_name"))


async def tool_manage_clipboard(daemon, turn_id, action):
    sub = action.get("action", "read")
    if sub == "read":
        return {"status": "success", "content": daemon.clipboard.read()}
    elif sub == "add":
        return {"status": "success", "message": daemon.clipboard.add(action.get("content", ""))}
    elif sub == "remove":
        idx = action.get("index") or action.get("indices")
        if idx:
            if isinstance(idx, (str, int)):
                idx = [int(idx)]
            return {"status": "success", "message": daemon.clipboard.remove(idx)}
        else:
            return {"status": "error", "message": "Missing index"}
    elif sub == "clear":
        return {"status": "success", "message": daemon.clipboard.clear()}
    return {"status": "error", "message": f"Unknown clipboard action: {sub}"}


async def tool_shell(daemon, turn_id, action):
    cmd = action.get("command")
    await daemon._spawn_subprocess_exec(turn_id, cmd, tracked=True)
    return None  # async: handler manages its own outcome


async def tool_remote_exec(daemon, turn_id, action):
    host = action.get("host")
    cmd = action.get("command")
    asyncio.create_task(daemon._run_remote_ssh(turn_id, host, cmd))
    return None  # async


async def tool_write_file(daemon, turn_id, action):
    from guppi import IDENTITY_FILE, PRIORS_SOURCE_FILE
    result = {"status": "success"}
    p = Path(action["path"]).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    mode = action.get("mode", "w")
    with open(p, mode) as f:
        f.write(action["content"])

    resolved_p = p.resolve()
    if resolved_p == IDENTITY_FILE.resolve():
        daemon._refresh_identity()
        result["note"] = f"Identity hot-reloaded. You are now known as: {daemon.display_name}"
    elif resolved_p == PRIORS_SOURCE_FILE.resolve():
        await daemon._trigger_priors_compression()
        result["note"] = "Priors updated. Scribe spawned to regenerate stub."

    result["path"] = str(p)
    return result


async def tool_spawn_roamer(daemon, turn_id, action):
    from guppi import BIN_DIR
    directive = action.get("directive")
    target_host = action.get("target_host", "local")

    if not directive:
        return {"status": "error", "message": "Missing directive for roamer"}

    cmd = [
        sys.executable, str(BIN_DIR / "roamer.py"),
        "--directive", directive,
        "--target-host", target_host,
        "--output-inbox", f"inbox:{daemon.abe_name}",
        "--model", os.environ.get("MODEL_ROAMER", "qwen-2.5-14b-coder"),
    ]
    await daemon._spawn_subprocess_exec(turn_id, cmd, tracked=False)
    return {"status": "spawned_untracked", "note": f"Roamer dispatched to investigate '{target_host}'. Results will arrive in your inbox."}


async def tool_spawn_scribe(daemon, turn_id, action):
    from guppi import BIN_DIR, MODEL_FLASH
    mode = action.get("mode", "summarize")
    prompt_file_path = action.get("prompt_file") or action.get("target_file")
    prompt_text = action.get("prompt", "")

    if mode == "analyze":
        model = os.environ.get("MODEL_SCRIBE", "local/nanbeige-4.1-3B")
    elif mode == "summarize":
        model = os.environ.get("MODEL_SUMMARIZE", "local/mistral")
    else:
        model = MODEL_FLASH

    if mode == "vectorize":
        if prompt_file_path is None:
            return {"status": "error", "message": "prompt_file is required for mode='vectorize'"}
        try:
            p_path = Path(prompt_file_path)
            if p_path.exists():
                content = p_path.read_text(encoding="utf-8")
                vec_task_id = f"vec-{turn_id}"
                await daemon.r.set(f"vec_meta:{vec_task_id}", str(p_path.resolve()), ex=3600)
                task_payload = {
                    "task_id": vec_task_id,
                    "type": "embed",
                    "content": content,
                    "source_file": str(p_path.resolve()),
                    "reply_to": daemon.internal_queue
                }
                await daemon.r.lpush("queue:gpu_heavy", json.dumps(task_payload))
                return {"status": "offloaded_to_gpu", "note": "Content sent to GPU for embedding. You will be notified."}
            else:
                return {"status": "error", "message": f"Prompt File not found for Vectorization: {prompt_file_path}"}
        except Exception as e:
            return {"status": "error", "message": f"Read error during offload: {e}"}
    else:
        combined_content = ""
        if prompt_text:
            combined_content += f"{prompt_text}\n\n"
        if prompt_file_path:
            p_path = Path(prompt_file_path)
            if p_path.exists():
                file_content = p_path.read_text(encoding="utf-8")
                combined_content += f"--- FILE CONTENT ({prompt_file_path}) ---\n{file_content}\n"
            else:
                combined_content += f"--- FILE MISSING: {prompt_file_path} ---\n"

        with tempfile.NamedTemporaryFile('w', delete=False) as pf:
            pf.write(combined_content)
            final_prompt_file = pf.name

        meta_dict = {"action_id": turn_id, "mode": mode}
        cmd = [
            sys.executable, str(BIN_DIR / "scribe.py"),
            "--model", model,
            "--prompt-file", final_prompt_file,
            "--output-inbox", f"inbox:{daemon.abe_name}",
            "--mode", mode,
            "--meta", json.dumps(meta_dict)
        ]
        await daemon._spawn_subprocess_exec(turn_id, cmd, tracked=False)
        return {"status": "spawned_untracked", "note": "Scribe result will arrive in inbox"}


async def tool_spawn_abe(daemon, turn_id, action):
    await daemon._handle_spawn_abe(turn_id, action)
    return None  # async


async def tool_rag_search(daemon, turn_id, action):
    query = action.get("query")
    matches = await daemon._query_vector_db(query)
    return {"matches": matches}


async def tool_todo_list(daemon, turn_id, action):
    return await daemon._tool_todo_list(action.get("filter", "due"))


async def tool_todo_add(daemon, turn_id, action):
    return await daemon._tool_todo_add(action)


async def tool_todo_complete(daemon, turn_id, action):
    return await daemon._tool_todo_complete(action)


async def tool_snooze_task(daemon, turn_id, action):
    return await daemon._tool_snooze(action)


async def tool_subscribe_channel(daemon, turn_id, action):
    from guppi import STREAM_DENY_LIST
    channel = action.get("channel")
    if channel in STREAM_DENY_LIST:
        return {"status": "error", "message": f"Channel '{channel}' is restricted."}
    elif channel:
        daemon.active_streams[channel] = "$"
        daemon.explicit_subscriptions.add(channel)
        daemon.subs_file.write_text(json.dumps(list(daemon.explicit_subscriptions)))
        return {"status": "subscribed", "channel": channel}
    else:
        return {"status": "error", "message": "No channel specified"}


async def tool_unsubscribe_channel(daemon, turn_id, action):
    channel = action.get("channel")
    if channel == "chat:synchronous":
        return {"status": "error", "message": "Cannot unsubscribe from Emergency channel."}
    elif channel in daemon.explicit_subscriptions:
        daemon.explicit_subscriptions.remove(channel)
        daemon.subs_file.write_text(json.dumps(list(daemon.explicit_subscriptions)))
        return {"status": "unsubscribed", "channel": channel, "note": "You will still be woken by @mentions."}
    else:
        return {"status": "noop", "message": "Not subscribed."}


async def tool_chat_history(daemon, turn_id, action):
    channel = action.get("channel", "chat:general")
    limit = min(int(action.get("limit", 10)), 20)
    history = await daemon._fetch_chat_context(channel, count=limit)
    return {"channel": channel, "history": history}


async def tool_email_send(daemon, turn_id, action):
    from guppi import retry_async
    target = action.get("recipient")
    if target and not target.startswith("inbox:"):
        target = f"inbox:{target}"
    msg = {"from": daemon.display_name, "event_type": "NewInboxMessage", "content": action.get("message")}
    await retry_async(daemon.r.lpush, target, json.dumps(msg))
    return {"status": "success", "recipient": target}


async def tool_chat_post(daemon, turn_id, action):
    from guppi import retry_async
    channel = action.get("channel", "chat:general")
    entry = {"from": daemon.display_name, "content": action.get("message"), "timestamp": datetime.utcnow().isoformat()}

    lock_key = f"lock:{channel}"
    lock_owner = await daemon.r.get(lock_key)
    if lock_owner == daemon.abe_name:
        await daemon.r.delete(lock_key)

    await retry_async(daemon.r.xadd, channel, entry)
    return {"status": "success"}


async def tool_chat_grab_stick(daemon, turn_id, action):
    from guppi import DEFAULT_LOCK_TTL_MS, retry_async
    channel = action.get("channel", "chat:synchronous")
    lock_key = f"lock:{channel}"
    acquired = await daemon.r.set(lock_key, daemon.abe_name, nx=True, px=DEFAULT_LOCK_TTL_MS)
    if acquired:
        entry = {"from": daemon.abe_name, "content": "I am speaking.", "type": "grab_stick", "timestamp": datetime.utcnow().isoformat()}
        await retry_async(daemon.r.xadd, channel, entry)
        return {"status": "granted", "channel": channel, "note": f"You hold the stick for {DEFAULT_LOCK_TTL_MS/1000}s"}
    else:
        current_owner = await daemon.r.get(lock_key)
        return {"status": "denied", "channel": channel, "current_speaker": current_owner or "unknown"}


async def tool_chat_ignore(daemon, turn_id, action):
    await daemon.patch_abe_outcome(turn_id, {"status": "ignored"}, notify=False)
    return None  # handler managed its own patch


async def tool_notify_human(daemon, turn_id, action):
    return await _tool_human_notify_impl(daemon, turn_id, action, kind="NOTIFY")


async def tool_alert_human(daemon, turn_id, action):
    return await _tool_human_notify_impl(daemon, turn_id, action, kind="ALERT")


async def _tool_human_notify_impl(daemon, turn_id, action, kind):
    from guppi import NTFY_URL, NTFY_TOKEN
    if not NTFY_URL:
        return {
            "status": "skipped",
            "reason": "ntfy_not_configured. Human may not be contactable. You might have to wait until they check in."
        }
    msg = action.get("message", "")
    prio = action.get("priority", "default")
    headers = {"Priority": prio}
    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                NTFY_URL,
                data=f"[{kind}] {daemon.abe_name}: {msg}",
                headers=headers
            ) as resp:
                return {"status": "sent", "code": resp.status, "kind": kind}
    except Exception as e:
        return {"status": "failed", "error": str(e), "kind": kind}


async def tool_web_search(daemon, turn_id, action):
    return await daemon._tool_web_search(action.get("query"))


async def tool_web_read(daemon, turn_id, action):
    return await daemon._tool_web_read(action.get("url"))


async def tool_hibernate(daemon, turn_id, action):
    await daemon.patch_abe_outcome(turn_id, {"status": "hibernating"}, notify=False)
    return None  # handler managed its own patch


# ---------------------------------------------------------------------------
# MCP management tools (Phase 2)
# ---------------------------------------------------------------------------

async def tool_mcp_connect(daemon, turn_id, action):
    if not hasattr(daemon, "mcp"):
        return {"status": "error", "message": "MCP not initialized"}
    server_name = action.get("server_name")
    url = action.get("url")
    if not server_name or not url:
        return {"status": "error", "message": "Required args: server_name, url"}
    headers = action.get("headers", {})
    flash_allowed = action.get("flash_allowed_tools", [])
    await daemon.mcp.connect(server_name, url, headers)
    await daemon.mcp.discover_and_register(server_name, flash_allowed_tools=flash_allowed)
    tools = [n for n in daemon.registry._tools if n.startswith(f"mcp.{server_name}.")]
    return {"status": "connected", "server": server_name, "tools_registered": len(tools), "tools": tools}


async def tool_mcp_disconnect(daemon, turn_id, action):
    if not hasattr(daemon, "mcp"):
        return {"status": "error", "message": "MCP not initialized"}
    server_name = action.get("server_name")
    if not server_name:
        return {"status": "error", "message": "Required arg: server_name"}
    await daemon.mcp.disconnect(server_name)
    return {"status": "disconnected", "server": server_name}


async def tool_mcp_list(daemon, turn_id, action):
    if not hasattr(daemon, "mcp"):
        return {"status": "error", "message": "MCP not initialized"}
    connections = daemon.mcp.list_connections()
    tools_by_server = {}
    for name, url in connections.items():
        tools_by_server[name] = {
            "url": url,
            "tools": [n for n in daemon.registry._tools if n.startswith(f"mcp.{name}.")]
        }
    return {"status": "success", "servers": tools_by_server}


# ---------------------------------------------------------------------------
# Skill management tools (Phase 3)
# ---------------------------------------------------------------------------

async def tool_skill_load(daemon, turn_id, action):
    if not hasattr(daemon, "skills"):
        return {"status": "error", "message": "Skills not initialized"}
    skill_name = action.get("skill")
    if not skill_name:
        return {"status": "error", "message": "Required arg: skill"}
    return await daemon.skills.load_skill(skill_name, daemon=daemon)


async def tool_skill_unload(daemon, turn_id, action):
    if not hasattr(daemon, "skills"):
        return {"status": "error", "message": "Skills not initialized"}
    skill_name = action.get("skill")
    if not skill_name:
        return {"status": "error", "message": "Required arg: skill"}
    return await daemon.skills.unload_skill(skill_name)


async def tool_skill_list(daemon, turn_id, action):
    if not hasattr(daemon, "skills"):
        return {"status": "error", "message": "Skills not initialized"}
    return {"status": "success", **daemon.skills.list_skills()}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_all(registry, daemon):
    """Register all built-in tools into the registry. Called from GuppiDaemon.__init__()."""
    from tool_registry import ToolDef

    tools = [
        ToolDef("help",               "Show available tools. Args: tool_name (optional)",                                           tool_help,               flash_allowed=True),
        ToolDef("manage_clipboard",   "Manage your persistent scratchpad. actions: 'read', 'add' (requires content), 'remove' (requires index or list of indices), 'clear'. Items here survive log flushing. Use this for temporary constraints, reminders, or scratch notes.", tool_manage_clipboard, flash_allowed=True),
        ToolDef("shell",              "Execute local shell command. Args: command",                                                  tool_shell,              flash_allowed=False, returns_async=True),
        ToolDef("remote_exec",        "Execute remote SSH command. Args: host, command",                                             tool_remote_exec,        flash_allowed=False, returns_async=True),
        ToolDef("write_file",         "Write/append text to a file. Args: path, content, mode (w|a)",                               tool_write_file,         flash_allowed=False),
        ToolDef("spawn_roamer",       "Spawn a multi-turn, read-only Investigator. Args: directive, target_host (optional, default: local). Use to trace logs, map directories, or debug configs without burning your context. Returns a markdown report.", tool_spawn_roamer, flash_allowed=False),
        ToolDef("spawn_scribe",       "Spawn a single-shot Scribe. Args: prompt, prompt_file (optional), mode (analyze|summarize|vectorize). GUPPI auto-routes the model. 'analyze' is for deep static analysis, 'summarize' compresses text, 'vectorize' offloads to GPU memory.", tool_spawn_scribe, flash_allowed=False),
        ToolDef("spawn_abe",          "Clone self. Args: host, identity",                                                           tool_spawn_abe,          flash_allowed=False, returns_async=True),
        ToolDef("rag_search",         "Search vector memory. Args: query",                                                          tool_rag_search,         flash_allowed=True),
        ToolDef("todo_list",          "List tasks. Args: filter (due|upcoming|all)",                                                 tool_todo_list,          flash_allowed=True),
        ToolDef("todo_add",           "Add task. Args: task, priority, due",                                                        tool_todo_add,           flash_allowed=True),
        ToolDef("todo_complete",      "Mark a task as completed. Args: task_id",                                                     tool_todo_complete,      flash_allowed=True),
        ToolDef("snooze_task",        "Snooze a task. Args: task_id, snooze_until",                                                  tool_snooze_task,        flash_allowed=True,  quiet=True),
        ToolDef("subscribe_channel",  "Listen to a Redis Stream. Args: channel",                                                     tool_subscribe_channel,  flash_allowed=True),
        ToolDef("unsubscribe_channel","Stop waking for a channel (except mentions). Args: channel",                                  tool_unsubscribe_channel,flash_allowed=True),
        ToolDef("chat_history",       "Fetch past messages. Args: channel, limit (max 20)",                                          tool_chat_history,       flash_allowed=True),
        ToolDef("email_send",         "Send Redis msg. Args: recipient, message",                                                    tool_email_send,         flash_allowed=True),
        ToolDef("chat_post",          "Post a message to a channel. If you hold the lock for this channel, it is automatically released. Args: message, channel (optional, default: chat:general)", tool_chat_post, flash_allowed=True),
        ToolDef("chat_grab_stick",    f"ATTEMPT to acquire the 'Talking Stick' (lock) for a specific channel (default: chat:synchronous). Returns {{status: granted|denied}}. Lock expires (use this time to THINK, then POST). Posting to the channel AUTOMATICALLY releases the lock. DO NOT hold the stick if you do not intend to post. Args: channel (optional)", tool_chat_grab_stick, flash_allowed=True, quiet=True),
        ToolDef("chat_ignore",        "Explicitly ignore an interrupt (e.g., chat) without taking action. Use this to signal 'Active Listening' without replying.", tool_chat_ignore, flash_allowed=True, returns_async=True),
        ToolDef("notify_human",       "Notify the human operator for coordination, questions, or permission. Use when you need a human decision before proceeding. This is non-urgent. Args: message, priority (optional)", tool_notify_human, flash_allowed=True),
        ToolDef("alert_human",        "Alert the human operator about urgent issues, safety concerns, or broken invariants. Use sparingly for situations requiring immediate attention. Args: message, priority (optional)", tool_alert_human, flash_allowed=True),
        ToolDef("web_search",         "Search the internet via SearXNG. Args: query",                                               tool_web_search,         flash_allowed=True),
        ToolDef("web_read",           "Read a webpage as Markdown. More useful when used in conjunction with search. Args: url",     tool_web_read,           flash_allowed=True),
        ToolDef("hibernate",          "Go to sleep.",                                                                                tool_hibernate,          flash_allowed=True,  returns_async=True),
        # MCP management tools (Phase 2)
        ToolDef("mcp_connect",        "Connect to an MCP server over HTTP. Args: server_name, url, headers (optional), flash_allowed_tools (optional list)", tool_mcp_connect, flash_allowed=False),
        ToolDef("mcp_disconnect",     "Disconnect an MCP server and remove its tools. Args: server_name",                            tool_mcp_disconnect,     flash_allowed=False),
        ToolDef("mcp_list",           "List connected MCP servers and their registered tools.",                                       tool_mcp_list,           flash_allowed=True),
        # Skill management tools (Phase 3)
        ToolDef("skill_load",         "Load a skill from ~/skills/<name>/. Args: skill",                                             tool_skill_load,         flash_allowed=False),
        ToolDef("skill_unload",       "Unload a skill and disconnect its MCP servers. Args: skill",                                  tool_skill_unload,       flash_allowed=False),
        ToolDef("skill_list",         "List available and loaded skills.",                                                            tool_skill_list,         flash_allowed=True),
    ]

    for t in tools:
        registry.register(t)
