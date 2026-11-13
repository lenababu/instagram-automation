"""Optional Telegram notifications. Fully no-op when the bot token/chat id are absent."""
from __future__ import annotations

import logging

import requests

from app.config import Settings

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, settings: Settings):
        self.s = settings

    def send(self, text: str) -> bool:
        if not self.s.telegram_enabled:
            log.info("[notify] %s", text.replace("\n", " | "))
            return False
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{self.s.telegram_bot_token}/sendMessage",
                json={"chat_id": self.s.telegram_chat_id, "text": text[:4000], "disable_web_page_preview": False},
                timeout=20,
            )
            if r.status_code != 200:
                log.warning("Telegram returned %s: %s", r.status_code, r.text[:200])
                return False
            return True
        except requests.RequestException as exc:
            log.warning("Telegram failed: %s", exc)
            return False

    # convenience wrappers --------------------------------------------------------
    def published(self, filename: str, permalink: str, caption: str) -> None:
        self.send(f"✅ Reel published: {filename}\n{permalink}\n\n{caption[:600]}")

    def failed(self, filename: str, error: str) -> None:
        self.send(f"❌ Publishing failed: {filename}\n{error[:800]}")

    def missing_thumbnail(self, filename: str) -> None:
        self.send(f"⚠️ No matching thumbnail for {filename}. Posting with Instagram's auto cover.")

    def no_videos(self) -> None:
        self.send("ℹ️ No unposted videos left in Google Drive. Add more to the Videos folder.")

    def quota(self, service: str, detail: str) -> None:
        self.send(f"⏳ {service} free quota exhausted. Today's post was skipped.\n{detail[:400]}")

    def auth(self, detail: str) -> None:
        self.send(f"🔑 Instagram authentication needs attention:\n{detail[:600]}\nSee README → Renewing the Meta token.")

    def dry_run(self, filename: str, caption: str, video_url: str) -> None:
        self.send(f"🧪 DRY RUN for {filename} (nothing published)\nVideo: {video_url}\n\n{caption[:800]}")
