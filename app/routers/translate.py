"""Cloud Translation passthrough endpoint.

Rubric: Google Services (Cloud Translation v3), Accessibility (multi-language
support), Code Quality (focused router with shared rate limiter).
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.deps import _check_rate, _get_translator, _run_translate, translate_limiter
from app.models import TranslateRequest, TranslateResponse
from app.translation import Translator

logger = logging.getLogger("votewise.routers.translate")

router = APIRouter()


@router.post("/api/translate", response_model=TranslateResponse)
async def api_translate(
    payload: TranslateRequest,
    request: Request,
    response: Response,
    translator: Annotated[Translator, Depends(_get_translator)],
) -> TranslateResponse:
    _check_rate(translate_limiter, request)
    response.headers["Cache-Control"] = "no-store"
    try:
        out = await _run_translate(translator, payload.text, payload.target, payload.source)
    except Exception:
        logger.exception("translation failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Translation is temporarily unavailable.",
        ) from None
    return TranslateResponse(text=out, target=payload.target)


__all__ = ["router"]
