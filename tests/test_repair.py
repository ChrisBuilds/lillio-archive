import json
import sqlite3
from datetime import date

from lillio_archive.config import Config
from lillio_archive.repair import repair_future_archive_dates


def test_repair_future_archive_dates_moves_files_and_updates_manifest(
    tmp_path,
) -> None:
    downloads = tmp_path / "downloads"
    old_dir = downloads / "2026-06-23"
    old_dir.mkdir(parents=True)
    media = old_dir / "2026-06-23_Title_123.mov"
    media.write_bytes(b"video")
    sidecar = media.with_name(f"{media.name}.json")
    sidecar.write_text(
        json.dumps(
            {
                "activity_date": "2026-06-23",
                "activity_id": "123",
                "title": "Title",
            }
        )
    )

    manifest = downloads / "manifest.sqlite3"
    connection = sqlite3.connect(manifest)
    connection.execute(
        """
        CREATE TABLE media (
            source_key TEXT PRIMARY KEY,
            source_url TEXT NOT NULL,
            activity_date TEXT,
            activity_date_source TEXT,
            media_type TEXT,
            title TEXT,
            description TEXT,
            filename TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO media VALUES (
            '123:video', 'https://example.test/123.video', '2026-06-23',
            'dom_inference', 'video/quicktime', 'Title', 'Description',
            ?, 'old-hash', 5
        )
        """,
        (str(media),),
    )
    connection.commit()
    connection.close()

    config = Config(
        download_dir=downloads,
        profile_dir=tmp_path / "profile",
        artifact_dir=tmp_path / "artifacts",
    )
    assert repair_future_archive_dates(config, today=date(2026, 6, 10)) == 1

    new_media = downloads / "2025-06-23" / "2025-06-23_Title_123.mov"
    assert new_media.read_bytes() == b"video"
    assert (
        json.loads(new_media.with_name(f"{new_media.name}.json").read_text())[
            "activity_date"
        ]
        == "2025-06-23"
    )
    connection = sqlite3.connect(manifest)
    row = connection.execute(
        "SELECT activity_date, filename, size_bytes FROM media"
    ).fetchone()
    connection.close()
    assert row == ("2025-06-23", str(new_media), 5)
