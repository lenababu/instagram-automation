"""Central configuration loaded from environment variables (GitHub Secrets in CI).

Nothing here is ever hardcoded; `.env` is only read for local development.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()  # no-op in GitHub Actions (no .env file); handy locally


class ConfigError(RuntimeError):
    pass


def _bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _env(name: str, default: str = "", required: bool = False) -> str:
    val = os.getenv(name, default)
    if required and not val:
        raise ConfigError(f"Missing required environment variable: {name}")
    return val


@dataclass
class Settings:
    # Google
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    drive_video_folder_id: str = ""
    drive_thumbnail_folder_id: str = ""
    sheets_id: str = ""
    sheet_tab_name: str = "Posting History"

    # Groq
    groq_api_key: str = ""
    vision_api_key: str = ""
    groq_whisper_model: str = "whisper-large-v3"
    groq_vision_model: str = "qwen/qwen3.8-27b"
    groq_text_model: str = "openai/gpt-oss-120b"

    # Cloudinary
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""

    # Meta
    meta_access_token: str = ""
    instagram_user_id: str = ""
    meta_api_version: str = "v24.0"

    # Telegram (optional)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Behaviour
    brand_context: str = ""
    dry_run: bool = True
    force_video: str = ""  # filename or numeric prefix to post instead of the next in order
    caption_mode: str = "files"  # "files": use captions/<video stem>.txt (skip AI); "ai": generate with Groq
    captions_dir: str = "captions"
    max_upload_mb: int = 95
    log_level: str = "INFO"
    work_dir: str = "work"

    secrets: list[str] = field(default_factory=list, repr=False)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def vision_key(self) -> str:
        return self.vision_api_key or self.groq_api_key


def load_settings(require_all: bool = True) -> Settings:
    """Build Settings from env. With require_all=True the core credentials must be present."""
    s = Settings(
        google_client_id=_env("GOOGLE_CLIENT_ID", required=require_all),
        google_client_secret=_env("GOOGLE_CLIENT_SECRET", required=require_all),
        google_refresh_token=_env("GOOGLE_REFRESH_TOKEN", required=require_all),
        drive_video_folder_id=_env("GOOGLE_DRIVE_VIDEO_FOLDER_ID", required=require_all),
        drive_thumbnail_folder_id=_env("GOOGLE_DRIVE_THUMBNAIL_FOLDER_ID", required=require_all),
        sheets_id=_env("GOOGLE_SHEETS_ID", required=require_all),
        sheet_tab_name=_env("GOOGLE_SHEET_TAB", "Posting History"),
        groq_api_key=_env("GROQ_API_KEY", required=require_all),
        vision_api_key=_env("VISION_API_KEY"),
        groq_whisper_model=_env("GROQ_WHISPER_MODEL", "whisper-large-v3"),
        groq_vision_model=_env("GROQ_VISION_MODEL", "qwen/qwen3.8-27b"),
        groq_text_model=_env("GROQ_TEXT_MODEL", "openai/gpt-oss-120b"),
        cloudinary_cloud_name=_env("CLOUDINARY_CLOUD_NAME", required=require_all),
        cloudinary_api_key=_env("CLOUDINARY_API_KEY", required=require_all),
        cloudinary_api_secret=_env("CLOUDINARY_API_SECRET", required=require_all),
        meta_access_token=_env("META_ACCESS_TOKEN", required=require_all),
        instagram_user_id=_env("INSTAGRAM_USER_ID", required=require_all),
        meta_api_version=_env("META_API_VERSION", "v24.0"),
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
        brand_context=_env("BRAND_CONTEXT"),
        dry_run=_bool("DRY_RUN", True),
        force_video=_env("FORCE_VIDEO").strip(),
        caption_mode=_env("CAPTION_MODE", "files").strip().lower() or "files",
        captions_dir=_env("CAPTIONS_DIR", "captions"),
        max_upload_mb=int(_env("MAX_UPLOAD_MB", "95") or 95),
        log_level=_env("LOG_LEVEL", "INFO"),
        work_dir=_env("WORK_DIR", "work"),
    )
    s.secrets = [
        v
        for v in (
            s.google_client_secret,
            s.google_refresh_token,
            s.groq_api_key,
            s.vision_api_key,
            s.cloudinary_api_secret,
            s.meta_access_token,
            s.telegram_bot_token,
        )
        if v and len(v) >= 8
    ]
    return s


class SecretRedactingFormatter(logging.Formatter):
    """Replaces any configured secret value with *** in every log line."""

    def __init__(self, secrets: list[str], fmt: str):
        super().__init__(fmt)
        self._pattern = (
            re.compile("|".join(re.escape(x) for x in sorted(secrets, key=len, reverse=True)))
            if secrets
            else None
        )

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        if self._pattern:
            msg = self._pattern.sub("***", msg)
        return msg


def setup_logging(settings: Settings) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        SecretRedactingFormatter(settings.secrets, "%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    for noisy in ("googleapiclient.discovery_cache", "urllib3", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
