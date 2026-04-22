"""Translation endpoint + language-pipeline tests."""

from __future__ import annotations

from fastapi.testclient import TestClient


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
