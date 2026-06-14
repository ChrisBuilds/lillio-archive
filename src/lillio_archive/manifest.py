import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .logging_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class MediaRecord:
    source_key: str
    source_url: str
    activity_date: str | None
    activity_date_source: str | None
    media_type: str | None
    title: str | None
    description: str | None
    filename: str
    sha256: str
    size_bytes: int
    list_date: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    metadata_fingerprint: str | None = None
    last_seen_at: str | None = None
    verification_state: str = "unverified"
    failure_details: str | None = None


class Manifest:
    def __init__(self, path: Path) -> None:
        logger.debug("Opening manifest database: %s", path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        path.chmod(0o600)
        self._migrate()

    def _migrate(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS media (
                source_key TEXT PRIMARY KEY,
                source_url TEXT NOT NULL,
                activity_date TEXT,
                activity_date_source TEXT,
                media_type TEXT,
                title TEXT,
                description TEXT,
                filename TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                downloaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        additions = {
            "activity_date_source": "TEXT",
            "title": "TEXT",
            "description": "TEXT",
            "list_date": "TEXT",
            "created_at": "TEXT",
            "updated_at": "TEXT",
            "metadata_fingerprint": "TEXT",
            "last_seen_at": "TEXT",
            "verification_state": "TEXT DEFAULT 'unverified'",
            "failure_details": "TEXT",
        }
        columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(media)")
        }
        for name, definition in additions.items():
            if name not in columns:
                self.connection.execute(
                    f"ALTER TABLE media ADD COLUMN {name} {definition}"
                )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                command TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                filters_json TEXT,
                totals_json TEXT,
                bytes INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS exports (
                source_key TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL,
                filename TEXT NOT NULL,
                batch TEXT NOT NULL,
                exported_at TEXT NOT NULL
            )
            """
        )
        legacy_table = self.connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'photos_exports'
            """
        ).fetchone()
        if legacy_table:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO exports (
                    source_key, sha256, filename, batch, exported_at
                )
                SELECT source_key, sha256, filename, batch, exported_at
                FROM photos_exports
                """
            )
            self.connection.execute("DROP TABLE photos_exports")
        self.connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS media_sha256 ON media(sha256)"
        )
        self.connection.commit()

    def get(self, source_key: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM media WHERE source_key = ?", (source_key,)
        ).fetchone()

    def contains_source(self, source_key: str) -> bool:
        return self.get(source_key) is not None

    def all(self) -> Iterable[sqlite3.Row]:
        return self.connection.execute("SELECT * FROM media ORDER BY activity_date")

    def contains_hash(self, sha256: str) -> bool:
        return (
            self.connection.execute(
                "SELECT 1 FROM media WHERE sha256 = ?", (sha256,)
            ).fetchone()
            is not None
        )

    def add(self, record: MediaRecord) -> None:
        values = record.__dict__
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        updates = ", ".join(
            f"{name}=excluded.{name}" for name in values if name != "source_key"
        )
        self.connection.execute(
            f"""
            INSERT INTO media ({columns}) VALUES ({placeholders})
            ON CONFLICT(source_key) DO UPDATE SET {updates}
            """,
            tuple(values.values()),
        )
        self.connection.commit()

    def update(self, source_key: str, **values: Any) -> None:
        if not values:
            return
        assignments = ", ".join(f"{name} = ?" for name in values)
        self.connection.execute(
            f"UPDATE media SET {assignments} WHERE source_key = ?",
            (*values.values(), source_key),
        )
        self.connection.commit()

    def latest_successful_run(self, command: str = "download") -> str | None:
        row = self.connection.execute(
            """
            SELECT finished_at FROM runs
            WHERE command = ? AND status = 'success'
            ORDER BY id DESC LIMIT 1
            """,
            (command,),
        ).fetchone()
        return row["finished_at"] if row else None

    def get_export(self, source_key: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM exports WHERE source_key = ?",
            (source_key,),
        ).fetchone()

    def record_export(
        self,
        *,
        source_key: str,
        sha256: str,
        filename: str,
        batch: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO exports (
                source_key, sha256, filename, batch, exported_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_key) DO UPDATE SET
                sha256=excluded.sha256,
                filename=excluded.filename,
                batch=excluded.batch,
                exported_at=excluded.exported_at
            """,
            (
                source_key,
                sha256,
                filename,
                batch,
                datetime.now(UTC).isoformat(),
            ),
        )
        self.connection.commit()

    def record_run(
        self,
        *,
        command: str,
        started_at: str,
        finished_at: str,
        filters_json: str,
        totals_json: str,
        bytes_count: int,
        status: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO runs (
                command, started_at, finished_at, filters_json,
                totals_json, bytes, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                command,
                started_at,
                finished_at,
                filters_json,
                totals_json,
                bytes_count,
                status,
            ),
        )
        self.connection.commit()

    def integrity_check(self) -> str:
        return self.connection.execute("PRAGMA integrity_check").fetchone()[0]

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Manifest":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
