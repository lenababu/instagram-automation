"""Cloudinary (free plan) hosting so Instagram can fetch the video and cover over HTTPS.

* public_id = "ig/<drive file id>" so uploads are idempotent: an existing asset is reused.
* Free plan: 25 credits/month, max ~100 MB per video upload. We re-encode above MAX_UPLOAD_MB.
* We never upgrade or switch plans; a quota error is surfaced as HostingError.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import cloudinary
import cloudinary.api
import cloudinary.uploader
import requests

from app.config import Settings

log = logging.getLogger(__name__)


class HostingError(RuntimeError):
    pass


@dataclass
class HostedMedia:
    video_url: str
    thumbnail_url: str | None
    video_public_id: str
    thumbnail_public_id: str | None


class MediaHosting:
    def __init__(self, settings: Settings):
        self.settings = settings
        cloudinary.config(
            cloud_name=settings.cloudinary_cloud_name,
            api_key=settings.cloudinary_api_key,
            api_secret=settings.cloudinary_api_secret,
            secure=True,
        )

    @staticmethod
    def public_id(drive_file_id: str) -> str:
        return f"ig/{drive_file_id}"

    def _existing(self, public_id: str, resource_type: str) -> str | None:
        try:
            res = cloudinary.api.resource(public_id, resource_type=resource_type)
            return res.get("secure_url")
        except cloudinary.api.NotFound:
            return None
        except Exception as exc:  # noqa: BLE001
            log.warning("Cloudinary lookup failed for %s: %s", public_id, exc)
            return None

    def _upload(self, path: str, public_id: str, resource_type: str) -> str:
        size_mb = os.path.getsize(path) / 1_048_576
        if resource_type == "video" and size_mb > 100:
            raise HostingError(f"Video is {size_mb:.0f} MB; Cloudinary free plan allows 100 MB")
        try:
            if resource_type == "video":
                res = cloudinary.uploader.upload_large(
                    path, public_id=public_id, resource_type="video", overwrite=False,
                    chunk_size=6_000_000, invalidate=False,
                )
            else:
                res = cloudinary.uploader.upload(path, public_id=public_id, resource_type="image", overwrite=False)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "quota" in msg.lower() or "limit" in msg.lower() or "420" in msg:
                raise HostingError(f"Cloudinary free quota problem: {msg}") from exc
            raise HostingError(f"Cloudinary upload failed: {msg}") from exc
        url = res.get("secure_url")
        if not url:
            raise HostingError(f"Cloudinary returned no URL: {res}")
        log.info("Uploaded %s (%.1f MB) -> %s", resource_type, size_mb, url)
        return url

    def host(self, video_path: str, thumb_path: str | None, drive_file_id: str, thumb_file_id: str | None) -> HostedMedia:
        vid_id = self.public_id(drive_file_id)
        video_url = self._existing(vid_id, "video") or self._upload(video_path, vid_id, "video")
        thumb_url = thumb_id = None
        if thumb_path and thumb_file_id:
            thumb_id = self.public_id(thumb_file_id)
            thumb_url = self._existing(thumb_id, "image") or self._upload(thumb_path, thumb_id, "image")
        verify_public_url(video_url)
        if thumb_url:
            verify_public_url(thumb_url)
        return HostedMedia(video_url, thumb_url, vid_id, thumb_id)

    def delete(self, drive_file_id: str, thumb_file_id: str | None = None) -> None:
        """Free up credits after a confirmed publish (Instagram has already copied the media)."""
        for pid, rtype in ((self.public_id(drive_file_id), "video"), (self.public_id(thumb_file_id or ""), "image")):
            if pid.endswith("/"):
                continue
            try:
                cloudinary.uploader.destroy(pid, resource_type=rtype, invalidate=True)
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not delete %s from Cloudinary: %s", pid, exc)


def verify_public_url(url: str) -> None:
    """Instagram must be able to GET the URL anonymously. Fail early if it can't."""
    try:
        r = requests.head(url, allow_redirects=True, timeout=30)
        if r.status_code >= 400:
            r = requests.get(url, stream=True, timeout=30)
        if r.status_code >= 400:
            raise HostingError(f"Media URL not publicly reachable ({r.status_code}): {url}")
    except requests.RequestException as exc:
        raise HostingError(f"Could not verify media URL {url}: {exc}") from exc
