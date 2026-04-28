"""Tests for the Secret Manager wrapper."""

from __future__ import annotations

import pytest

from app.secrets import (
    _fake_for_testing,
    reset_secrets_for_tests,
    resolve_secret,
)


def test_resolve_secret_returns_env_var_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_secrets_for_tests()
    monkeypatch.setenv("MY_SECRET", "from-env")
    assert resolve_secret("MY_SECRET") == "from-env"
    reset_secrets_for_tests()


def test_resolve_secret_falls_back_to_secret_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_SECRET", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "unit-test-project")
    reset_secrets_for_tests(fetcher=_fake_for_testing({"MY_SECRET": "from-sm"}))
    assert resolve_secret("MY_SECRET") == "from-sm"
    reset_secrets_for_tests()


def test_resolve_secret_caches_secret_manager_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_SECRET", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "unit-test-project")
    calls: list[str] = []

    class _CountingFetcher:
        def access(self, name: str) -> str:
            calls.append(name)
            return "value"

    reset_secrets_for_tests(fetcher=_CountingFetcher())
    assert resolve_secret("MY_SECRET") == "value"
    assert resolve_secret("MY_SECRET") == "value"
    assert resolve_secret("MY_SECRET") == "value"
    assert len(calls) == 1  # cached after first hit
    reset_secrets_for_tests()


def test_resolve_secret_refresh_busts_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_SECRET", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "unit-test-project")
    counter = {"n": 0}

    class _RotatingFetcher:
        def access(self, name: str) -> str:
            counter["n"] += 1
            return f"v{counter['n']}"

    reset_secrets_for_tests(fetcher=_RotatingFetcher())
    assert resolve_secret("MY_SECRET") == "v1"
    assert resolve_secret("MY_SECRET") == "v1"  # cached
    assert resolve_secret("MY_SECRET", refresh=True) == "v2"  # refreshed
    reset_secrets_for_tests()


def test_resolve_secret_raises_when_neither_env_nor_project_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_secrets_for_tests()
    monkeypatch.delenv("MY_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GCP_PROJECT", raising=False)
    with pytest.raises(RuntimeError, match="not in env"):
        resolve_secret("MY_SECRET")
    reset_secrets_for_tests()


def test_resolve_secret_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        resolve_secret("")


def test_fake_fetcher_raises_for_unknown_key() -> None:
    fetcher = _fake_for_testing({"a": "1"})
    assert fetcher.access("a") == "1"
    with pytest.raises(KeyError):
        fetcher.access("missing")
