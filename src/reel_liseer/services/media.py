import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import imageio_ffmpeg

from reel_liseer import config

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

TELEGRAM_SAFE_VIDEO_CODECS = {"h264", "hevc", "av1"}

THUMBNAIL_MAX_SIDE = 320

# The host may report many cores while the container is throttled to a fraction of one
# (0.5 vCPU). Letting x264 spawn a thread per host core in that situation causes heavy
# context switching, so every ffmpeg call pins its thread count.
FFMPEG_THREADS = config.FFMPEG_THREADS

# Telegram does not need 1080p in a group chat, and halving the pixel count cuts the
# fallback transcode cost (CPU time and output size) roughly fourfold.
MAX_TRANSCODE_HEIGHT = 1280

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_VIDEO_STREAM_RE = re.compile(
    r"Stream #\d+:\d+.*?: Video: (?P<codec>[a-zA-Z0-9_]+).*?(?P<width>\d{2,5})x(?P<height>\d{2,5})",
)
_IMAGE_STREAM_RE = re.compile(
    r"Stream #\d+:\d+.*?: Video: (?P<codec>mjpeg|png|bmp|webp|gif).*?(?P<width>\d{2,5})x(?P<height>\d{2,5})"
)


@dataclass
class MediaInfo:
    duration: int | None = None
    width: int | None = None
    height: int | None = None
    vcodec: str | None = None
    acodec: str | None = None
    is_gif: bool = False

    def is_animation(self) -> bool:
        return self.is_gif or (self.vcodec or "").lower() == "gif"

    def needs_transcode(self) -> bool:
        if self.is_animation():
            return False
        return (self.vcodec or "").lower() not in TELEGRAM_SAFE_VIDEO_CODECS


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def _base_ffmpeg_args() -> list[str]:
    return [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error",
            "-threads", str(FFMPEG_THREADS)]


def probe(path, fallback: dict | None = None) -> MediaInfo:
    fallback = fallback or {}
    info = MediaInfo(
        duration=fallback.get("duration"),
        width=fallback.get("width"),
        height=fallback.get("height"),
    )

    try:
        result = _run([FFMPEG, "-hide_banner", "-nostdin", "-i", str(path)])
    except OSError:
        return info

    output = f"{result.stdout}\n{result.stderr}"

    match = _DURATION_RE.search(output)
    if match:
        hours, minutes, seconds = match.groups()
        info.duration = int(float(hours) * 3600 + int(minutes) * 60 + float(seconds))

    stream = _IMAGE_STREAM_RE.search(output) or _VIDEO_STREAM_RE.search(output)
    if stream:
        info.vcodec = stream.group("codec").lower()
        info.width = int(stream.group("width"))
        info.height = int(stream.group("height"))
        info.is_gif = info.vcodec == "gif"

    return info


def make_thumbnail(path) -> Path | None:
    path = Path(path)
    thumb = path.with_name(f"{path.stem}_{uuid4().hex}.jpg")

    result = _run([
        *_base_ffmpeg_args(), "-y", "-ss", "0", "-i", str(path),
        "-frames:v", "1",
        "-vf", f"scale={THUMBNAIL_MAX_SIDE}:-2:force_original_aspect_ratio=decrease",
        "-q:v", "6",
        str(thumb),
    ])

    if result.returncode != 0 or not thumb.exists() or thumb.stat().st_size == 0:
        thumb.unlink(missing_ok=True)
        return None

    return thumb


def to_telegram_safe(path) -> Path:
    path = Path(path)
    output = path.with_name(f"{path.stem}_{uuid4().hex}_safe.mp4")

    result = _run([
        *_base_ffmpeg_args(), "-y", "-i", str(path),
        "-vf", f"scale=-2:min(ih\\,{MAX_TRANSCODE_HEIGHT})",
        "-c:v", "libx264", "-preset", config.FFMPEG_PRESET, "-crf", str(config.FFMPEG_CRF),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "96k",
        str(output),
    ])

    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        output.unlink(missing_ok=True)
        raise RuntimeError(f"transcode failed: {result.stderr.strip()[:400]}")

    return output


def cleanup_downloads() -> int:
    """Remove leftovers from a previous crash/restart so the disk never fills up."""
    removed = 0
    download_path = Path(config.DOWNLOAD_PATH)
    if not download_path.is_dir():
        return removed

    for leftover in download_path.iterdir():
        try:
            if leftover.is_file():
                leftover.unlink()
                removed += 1
        except OSError:
            continue

    return removed
