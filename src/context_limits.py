"""Shared context window limit loader for Volition services."""
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_FALLBACK_DEFAULT = 32_768

def _load():
    config_path = os.environ.get("CONTEXT_LIMITS_FILE")
    if config_path:
        config_path = Path(config_path)
    else:
        config_path = Path(__file__).parent / "config" / "context_limits.json"

    try:
        with open(config_path) as f:
            data = json.load(f)
        return data.get("models", {}), data.get("default_context_limit", _FALLBACK_DEFAULT)
    except FileNotFoundError:
        logger.warning("context_limits.json not found at %s, using default %d", config_path, _FALLBACK_DEFAULT)
        return {}, _FALLBACK_DEFAULT
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Failed to parse context_limits.json: %s", e)
        return {}, _FALLBACK_DEFAULT

CONTEXT_LIMITS, DEFAULT_CONTEXT_LIMIT = _load()
