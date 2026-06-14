import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Optional

from .config import Config
from .logging_config import get_logger
from .metadata import embed_jpeg_metadata


logger = get_logger(__name__)


def _correct_future_date(value: str, today: date) -> str:
    parsed = date.fromisoformat(value)
    while parsed > today:
        parsed = parsed.replace(year=parsed.year - 1)
    return parsed.isoformat()


def repair_future_archive_dates(
    config: Config, *, today: Optional[date] = None
) -> int:
    reference = today or date.today()
    if not config.manifest_path.exists():
        logger.info("No manifest found; no archive dates require repair")
        return 0

    connection = sqlite3.connect(config.manifest_path)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT source_key, activity_date, filename, title, description
        FROM media
        WHERE activity_date > ?
        ORDER BY activity_date, source_key
        """,
        (reference.isoformat(),),
    ).fetchall()
    logger.info("Found %d future-dated archive item(s)", len(rows))

    repaired = 0
    try:
        for row in rows:
            old_date = row["activity_date"]
            new_date = _correct_future_date(old_date, reference)
            old_path = Path(row["filename"])
            filename = old_path.name
            if filename.startswith(f"{old_date}_"):
                filename = f"{new_date}_{filename[len(old_date) + 1:]}"
            new_path = config.download_dir / new_date / filename
            new_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            new_path.parent.chmod(0o700)
            if new_path.exists() and new_path != old_path:
                raise RuntimeError(f"Date repair destination already exists: {new_path}")

            old_sidecar = old_path.with_name(f"{old_path.name}.json")
            new_sidecar = new_path.with_name(f"{new_path.name}.json")
            old_path.replace(new_path)
            if old_sidecar.exists():
                old_sidecar.replace(new_sidecar)
                metadata = json.loads(new_sidecar.read_text())
                metadata["activity_date"] = new_date
                new_sidecar.write_text(
                    json.dumps(
                        metadata,
                        indent=2,
                        ensure_ascii=True,
                        sort_keys=True,
                    )
                    + "\n"
                )
                new_sidecar.chmod(0o600)

            embed_jpeg_metadata(
                new_path,
                title=row["title"],
                description=row["description"],
                activity_date=new_date,
            )
            body = new_path.read_bytes()
            digest = hashlib.sha256(body).hexdigest()
            connection.execute(
                """
                UPDATE media
                SET activity_date = ?, filename = ?, sha256 = ?, size_bytes = ?
                WHERE source_key = ?
                """,
                (
                    new_date,
                    str(new_path),
                    digest,
                    len(body),
                    row["source_key"],
                ),
            )
            connection.commit()
            repaired += 1
            logger.info(
                "Repaired date for %s: %s -> %s",
                row["source_key"],
                old_date,
                new_date,
            )
            try:
                old_path.parent.rmdir()
            except OSError:
                pass
    finally:
        connection.close()

    logger.info("Date repair complete: %d item(s) updated", repaired)
    return repaired
