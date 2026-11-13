"""FFmpeg helpers: probe, extract audio, extract frames, re-encode oversized videos.

The source file is never modified; every output goes to a new path in the work dir.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Groq Whisper free-tier upload limit is 25 MB per request
WHISPER_MAX_BYTES = 24 * 1024 * 1024


class FFmpegError(RuntimeError):
    pass


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    log.debug("ffmpeg: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(proc.stderr[-2000:])
    return proc


def ensure_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise FFmpegError(f"{tool} not found on PATH")


@dataclass
class VideoInfo:
    duration: float
    width: int
    height: int
    has_audio: bool
    size_bytes: int

    @property
    def size_mb(self) -> float:
        return self.size_bytes / 1_048_576


def probe(path: str) -> VideoInfo:
    proc = _run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path]
    )
    data = json.loads(proc.stdout)
    video = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    if video is None:
        raise FFmpegError("No video stream found")
    has_audio = any(s.get("codec_type") == "audio" for s in data["streams"])
    duration = float(data["format"].get("duration") or video.get("duration") or 0)
    return VideoInfo(
        duration=duration,
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        has_audio=has_audio,
        size_bytes=os.path.getsize(path),
    )


def validate_for_reels(info: VideoInfo) -> list[str]:
    """Return a list of problems (empty = fine). Limits from Meta's Reels spec."""
    problems = []
    if info.duration < 3:
        problems.append(f"too short ({info.duration:.1f}s, min 3s)")
    if info.duration > 15 * 60:
        problems.append(f"too long ({info.duration/60:.1f} min, max 15 min)")
    if info.width and info.height:
        ratio = info.width / info.height
        if not (0.01 < ratio < 10):
            problems.append("invalid aspect ratio")
    return problems


def extract_audio(video_path: str, out_dir: str) -> str:
    """Mono 16 kHz MP3 at a low bitrate: small enough for Whisper, good enough for speech."""
    out = os.path.join(out_dir, "audio.mp3")
    _run(
        ["ffmpeg", "-y", "-v", "error", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000",
         "-b:a", "48k", out]
    )
    return out


def split_audio(audio_path: str, out_dir: str, chunk_seconds: int = 600) -> list[str]:
    """Split audio into fixed-length chunks when it exceeds the upload limit."""
    if os.path.getsize(audio_path) <= WHISPER_MAX_BYTES:
        return [audio_path]
    pattern = os.path.join(out_dir, "audio_chunk_%03d.mp3")
    _run(
        ["ffmpeg", "-y", "-v", "error", "-i", audio_path, "-f", "segment", "-segment_time",
         str(chunk_seconds), "-c", "copy", pattern]
    )
    chunks = sorted(
        os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.startswith("audio_chunk_")
    )
    log.info("Audio split into %d chunks", len(chunks))
    return chunks


def extract_frames(video_path: str, out_dir: str, duration: float, count: int = 5) -> list[str]:
    """Grab `count` evenly spaced frames (skipping the very start/end), JPEG, max 768px wide."""
    frames = []
    count = max(1, count)
    for i in range(count):
        t = duration * (i + 1) / (count + 1) if duration > 0 else 0
        out = os.path.join(out_dir, f"frame_{i:02d}.jpg")
        _run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", video_path, "-frames:v", "1",
             "-vf", "scale='min(768,iw)':-2", "-q:v", "4", out]
        )
        if os.path.exists(out):
            frames.append(out)
    return frames


def shrink_video(video_path: str, out_dir: str, target_mb: int) -> str:
    """Re-encode to fit the hosting upload limit (H.264/AAC, Instagram friendly)."""
    info = probe(video_path)
    target_bits = target_mb * 8 * 1_000_000 * 0.92  # 8% headroom
    audio_kbps = 128
    video_kbps = max(800, int(target_bits / max(info.duration, 1) / 1000) - audio_kbps)
    out = os.path.join(out_dir, "video_shrunk.mp4")
    _run(
        ["ffmpeg", "-y", "-v", "error", "-i", video_path, "-c:v", "libx264", "-preset", "fast",
         "-b:v", f"{video_kbps}k", "-maxrate", f"{int(video_kbps*1.1)}k", "-bufsize", f"{video_kbps*2}k",
         "-vf", "scale='min(1080,iw)':-2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", f"{audio_kbps}k",
         "-movflags", "+faststart", out]
    )
    log.info("Re-encoded video to %.1f MB", os.path.getsize(out) / 1_048_576)
    return out


def cleanup(work_dir: str) -> None:
    shutil.rmtree(work_dir, ignore_errors=True)
