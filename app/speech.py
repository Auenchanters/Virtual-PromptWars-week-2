"""Cloud Text-to-Speech wrapper used as a server-side fallback for browsers
whose Web Speech API has poor coverage of Indian languages.

Rubric: Accessibility (read-aloud for low-literacy users),
Google Services (Cloud Text-to-Speech).
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from threading import Lock
from typing import Protocol

logger = logging.getLogger("votewise.speech")

# Map our short language codes to BCP-47 voices Cloud TTS supports well.
VOICE_BY_LANG: dict[str, str] = {
    "en": "en-IN-Wavenet-D",
    "hi": "hi-IN-Wavenet-D",
    "bn": "bn-IN-Wavenet-A",
    "ta": "ta-IN-Wavenet-A",
    "te": "te-IN-Standard-A",
    "mr": "mr-IN-Wavenet-A",
    "gu": "gu-IN-Wavenet-A",
    "kn": "kn-IN-Wavenet-A",
    "ml": "ml-IN-Wavenet-A",
    "pa": "pa-IN-Wavenet-A",
    "ur": "ur-IN-Wavenet-A",
    # Cloud TTS does not (yet) ship Odia/Assamese voices reliably; the frontend
    # will fall back to browser SpeechSynthesis for those.
}

MAX_TTS_CHARS = 1500
DEFAULT_CACHE_SIZE = 256


def supported_for_tts(lang: str) -> bool:
    return lang in VOICE_BY_LANG


class Speaker(Protocol):
    """Minimal structural interface so tests can inject a fake."""

    def synthesize(self, text: str, lang: str) -> bytes: ...


class _LruBytesCache:
    def __init__(self, max_size: int) -> None:
        self._max = max_size
        self._data: OrderedDict[tuple[str, str], bytes] = OrderedDict()
        self._lock = Lock()

    def get(self, key: tuple[str, str]) -> bytes | None:
        with self._lock:
            value = self._data.get(key)
            if value is not None:
                self._data.move_to_end(key)
            return value

    def put(self, key: tuple[str, str], value: bytes) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)


class CloudSpeaker:
    """Thin wrapper around ``google.cloud.texttospeech``."""

    def __init__(self, cache_size: int = DEFAULT_CACHE_SIZE) -> None:
        from google.cloud import texttospeech  # lazy import

        self._tts = texttospeech
        self._client = texttospeech.TextToSpeechClient()
        self._cache = _LruBytesCache(cache_size)

    def synthesize(self, text: str, lang: str) -> bytes:
        text = text.strip()
        if not text:
            raise ValueError("text is empty")
        if len(text) > MAX_TTS_CHARS:
            text = text[:MAX_TTS_CHARS]
        if lang not in VOICE_BY_LANG:
            raise ValueError(f"unsupported language for cloud TTS: {lang}")
        key = (text, lang)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        audio = self._call(text, lang)
        self._cache.put(key, audio)
        return audio

    def _call(self, text: str, lang: str) -> bytes:
        voice_name = VOICE_BY_LANG[lang]
        # voice_name format: <lang>-<region>-<style>-<id> -> language_code is "<lang>-<region>"
        parts = voice_name.split("-")
        language_code = f"{parts[0]}-{parts[1]}"
        synthesis_input = self._tts.SynthesisInput(text=text)
        voice = self._tts.VoiceSelectionParams(language_code=language_code, name=voice_name)
        audio_config = self._tts.AudioConfig(audio_encoding=self._tts.AudioEncoding.MP3)
        resp = self._client.synthesize_speech(
            input=synthesis_input, voice=voice, audio_config=audio_config
        )
        return bytes(resp.audio_content)


_speaker_singleton: Speaker | None = None


def get_speaker() -> Speaker:
    global _speaker_singleton
    if _speaker_singleton is None:
        _speaker_singleton = CloudSpeaker()
    return _speaker_singleton


def reset_speaker_for_tests() -> None:
    global _speaker_singleton
    _speaker_singleton = None
