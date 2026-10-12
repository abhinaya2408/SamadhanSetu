"""Groq API client wrapper.

All Groq traffic stays inside the Python backend; the browser never sees the
API key and never talks to Groq directly. Every helper returns None on failure
so callers fall back to the deterministic local rules.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Optional

from ..config import settings

_client = None
_client_lock = threading.Lock()
_last_error: Optional[str] = None
_cache: dict[str, Any] = {}
_CACHE_MAX = 256


def _get_client():
    global _client
    if not settings.groq_configured:
        return None
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            try:
                from groq import Groq  # official SDK
                _client = Groq(
                    api_key=settings.groq_api_key,
                    timeout=settings.groq_timeout,
                    max_retries=settings.groq_max_retries,
                )
            except Exception as exc:  # pragma: no cover - import/config issues
                _set_error(f"Groq client init failed: {exc}")
                return None
    return _client


def _set_error(msg: str) -> None:
    global _last_error
    _last_error = msg


def last_error() -> Optional[str]:
    return _last_error


def is_available() -> bool:
    return settings.groq_configured and _get_client() is not None


def health() -> dict:
    """Diagnostic info — never reveals the API key."""
    ok = is_available()
    return {
        "provider": "Groq",
        "configured": bool(settings.groq_api_key),
        "enabled": settings.groq_enabled,
        "model": settings.groq_model,
        "reachable_now": ok,
        "last_error": _last_error,
    }


def chat_json(
    system: str,
    user: str,
    *,
    temperature: float = 0.2,
    max_tokens: int = 900,
    use_cache: bool = True,
) -> Optional[dict]:
    """Call Groq chat completions requesting JSON; parse defensively.

    Returns the parsed dict or None (caller must have a local fallback).
    Never raises.
    """
    client = _get_client()
    if client is None:
        return None

    cache_key = f"{settings.groq_model}|{system[:80]}|{user[:400]}"
    if use_cache and cache_key in _cache:
        return _cache[cache_key]

    try:
        resp = client.chat.completions.create(
            model=settings.groq_model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = resp.choices[0].message.content or ""
        parsed = _extract_json(content)
        if parsed is None:
            _set_error("Groq returned a non-JSON response")
            return None
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        if use_cache:
            _cache[cache_key] = parsed
        _set_error(None)
        return parsed
    except Exception as exc:  # includes 429 rate limits and timeouts
        _set_error(str(exc)[:300])
        time.sleep(0.2)  # be gentle on the free tier after a failure
        return None


def chat_text(system: str, user: str, *, max_tokens: int = 500, temperature: float = 0.3) -> Optional[str]:
    """Free-text completion; returns None on failure."""
    client = _get_client()
    if client is None:
        return None
    try:
        resp = client.chat.completions.create(
            model=settings.groq_model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        _set_error(None)
        return (resp.choices[0].message.content or "").strip() or None
    except Exception as exc:
        _set_error(str(exc)[:300])
        return None


def _extract_json(text: str) -> Optional[dict]:
    """Parse a JSON object from a model reply, tolerating code fences and prose."""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text).strip().strip("`")
    candidates = [cleaned]
    # Fall back to the first {...} block
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if 0 <= start < end:
        candidates.append(cleaned[start : end + 1])
    for cand in candidates:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            continue
    return None
