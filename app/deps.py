"""Shared dependency providers, rate limiters and small helpers used by every router.

Rubric: Code Quality (single-responsibility module, every router pulls its
plumbing from here so handlers only carry endpoint logic).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import anyio
from fastapi import HTTPException, Request, status

from app.analytics import Analytics, get_analytics
from app.chat import ChatMessage, ChatResult, GeminiClient, get_client
from app.dlp import Redactor, get_redactor
from app.limiter import RateLimiter
from app.places import PlacesClient, get_places_client
from app.speech import Speaker, get_speaker
from app.translation import Translator, get_translator

MAX_MESSAGE_CHARS = 1000
MAX_HISTORY_MESSAGES = 20

RATE_LIMIT_REQUESTS = 30
RATE_LIMIT_WINDOW_SECONDS = 60
TRANSLATE_RATE_LIMIT_REQUESTS = 60
TTS_RATE_LIMIT_REQUESTS = 20

# Process-wide rate-limiter singletons. Tests reset them between cases via the
# ``_reset_limiters`` helper in conftest.
rate_limiter = RateLimiter(RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)
translate_limiter = RateLimiter(TRANSLATE_RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)
tts_limiter = RateLimiter(TTS_RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)


def get_gemini_client(request: Request) -> GeminiClient:
    """Resolve the Gemini client from app.state, lazily creating it on first use."""
    client: GeminiClient | None = request.app.state.gemini_client
    if client is None:
        client = get_client()
        request.app.state.gemini_client = client
    return client


def _get_translator() -> Translator:
    return get_translator()


def _get_speaker() -> Speaker:
    return get_speaker()


def _get_analytics() -> Analytics:
    return get_analytics()


def _get_redactor() -> Redactor:
    return get_redactor()


def _get_places() -> PlacesClient:
    return get_places_client()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Cloud Run appends the immediate client as the LAST hop.
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _check_rate(limiter: RateLimiter, request: Request) -> None:
    allowed, retry_after = limiter.check(_client_ip(request))
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please wait a moment and try again.",
            headers={"Retry-After": str(retry_after)},
        )


async def _run_generate(
    client: GeminiClient, history: list[ChatMessage], use_grounding: bool
) -> ChatResult:
    """Offload the blocking Gemini call to a worker thread."""
    return await anyio.to_thread.run_sync(client.generate, history, use_grounding)


async def _run_translate(
    translator: Translator, text: str, target: str, source: str | None = None
) -> str:
    return await anyio.to_thread.run_sync(lambda: translator.translate(text, target, source))


def _sse(event: Mapping[str, Any]) -> bytes:
    """Encode one SSE ``data:`` frame from a JSON-serialisable mapping.

    Accepts ``Mapping`` (not just ``dict``) so TypedDict payloads from
    [app/models.py](app/models.py) pass ``mypy --strict``.
    """
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


__all__ = [
    "MAX_HISTORY_MESSAGES",
    "MAX_MESSAGE_CHARS",
    "RATE_LIMIT_REQUESTS",
    "RATE_LIMIT_WINDOW_SECONDS",
    "TRANSLATE_RATE_LIMIT_REQUESTS",
    "TTS_RATE_LIMIT_REQUESTS",
    "_check_rate",
    "_client_ip",
    "_get_analytics",
    "_get_places",
    "_get_redactor",
    "_get_speaker",
    "_get_translator",
    "_run_generate",
    "_run_translate",
    "_sse",
    "get_gemini_client",
    "rate_limiter",
    "translate_limiter",
    "tts_limiter",
]
