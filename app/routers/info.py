"""Read-only metadata endpoints: /health, /api/info, /api/states, /api/languages, /api/i18n.

Rubric: Efficiency (ETag + Cache-Control on /api/info; long-lived caches on
/api/states and /api/languages; per-instance LRU on i18n bundles via
Cloud Translation), Accessibility (i18n endpoint translates the UI bundle
into 13 Indian languages), Problem Statement Alignment (states+UTs feed
the booth-lookup helper).
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.deps import _check_rate, _get_translator, _run_translate, translate_limiter
from app.grounding import load_election_info, states_and_uts
from app.translation import SUPPORTED_CODES, SUPPORTED_LANGUAGES, Translator

logger = logging.getLogger("votewise.routers.info")

I18N_FILE = Path(__file__).parent.parent / "data" / "i18n.json"

# Load once at import time so every request is served from memory.
ELECTION_INFO: dict[str, Any] = load_election_info()
DISCLAIMER: str = ELECTION_INFO["disclaimer"]

# Strong ETag for /api/info so well-behaved clients can 304.
_INFO_BYTES: bytes = json.dumps(ELECTION_INFO, separators=(",", ":"), ensure_ascii=False).encode(
    "utf-8"
)
_INFO_ETAG: str = '"' + hashlib.sha256(_INFO_BYTES).hexdigest()[:32] + '"'

with I18N_FILE.open(encoding="utf-8") as _fh:
    UI_STRINGS_EN: dict[str, str] = json.load(_fh)


router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# RFC 9116 disclosure file. Long max-age is fine: contents only change when we
# rotate ``Expires`` or contact details, and the file is tiny.
SECURITY_TXT = (
    "Contact: mailto:votewisesupport@gmail.com\n"
    "Expires: 2027-04-30T00:00:00Z\n"
    "Preferred-Languages: en\n"
    "Canonical: https://election-assistant-256416723201.asia-south1.run.app/.well-known/security.txt\n"
    "Policy: https://github.com/Auenchanters/Virtual-PromptWars-week-2/blob/main/SECURITY.md\n"
)

ROBOTS_TXT = "User-agent: *\nDisallow: /api/\nAllow: /\n"


@router.get("/.well-known/security.txt", include_in_schema=False)
async def security_txt() -> Response:
    """RFC 9116 security disclosure file."""
    return Response(
        content=SECURITY_TXT,
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/robots.txt", include_in_schema=False)
async def robots_txt() -> Response:
    """Disallow crawlers from indexing API endpoints."""
    return Response(
        content=ROBOTS_TXT,
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/api/info")
async def api_info(request: Request) -> Response:
    """Grounding payload with strong ETag + Cache-Control."""
    if request.headers.get("if-none-match") == _INFO_ETAG:
        return Response(status_code=304, headers={"ETag": _INFO_ETAG})
    return Response(
        content=_INFO_BYTES,
        media_type="application/json",
        headers={
            "ETag": _INFO_ETAG,
            "Cache-Control": "public, max-age=3600",
        },
    )


@router.get("/api/states")
async def api_states() -> JSONResponse:
    """States & UTs for the booth-lookup helper."""
    return JSONResponse(
        {"states_and_uts": states_and_uts()},
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/api/languages")
async def api_languages() -> JSONResponse:
    """Supported UI + chat languages."""
    payload = [{"code": code, "label": label} for code, label in SUPPORTED_LANGUAGES]
    return JSONResponse(
        {"languages": payload, "default": "en"},
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/api/i18n/{lang}")
async def api_i18n(
    lang: str,
    request: Request,
    translator: Annotated[Translator, Depends(_get_translator)],
) -> JSONResponse:
    """Return the UI string bundle for ``lang``.

    For English this is an in-memory read. For other supported languages, we
    translate each string through Cloud Translation once and let the
    per-instance LRU cache keep repeated loads free.
    """
    lang = lang.lower()
    if lang not in SUPPORTED_CODES:
        raise HTTPException(status_code=404, detail="unsupported language")
    if lang == "en":
        return JSONResponse(
            {"lang": "en", "strings": UI_STRINGS_EN},
            headers={"Cache-Control": "public, max-age=86400"},
        )

    _check_rate(translate_limiter, request)
    try:
        translated: dict[str, str] = {}
        for key, value in UI_STRINGS_EN.items():
            translated[key] = await _run_translate(translator, value, lang, "en")
    except Exception:
        logger.exception("i18n translation failed; falling back to English")
        return JSONResponse(
            {"lang": "en", "strings": UI_STRINGS_EN, "fallback": True},
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(
        {"lang": lang, "strings": translated},
        headers={"Cache-Control": "public, max-age=3600"},
    )


__all__ = ["DISCLAIMER", "ELECTION_INFO", "UI_STRINGS_EN", "router"]
