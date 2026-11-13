import logging
import os
import shutil
import subprocess

import pytest

from app.config import SecretRedactingFormatter, load_settings
from app import video_processor as vp


def test_secret_redaction():
    fmt = SecretRedactingFormatter(["supersecret123"], "%(message)s")
    rec = logging.LogRecord("x", logging.INFO, "", 0, "token=supersecret123 ok", None, None)
    assert fmt.format(rec) == "token=*** ok"


def test_missing_required_env_raises(monkeypatch):
    for k in list(os.environ):
        if k.startswith(("GOOGLE_", "GROQ_", "CLOUDINARY_", "META_", "INSTAGRAM_")):
            monkeypatch.delenv(k, raising=False)
    with pytest.raises(Exception):
        load_settings()


def test_dry_run_defaults_true(monkeypatch):
    monkeypatch.delenv("DRY_RUN", raising=False)
    assert load_settings(require_all=False).dry_run is True
    monkeypatch.setenv("DRY_RUN", "false")
    assert load_settings(require_all=False).dry_run is False


def test_validate_for_reels():
    assert vp.validate_for_reels(vp.VideoInfo(2, 1080, 1920, True, 1)) == ["too short (2.0s, min 3s)"]
    assert vp.validate_for_reels(vp.VideoInfo(30, 1080, 1920, True, 1)) == []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_ffmpeg_pipeline_on_synthetic_video(tmp_path):
    src = str(tmp_path / "src.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=360x640:rate=24",
         "-f", "lavfi", "-i", "sine=frequency=440", "-t", "4", "-pix_fmt", "yuv420p", "-c:a", "aac", src],
        check=True,
    )
    info = vp.probe(src)
    assert 3.5 < info.duration < 4.5 and info.has_audio and info.width == 360
    audio = vp.extract_audio(src, str(tmp_path))
    assert os.path.getsize(audio) > 1000
    frames = vp.extract_frames(src, str(tmp_path), info.duration, count=3)
    assert len(frames) == 3 and all(os.path.getsize(f) > 500 for f in frames)
    assert vp.split_audio(audio, str(tmp_path)) == [audio]
