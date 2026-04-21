"""FastAPI entrypoint: serves the static UI and the chat + info JSON APIs."""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from app.chat import ChatMessage, GeminiClient, get_client, trim_history
from app.grounding import load_election_info

logger = logging.getLogger("election_assistant")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(levelname)s %(name)s %(message)s")

STATIC_DIR = Path(__file__).parent / "static"

MAX_MESSAGE_CHARS = 1000
MAX_HISTORY_MESSAGES = 20
RATE_LIMIT_REQUESTS = 30
RATE_LIMIT_WINDOW_SECONDS = 60
DEFAULT_ALLOWED_ORIGINS = (
    "https://election-assistant-256416723201.asia-south1.run.app"
)
ELECTION_INFO = load_election_info()
DISCLAIMER = ELECTION_INFO["disclaimer"]


class ChatTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ChatRequest(BaseModel):
    history: list[ChatTurn] = Field(default_factory=list, max_length=MAX_HISTORY_MESSAGES)
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)

    @field_validator("message")
    @classmethod
    def _strip_message(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("message must not be blank")
        return stripped


class ChatResponse(BaseModel):
    reply: str
    disclaimer: str


class RateLimiter:
    """In-memory sliding-window limiter keyed by client IP.

    Fine for a single Cloud Run instance; for multi-instance, swap for a shared store.
    """

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = {}

    @property
    def max_requests(self) -> int:
        return self._max

    def reset(self) -> None:
        self._hits.clear()

    def check(self, key: str) -> bool:
        now = time.monotonic()
        stale_keys = [
            hit_key
            for hit_key, q in self._hits.items()
            if not q or now - q[-1] > self._window * 2
        ]
        for hit_key in stale_keys:
            del self._hits[hit_key]

        q = self._hits.setdefault(key, deque())
        while q and now - q[0] > self._window:
            q.popleft()
        if len(q) >= self._max:
            return False
        q.append(now)
        return True


rate_limiter = RateLimiter(RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.gemini_client = get_client()
    yield


def get_gemini_client(request: Request) -> GeminiClient:
    return request.app.state.gemini_client


app = FastAPI(
    title="Election Process Education Assistant",
    description="Helps Indian voters understand the election process, timelines, and steps.",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

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


@app.middleware("http")
async def add_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=86400, immutable"
    return response


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/info")
def api_info() -> JSONResponse:
    return JSONResponse(ELECTION_INFO)


@app.post("/api/chat", response_model=ChatResponse)
def api_chat(
    payload: ChatRequest,
    request: Request,
    client: Annotated[GeminiClient, Depends(get_gemini_client)],
) -> ChatResponse:
    if not rate_limiter.check(_client_ip(request)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please wait a moment and try again.",
        )

    history = [ChatMessage(role=turn.role, text=turn.text) for turn in payload.history]
    history.append(ChatMessage(role="user", text=payload.message))
    history = trim_history(history)

    try:
        reply = client.generate(history)
    except Exception:
        logger.exception("Gemini call failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The assistant is temporarily unavailable. Please try again shortly.",
        ) from None

    return ChatResponse(reply=reply, disclaimer=DISCLAIMER)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
