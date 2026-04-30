"""Security middlewares: response headers and request body-size guard.

Rubric: Security (defence in depth), Code Quality (single-responsibility modules).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse

logger = logging.getLogger("votewise.security")

# 16 KiB is well above any legitimate chat payload (1 KB message + ~20 history turns)
# and well below anything that could be used to OOM the process.
DEFAULT_MAX_BODY_BYTES = 16 * 1024

CSP = (
    "default-src 'self'; "
    "style-src 'self'; "
    "script-src 'self'; "
    "img-src 'self' data:; "
    "media-src 'self' blob: data:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "manifest-src 'self'; "
    "worker-src 'self'; "
    "object-src 'none'"
)

PERMISSIONS_POLICY = (
    "camera=(), microphone=(self), geolocation=(), interest-cohort=(), "
    "browsing-topics=(), payment=(), usb=()"
)


async def security_headers_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Attach hardening headers and a request id to every response."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    response = await call_next(request)

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = PERMISSIONS_POLICY
    response.headers["Content-Security-Policy"] = CSP
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    # Strip the uvicorn server fingerprint (defence-in-depth, cheap to remove).
    if "server" in response.headers:
        del response.headers["server"]

    # HSTS only over HTTPS (Cloud Run terminates TLS and forwards X-Forwarded-Proto).
    forwarded_proto = request.headers.get("x-forwarded-proto", "").lower()
    if forwarded_proto == "https" or request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = (
            "max-age=63072000; includeSubDomains; preload"
        )

    return response


def make_body_size_middleware(
    max_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """Reject requests whose declared Content-Length exceeds ``max_bytes``."""

    async def _middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > max_bytes:
                    logger.warning(
                        "rejecting oversized request",
                        extra={"content_length": cl, "limit": max_bytes, "path": request.url.path},
                    )
                    return JSONResponse(
                        {"detail": "Request body too large."},
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    )
            except ValueError:
                return JSONResponse(
                    {"detail": "Invalid Content-Length."},
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
        return await call_next(request)

    return _middleware
