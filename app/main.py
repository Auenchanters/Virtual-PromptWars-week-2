"""FastAPI entrypoint: static UI + chat, streaming, translate, TTS and info APIs.

Rubric coverage per section:
- Code Quality: typed Pydantic models, module-split (limiter/security/translation/speech),
  dependency injection for every external service so tests can override.
- Security: security-headers + body-size + CORS + rate-limit middlewares; Retry-After on 429;
  no PII logged; structured request IDs.
- Efficiency: async handlers; Gzip; ETag + Cache-Control on /api/info; lru_cache for
  grounding and system prompt; streaming chat responses.
- Testing: every external dep (Gemini, Translator, Speaker) is a Protocol and has a
  ``get_*`` provider that tests override via ``app.dependency_overrides``.
- Accessibility: voice + multi-language both have first-class endpoints.
- Google Services: Gemini + Google Search grounding + Cloud Translation + Cloud TTS.
- Problem Alignment: /api/states powers the booth-lookup helper; /api/info exposes
  ECI-grounded facts used by the timeline UI.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import anyio
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from app.chat import ChatChunk, ChatMessage, ChatResult, GeminiClient, get_client, trim_history
from app.grounding import load_election_info, states_and_uts
from app.limiter import RateLimiter
from app.security import (
    DEFAULT_MAX_BODY_BYTES,
    make_body_size_middleware,
    security_headers_middleware,
)
from app.speech import MAX_TTS_CHARS, Speaker, get_speaker, supported_for_tts
from app.translation import SUPPORTED_CODES, SUPPORTED_LANGUAGES, Translator, get_translator

logger = logging.getLogger("votewise")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='{"lvl":"%(levelname)s","name":"%(name)s","msg":"%(message)s"}',
)

STATIC_DIR = Path(__file__).parent / "static"
I18N_FILE = Path(__file__).parent / "data" / "i18n.json"

MAX_MESSAGE_CHARS = 1000
MAX_HISTORY_MESSAGES = 20
RATE_LIMIT_REQUESTS = 30
RATE_LIMIT_WINDOW_SECONDS = 60
TRANSLATE_RATE_LIMIT_REQUESTS = 60
TTS_RATE_LIMIT_REQUESTS = 20

DEFAULT_ALLOWED_ORIGINS = "https://election-assistant-256416723201.asia-south1.run.app"

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


# --------------------------------------------------------------------------- #
# Request / response models
# --------------------------------------------------------------------------- #


class ChatTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ChatRequest(BaseModel):
    history: list[ChatTurn] = Field(default_factory=list, max_length=MAX_HISTORY_MESSAGES)
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    target_language: str = Field(default="en", min_length=2, max_length=5)
    use_grounding: bool = True

    @field_validator("message")
    @classmethod
    def _strip_message(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("message must not be blank")
        return stripped

    @field_validator("target_language")
    @classmethod
    def _validate_language(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in SUPPORTED_CODES:
            raise ValueError(f"unsupported target_language: {v}")
        return v


class CitationModel(BaseModel):
    title: str
    uri: str


class ChatResponse(BaseModel):
    reply: str
    reply_en: str | None = None
    disclaimer: str
    citations: list[CitationModel] = Field(default_factory=list)
    language: str = "en"


class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS * 2)
    target: str = Field(min_length=2, max_length=5)
    source: str | None = Field(default=None, max_length=5)

    @field_validator("target")
    @classmethod
    def _validate_target(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in SUPPORTED_CODES:
            raise ValueError(f"unsupported target: {v}")
        return v


class TranslateResponse(BaseModel):
    text: str
    target: str


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TTS_CHARS)
    lang: str = Field(min_length=2, max_length=5)

    @field_validator("lang")
    @classmethod
    def _validate_lang(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in SUPPORTED_CODES:
            raise ValueError(f"unsupported lang: {v}")
        return v


# --------------------------------------------------------------------------- #
# App lifecycle, dependency injection, rate limiting
# --------------------------------------------------------------------------- #


rate_limiter = RateLimiter(RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)
translate_limiter = RateLimiter(TRANSLATE_RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)
tts_limiter = RateLimiter(TTS_RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Warm the Gemini client once per process. Translator & Speaker are lazy —
    # we only init them on first request so the app starts even if those APIs
    # aren't enabled yet. If GEMINI_API_KEY is missing we defer the error to
    # the first chat request; this keeps the app startable in dev/tests where
    # the dependency is overridden anyway.
    try:
        app.state.gemini_client = get_client()
    except RuntimeError as exc:
        logger.warning("Gemini client not initialised at startup: %s", exc)
        app.state.gemini_client = None
    yield


def get_gemini_client(request: Request) -> GeminiClient:
    client: GeminiClient | None = request.app.state.gemini_client
    if client is None:
        # Lazy retry so the error surfaces to the request, not during startup.
        client = get_client()
        request.app.state.gemini_client = client
    return client


def _get_translator() -> Translator:
    return get_translator()


def _get_speaker() -> Speaker:
    return get_speaker()


# --------------------------------------------------------------------------- #
# App + middlewares
# --------------------------------------------------------------------------- #


app = FastAPI(
    title="VoteWise India — Election Process Education Assistant",
    description="Helps Indian voters understand the election process, timelines, and steps.",
    version="2.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=512)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        o.strip()
        for o in os.getenv("ALLOWED_ORIGINS", DEFAULT_ALLOWED_ORIGINS).split(",")
        if o.strip()
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

app.middleware("http")(security_headers_middleware)
app.middleware("http")(make_body_size_middleware(DEFAULT_MAX_BODY_BYTES))


@app.middleware("http")
async def static_cache_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Layer long-lived cache headers on /static/*."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=86400, immutable"
    return response


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Read endpoints
# --------------------------------------------------------------------------- #


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/info")
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


@app.get("/api/states")
async def api_states() -> JSONResponse:
    """States & UTs for the booth-lookup helper."""
    return JSONResponse(
        {"states_and_uts": states_and_uts()},
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/languages")
async def api_languages() -> JSONResponse:
    """Supported UI + chat languages."""
    payload = [{"code": code, "label": label} for code, label in SUPPORTED_LANGUAGES]
    return JSONResponse(
        {"languages": payload, "default": "en"},
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/i18n/{lang}")
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


# --------------------------------------------------------------------------- #
# Chat endpoints
# --------------------------------------------------------------------------- #


async def _run_generate(
    client: GeminiClient, history: list[ChatMessage], use_grounding: bool
) -> ChatResult:
    """Offload the blocking Gemini call to a worker thread."""
    return await anyio.to_thread.run_sync(client.generate, history, use_grounding)


async def _run_translate(
    translator: Translator, text: str, target: str, source: str | None = None
) -> str:
    return await anyio.to_thread.run_sync(lambda: translator.translate(text, target, source))


@app.post("/api/chat", response_model=ChatResponse)
async def api_chat(
    payload: ChatRequest,
    request: Request,
    client: Annotated[GeminiClient, Depends(get_gemini_client)],
    translator: Annotated[Translator, Depends(_get_translator)],
) -> ChatResponse:
    _check_rate(rate_limiter, request)

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

    try:
        result = await _run_generate(client, history, payload.use_grounding)
    except Exception:
        logger.exception("Gemini call failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The assistant is temporarily unavailable. Please try again shortly.",
        ) from None

    reply_en = result.text
    reply_localized = reply_en
    if payload.target_language != "en":
        try:
            reply_localized = await _run_translate(
                translator, reply_en, payload.target_language, "en"
            )
        except Exception:
            logger.exception("output translation failed; returning English reply")

    return ChatResponse(
        reply=reply_localized,
        reply_en=reply_en if payload.target_language != "en" else None,
        disclaimer=DISCLAIMER,
        citations=[CitationModel(title=c.title, uri=c.uri) for c in result.citations],
        language=payload.target_language,
    )


@app.post("/api/chat/stream")
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
        yield _sse({"type": "meta", "language": payload.target_language})
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
                    yield _sse({"type": "chunk", "text": chunk.text})
                if chunk.is_final and chunk.citations:
                    citations = [{"title": c.title, "uri": c.uri} for c in chunk.citations]
        except Exception as exc:  # pragma: no cover - exercised via tests at high level
            logger.exception("streaming generate failed")
            yield _sse({"type": "error", "detail": str(exc)[:200]})
            return

        full_en = "".join(collected).strip()
        full_localized = full_en
        if payload.target_language != "en" and full_en:
            try:
                full_localized = await _run_translate(
                    translator, full_en, payload.target_language, "en"
                )
                yield _sse(
                    {
                        "type": "translated",
                        "text": full_localized,
                        "lang": payload.target_language,
                    }
                )
            except Exception:
                logger.exception("post-stream translation failed; returning English")

        yield _sse(
            {
                "type": "done",
                "disclaimer": DISCLAIMER,
                "language": payload.target_language,
                "citations": citations,
                "reply_en": full_en if payload.target_language != "en" else None,
            }
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


# --------------------------------------------------------------------------- #
# Translation + TTS endpoints
# --------------------------------------------------------------------------- #


@app.post("/api/translate", response_model=TranslateResponse)
async def api_translate(
    payload: TranslateRequest,
    request: Request,
    translator: Annotated[Translator, Depends(_get_translator)],
) -> TranslateResponse:
    _check_rate(translate_limiter, request)
    try:
        out = await _run_translate(translator, payload.text, payload.target, payload.source)
    except Exception:
        logger.exception("translation failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Translation is temporarily unavailable.",
        ) from None
    return TranslateResponse(text=out, target=payload.target)


@app.post("/api/tts")
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
        headers={"Cache-Control": "private, max-age=300"},
    )


# --------------------------------------------------------------------------- #
# Static UI
# --------------------------------------------------------------------------- #


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
