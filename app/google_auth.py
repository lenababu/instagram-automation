"""Google OAuth credentials built from a long-lived refresh token (no browser needed in CI)."""
from __future__ import annotations

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from app.config import Settings

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]


class GoogleAuthError(RuntimeError):
    pass


def get_credentials(settings: Settings) -> Credentials:
    creds = Credentials(
        token=None,
        refresh_token=settings.google_refresh_token,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    try:
        creds.refresh(Request())
    except Exception as exc:  # noqa: BLE001
        raise GoogleAuthError(
            "Google authentication failed. The refresh token may be revoked or expired "
            "(OAuth apps left in 'Testing' publishing status expire tokens after 7 days; "
            "set the app to 'In production' or re-run scripts/get_google_refresh_token.py)."
        ) from exc
    return creds
