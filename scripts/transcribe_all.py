"""One-off helper: transcribe every video in the Drive folder and print the transcripts.

Used once to write the pre-made captions in captions/. Not part of the daily job.
Run in GitHub Actions via the "Transcribe all videos" workflow, or locally with a .env.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import video_processor as vp  # noqa: E402
from app.audio_transcription import transcribe  # noqa: E402
from app.config import load_settings, setup_logging  # noqa: E402
from app.drive_service import DriveService  # noqa: E402


def main() -> int:
    settings = load_settings(require_all=False)
    setup_logging(settings)
    vp.ensure_ffmpeg()
    drive = DriveService(settings)
    only = {s.strip() for s in os.environ.get("ONLY", "").split(",") if s.strip()}
    for cand in drive.list_candidates():
        stem = cand.video.stem
        if only and not any(stem.startswith(o) or stem == o for o in only):
            continue
        with tempfile.TemporaryDirectory() as tmp:
            path = drive.download(cand.video, tmp)
            info = vp.probe(path)
            print(f"\n===== {cand.video.name} ({info.duration:.0f}s) =====")
            if not info.has_audio:
                print("(no audio)")
                continue
            try:
                audio = vp.extract_audio(path, tmp)
                t = transcribe(audio, tmp, settings)
                print(t.english if t.language != "en" and t.english else t.text)
            except Exception as exc:  # noqa: BLE001
                print(f"(transcription failed: {exc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
