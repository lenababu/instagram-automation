"""Speech-to-text with Groq Whisper (whisper-large-v3) plus an English translation."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from groq import Groq, RateLimitError

from app.config import Settings
from app.video_processor import split_audio

log = logging.getLogger(__name__)


class QuotaExhausted(RuntimeError):
    """Raised when a free-tier quota is used up; the run should skip, never fall back to paid APIs."""


@dataclass
class Transcript:
    text: str
    language: str
    english: str  # English translation (same as text if already English / empty if none)

    @property
    def has_speech(self) -> bool:
        return len(self.text.strip()) >= 5


def _is_music_or_noise(text: str) -> bool:
    t = text.strip().lower()
    return t in {"", "[music]", "(music)", "[applause]", "thank you.", "thanks for watching."}


def transcribe(audio_path: str, work_dir: str, settings: Settings, client: Groq | None = None) -> Transcript:
    client = client or Groq(api_key=settings.groq_api_key)
    chunks = split_audio(audio_path, work_dir)
    texts, langs = [], []
    try:
        for chunk in chunks:
            with open(chunk, "rb") as fh:
                res = client.audio.transcriptions.create(
                    file=(os.path.basename(chunk), fh.read()),
                    model=settings.groq_whisper_model,
                    response_format="verbose_json",
                    temperature=0,
                )
            texts.append((res.text or "").strip())
            langs.append(getattr(res, "language", "") or "")
    except RateLimitError as exc:
        raise QuotaExhausted(f"Groq Whisper rate limit: {exc}") from exc

    text = " ".join(t for t in texts if t)
    language = next((l for l in langs if l), "unknown")
    if _is_music_or_noise(text):
        return Transcript(text="", language=language, english="")

    english = text
    if language.lower() not in {"en", "english"} and text:
        try:
            parts = []
            for chunk in chunks:
                with open(chunk, "rb") as fh:
                    tr = client.audio.translations.create(
                        file=(os.path.basename(chunk), fh.read()),
                        model=settings.groq_whisper_model,
                        response_format="json",
                        temperature=0,
                    )
                parts.append((tr.text or "").strip())
            english = " ".join(p for p in parts if p)
        except RateLimitError as exc:
            raise QuotaExhausted(f"Groq Whisper (translate) rate limit: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            log.warning("Translation failed, keeping original transcript: %s", exc)
    log.info("Transcribed %d chars (language=%s)", len(text), language)
    return Transcript(text=text, language=language, english=english)
