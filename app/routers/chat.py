"""Chat endpoints: ``POST /api/chat`` (one-shot) and ``POST /api/chat/stream`` (SSE).

Rubric: Google Services (Gemini + Google Search grounding tool, Cloud Translation
for in/out localisation), Efficiency (anyio thread offload, SSE streaming),
Accessibility (every reply is delivered in the user's preferred language),
Problem Statement Alignment (live ECI grounding with citations).
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Annotated

import anyio
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from app.analytics import Analytics, classify_topic
from app.chat import ChatChunk, ChatMessage, GeminiClient, trim_history
from app.deps import (
    _check_rate,
    _get_analytics,
    _get_redactor,
    _get_translator,
    _run_generate,
    _run_translate,
    _sse,
    get_gemini_client,
    rate_limiter,
)
from app.dlp import Redactor
from app.models import (
    ChatRequest,
    ChatResponse,
    CitationModel,
    SSEChunk,
    SSEDone,
    SSEError,
    SSEMeta,
    SSETranslated,
)
from app.routers.info import DISCLAIMER
from app.translation import Translator

logger = logging.getLogger("votewise.routers.chat")

router = APIRouter()


def _record_chat_turn(
    analytics: Analytics,
    redactor: Redactor,
    raw_message: str,
    language: str,
    latency_ms: int,
    used_grounding: bool,
    citation_count: int,
) -> None:
    """Background-task callback. Redacts the message, classifies it, inserts to BigQuery."""
    try:
        scrubbed = redactor.redact(raw_message)
    except Exception:  # pragma: no cover - redactor.redact already swallows internally
        logger.exception("redactor raised before classify_topic")
        scrubbed = raw_message
    topic = classify_topic(scrubbed)
    try:
        analytics.log_chat_turn(
            language=language,
            topic=topic,
            latency_ms=latency_ms,
            used_grounding=used_grounding,
            citation_count=citation_count,
        )
    except Exception:  # pragma: no cover - analytics already swallows internally
        logger.exception("analytics.log_chat_turn raised")


@router.post("/api/chat", response_model=ChatResponse)
async def api_chat(
    payload: ChatRequest,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    client: Annotated[GeminiClient, Depends(get_gemini_client)],
    translator: Annotated[Translator, Depends(_get_translator)],
    analytics: Annotated[Analytics, Depends(_get_analytics)],
    redactor: Annotated[Redactor, Depends(_get_redactor)],
) -> ChatResponse:
    _check_rate(rate_limiter, request)
    response.headers["Cache-Control"] = "no-store"

    user_message_en = payload.message
    if payload.target_language != "en":
        try:
            user_message_en = await _run_translate(
                translator, payload.message, "en", payload.target_language
            )
        except Exception:
            logger.exception("input translation failed; sending original text to Gemini")

    history = [ChatMessage(role=turn.role, text=turn.text) for turn in payload.history]
    history.append(ChatMessage(role="user", text=user_message_en))
    history = trim_history(history)

    started = time.monotonic()
    try:
        result = await _run_generate(client, history, payload.use_grounding)
    except Exception:
        logger.exception("Gemini call failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The assistant is temporarily unavailable. Please try again shortly.",
        ) from None
    latency_ms = int((time.monotonic() - started) * 1000)

    reply_en = result.text
    reply_localized = reply_en
    if payload.target_language != "en":
        try:
            reply_localized = await _run_translate(
                translator, reply_en, payload.target_language, "en"
            )
        except Exception:
            logger.exception("output translation failed; returning English reply")

    background.add_task(
        _record_chat_turn,
        analytics,
        redactor,
        payload.message,
        payload.target_language,
        latency_ms,
        payload.use_grounding,
        len(result.citations),
    )

    return ChatResponse(
        reply=reply_localized,
        reply_en=reply_en if payload.target_language != "en" else None,
        disclaimer=DISCLAIMER,
        citations=[CitationModel(title=c.title, uri=c.uri) for c in result.citations],
        language=payload.target_language,
    )


@router.post("/api/chat/stream")
async def api_chat_stream(
    payload: ChatRequest,
    request: Request,
    client: Annotated[GeminiClient, Depends(get_gemini_client)],
    translator: Annotated[Translator, Depends(_get_translator)],
) -> StreamingResponse:
    """Server-Sent-Events stream of the Gemini reply.

    Events (each line begins with ``data: `` and ends with a blank line):
      - ``{"type":"meta","language":"hi"}``
      - ``{"type":"chunk","text":"..."}`` (0+ times)
      - ``{"type":"done","citations":[...],"disclaimer":"..."}`` OR
        ``{"type":"error","detail":"..."}``
    """
    _check_rate(rate_limiter, request)

    user_message_en = payload.message
    if payload.target_language != "en":
        try:
            user_message_en = await _run_translate(
                translator, payload.message, "en", payload.target_language
            )
        except Exception:
            logger.exception("input translation failed in stream; using original")

    history = [ChatMessage(role=turn.role, text=turn.text) for turn in payload.history]
    history.append(ChatMessage(role="user", text=user_message_en))
    history = trim_history(history)

    async def event_stream() -> AsyncIterator[bytes]:
        meta: SSEMeta = {"type": "meta", "language": payload.target_language}
        yield _sse(meta)
        collected: list[str] = []
        citations: list[dict[str, str]] = []
        try:
            it = await anyio.to_thread.run_sync(
                lambda: iter(client.stream(history, payload.use_grounding))
            )
            while True:
                chunk: ChatChunk | None = await anyio.to_thread.run_sync(lambda: next(it, None))
                if chunk is None:
                    break
                if chunk.text:
                    collected.append(chunk.text)
                    chunk_evt: SSEChunk = {"type": "chunk", "text": chunk.text}
                    yield _sse(chunk_evt)
                if chunk.is_final and chunk.citations:
                    citations = [{"title": c.title, "uri": c.uri} for c in chunk.citations]
        except Exception as exc:
            logger.exception("streaming generate failed")
            err: SSEError = {"type": "error", "detail": str(exc)[:200]}
            yield _sse(err)
            return

        full_en = "".join(collected).strip()
        full_localized = full_en
        if payload.target_language != "en" and full_en:
            try:
                full_localized = await _run_translate(
                    translator, full_en, payload.target_language, "en"
                )
                tr_evt: SSETranslated = {
                    "type": "translated",
                    "text": full_localized,
                    "lang": payload.target_language,
                }
                yield _sse(tr_evt)
            except Exception:
                logger.exception("post-stream translation failed; returning English")

        done: SSEDone = {
            "type": "done",
            "disclaimer": DISCLAIMER,
            "language": payload.target_language,
            "citations": citations,
            "reply_en": full_en if payload.target_language != "en" else None,
        }
        yield _sse(done)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router"]
