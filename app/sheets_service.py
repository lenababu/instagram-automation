"""Google Sheets as the persistent posting database.

Spreadsheet: "Instagram Posting History". Each row is one Drive video, keyed by its
Drive file ID. Status transitions:

    Pending -> Processing -> Analysed -> Ready -> Published
                                   -> Failed / Skipped
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import gspread

from app.config import Settings
from app.google_auth import get_credentials

log = logging.getLogger(__name__)

COLUMNS = [
    "Filename",
    "Google Drive File ID",
    "Thumbnail File ID",
    "Caption",
    "Status",
    "Processing Date",
    "Published Date",
    "Instagram Media ID",
    "Instagram Permalink",
    "Error Message",
    "Retry Count",
    "IG Container ID",  # extra: lets an interrupted run resume safely
    "Video URL",  # extra: Cloudinary URL already uploaded (avoids re-upload)
    "Thumbnail URL",
]

STATUS_PENDING = "Pending"
STATUS_PROCESSING = "Processing"
STATUS_ANALYSED = "Analysed"
STATUS_READY = "Ready"
STATUS_PUBLISHED = "Published"
STATUS_FAILED = "Failed"
STATUS_SKIPPED = "Skipped"

TERMINAL_STATUSES = {STATUS_PUBLISHED, STATUS_SKIPPED}
IN_FLIGHT_STATUSES = {STATUS_PROCESSING, STATUS_ANALYSED, STATUS_READY}
MAX_RETRIES = 3


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


@dataclass
class PostRecord:
    filename: str
    drive_file_id: str
    thumbnail_file_id: str = ""
    caption: str = ""
    status: str = STATUS_PENDING
    processing_date: str = ""
    published_date: str = ""
    instagram_media_id: str = ""
    instagram_permalink: str = ""
    error_message: str = ""
    retry_count: int = 0
    ig_container_id: str = ""
    video_url: str = ""
    thumbnail_url: str = ""
    row_number: int = field(default=0, repr=False)  # 1-based sheet row; 0 = not yet in sheet

    def to_row(self) -> list:
        return [
            self.filename,
            self.drive_file_id,
            self.thumbnail_file_id,
            self.caption,
            self.status,
            self.processing_date,
            self.published_date,
            self.instagram_media_id,
            self.instagram_permalink,
            self.error_message,
            self.retry_count,
            self.ig_container_id,
            self.video_url,
            self.thumbnail_url,
        ]

    @classmethod
    def from_row(cls, row: list, row_number: int) -> "PostRecord":
        vals = list(row) + [""] * (len(COLUMNS) - len(row))
        try:
            retries = int(vals[10] or 0)
        except ValueError:
            retries = 0
        return cls(
            filename=vals[0],
            drive_file_id=vals[1],
            thumbnail_file_id=vals[2],
            caption=vals[3],
            status=vals[4] or STATUS_PENDING,
            processing_date=vals[5],
            published_date=vals[6],
            instagram_media_id=vals[7],
            instagram_permalink=vals[8],
            error_message=vals[9],
            retry_count=retries,
            ig_container_id=vals[11],
            video_url=vals[12],
            thumbnail_url=vals[13],
            row_number=row_number,
        )


class SheetsService:
    def __init__(self, settings: Settings, worksheet=None):
        self.settings = settings
        if worksheet is not None:
            self.ws = worksheet
        else:
            client = gspread.authorize(get_credentials(settings))
            spreadsheet = client.open_by_key(settings.sheets_id)
            try:
                self.ws = spreadsheet.worksheet(settings.sheet_tab_name)
            except gspread.WorksheetNotFound:
                self.ws = spreadsheet.add_worksheet(settings.sheet_tab_name, rows=500, cols=len(COLUMNS))
        self.ensure_header()

    # ---- schema --------------------------------------------------------------------
    def ensure_header(self) -> None:
        first = self.ws.row_values(1)
        if first[: len(COLUMNS)] != COLUMNS:
            if not first:
                self.ws.append_row(COLUMNS, value_input_option="RAW")
            else:
                # Header exists but is older/shorter: extend it in place without touching data.
                self.ws.update(range_name="A1", values=[COLUMNS], value_input_option="RAW")
            log.info("Sheet header written/updated")

    # ---- reads ---------------------------------------------------------------------
    def all_records(self) -> list[PostRecord]:
        rows = self.ws.get_all_values()
        return [PostRecord.from_row(r, i + 2) for i, r in enumerate(rows[1:]) if any(r)]

    def by_file_id(self) -> dict[str, PostRecord]:
        return {r.drive_file_id: r for r in self.all_records() if r.drive_file_id}

    # ---- writes --------------------------------------------------------------------
    def upsert(self, rec: PostRecord) -> PostRecord:
        values = [str(v) if v is not None else "" for v in rec.to_row()]
        if rec.row_number:
            self.ws.update(range_name=f"A{rec.row_number}", values=[values], value_input_option="RAW")
        else:
            self.ws.append_row(values, value_input_option="RAW")
            rec.row_number = len(self.ws.get_all_values())
        log.info("Sheet: %s -> %s", rec.filename, rec.status)
        return rec
