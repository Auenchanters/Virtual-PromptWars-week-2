"""Cloud Text-to-Speech endpoint with WaveNet voices for Indian languages.

Rubric: Google Services (Cloud Text-to-Speech), Accessibility (read-aloud
fallback when browser SpeechSynthesis lacks the voice).
"""

from __future__ import annotations

import logging
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.deps import _check_rate, _get_speaker, tts_limiter
from app.models import TtsRequest
from app.speech import Speaker, supported_for_tts

logger = logging.getLogger("votewise.routers.tts")

router = APIRouter()


@router.post("/api/tts")
async def api_tts(
    payload: TtsRequest,
    request: Request,
    speaker: Annotated[Speaker, Depends(_get_speaker)],
) -> Response:
    _check_rate(tts_limiter, request)
    if not supported_for_tts(payload.lang):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cloud TTS has no voice for '{payload.lang}'; use the browser voice instead.",
        )
    try:
        audio = await anyio.to_thread.run_sync(
            lambda: speaker.synthesize(payload.text, payload.lang)
        )
    except Exception:
        logger.exception("tts failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Text-to-speech is temporarily unavailable.",
        ) from None
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router"]
