"""Google Drive access: list videos/thumbnails, match pairs, download by file ID.

Files are only ever read. Nothing is renamed, moved, deleted or overwritten.
"""
from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from natsort import natsorted

from app.config import Settings
from app.google_auth import get_credentials

log = logging.getLogger(__name__)

# Formats Instagram accepts for Reels and cover images
VIDEO_EXTENSIONS = {".mp4", ".mov"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


@dataclass(frozen=True)
class DriveFile:
    file_id: str
    name: str
    mime_type: str = ""
    size: int = 0

    @property
    def stem(self) -> str:
        return os.path.splitext(self.name)[0]

    @property
    def extension(self) -> str:
        return os.path.splitext(self.name)[1].lower()


@dataclass(frozen=True)
class VideoCandidate:
    video: DriveFile
    thumbnail: DriveFile | None

    @property
    def has_thumbnail(self) -> bool:
        return self.thumbnail is not None


class DriveService:
    def __init__(self, settings: Settings, service=None):
        self.settings = settings
        self._service = service or build(
            "drive", "v3", credentials=get_credentials(settings), cache_discovery=False
        )

    # ---- listing -----------------------------------------------------------------
    def list_folder(self, folder_id: str) -> list[DriveFile]:
        files: list[DriveFile] = []
        page_token = None
        while True:
            resp = (
                self._service.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    fields="nextPageToken, files(id, name, mimeType, size)",
                    pageSize=200,
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            for f in resp.get("files", []):
                files.append(
                    DriveFile(
                        file_id=f["id"],
                        name=f["name"],
                        mime_type=f.get("mimeType", ""),
                        size=int(f.get("size", 0) or 0),
                    )
                )
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return files

    def list_videos(self) -> list[DriveFile]:
        files = [
            f
            for f in self.list_folder(self.settings.drive_video_folder_id)
            if f.extension in VIDEO_EXTENSIONS
        ]
        return sort_naturally(files)

    def list_thumbnails(self) -> list[DriveFile]:
        return [
            f
            for f in self.list_folder(self.settings.drive_thumbnail_folder_id)
            if f.extension in IMAGE_EXTENSIONS
        ]

    def list_candidates(self) -> list[VideoCandidate]:
        """Videos in natural order, each paired with the thumbnail sharing its stem (if any)."""
        return match_thumbnails(self.list_videos(), self.list_thumbnails())

    # ---- download ----------------------------------------------------------------
    def download(self, file: DriveFile, dest_dir: str) -> str:
        """Download a file by ID into dest_dir. The Drive original is untouched."""
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, f"{file.file_id}{file.extension}")
        request = self._service.files().get_media(fileId=file.file_id, supportsAllDrives=True)
        with io.FileIO(dest, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request, chunksize=16 * 1024 * 1024)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    log.debug("Downloading %s: %d%%", file.name, int(status.progress() * 100))
        log.info("Downloaded %s (%.1f MB)", file.name, os.path.getsize(dest) / 1_048_576)
        return dest


# ---- pure helpers (unit-tested) -----------------------------------------------------
def sort_naturally(files: list[DriveFile]) -> list[DriveFile]:
    """001.mp4, 002.mp4, 010.mp4 ... and 2.mp4 before 10.mp4."""
    return natsorted(files, key=lambda f: f.name.lower())


def match_thumbnails(videos: list[DriveFile], thumbnails: list[DriveFile]) -> list[VideoCandidate]:
    """Pair each video with the image whose filename stem matches (001.mp4 -> 001.jpg)."""
    by_stem: dict[str, DriveFile] = {}
    for t in thumbnails:
        key = t.stem.lower()
        # Prefer jpg over png when both exist; otherwise first seen wins
        if key not in by_stem or (t.extension in {".jpg", ".jpeg"} and by_stem[key].extension == ".png"):
            by_stem[key] = t
    return [VideoCandidate(video=v, thumbnail=by_stem.get(v.stem.lower())) for v in videos]
