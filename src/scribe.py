#!/usr/bin/env python3
"""
scribe.py - Production Scribe Worker for Volition 6.5

This is the ephemeral "Tasker" process spawned by GUPPI.
It performs heavy lifting (LLM calls) and reports back via Redis.

Status: RELEASE 6.5.1
- Architecture: Split-Brain Compatible
- Provider: Multi-Backend (Google Native or OpenRouter)
- Feature: Thinking Model Support (Suffix Parsing)
- DEPRECATION: 'vectorize' mode is disabled. Use GUPPI GPU Offload.

Usage:
  python3 scribe.py --model google/gemini-3-flash-preview:thinking --prompt-file /tmp/p.txt --output-inbox inbox:abe-01 --meta '{"source": "log-1"}'

Dependencies:
  pip install redis google-genai aiohttp
"""

import argparse
import asyncio
import json
import os
import sys
import logging
import aiohttp
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import redis.asyncio as redis

# We import google.genai conditionally or just import it (assuming env has it)
try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None

# --- Configuration ---
DEFAULT_REDIS_URL = os.environ.get("REDIS_URL")
VECTOR_DB_PATH = Path(os.environ.get("MEMORY_DIR", "./memory")) / "vector.db"

# v6.4 Provider Config
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "openrouter") # "google" or "openrouter"

# Google
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# OpenRouter
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_SITE_URL = os.environ.get("OPENROUTER_SITE_URL", "https://volition.indoria.org")
OPENROUTER_APP_NAME = os.environ.get("OPENROUTER_APP_NAME", "Volition")

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
    """Dispatches to the correct provider."""
    if LLM_PROVIDER == "openrouter":
        return await _run_openrouter(model_name, prompt_text)
    else:
        return await _run_google(model_name, prompt_text)

async def _run_google(model_name: str, prompt_text: str) -> str:
    """Calls the Gemini API (Native)."""
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY environment variable not set.")
    if not genai:
        raise ImportError("google-genai library not installed.")

    # Map friendly names to actual model IDs if needed
    # Handle explicit google/ prefix if GUPPI sends it
    model_id = model_name.replace("google/", "")
    # Strip thinking suffix if passed to native (not supported this way in native yet usually)
    if ":thinking" in model_id:
        model_id = model_id.split(":")[0]
    
    if "flash" in model_name and "2.5" not in model_name and "3" not in model_name:
         model_id = "gemini-2.5-flash"
    
    logger.info(f"Calling Google LLM: {model_id}")
    
    # We wrap synchronous Google call in thread if needed
    def _call():
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model=model_id,
            contents=prompt_text,
            config=types.GenerateContentConfig(
                temperature=1.0, 
            )
        )
        return response.text

    return await asyncio.to_thread(_call)

async def _run_openrouter(model_name: str, prompt_text: str) -> str:
    """Calls OpenRouter."""
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY environment variable not set.")
    
    # --- FIX: Generic Auto-Correction & Thinking Support ---
    model_id = model_name
    use_thinking = False
    
    # 1. Check for Thinking Suffix
    if ":thinking" in model_id:
        model_id = model_id.split(":")[0]
        use_thinking = True
    
    # 2. If it doesn't have a provider prefix (no slash), and looks like gemini...
    if "/" not in model_id and "gemini" in model_id.lower():
        # Prepend google/
        model_id = f"google/{model_id}"
        
    # --------------------------------------------------
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": OPENROUTER_SITE_URL,
        "X-Title": OPENROUTER_APP_NAME,
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": model_id, # Use the corrected ID
        "messages": [{"role": "user", "content": prompt_text}],
        "response_format": {"type": "json_object"},
        "temperature": 1.0
    }
    
    # v6.5.1: Enable Thinking if requested (Unified Parameter)
    if use_thinking:
        payload["reasoning"] = {
            "effort": "high"
        }

    logger.info(f"Calling OpenRouter: {model_id} (orig: {model_name}) Thinking={use_thinking}")

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status != 200:
                err = await resp.text()
                raise Exception(f"OpenRouter Error {resp.status}: {err}")
            data = await resp.json()
            return data["choices"][0]["message"]["content"]

async def main():
    parser = argparse.ArgumentParser(description="Volition Scribe")
    # UPDATED DEFAULT
    parser.add_argument("--model", default="google/gemini-3-flash-preview", help="Model to use")
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