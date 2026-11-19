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
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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


def select_forced(
    candidates: list[VideoCandidate], records: dict[str, PostRecord], wanted: str
) -> Selection:
    """Pick one specific video by exact filename or by its numeric prefix (e.g. "6" or "06").

    Raises ValueError when nothing matches or the match was already published/skipped.
    """
    wanted = wanted.strip().lower()
    matches = [c for c in candidates if c.video.name.lower() == wanted]
    if not matches and wanted.isdigit():
        matches = [
            c for c in candidates
            if (m := re.match(r"\d+", c.video.name)) and int(m.group(0)) == int(wanted)
        ]
    if len(matches) != 1:
        raise ValueError(f"FORCE_VIDEO={wanted!r} matched {len(matches)} videos, expected exactly 1")
    c = matches[0]
    rec = records.get(c.video.file_id)
    if is_already_published(rec):
        raise ValueError(f"{c.video.name} is already {rec.status}; refusing to post it again")
    if rec is None:
        rec = PostRecord(
            filename=c.video.name,
            drive_file_id=c.video.file_id,
            thumbnail_file_id=c.thumbnail.file_id if c.thumbnail else "",
            status=STATUS_PENDING,
        )
    log.info("Forced selection: %s", c.video.name)
    return Selection(c, rec, resumed=rec.status in IN_FLIGHT_STATUSES)


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
        if rec.status in ("", STATUS_PENDING):
            # A row can be queued up front (by hand or by the ERP) with its caption already
            # filled in. Without this branch such a row is never picked and the whole queue
            # silently reports "nothing left to post".
            return Selection(c, rec, resumed=False)
    return None


def published_within(records: dict[str, PostRecord], hours: float, now: datetime | None = None) -> PostRecord | None:
    """Return a record published less than `hours` ago (to stop a second cron firing posting twice)."""
    if hours <= 0:
        return None
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    for rec in records.values():
        if not rec.published_date:
            continue
        try:
            when = datetime.strptime(rec.published_date, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if when >= cutoff:
            return rec
    return None
