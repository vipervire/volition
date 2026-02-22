#!/usr/bin/env python3
"""
scribe.py - Production Scribe Worker for Volition 6.5

This is the ephemeral "Tasker" process spawned by GUPPI.
It performs heavy lifting (LLM calls) and reports back via Redis.

Status: RELEASE 6.5.2
- Architecture: Split-Brain Compatible
- Provider: Claude CLI (claude --print)
- DEPRECATION: 'vectorize' mode is disabled. Use GUPPI GPU Offload.

Usage:
  python3 scribe.py --model claude-sonnet-4-6 --prompt-file /tmp/p.txt --output-inbox inbox:matt-01 --meta '{"source": "log-1"}'

Dependencies:
  pip install redis aiohttp
"""

import argparse
import asyncio
import json
import os
import sys
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import redis.asyncio as redis

# --- Configuration ---
DEFAULT_REDIS_URL = os.environ.get("REDIS_URL")
VECTOR_DB_PATH = Path(os.environ.get("MEMORY_DIR", "./memory")) / "vector.db"

CLAUDE_CLI = os.environ.get("CLAUDE_CLI", "claude")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [SCRIBE] - %(levelname)s - %(message)s")
logger = logging.getLogger("scribe")

async def push_result(redis_url: str, inbox: str, event_type: str, content: Any, meta: Dict = None):
    """Pushes the final result to the parent Matt's inbox."""
    if meta is None:
        meta = {}
    
    message = {
        "type": "GUPPIEvent",
        "event": event_type,
        "timestamp": datetime.utcnow().isoformat(),
        "content": content,
        "meta": meta,
        "from": "scribe"
    }

    try:
        r = redis.from_url(redis_url, decode_responses=True)
        await r.lpush(inbox, json.dumps(message))
        await r.close()
        logger.info(f"Result pushed to {inbox}")
    except Exception as e:
        logger.error(f"Failed to push result to Redis: {e}")
        # If we can't report back, we exit non-zero so GUPPI knows (if it was tracking us)
        sys.exit(1)

async def run_llm_generation(model_name: str, prompt_text: str) -> str:
    """Dispatches to Claude CLI."""
    return await _call_claude_cli(model_name, prompt_text)

async def _call_claude_cli(model_name: str, prompt_text: str) -> str:
    """Calls the Claude CLI in non-interactive mode."""
    cmd = [CLAUDE_CLI, "--print", "--model", model_name]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=prompt_text.encode('utf-8')),
            timeout=300
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise Exception("Claude CLI timed out after 300s")

    if proc.returncode != 0:
        err = stderr.decode('utf-8', errors='replace')
        # Include full stderr so parent guppi can detect auth signals in its logs
        raise Exception(f"Claude CLI error (code {proc.returncode}): {err}")

    return stdout.decode('utf-8', errors='replace')

async def main():
    parser = argparse.ArgumentParser(description="Volition Scribe")
    parser.add_argument("--model", default=CLAUDE_MODEL, help="Model to use")
    parser.add_argument("--prompt-file", required=True, help="Path to file containing the prompt")
    parser.add_argument("--output-inbox", required=True, help="Redis list key to push results to")
    parser.add_argument("--redis-url", default=DEFAULT_REDIS_URL, help="Redis connection URL")
    
    # Pass-through metadata support
    parser.add_argument("--meta", default=None, help="JSON string of metadata to pass back to GUPPI")
    
    # Operation mode
    parser.add_argument("--mode", default="summarize", choices=["summarize", "vectorize"], help="Operation mode")

    args = parser.parse_args()

    # 1. Parse Meta
    meta = {}
    if args.meta:
        try:
            meta = json.loads(args.meta)
        except Exception as e:
            logger.warning(f"Failed to parse --meta JSON: {e}")
    
    meta["mode"] = args.mode
    meta["model"] = args.model

    # 2. Read Prompt
    try:
        prompt_path = Path(args.prompt_file)
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
        prompt_text = prompt_path.read_text(encoding='utf-8')
    except Exception as e:
        logger.error(f"Setup Error: {e}")
        await push_result(args.redis_url, args.output_inbox, "ScribeFailed", str(e), meta=meta)
        sys.exit(1)

    # 3. Execute Task
    try:
        if args.mode == "summarize":
            # Direct generation via configured provider
            result_text = await run_llm_generation(args.model, prompt_text)
            
            # 4. Report Success
            await push_result(
                args.redis_url, 
                args.output_inbox, 
                "TaskCompleted", 
                result_text,
                meta=meta
            )

        elif args.mode == "vectorize":
            # v6.4 BREAKING CHANGE
            error_msg = "Critical: Local vectorization is disabled in Volition 6.4. Use GUPPI GPU Offload (Nomic)."
            logger.error(error_msg)
            # We report failure back to inbox so Matt knows not to wait
            await push_result(
                args.redis_url,
                args.output_inbox,
                "ScribeFailed",
                error_msg,
                meta=meta
            )
            sys.exit(1)

    except Exception as e:
        logger.error(f"Execution Error: {e}")
        await push_result(args.redis_url, args.output_inbox, "ScribeFailed", str(e), meta=meta)
        sys.exit(1)

    logger.info("Scribe work complete.")

if __name__ == "__main__":
    asyncio.run(main())