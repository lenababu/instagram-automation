"""The daily pipeline (one video per run). Called by main.py from GitHub Actions.

Scheduling itself is done by GitHub Actions cron (see .github/workflows); this module is the
step-by-step job that one scheduled execution performs, with status checkpoints in Sheets so
an interrupted run can resume safely.
"""
from __future__ import annotations

import logging
import os
import uuid

from app import video_processor as vp
from app.audio_transcription import QuotaExhausted, Transcript, transcribe
from app.caption_generator import generate_caption
from app.config import Settings
from app.drive_service import DriveService
from app.duplicate_checker import Selection, is_already_published, select_forced, select_next
from app.instagram_publisher import InstagramError, InstagramPublisher, RateLimited, TokenExpired
from app.media_hosting import HostingError, MediaHosting
from app.notifications import Notifier
from app.sheets_service import (
    STATUS_ANALYSED,
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_PUBLISHED,
    STATUS_READY,
    STATUS_SKIPPED,
    SheetsService,
    now_iso,
)
from app.visual_analysis import MAX_IMAGES, VisualAnalysis, analyse_frames

log = logging.getLogger(__name__)


class RunOutcome:
    PUBLISHED = "published"
    DRY_RUN = "dry_run"
    NOTHING_TO_DO = "nothing_to_do"
    SKIPPED = "skipped"
    FAILED = "failed"


def run_daily_job(settings: Settings) -> str:
    notifier = Notifier(settings)
    work_dir = os.path.join(settings.work_dir, uuid.uuid4().hex[:8])
    os.makedirs(work_dir, exist_ok=True)
    try:
        return _run(settings, notifier, work_dir)
    finally:
        vp.cleanup(settings.work_dir)


def _run(settings: Settings, notifier: Notifier, work_dir: str) -> str:
    vp.ensure_ffmpeg()
    drive = DriveService(settings)
    sheets = SheetsService(settings)
    publisher = InstagramPublisher(settings)

    # 0) Token health first: no point processing if we cannot publish.
    try:
        days = publisher.token_days_left()
        if days is not None and days <= 7:
            notifier.auth(f"Meta access token expires in {days} day(s). Renew it now.")
        if days is not None and days < 0:
            return RunOutcome.FAILED
    except TokenExpired as exc:
        notifier.auth(str(exc))
        return RunOutcome.FAILED

    # 1) Select the next video (history check + duplicate prevention)
    candidates = drive.list_candidates()
    log.info("Drive: %d videos, %d with thumbnails", len(candidates), sum(c.has_thumbnail for c in candidates))
    records = sheets.by_file_id()
    if settings.force_video:
        try:
            sel = select_forced(candidates, records, settings.force_video)
        except ValueError as exc:
            log.error("%s", exc)
            notifier.failed(settings.force_video, str(exc))
            return RunOutcome.FAILED
    else:
        sel = select_next(candidates, records)
    if sel is None:
        notifier.no_videos()
        return RunOutcome.NOTHING_TO_DO
    rec, cand = sel.record, sel.candidate
    log.info("Selected %s (drive id %s)%s", cand.video.name, cand.video.file_id, " [resume]" if sel.resumed else "")

    # Double-check against Instagram state when resuming a job that already had a container
    if sel.resumed and rec.ig_container_id and rec.caption:
        found = publisher.find_recent_media_by_caption(rec.caption)
        if found:
            rec.status, rec.instagram_media_id, rec.instagram_permalink = STATUS_PUBLISHED, found.media_id, found.permalink
            rec.published_date = rec.published_date or now_iso()
            sheets.upsert(rec)
            notifier.published(rec.filename, found.permalink, rec.caption)
            return RunOutcome.PUBLISHED

    if not cand.has_thumbnail:
        notifier.missing_thumbnail(cand.video.name)

    previous_status = rec.status
    if not settings.dry_run:
        if sel.resumed or previous_status == STATUS_FAILED:
            rec.retry_count += 1
        rec.status = STATUS_PROCESSING
        rec.processing_date = now_iso()
        rec.error_message = ""
        sheets.upsert(rec)

    try:
        # 2) Download + validate
        video_path = drive.download(cand.video, work_dir)
        thumb_path = drive.download(cand.thumbnail, work_dir) if cand.thumbnail else None
        info = vp.probe(video_path)
        problems = vp.validate_for_reels(info)
        if problems:
            raise SkipVideo("Invalid video for Reels: " + ", ".join(problems))
        log.info("Video: %.1fs %dx%d %.1f MB audio=%s", info.duration, info.width, info.height, info.size_mb, info.has_audio)

        # 3) Analysis (audio + visual) -> caption; reuse caption when resuming
        if rec.caption and sel.resumed and previous_status in (STATUS_ANALYSED, STATUS_READY):
            caption_text = rec.caption
            log.info("Reusing caption from interrupted run")
        else:
            transcript = _transcribe(video_path, info, work_dir, settings)
            frames = vp.extract_frames(video_path, work_dir, info.duration, count=MAX_IMAGES)
            visual = _analyse(frames, settings)
            previous_hooks = [r.caption.split("\n", 1)[0] for r in records.values() if r.caption]
            caption_text = generate_caption(transcript, visual, settings, previous_hooks).render()
            rec.caption = caption_text
            if not settings.dry_run:
                rec.status = STATUS_ANALYSED
                sheets.upsert(rec)
        log.info("Caption:\n%s", caption_text)

        # 4) Host media (idempotent per Drive file id)
        if info.size_mb > settings.max_upload_mb:
            video_path = vp.shrink_video(video_path, work_dir, settings.max_upload_mb)
        hosting = MediaHosting(settings)
        hosted = hosting.host(video_path, thumb_path, cand.video.file_id, cand.thumbnail.file_id if cand.thumbnail else None)
        rec.video_url, rec.thumbnail_url = hosted.video_url, hosted.thumbnail_url or ""

        if settings.dry_run:
            notifier.dry_run(cand.video.name, caption_text, hosted.video_url)
            log.info("DRY RUN complete. Nothing was published and the sheet was not changed.")
            return RunOutcome.DRY_RUN

        rec.status = STATUS_READY
        sheets.upsert(rec)

        # 5) Publish via the official API
        result, container_id = publisher.publish_reel(
            hosted.video_url, caption_text, hosted.thumbnail_url, existing_container=rec.ig_container_id or None
        )
        rec.ig_container_id = container_id
        rec.status = STATUS_PUBLISHED
        rec.instagram_media_id = result.media_id
        rec.instagram_permalink = result.permalink
        rec.published_date = now_iso()
        rec.error_message = ""
        sheets.upsert(rec)
        notifier.published(cand.video.name, result.permalink, caption_text)

        # 6) Free Cloudinary credits (Instagram has copied the media by now)
        hosting.delete(cand.video.file_id, cand.thumbnail.file_id if cand.thumbnail else None)
        return RunOutcome.PUBLISHED

    except SkipVideo as exc:
        log.warning("Skipping %s: %s", cand.video.name, exc)
        if not settings.dry_run:
            rec.status, rec.error_message = STATUS_SKIPPED, str(exc)
            sheets.upsert(rec)
        notifier.failed(cand.video.name, f"Skipped: {exc}")
        return RunOutcome.SKIPPED
    except QuotaExhausted as exc:
        _mark_failed(sheets, rec, settings, str(exc))
        notifier.quota("Groq", str(exc))
        return RunOutcome.SKIPPED
    except TokenExpired as exc:
        _mark_failed(sheets, rec, settings, str(exc))
        notifier.auth(str(exc))
        return RunOutcome.FAILED
    except RateLimited as exc:
        _mark_failed(sheets, rec, settings, str(exc))
        notifier.quota("Instagram API", str(exc))
        return RunOutcome.SKIPPED
    except (HostingError, InstagramError, vp.FFmpegError) as exc:
        _mark_failed(sheets, rec, settings, str(exc))
        notifier.failed(cand.video.name, str(exc))
        return RunOutcome.FAILED
    except Exception as exc:  # noqa: BLE001
        log.exception("Unexpected failure")
        _mark_failed(sheets, rec, settings, f"{type(exc).__name__}: {exc}")
        notifier.failed(cand.video.name, f"{type(exc).__name__}: {exc}")
        return RunOutcome.FAILED


class SkipVideo(Exception):
    pass


def _mark_failed(sheets: SheetsService, rec, settings: Settings, error: str) -> None:
    if settings.dry_run or is_already_published(rec):
        return
    rec.status = STATUS_FAILED
    rec.error_message = error[:900]
    sheets.upsert(rec)


def _transcribe(video_path: str, info: vp.VideoInfo, work_dir: str, settings: Settings) -> Transcript | None:
    if not info.has_audio:
        log.info("No audio stream; visual-only analysis")
        return None
    audio = vp.extract_audio(video_path, work_dir)
    try:
        return transcribe(audio, work_dir, settings)
    except QuotaExhausted:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("Transcription failed, continuing with visuals only: %s", exc)
        return None


def _analyse(frames: list[str], settings: Settings) -> VisualAnalysis:
    try:
        return analyse_frames(frames, settings)
    except QuotaExhausted:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("Visual analysis failed: %s", exc)
        return VisualAnalysis(summary="(visual analysis unavailable)")
