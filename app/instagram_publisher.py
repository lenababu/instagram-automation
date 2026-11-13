"""Official Instagram Graph API (Meta) Reels publishing.

Flow: POST /{ig-user-id}/media (REELS container) -> poll /{container}?fields=status_code
      -> POST /{ig-user-id}/media_publish -> GET /{media-id}?fields=permalink

Tokens come from env only. Token expiry is detected (OAuth error codes 190/463/467)
and raised as TokenExpired so the run can notify you instead of retrying blindly.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests

from app.config import Settings

log = logging.getLogger(__name__)
GRAPH = "https://graph.facebook.com"


class InstagramError(RuntimeError):
    def __init__(self, message: str, code: int | None = None, subcode: int | None = None):
        super().__init__(message)
        self.code = code
        self.subcode = subcode


class TokenExpired(InstagramError):
    pass


class RateLimited(InstagramError):
    pass


@dataclass
class PublishResult:
    media_id: str
    permalink: str


class InstagramPublisher:
    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self.s = settings
        self.base = f"{GRAPH}/{settings.meta_api_version}"
        self.http = session or requests.Session()

    # ---- low level -------------------------------------------------------------------
    def _call(self, method: str, path: str, **params) -> dict:
        params["access_token"] = self.s.meta_access_token
        url = f"{self.base}/{path.lstrip('/')}"
        for attempt in range(3):
            try:
                r = self.http.request(method, url, params=params if method == "GET" else None,
                                      data=None if method == "GET" else params, timeout=60)
            except requests.RequestException as exc:
                if attempt == 2:
                    raise InstagramError(f"Network error calling {path}: {exc}") from exc
                time.sleep(5 * (attempt + 1))
                continue
            try:
                data = r.json()
            except ValueError:
                data = {"error": {"message": r.text[:300]}}
            if "error" in data:
                err = data["error"]
                code, sub = err.get("code"), err.get("error_subcode")
                msg = f"Graph API error {code}/{sub}: {err.get('message')}"
                if code in (190, 102) or sub in (463, 467, 460):
                    raise TokenExpired(msg, code, sub)
                if code in (4, 17, 32, 613) or (code == 80001):
                    raise RateLimited(msg, code, sub)
                if code in (1, 2) and attempt < 2:  # transient Meta errors
                    time.sleep(10)
                    continue
                raise InstagramError(msg, code, sub)
            return data
        raise InstagramError(f"Gave up calling {path}")

    # ---- auth --------------------------------------------------------------------------
    def verify_token(self) -> dict:
        """Confirms the token works and returns the IG account (username) + expiry info."""
        me = self._call("GET", self.s.instagram_user_id, fields="id,username")
        info = {}
        try:
            dbg = self._call("GET", "debug_token", input_token=self.s.meta_access_token)
            d = dbg.get("data", {})
            info = {"expires_at": d.get("expires_at"), "data_access_expires_at": d.get("data_access_expires_at"),
                    "scopes": d.get("scopes", [])}
        except InstagramError as exc:
            log.debug("debug_token not available: %s", exc)
        return {**me, **info}

    def token_days_left(self) -> int | None:
        info = self.verify_token()
        exp = info.get("expires_at")
        if not exp:  # 0 = never expires (e.g. system-user token)
            return None
        return int((exp - time.time()) // 86400)

    # ---- publishing ----------------------------------------------------------------------
    def create_reel_container(self, video_url: str, caption: str, cover_url: str | None,
                              share_to_feed: bool = True) -> str:
        params = {"media_type": "REELS", "video_url": video_url, "caption": caption,
                  "share_to_feed": "true" if share_to_feed else "false"}
        if cover_url:
            params["cover_url"] = cover_url
        data = self._call("POST", f"{self.s.instagram_user_id}/media", **params)
        cid = data["id"]
        log.info("Created Reel container %s", cid)
        return cid

    def container_status(self, container_id: str) -> tuple[str, str]:
        data = self._call("GET", container_id, fields="status_code,status")
        return data.get("status_code", "UNKNOWN"), data.get("status", "")

    def wait_until_ready(self, container_id: str, timeout_s: int = 600, poll_s: int = 15) -> None:
        deadline = time.time() + timeout_s
        while True:
            code, status = self.container_status(container_id)
            log.info("Container %s: %s %s", container_id, code, status)
            if code == "FINISHED":
                return
            if code == "ERROR":
                raise InstagramError(f"Instagram failed to process the video: {status}")
            if code == "EXPIRED":
                raise InstagramError("Container expired before publishing")
            if code == "PUBLISHED":
                return
            if time.time() > deadline:
                raise InstagramError(f"Timed out waiting for container {container_id} (last {code})")
            time.sleep(poll_s)

    def publish(self, container_id: str) -> str:
        data = self._call("POST", f"{self.s.instagram_user_id}/media_publish", creation_id=container_id)
        return data["id"]

    def permalink(self, media_id: str) -> str:
        try:
            return self._call("GET", media_id, fields="permalink").get("permalink", "")
        except InstagramError as exc:
            log.warning("Could not fetch permalink: %s", exc)
            return ""

    def find_recent_media_by_caption(self, caption: str, limit: int = 10) -> PublishResult | None:
        """Used after a timeout: did the reel actually get published?"""
        data = self._call("GET", f"{self.s.instagram_user_id}/media", fields="id,caption,permalink,timestamp", limit=limit)
        probe = caption.strip()[:60]
        for m in data.get("data", []):
            if probe and (m.get("caption") or "").strip().startswith(probe):
                return PublishResult(m["id"], m.get("permalink", ""))
        return None

    def publish_reel(self, video_url: str, caption: str, cover_url: str | None,
                     existing_container: str | None = None) -> tuple[PublishResult, str]:
        """Full flow. Returns (result, container_id). Reuses an existing container if given."""
        cid = existing_container
        if cid:
            try:
                code, _ = self.container_status(cid)
                if code in ("ERROR", "EXPIRED"):
                    cid = None
            except InstagramError:
                cid = None
        if not cid:
            cid = self.create_reel_container(video_url, caption, cover_url)
        self.wait_until_ready(cid)
        try:
            media_id = self.publish(cid)
        except InstagramError as exc:
            # Never assume failure on timeout/ambiguous errors: check Instagram's actual state.
            found = self.find_recent_media_by_caption(caption)
            if found:
                log.warning("Publish call errored but the Reel exists on Instagram: %s", found.media_id)
                return found, cid
            raise exc
        return PublishResult(media_id, self.permalink(media_id)), cid
