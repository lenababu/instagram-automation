"""Frame-level visual analysis with a real multimodal model (Groq Llama 4 Scout, free tier).

Frames are sent as base64 JPEG image parts. The model is told explicitly not to invent
details it cannot see.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass, field

from groq import Groq, RateLimitError

from app.audio_transcription import QuotaExhausted
from app.config import Settings

log = logging.getLogger(__name__)
PROMPT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "visual_analysis_prompt.txt")
MAX_IMAGES = 3  # Groq Qwen 3.8 accepts up to 3 images per request


@dataclass
class VisualAnalysis:
    summary: str = ""
    people: str = ""
    products: str = ""
    actions: str = ""
    setting: str = ""
    text_on_screen: str = ""
    mood: str = ""
    purpose: str = ""
    raw: dict = field(default_factory=dict)

    def as_text(self) -> str:
        lines = [
            f"Summary: {self.summary}",
            f"People: {self.people}",
            f"Products/objects: {self.products}",
            f"Actions: {self.actions}",
            f"Setting: {self.setting}",
            f"Text on screen: {self.text_on_screen}",
            f"Mood/style: {self.mood}",
            f"Likely purpose: {self.purpose}",
        ]
        return "\n".join(l for l in lines if not l.endswith(": "))


def _load_prompt() -> str:
    with open(PROMPT_PATH, encoding="utf-8") as fh:
        return fh.read()


def _b64(path: str) -> str:
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode()


def parse_json_block(text: str) -> dict:
    """Pull the first {...} object out of a model reply (tolerates code fences / chatter)."""
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise ValueError("no JSON object in model reply")
    return json.loads(m.group(0))


def analyse_frames(frames: list[str], settings: Settings, client: Groq | None = None) -> VisualAnalysis:
    if not frames:
        return VisualAnalysis(summary="No frames available")
    client = client or Groq(api_key=settings.vision_key)
    content = [{"type": "text", "text": _load_prompt()}]
    for f in frames[:MAX_IMAGES]:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_b64(f)}"}})
    try:
        res = client.chat.completions.create(
            model=settings.groq_vision_model,
            messages=[{"role": "user", "content": content}],
            temperature=0.2,
            max_completion_tokens=1500,
        )
    except RateLimitError as exc:
        raise QuotaExhausted(f"Groq vision rate limit: {exc}") from exc
    reply = res.choices[0].message.content or ""
    try:
        data = parse_json_block(reply)
    except (ValueError, json.JSONDecodeError):
        log.warning("Vision reply was not JSON; using plain text summary")
        return VisualAnalysis(summary=reply.strip()[:1200], raw={"text": reply})
    va = VisualAnalysis(
        summary=str(data.get("summary", "")),
        people=str(data.get("people", "")),
        products=str(data.get("products", "")),
        actions=str(data.get("actions", "")),
        setting=str(data.get("setting", "")),
        text_on_screen=str(data.get("text_on_screen", "")),
        mood=str(data.get("mood", "")),
        purpose=str(data.get("purpose", "")),
        raw=data,
    )
    log.info("Visual analysis: %s", va.summary[:160])
    return va
