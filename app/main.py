"""Entry point used by GitHub Actions.

    python -m app.main              # daily job (DRY_RUN env decides)
    python -m app.main --dry-run    # force dry run
    python -m app.main --check      # connectivity/credential checks only (no processing)
"""
from __future__ import annotations

import argparse
import logging
import sys

from app.config import ConfigError, load_settings, setup_logging

log = logging.getLogger("main")


def run_checks(settings) -> int:
    """Validate every integration without downloading or publishing anything."""
    from app.drive_service import DriveService
    from app.instagram_publisher import InstagramPublisher
    from app.media_hosting import MediaHosting
    from app.sheets_service import SheetsService
    from app.video_processor import ensure_ffmpeg
    import cloudinary.api
    from groq import Groq

    ok = True

    def step(name, fn):
        nonlocal ok
        try:
            out = fn()
            log.info("OK   %-22s %s", name, out if out is not None else "")
        except Exception as exc:  # noqa: BLE001
            ok = False
            log.error("FAIL %-22s %s", name, exc)

    step("ffmpeg", ensure_ffmpeg)
    drive = {}

    def _drive():
        d = DriveService(settings)
        cands = d.list_candidates()
        drive["c"] = cands
        missing = [c.video.name for c in cands if not c.has_thumbnail]
        return f"{len(cands)} videos, order: {[c.video.name for c in cands[:5]]}, missing thumbs: {missing}"

    step("google drive", _drive)
    step("google sheets", lambda: f"{len(SheetsService(settings).all_records())} rows")
    step("groq", lambda: f"{len(Groq(api_key=settings.groq_api_key).models.list().data)} models visible")
    step("cloudinary", lambda: (MediaHosting(settings), cloudinary.api.ping())[1])
    step("instagram token", lambda: InstagramPublisher(settings).verify_token())
    step("telegram", lambda: "enabled" if settings.telegram_enabled else "disabled (optional)")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="never publish, never write to the sheet")
    parser.add_argument("--check", action="store_true", help="only verify connections and exit")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        settings.dry_run = True
    setup_logging(settings)
    log.info("Mode: %s", "DRY RUN" if settings.dry_run else "LIVE PUBLISH")

    if args.check:
        return run_checks(settings)

    from app.scheduler import RunOutcome, run_daily_job

    outcome = run_daily_job(settings)
    log.info("Outcome: %s", outcome)
    # Exit non-zero only for real failures so GitHub marks the run red (and emails you).
    return 1 if outcome == RunOutcome.FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
