"""Minimal Claude API client using plain HTTPS, so no SDK is required.

Returns None (never raises) when there is no API key or the request fails, so every
feature that uses it has a rule-based fallback.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import requests

log = logging.getLogger(__name__)
API_URL = "https://api.anthropic.com/v1/messages"


def _load_dotenv() -> None:
    env = Path(__file__).resolve().parent.parent / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def available() -> bool:
    _load_dotenv()
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def complete(prompt: str, max_tokens: int = 600) -> str | None:
    _load_dotenv()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    model = os.environ.get("JOBPILOT_MODEL", "claude-sonnet-5-5")
    try:
        r = requests.post(
            API_URL,
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]},
            timeout=90,
        )
        r.raise_for_status()
        blocks = r.json().get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text") or None
    except Exception as exc:
        log.warning("Claude API request failed: %s", exc)
        return None
