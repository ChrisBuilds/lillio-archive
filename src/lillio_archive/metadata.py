import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import piexif

from .logging_config import get_logger


logger = get_logger(__name__)


def sidecar_path(media_path: Path) -> Path:
    return media_path.with_name(f"{media_path.name}.json")


def write_sidecar(media_path: Path, metadata: Dict[str, Any]) -> Path:
    path = sidecar_path(media_path)
    path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=True, sort_keys=True) + "\n"
    )
    path.chmod(0o600)
    logger.debug("Wrote metadata sidecar: %s", path)
    return path


def embed_jpeg_metadata(
    path: Path,
    *,
    title: Optional[str],
    description: Optional[str],
    activity_date: Optional[str],
) -> bool:
    if path.suffix.lower() not in {".jpg", ".jpeg"}:
        return False

    try:
        exif_dict = piexif.load(str(path))
        zeroth = exif_dict.setdefault("0th", {})
        exif = exif_dict.setdefault("Exif", {})
        if title:
            zeroth[piexif.ImageIFD.XPTitle] = (title + "\0").encode("utf-16le")
        if description:
            zeroth[piexif.ImageIFD.ImageDescription] = description.encode(
                "utf-8"
            )
            zeroth[piexif.ImageIFD.XPComment] = (
                description + "\0"
            ).encode("utf-16le")
        if activity_date:
            timestamp = datetime.strptime(activity_date, "%Y-%m-%d").strftime(
                "%Y:%m:%d 00:00:00"
            )
            exif[piexif.ExifIFD.DateTimeOriginal] = timestamp.encode("ascii")
            exif[piexif.ExifIFD.DateTimeDigitized] = timestamp.encode("ascii")
        piexif.insert(piexif.dump(exif_dict), str(path))
        logger.debug("Embedded JPEG EXIF metadata: %s", path)
        return True
    except (OSError, ValueError, piexif.InvalidImageDataError) as error:
        logger.warning("Could not embed JPEG metadata in %s: %s", path, error)
        return False
