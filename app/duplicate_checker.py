"""Decides which video to post next and guarantees nothing is published twice.

Rules:
* Drive file ID is the identity. A Published or Skipped ID is never chosen again.
* An in-flight record (Processing/Analysed/Ready) from an interrupted run is resumed
  first, provided it has retries left; otherwise it is marked Failed and skipped.
* Failed records are retried up to MAX_RETRIES, after that they are left alone.
* Otherwise: first video in natural filename order that has no row yet.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.drive_service import VideoCandidate
from app.sheets_service import (
    IN_FLIGHT_STATUSES,
    MAX_RETRIES,
    STATUS_FAILED,
    STATUS_PENDING,
    TERMINAL_STATUSES,
    PostRecord,
)

log = logging.getLogger(__name__)


@dataclass
class Selection:
    candidate: VideoCandidate
    record: PostRecord
    resumed: bool  # True when picking up an interrupted run


def is_already_published(record: PostRecord | None) -> bool:
    return bool(record and (record.status in TERMINAL_STATUSES or record.instagram_media_id))


def select_next(
    candidates: list[VideoCandidate], records: dict[str, PostRecord]
) -> Selection | None:
    # 1) Resume interrupted work first (idempotent recovery)
    for c in candidates:
        rec = records.get(c.video.file_id)
        if rec and rec.status in IN_FLIGHT_STATUSES and not is_already_published(rec):
            if rec.retry_count >= MAX_RETRIES:
                log.warning("Giving up on %s after %d retries", rec.filename, rec.retry_count)
                continue
            log.info("Resuming interrupted job for %s (status %s)", rec.filename, rec.status)
            return Selection(c, rec, resumed=True)

    # 2) Fresh or retryable videos in natural order
    for c in candidates:
        rec = records.get(c.video.file_id)
        if is_already_published(rec):
            continue
        if rec is None:
            rec = PostRecord(
                filename=c.video.name,
                drive_file_id=c.video.file_id,
                thumbnail_file_id=c.thumbnail.file_id if c.thumbnail else "",
                status=STATUS_PENDING,
            )
            return Selection(c, rec, resumed=False)
        if rec.status == STATUS_FAILED and rec.retry_count < MAX_RETRIES:
            return Selection(c, rec, resumed=False)
    return None
