"""Cross-post the same Reel to a Facebook Page (free, same Meta app as Instagram).

Flow (Reels API):
  GET  /me/accounts                       -> Page access token for the configured Page
  POST /{page-id}/video_reels start       -> video_id + upload_url
  POST {upload_url} (header file_url=...) -> Facebook pulls the hosted file itself
  POST /{page-id}/video_reels finish      -> PUBLISHED
  GET  /{video-id}?fields=permalink_url   -> link

Cross-posting is best-effort by design: the Instagram post is the primary action and must
never fail because Facebook did. Callers catch FacebookError and carry on.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests

from app.config import Settings

log = logging.getLogger(__name__)
GRAPH = "https://graph.facebook.com"
# Posting to a Page needs this on top of the Instagram permissions.
REQUIRED_PERMISSION = "pages_manage_posts"


class FacebookError(RuntimeError):
    pass


@dataclass
class FacebookPost:
    video_id: str
    permalink: str


class FacebookPublisher:
    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self.s = settings
        self.base = f"{GRAPH}/{settings.meta_api_version}"
        self.http = session or requests.Session()
        self._page_token: str | None = None
        self._page_id: str | None = None

    # ---- low level -------------------------------------------------------------------
    def _call(self, method: str, path: str, token: str | None = None, **params) -> dict:
        params["access_token"] = token or self.s.meta_access_token
        url = f"{self.base}/{path.lstrip('/')}"
        try:
            r = self.http.request(
                method, url,
                params=params if method == "GET" else None,
                data=None if method == "GET" else params,
                timeout=120,
            )
        except requests.RequestException as exc:
            raise FacebookError(f"Network error calling {path}: {exc}") from exc
        try:
            data = r.json()
        except ValueError:
            data = {"error": {"message": r.text[:300]}}
        if "error" in data:
            err = data["error"]
            raise FacebookError(f"Graph API error {err.get('code')}: {err.get('message')}")
        return data

    # ---- page token ------------------------------------------------------------------
    def page(self) -> tuple[str, str]:
        """Return (page_id, page_access_token) for the configured Page."""
        if self._page_token and self._page_id:
            return self._page_id, self._page_token
        data = self._call("GET", "me/accounts", fields="id,name,access_token")
        pages = data.get("data", [])
        if not pages:
            raise FacebookError("No Facebook Page is available to this token")
        wanted = self.s.facebook_page_id
        chosen = next((p for p in pages if p["id"] == wanted), None) if wanted else pages[0]
        if chosen is None:
            raise FacebookError(f"Page {wanted} not found on this token")
        self._page_id, self._page_token = chosen["id"], chosen["access_token"]
        log.info("Facebook Page: %s (%s)", chosen.get("name", "?"), self._page_id)
        return self._page_id, self._page_token

    def has_permission(self) -> bool:
        """True when the token carries pages_manage_posts (needed to publish)."""
        try:
            data = self._call("GET", "me/permissions")
        except FacebookError as exc:
            log.warning("Could not read token permissions: %s", exc)
            return False
        granted = {d["permission"] for d in data.get("data", []) if d.get("status") == "granted"}
        return REQUIRED_PERMISSION in granted

    # ---- publishing ------------------------------------------------------------------
    def publish_reel(self, video_url: str, caption: str) -> FacebookPost:
        page_id, page_token = self.page()

        start = self._call("POST", f"{page_id}/video_reels", token=page_token, upload_phase="start")
        video_id, upload_url = start.get("video_id"), start.get("upload_url")
        if not video_id or not upload_url:
            raise FacebookError(f"Reels upload did not start: {start}")

        # Facebook fetches the hosted file itself; no bytes leave this runner.
        try:
            r = self.http.post(
                upload_url,
                headers={"Authorization": f"OAuth {page_token}", "file_url": video_url},
                timeout=300,
            )
            body = r.json() if r.content else {}
        except requests.RequestException as exc:
            raise FacebookError(f"Network error uploading to Facebook: {exc}") from exc
        except ValueError:
            body = {}
        if not body.get("success", True) or "error" in body:
            raise FacebookError(f"Facebook rejected the upload: {body}")

        self._call(
            "POST", f"{page_id}/video_reels", token=page_token,
            upload_phase="finish", video_id=video_id,
            video_state="PUBLISHED", description=caption,
        )
        self._wait_published(video_id, page_token)
        return FacebookPost(video_id=video_id, permalink=self._permalink(video_id, page_token))

    def _wait_published(self, video_id: str, page_token: str, timeout_s: int = 300) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                data = self._call("GET", video_id, token=page_token, fields="status")
            except FacebookError as exc:
                log.warning("Facebook status check failed: %s", exc)
                return
            phase = (data.get("status") or {}).get("video_status", "")
            if phase in {"ready", "published"}:
                return
            if phase == "error":
                raise FacebookError(f"Facebook reported processing error: {data.get('status')}")
            time.sleep(10)
        log.warning("Facebook video %s still processing after %ds; it will finish on its own",
                    video_id, timeout_s)

    def _permalink(self, video_id: str, page_token: str) -> str:
        try:
            data = self._call("GET", video_id, token=page_token, fields="permalink_url")
        except FacebookError:
            return ""
        link = data.get("permalink_url", "")
        return f"https://www.facebook.com{link}" if link.startswith("/") else link
