from lillio_archive.manifest import Manifest, MediaRecord


def test_manifest_tracks_sources_and_hashes(tmp_path) -> None:
    path = tmp_path / "manifest.sqlite3"
    record = MediaRecord(
        source_key="source-1",
        source_url="https://example.test/media.jpg",
        activity_date="2026-06-09",
        activity_date_source="journal_api",
        media_type="image/jpeg",
        title="Title",
        description="Description",
        filename="downloads/media.jpg",
        sha256="abc123",
        size_bytes=42,
    )

    with Manifest(path) as manifest:
        assert not manifest.contains_source(record.source_key)
        assert not manifest.contains_hash(record.sha256)
        manifest.add(record)
        assert manifest.contains_source(record.source_key)
        assert manifest.contains_hash(record.sha256)


def test_manifest_migrates_legacy_caption_schema(tmp_path) -> None:
    import sqlite3

    path = tmp_path / "manifest.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE media (
            source_key TEXT PRIMARY KEY,
            source_url TEXT NOT NULL,
            activity_date TEXT,
            media_type TEXT,
            caption TEXT,
            filename TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            downloaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.commit()
    connection.close()

    with Manifest(path):
        pass

    connection = sqlite3.connect(path)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(media)")}
    connection.close()
    assert {"title", "description", "activity_date_source"}.issubset(columns)


def test_manifest_migrates_photos_export_ledger(tmp_path) -> None:
    import sqlite3

    path = tmp_path / "manifest.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE photos_exports (
            source_key TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            filename TEXT NOT NULL,
            batch TEXT NOT NULL,
            exported_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO photos_exports VALUES (
            'item-1:image', 'abc123', 'exports/old.jpg',
            'legacy', '2025-01-01T00:00:00+00:00'
        )
        """
    )
    connection.commit()
    connection.close()

    with Manifest(path) as manifest:
        row = manifest.get_export("item-1:image")
        assert row is not None
        assert row["sha256"] == "abc123"
        assert (
            manifest.connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'photos_exports'
                """
            ).fetchone()
            is None
        )
