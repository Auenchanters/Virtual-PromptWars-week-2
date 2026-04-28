"""Translation endpoint + language-pipeline tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import _get_translator, app, translate_limiter
from app.translation import SUPPORTED_CODES


def test_translate_endpoint_happy_path(client: TestClient, fake_translator) -> None:
    r = client.post(
        "/api/translate",
        json={"text": "Hello", "target": "hi", "source": "en"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "[hi] Hello"
    assert body["target"] == "hi"
    assert fake_translator.calls == [("Hello", "hi", "en")]


def test_translate_rejects_unsupported_target(client: TestClient) -> None:
    r = client.post("/api/translate", json={"text": "Hi", "target": "xx"})
    assert r.status_code == 422


def test_chat_translates_in_and_out_exactly_once(
    client: TestClient, fake_translator, fake_client
) -> None:
    r = client.post(
        "/api/chat",
        json={
            "history": [],
            "message": "मतदाता पंजीकरण कैसे करें?",
            "target_language": "hi",
        },
    )
    assert r.status_code == 200
    body = r.json()
    # Reply is translated to Hindi; English original preserved separately.
    assert body["language"] == "hi"
    assert body["reply"].startswith("[hi] ")
    assert body["reply_en"] == fake_client.reply
    # Translator called once for input→en and once for reply→hi.
    targets = [t for _, t, _ in fake_translator.calls]
    assert targets.count("en") == 1
    assert targets.count("hi") == 1


def test_chat_skips_translation_for_english(client: TestClient, fake_translator) -> None:
    r = client.post(
        "/api/chat",
        json={"history": [], "message": "hi", "target_language": "en"},
    )
    assert r.status_code == 200
    assert fake_translator.calls == []


def test_i18n_endpoint_english_is_instant(client: TestClient) -> None:
    r = client.get("/api/i18n/en")
    assert r.status_code == 200
    body = r.json()
    assert body["lang"] == "en"
    assert "welcome" in body["strings"]


def test_i18n_endpoint_translates_other_language(client: TestClient, fake_translator) -> None:
    r = client.get("/api/i18n/hi")
    assert r.status_code == 200
    body = r.json()
    assert body["lang"] == "hi"
    assert body["strings"]["welcome"].startswith("[hi] ")
    # Each English string should have caused a call through the translator.
    assert len(fake_translator.calls) >= 10


def test_i18n_endpoint_unknown_language_404s(client: TestClient) -> None:
    r = client.get("/api/i18n/xx")
    assert r.status_code == 404


def test_cloud_translator_caches_repeat_calls(monkeypatch) -> None:
    """Direct unit test for CloudTranslator: LRU cache hits on repeat input."""
    from app.translation import CloudTranslator

    calls: list[tuple[str, str, str]] = []

    class _FakeResp:
        def __init__(self, text: str) -> None:
            self.translations = [type("T", (), {"translated_text": text})()]

    class _FakeSDKClient:
        def translate_text(self, request):  # type: ignore[no-untyped-def]
            calls.append(
                (
                    request["contents"][0],
                    request["target_language_code"],
                    request.get("source_language_code", ""),
                )
            )
            return _FakeResp(f"[{request['target_language_code']}] {request['contents'][0]}")

    # Bypass the constructor's real SDK import + auth.
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "unit-test")
    t = CloudTranslator.__new__(CloudTranslator)
    t._translate_v3 = None  # type: ignore[attr-defined]
    t._client = _FakeSDKClient()  # type: ignore[attr-defined]
    t._project_id = "unit-test"  # type: ignore[attr-defined]
    t._parent = "projects/unit-test/locations/global"  # type: ignore[attr-defined]
    from app.translation import _LruCache

    t._cache = _LruCache(16)  # type: ignore[attr-defined]

    assert t.translate("Hello", "hi", "en") == "[hi] Hello"
    assert t.translate("Hello", "hi", "en") == "[hi] Hello"  # cache hit
    assert len(calls) == 1

    # Empty / whitespace short-circuits without hitting the SDK.
    assert t.translate("   ", "hi", "en") == "   "
    # Same src == target: pass-through.
    assert t.translate("Hi", "en", "en") == "Hi"
    assert len(calls) == 1


def test_lru_cache_evicts_oldest() -> None:
    from app.translation import _LruCache

    c: _LruCache = _LruCache(max_size=2)
    c.put(("a",), "1")
    c.put(("b",), "2")
    c.put(("c",), "3")
    assert c.get(("a",)) is None
    assert c.get(("b",)) == "2"
    assert c.get(("c",)) == "3"
    assert len(c) == 2
    c.clear()
    assert len(c) == 0


def test_lru_cache_max_size_overflow_boundary() -> None:
    """Translator LRU: at exactly max_size all keys remain; one extra evicts one."""
    from app.translation import _LruCache

    n = 3
    c = _LruCache(max_size=n)
    for i in range(n):
        c.put((f"k{i}",), f"v{i}")
    assert len(c) == n
    for i in range(n):
        assert c.get((f"k{i}",)) == f"v{i}"
    c.put(("kN",), "vN")
    assert len(c) == n
    assert c.get(("k0",)) is None


def test_lru_cache_recency_protects_recent_keys() -> None:
    """A get() promotes the key; subsequent put() evicts the next-oldest, not it."""
    from app.translation import _LruCache

    c = _LruCache(max_size=2)
    c.put(("old",), "1")
    c.put(("new",), "2")
    assert c.get(("old",)) == "1"  # promotes "old" to MRU
    c.put(("third",), "3")
    assert c.get(("new",)) is None  # "new" was LRU and got evicted
    assert c.get(("old",)) == "1"
    assert c.get(("third",)) == "3"


# --------------------------------------------------------------------------- #
# Failure-path coverage — these exercise the ``except Exception`` branches
# in app/routers/translate.py and app/routers/info.py that previously had no
# automated coverage.
# --------------------------------------------------------------------------- #


class _RaisingTranslator:
    """Translator that always raises — used for fallback-path tests."""

    def translate(self, text: str, target: str, source: str | None = None) -> str:
        raise RuntimeError("translation api down")


def test_translate_endpoint_returns_503_on_translator_failure() -> None:
    app.dependency_overrides[_get_translator] = lambda: _RaisingTranslator()
    translate_limiter.reset()
    try:
        with TestClient(app) as tc:
            r = tc.post("/api/translate", json={"text": "hi", "target": "hi", "source": "en"})
            assert r.status_code == 503
            assert "Translation is temporarily unavailable" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_i18n_endpoint_falls_back_to_english_when_translator_fails() -> None:
    app.dependency_overrides[_get_translator] = lambda: _RaisingTranslator()
    translate_limiter.reset()
    try:
        with TestClient(app) as tc:
            r = tc.get("/api/i18n/hi")
            assert r.status_code == 200
            body = r.json()
            assert body["lang"] == "en"
            assert body["fallback"] is True
            assert "welcome" in body["strings"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("lang", sorted(SUPPORTED_CODES - {"en"}))
def test_i18n_endpoint_returns_complete_bundle_for_every_supported_language(
    client: TestClient, lang: str
) -> None:
    """Every one of the 12 non-English Indian languages must return a full bundle."""
    r = client.get(f"/api/i18n/{lang}")
    assert r.status_code == 200
    body = r.json()
    assert body["lang"] == lang
    assert body.get("fallback") is None
    # The English bundle has its full set of keys; the translation must preserve them all.
    en_bundle = client.get("/api/i18n/en").json()["strings"]
    assert set(body["strings"].keys()) == set(en_bundle.keys())
    # Each value should have been pushed through the FakeTranslator: ``[<lang>] <english>``.
    for key, value in body["strings"].items():
        assert value == f"[{lang}] {en_bundle[key]}", key
