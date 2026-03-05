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
  # prompt-file only (file contains full prompt):
  python3 scribe.py --model haiku --prompt-file /tmp/p.txt --output-inbox inbox:abe-01 --meta '{"source": "log-1"}'

  # Note: when spawned via GUPPI's spawn_scribe, prompt+prompt_file are merged by GUPPI
  # into a single temp file before scribe.py is called. Scribe always receives one file.

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

import aiohttp
import redis.asyncio as redis

# --- Configuration ---
DEFAULT_REDIS_URL = os.environ.get("REDIS_URL")
VECTOR_DB_PATH = Path(os.environ.get("MEMORY_DIR", "./memory")) / "vector.db"

CLAUDE_CLI = os.environ.get("CLAUDE_CLI", "claude")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "haiku")
LOCAL_API_URL = os.environ.get("LOCAL_API_URL", "http://localhost:8080/v1")
LOCAL_API_KEY = os.environ.get("LOCAL_API_KEY", "not-needed")

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [SCRIBE] - %(levelname)s - %(message)s")
logger = logging.getLogger("scribe")

async def push_result(redis_url: str, inbox: str, event_type: str, content: Any, meta: Dict = None):
    """Pushes the final result to the parent Abe's inbox."""
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
    """Routes to local API or Claude CLI based on model prefix."""
    if model_name.startswith("local/"):
        return await _call_local_api(model_name, prompt_text)
    return await _call_claude_cli(model_name, prompt_text)

async def _call_claude_cli(model_name: str, prompt_text: str) -> str:
    """Calls the Claude CLI in non-interactive mode."""
    cmd = [
        CLAUDE_CLI, "--print", "--model", model_name,
        "--system-prompt", "You are a log analysis scribe. Produce clean markdown summaries. Do not use any tools.",
        "--output-format", "json",
        "--max-turns", "1",
        "--tools", "",
        "--no-session-persistence",
        "--disable-slash-commands",
    ]
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

    raw = stdout.decode('utf-8', errors='replace')
    try:
        envelope = json.loads(raw)
        return envelope.get("result", raw)
    except Exception:
        return raw

async def _call_local_api(model_name: str, prompt_text: str) -> str:
    """Calls a local OpenAI-compatible API (e.g., llama.cpp)."""
    actual_model = model_name.removeprefix("local/")
    payload = {
        "model": actual_model,
        "messages": [
            {"role": "system", "content": "You are a log analysis scribe. Produce clean markdown summaries."},
            {"role": "user", "content": prompt_text}
        ],
        "temperature": 0.3
    }
    headers = {
        "Authorization": f"Bearer {LOCAL_API_KEY}",
        "Content-Type": "application/json"
    }

    async with aiohttp.ClientSession() as session:
        url = f"{LOCAL_API_URL}/chat/completions"
        async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=1200)) as resp:
            if resp.status != 200:
                error_body = await resp.text()
                raise Exception(f"Local API error ({resp.status}): {error_body}")
            data = await resp.json()

            content = data["choices"][0]["message"]["content"]
            # Strip <think> tags if present (local models may include reasoning)
            if "<think>" in content:
                import re
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            return content

async def main():
    parser = argparse.ArgumentParser(description="Volition Scribe")
    parser.add_argument("--model", default=CLAUDE_MODEL, help="Model to use")
    parser.add_argument("--prompt-file", required=True, help="Path to file containing the prompt")
    parser.add_argument("--output-inbox", required=True, help="Redis list key to push results to")
    parser.add_argument("--redis-url", default=DEFAULT_REDIS_URL, help="Redis connection URL")
    
    # Pass-through metadata support
    parser.add_argument("--meta", default=None, help="JSON string of metadata to pass back to GUPPI")
    
    # Operation mode
    parser.add_argument("--mode", default="summarize", choices=["summarize", "vectorize", "analyze"], help="Operation mode")

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

        elif args.mode == "analyze":
            result_text = await run_llm_generation(args.model, prompt_text)
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
            # We report failure back to inbox so Abe knows not to wait
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