import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from .logging_config import get_logger

logger = get_logger(__name__)


def tools_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def embed_video_metadata(
    path: Path,
    *,
    title: str | None,
    description: str | None,
    activity_date: str | None,
) -> bool:
    if path.suffix.lower() not in {".mov", ".mp4", ".m4v"}:
        return False
    if not tools_available():
        logger.warning("ffmpeg/ffprobe unavailable; video metadata not embedded")
        return False

    output = path.with_name(f".{path.stem}.metadata{path.suffix}")
    command = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-map",
        "0",
        "-c",
        "copy",
    ]
    if title:
        command.extend(["-metadata", f"title={title}"])
    if description:
        command.extend(["-metadata", f"description={description}"])
        command.extend(["-metadata", f"comment={description}"])
    if activity_date:
        timestamp = datetime.strptime(activity_date, "%Y-%m-%d").strftime(
            "%Y-%m-%dT00:00:00Z"
        )
        command.extend(["-metadata", f"creation_time={timestamp}"])
    command.append(str(output))
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        output.chmod(0o600)
        output.replace(path)
        return True
    except (OSError, subprocess.CalledProcessError) as error:
        output.unlink(missing_ok=True)
        logger.warning("Could not embed video metadata in %s: %s", path, error)
        return False


def probe_video(path: Path) -> dict | None:
    if not tools_available():
        return None
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format_tags=title,description,comment,creation_time",
        "-of",
        "json",
        str(path),
    ]
    try:
        import json

        result = subprocess.run(command, check=True, capture_output=True, text=True)
        return json.loads(result.stdout).get("format", {}).get("tags", {})
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None
