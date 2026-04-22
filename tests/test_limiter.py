"""Unit tests for the extracted rate limiter."""

from __future__ import annotations

import pytest

from app.limiter import RateLimiter


def test_validates_positive_arguments() -> None:
    with pytest.raises(ValueError):
        RateLimiter(0, 60)
    with pytest.raises(ValueError):
        RateLimiter(1, 0)


def test_allows_up_to_max_then_blocks() -> None:
    lim = RateLimiter(3, 60)
    for _ in range(3):
        allowed, retry = lim.check("a")
        assert allowed and retry == 0
    allowed, retry = lim.check("a")
    assert not allowed
    assert retry >= 1


def test_keys_are_isolated() -> None:
    lim = RateLimiter(1, 60)
    assert lim.check("a") == (True, 0)
    assert lim.check("b") == (True, 0)
    assert lim.check("a")[0] is False


def test_reset_clears_state() -> None:
    lim = RateLimiter(1, 60)
    lim.check("a")
    assert lim.check("a")[0] is False
    lim.reset()
    assert lim.check("a")[0] is True


def test_max_requests_property() -> None:
    assert RateLimiter(42, 60).max_requests == 42
