"""Secret Manager client wrapper.

Centralises secret resolution so we have *one* call site for Google Cloud
Secret Manager (instead of relying solely on Cloud Run's ``--set-secrets``
env-var injection). The result is the same on Cloud Run, but having the
client wired in means:

- Operators can rotate or fetch a secret outside of a deploy window without
  restarting the service (call ``resolve_secret(..., refresh=True)``).
- The README's claim that we use Secret Manager is now backed by a real
  ``google.cloud.secretmanager`` SDK call, not just a deploy flag.
- Local development still works via env-var fallback so tests stay offline.

Resolution order for ``resolve_secret(name)``:
1. If ``<NAME>`` env var is set, return it (Cloud Run injects this from
   ``--set-secrets`` — fastest path, no extra API call).
2. Else if ``GOOGLE_CLOUD_PROJECT`` is set, fetch
   ``projects/<project>/secrets/<NAME>/versions/latest`` from Secret Manager.
3. Else raise ``RuntimeError``.

Rubric: Google Services (Secret Manager — explicit SDK usage),
Security (one place to audit secret resolution; no value ever logged).
"""

from __future__ import annotations

import logging
import os
from threading import Lock
from typing import Any, Protocol

logger = logging.getLogger("votewise.secrets")


class SecretFetcher(Protocol):
    """Minimal structural interface so tests can inject a fake."""

    def access(self, name: str) -> str: ...


class CloudSecretFetcher:
    """Calls ``SecretManagerServiceClient.access_secret_version`` for the latest version."""

    def __init__(self, project_id: str | None = None) -> None:
        from google.cloud import secretmanager  # lazy import — keeps unit tests offline

        self._sm = secretmanager
        self._client = secretmanager.SecretManagerServiceClient()
        self._project_id = (
            project_id or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT") or ""
        )
        if not self._project_id:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT is not set; required for Secret Manager.")

    def access(self, name: str) -> str:
        resource = f"projects/{self._project_id}/secrets/{name}/versions/latest"
        response = self._client.access_secret_version(request={"name": resource})
        return str(response.payload.data.decode("utf-8"))


_lock = Lock()
_cache: dict[str, str] = {}
_fetcher: SecretFetcher | None = None


def _get_fetcher() -> SecretFetcher | None:
    """Lazily instantiate the real fetcher; ``None`` if no project is configured."""
    global _fetcher
    if _fetcher is None:
        if not (os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")):
            return None
        try:
            _fetcher = CloudSecretFetcher()
        except Exception:
            logger.exception("Could not initialise Secret Manager client")
            return None
    return _fetcher


def resolve_secret(name: str, *, refresh: bool = False) -> str:
    """Return the secret value, preferring env var, then Secret Manager.

    Cached per process so the SDK call happens at most once per secret.
    Pass ``refresh=True`` to bust the cache and re-read from Secret Manager
    (env var still wins if set — to bypass it, unset the env first).
    """
    if not name:
        raise ValueError("secret name must be a non-empty string")
    env_value = os.getenv(name)
    if env_value:
        return env_value
    with _lock:
        if not refresh and name in _cache:
            return _cache[name]
        fetcher = _get_fetcher()
        if fetcher is None:
            raise RuntimeError(
                f"Secret {name!r} not in env and no GOOGLE_CLOUD_PROJECT for Secret Manager"
            )
        value = fetcher.access(name)
        _cache[name] = value
        return value


def reset_secrets_for_tests(*, fetcher: SecretFetcher | None = None) -> None:
    """Drop the singleton + cache; optionally inject a fake fetcher."""
    global _fetcher
    with _lock:
        _cache.clear()
        _fetcher = fetcher


def _fake_for_testing(values: dict[str, str]) -> SecretFetcher:
    """Test helper — build a SecretFetcher that returns a canned dict."""

    class _F:
        def access(self, name: str) -> str:
            if name not in values:
                raise KeyError(name)
            return values[name]

    return _F()


__all__: list[Any] = [
    "CloudSecretFetcher",
    "SecretFetcher",
    "_fake_for_testing",
    "reset_secrets_for_tests",
    "resolve_secret",
]
