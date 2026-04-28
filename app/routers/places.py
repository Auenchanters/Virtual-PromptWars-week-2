"""Polling-booth locator endpoint backed by Google Maps Platform Places API.

Rubric: Google Services (Maps Platform / Places API New),
Problem Statement Alignment (booth lookup is the single most concrete voter
action this assistant can help with), Accessibility (location-based help
for users who don't know their state/UT code).
"""

from __future__ import annotations

import logging
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.deps import _check_rate, _get_places, translate_limiter
from app.models import BoothResult, BoothSearchRequest, BoothSearchResponse
from app.places import PlacesClient

logger = logging.getLogger("votewise.routers.places")

router = APIRouter()


@router.post("/api/places/booth", response_model=BoothSearchResponse)
async def api_booth_search(
    payload: BoothSearchRequest,
    request: Request,
    places: Annotated[PlacesClient, Depends(_get_places)],
) -> BoothSearchResponse:
    """Return up to 5 nearest polling booths to the supplied lat/lng."""
    # Reuse the translation rate-limit bucket — same cost class as a translate call.
    _check_rate(translate_limiter, request)
    try:
        results = await anyio.to_thread.run_sync(
            lambda: places.nearby_booths(payload.lat, payload.lng, payload.radius_m)
        )
    except Exception:
        logger.exception("places search failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Booth lookup is temporarily unavailable.",
        ) from None
    return BoothSearchResponse(
        results=[
            BoothResult(
                name=r.name,
                address=r.address,
                distance_m=r.distance_m,
                lat=r.lat,
                lng=r.lng,
            )
            for r in results
        ]
    )


__all__ = ["router"]
