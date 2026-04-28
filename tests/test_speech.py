"""Cloud TTS endpoint tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import _get_speaker, app, tts_limiter


def test_tts_returns_mp3_bytes(client: TestClient, fake_speaker) -> None:
    r = client.post("/api/tts", json={"text": "Namaste", "lang": "hi"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.content == fake_speaker.FAKE_AUDIO
    assert fake_speaker.calls == [("Namaste", "hi")]


def test_tts_rejects_unknown_language(client: TestClient) -> None:
    r = client.post("/api/tts", json={"text": "hi", "lang": "xx"})
    assert r.status_code == 422


def test_tts_rejects_language_without_voice(client: TestClient) -> None:
    # Odia is in SUPPORTED_CODES but has no Cloud TTS voice configured — server
    # must return 400 so the frontend can fall back to browser SpeechSynthesis.
    r = client.post("/api/tts", json={"text": "hi", "lang": "or"})
    assert r.status_code == 400


def test_tts_rejects_empty_text(client: TestClient) -> None:
    r = client.post("/api/tts", json={"text": "", "lang": "hi"})
    assert r.status_code == 422


def test_speech_module_exports_voice_map() -> None:
    from app.speech import VOICE_BY_LANG, supported_for_tts

    assert supported_for_tts("hi")
    assert not supported_for_tts("or")
    assert VOICE_BY_LANG["hi"].startswith("hi-IN")


class _RaisingSpeaker:
    """Speaker that always raises — used for the 503 fallback test."""

    def synthesize(self, text: str, lang: str) -> bytes:
        raise RuntimeError("tts api down")


def test_tts_returns_503_when_synthesis_fails() -> None:
    app.dependency_overrides[_get_speaker] = lambda: _RaisingSpeaker()
    tts_limiter.reset()
    try:
        with TestClient(app) as tc:
            r = tc.post("/api/tts", json={"text": "Namaste", "lang": "hi"})
            assert r.status_code == 503
            assert "Text-to-speech is temporarily unavailable" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_cloud_speaker_caches_and_truncates() -> None:
    from app.speech import MAX_TTS_CHARS, CloudSpeaker, _LruBytesCache

    calls: list[tuple[str, str]] = []

    class _FakeResp:
        audio_content = b"ID3-fake-mp3"

    class _FakeSDKClient:
        def synthesize_speech(self, input, voice, audio_config):  # type: ignore[no-untyped-def]
            calls.append((input.text, voice.language_code))
            return _FakeResp()

    class _FakeTTSModule:
        SynthesisInput = staticmethod(lambda text: type("I", (), {"text": text})())
        VoiceSelectionParams = staticmethod(
            lambda language_code, name: type(
                "V", (), {"language_code": language_code, "name": name}
            )()
        )
        AudioConfig = staticmethod(lambda audio_encoding: object())

        class AudioEncoding:
            MP3 = "MP3"

    s = CloudSpeaker.__new__(CloudSpeaker)
    s._tts = _FakeTTSModule  # type: ignore[attr-defined]
    s._client = _FakeSDKClient()  # type: ignore[attr-defined]
    s._cache = _LruBytesCache(8)  # type: ignore[attr-defined]

    out = s.synthesize("Namaste", "hi")
    out2 = s.synthesize("Namaste", "hi")  # cached
    assert out == out2 == b"ID3-fake-mp3"
    assert len(calls) == 1

    # Oversized text is truncated silently.
    big = "a" * (MAX_TTS_CHARS + 500)
    s.synthesize(big, "hi")
    assert len(calls[-1][0]) == MAX_TTS_CHARS

    # Rejects empty input and unsupported lang.
    import pytest

    with pytest.raises(ValueError):
        s.synthesize("   ", "hi")
    with pytest.raises(ValueError):
        s.synthesize("x", "or")
