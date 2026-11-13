"""Caption generation with a Groq text model, driven by prompts/caption_prompt.txt."""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

from groq import Groq, RateLimitError

from app.audio_transcription import QuotaExhausted, Transcript
from app.config import Settings
from app.visual_analysis import VisualAnalysis, parse_json_block

log = logging.getLogger(__name__)
PROMPT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "caption_prompt.txt")
INSTAGRAM_CAPTION_LIMIT = 2200


@dataclass
class Caption:
    hook: str
    body: str
    description: str
    cta: str
    hashtags: list[str]

    def render(self) -> str:
        tags = " ".join(h if h.startswith("#") else f"#{h}" for h in self.hashtags)
        parts = [self.hook.strip(), "", self.body.strip()]
        if self.description.strip() and self.description.strip() not in self.body:
            parts += ["", self.description.strip()]
        if self.cta.strip():
            parts += ["", self.cta.strip()]
        if tags:
            parts += ["", tags]
        text = "\n".join(parts).strip()
        return text[:INSTAGRAM_CAPTION_LIMIT]


def _load_prompt() -> str:
    with open(PROMPT_PATH, encoding="utf-8") as fh:
        return fh.read()


def build_prompt(
    transcript: Transcript | None,
    visual: VisualAnalysis,
    brand_context: str,
    previous_hooks: list[str],
) -> str:
    template = _load_prompt()
    return (
        template.replace("{{TRANSCRIPT}}", (transcript.text if transcript and transcript.has_speech else "(no speech)"))
        .replace("{{TRANSLATION}}", (transcript.english if transcript and transcript.has_speech else "(none)"))
        .replace("{{LANGUAGE}}", (transcript.language if transcript else "unknown"))
        .replace("{{VISUAL}}", visual.as_text() or "(unavailable)")
        .replace("{{TOPIC}}", visual.summary or "(unknown)")
        .replace("{{PURPOSE}}", visual.purpose or "(unknown)")
        .replace("{{BRAND}}", brand_context or "(none configured)")
        .replace("{{PREVIOUS_HOOKS}}", "\n".join(f"- {h}" for h in previous_hooks[-10:]) or "(none yet)")
    )


def _clean_hashtags(tags) -> list[str]:
    out = []
    for t in tags if isinstance(tags, list) else str(tags).split():
        t = re.sub(r"[^0-9A-Za-z_ഀ-ൿ]", "", str(t))  # allow Malayalam letters too
        if t and f"#{t.lower()}" not in {x.lower() for x in out}:
            out.append(f"#{t}")
    return out[:8]


def generate_caption(
    transcript: Transcript | None,
    visual: VisualAnalysis,
    settings: Settings,
    previous_hooks: list[str] | None = None,
    client: Groq | None = None,
) -> Caption:
    client = client or Groq(api_key=settings.groq_api_key)
    prompt = build_prompt(transcript, visual, settings.brand_context, previous_hooks or [])
    try:
        res = client.chat.completions.create(
            model=settings.groq_text_model,
            messages=[
                {"role": "system", "content": "You write Instagram captions. Reply with JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.8,
            max_completion_tokens=700,
            response_format={"type": "json_object"},
        )
    except RateLimitError as exc:
        raise QuotaExhausted(f"Groq text rate limit: {exc}") from exc
    reply = res.choices[0].message.content or ""
    try:
        data = parse_json_block(reply)
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Caption model returned invalid JSON: {reply[:200]}") from exc

    cap = Caption(
        hook=str(data.get("hook", "")).strip(),
        body=str(data.get("caption", "")).strip(),
        description=str(data.get("description", "")).strip(),
        cta=str(data.get("cta", "")).strip(),
        hashtags=_clean_hashtags(data.get("hashtags", [])),
    )
    if not cap.hook or not cap.body:
        raise RuntimeError("Caption model returned an empty hook or caption")
    if len(cap.hashtags) < 5:
        log.warning("Only %d hashtags returned", len(cap.hashtags))
    log.info("Caption hook: %s", cap.hook)
    return cap
