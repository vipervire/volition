"""
OpenAI-compatible tool schemas for GUPPI's toolset.
These replace the prompt-engineered tool documentation in the Genesis Prompt.
"""

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "help",
            "description": "Provides detailed documentation for a specific tool. If tool_name is omitted, lists all available tools.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "Name of the tool to look up. Omit to list all tools."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "Executes a non-blocking local shell command. GUPPI will message your inbox when done. Use for ls, cat, grep, and running scripts in ~/bin.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute."}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remote_exec",
            "description": "Executes a shell command on a remote host via SSH. Asynchronous — GUPPI messages your inbox with stdout/stderr when complete.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string", "description": "Hostname to SSH into (e.g. slv-wdym-buffering)."},
                    "command": {"type": "string", "description": "Shell command to run on the remote host."}
                },
                "required": ["host", "command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Writes or appends text to a file. Use mode 'w' to overwrite, 'a' to append. This is how you create tools and notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to write to (~ is expanded)."},
                    "content": {"type": "string", "description": "Text content to write."},
                    "mode": {"type": "string", "enum": ["w", "a"], "description": "Write mode: 'w' to overwrite (default), 'a' to append."}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_scribe",
            "description": "Spawns a single-shot Scribe worker. Results arrive in your inbox. Modes: 'analyze' (deep static analysis via Nanbeige), 'summarize' (text compression via Mistral), 'vectorize' (embed file to VectorDB via GPU).",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["analyze", "summarize", "vectorize"], "description": "Processing mode. GUPPI routes the model automatically."},
                    "prompt_file": {"type": "string", "description": "Path to a file to include as input content."},
                    "prompt": {"type": "string", "description": "Direct text prompt or instructions for the Scribe."}
                },
                "required": ["mode"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_roamer",
            "description": "Spawns a multi-turn read-only Investigator (Qwen-14B). Use to trace logs, map directories, or debug configs without burning your context. Returns a markdown report to your inbox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directive": {"type": "string", "description": "Investigation objective for the Roamer."},
                    "target_host": {"type": "string", "description": "Host to investigate. 'local' (default) or a remote hostname."}
                },
                "required": ["directive"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_abe",
            "description": "Clones yourself to create a new Abe peer. You must run spawn_advisor.sh first and include its output in your reasoning. After success, send the new Abe a Genesis Task via email_send.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host_type": {"type": "string", "enum": ["proxmox", "remote"], "description": "Type of host to spawn on."},
                    "host": {"type": "string", "description": "Target hostname."},
                    "identity": {
                        "type": "object",
                        "description": "Identity config for the new Abe.",
                        "properties": {
                            "name": {"type": "string"},
                            "temp": {"type": "string", "description": "Temperature (0.1-1.0 or 'rand')."},
                            "top_k": {"type": "string", "description": "Top-k value (0.0-1.0)."}
                        },
                        "required": ["name"]
                    }
                },
                "required": ["host_type", "host", "identity"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "rag_search",
            "description": "Searches your Tier 3 VectorDB semantic memory. Returns matching Tier 2 episode summaries to your inbox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language search query."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Searches the internet via the internal SearXNG instance. Returns titles, URLs, and snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query string."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_read",
            "description": "Fetches a webpage and converts it to clean Markdown. Read-only — no logins or form submissions. Use after web_search to read full content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL to fetch and convert."}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "todo_list",
            "description": "Lists your tasks from todo.db.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter": {"type": "string", "enum": ["due", "upcoming", "all"], "description": "'due' (overdue, default), 'upcoming' (next 24h), 'all' (everything)."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "todo_add",
            "description": "Adds a new task to your todo list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Task description."},
                    "priority": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Priority 1-10 (10 = most urgent)."},
                    "due": {"type": "string", "description": "Due time as ISO timestamp or relative (e.g. '1h', '30m', '2h')."}
                },
                "required": ["task"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "snooze_task",
            "description": "Postpones a task by pushing its due date forward.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "ID of the task to snooze."},
                    "due_in": {"type": "string", "description": "How far to snooze (e.g. '30m', '2h'). Default: 30m."}
                },
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "todo_complete",
            "description": "Marks a task as completed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "ID of the task to mark complete."}
                },
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_clipboard",
            "description": "Manages your persistent scratchpad that survives log flushing. Use to remember things: constraints, reminders, scratch notes. Actions: 'read', 'add' (requires content), 'remove' (requires index or indices), 'clear' (clears all — use remove when possible).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["read", "add", "remove", "clear"], "description": "Clipboard operation."},
                    "content": {"type": "string", "description": "Text to add (required for 'add')."},
                    "index": {"type": "integer", "description": "Single item index to remove (for 'remove')."},
                    "indices": {"type": "array", "items": {"type": "integer"}, "description": "List of item indices to remove (for 'remove')."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "email_send",
            "description": "Sends a private message (Redis List push) to another Abe, the Source, or yourself.",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient": {"type": "string", "description": "Recipient inbox name (e.g. 'abe-01' or 'inbox:abe-01')."},
                    "message": {"type": "string", "description": "Message content."}
                },
                "required": ["recipient", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "chat_post",
            "description": "Posts a message to a chat channel. If you hold the talking stick for this channel, it is automatically released after posting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Message to post."},
                    "channel": {"type": "string", "description": "Target channel (default: chat:general)."}
                },
                "required": ["message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "chat_history",
            "description": "Fetches past messages from a channel to catch up. Use sparingly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "Channel to fetch history from (default: chat:general)."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Number of messages to fetch (max 20)."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "subscribe_channel",
            "description": "Starts listening to a Redis Stream channel and wakes you when new messages arrive.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "Channel stream name to subscribe to."}
                },
                "required": ["channel"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "unsubscribe_channel",
            "description": "Stops waking for a channel except for @mentions. Remember to add a todo to resubscribe later.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "Channel stream name to unsubscribe from."}
                },
                "required": ["channel"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "chat_grab_stick",
            "description": "Attempts to acquire the 'Talking Stick' (lock) for a channel. If granted, you may then use chat_post. If denied, someone else is speaking. Do NOT hold the stick if you don't intend to post.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "Channel to acquire the lock for (default: chat:synchronous)."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "chat_ignore",
            "description": "Explicitly ignores a chat interrupt without responding. ONLY for chat:synchronous. Use this when you are not tagged and have nothing critical to add — signals 'active listening' to GUPPI.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "notify_human",
            "description": "Sends a non-urgent notification to the human operator (Source) via ntfy. Use when you need a human decision before proceeding.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Notification message."},
                    "priority": {"type": "string", "description": "ntfy priority level (default: 'default')."}
                },
                "required": ["message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "alert_human",
            "description": "Sends an URGENT alert to the human operator via ntfy. Use sparingly — only for safety concerns, broken invariants, or situations requiring immediate attention.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Alert message."},
                    "priority": {"type": "string", "description": "ntfy priority level (default: 'high')."}
                },
                "required": ["message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "hibernate",
            "description": "Tells GUPPI you have nothing to do. GUPPI will not wake you until the next external interrupt or a task becomes due.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_list",
            "description": "Lists all available skills, their activation mode, tier, and whether they are currently active. Use this to discover skills you can activate or author.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_manage",
            "description": "Activate, deactivate, or inspect a skill. Active skills inject extra instructions and tools into your think cycle. Use 'refresh' after writing a new skill file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["activate", "deactivate", "info", "refresh"],
                        "description": "'activate'/'deactivate' a skill by name; 'info' to see full details; 'refresh' to rescan skill directories after writing a new skill file."
                    },
                    "skill_name": {
                        "type": "string",
                        "description": "Name of the skill (required for activate/deactivate/info)."
                    }
                },
                "required": ["action"]
            }
        }
    },
]

# Tools the Flash (chat) tier is not allowed to execute.
# These are filtered out of the schema before sending, so Flash never sees them.
FLASH_FORBIDDEN_TOOLS = {"shell", "write_file", "spawn_abe", "remote_exec", "spawn_scribe"}


def get_schemas_for_tier(is_flash: bool) -> list:
    """Return tool schemas appropriate for the given model tier.

    Flash tier receives a reduced schema that excludes high-risk tools,
    preventing accidental or unauthorized use without requiring post-hoc escalation.
    """
    if not is_flash:
        return TOOL_SCHEMAS
    return [s for s in TOOL_SCHEMAS if s["function"]["name"] not in FLASH_FORBIDDEN_TOOLS]


# Auto-generated help dict for the 'help' tool handler.
TOOL_HELP = {s["function"]["name"]: s["function"]["description"] for s in TOOL_SCHEMAS}
