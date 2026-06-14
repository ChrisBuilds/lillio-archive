from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from lillio_archive.browser import MediaCandidate
from lillio_archive.config import Config
from lillio_archive.downloader import (
    candidate_in_range,
    existing_file_valid,
    request_with_retry,
    valid_archive_source_keys,
)
from lillio_archive.manifest import Manifest, MediaRecord


def candidate(**changes):
    values = {
        "activity_id": "123",
        "media_type": "image",
        "source_url": "https://example.test/123.image",
        "activity_date": "2025-12-23",
        "activity_date_source": "journal_api",
        "title": "Title",
        "description": "Description",
        "created_at": "2025-12-23T12:00:00+00:00",
    }
    values.update(changes)
    return MediaCandidate(**values)


def test_candidate_filters_are_inclusive() -> None:
    item = candidate()
    assert candidate_in_range(
        item,
        since=date(2025, 12, 23),
        until=date(2025, 12, 23),
        created_after=None,
    )
    assert not candidate_in_range(
        item,
        since=date(2025, 12, 24),
        until=None,
        created_after=None,
    )
    assert candidate_in_range(
        item,
        since=None,
        until=None,
        created_after=datetime(2025, 12, 22, tzinfo=timezone.utc),
    )


def test_existing_file_validation_detects_corruption(tmp_path) -> None:
    path = tmp_path / "media.jpg"
    path.write_bytes(b"good")
    import hashlib

    row = {
        "filename": str(path),
        "size_bytes": 4,
        "sha256": hashlib.sha256(b"good").hexdigest(),
    }
    assert existing_file_valid(row)
    path.write_bytes(b"bad")
    assert not existing_file_valid(row)


def test_incremental_pagination_only_uses_valid_archive_files(tmp_path) -> None:
    config = Config(
        download_dir=tmp_path / "downloads",
        artifact_dir=tmp_path / "artifacts",
        profile_dir=tmp_path / "profile",
    )
    valid = config.download_dir / "valid.jpg"
    corrupt = config.download_dir / "corrupt.jpg"
    valid.parent.mkdir(parents=True)
    valid.write_bytes(b"valid")
    corrupt.write_bytes(b"changed")
    import hashlib

    with Manifest(config.manifest_path) as manifest:
        for key, path, expected in (
            ("1:image", valid, b"valid"),
            ("2:image", corrupt, b"original"),
        ):
            manifest.add(
                MediaRecord(
                    source_key=key,
                    source_url=f"https://example.test/{key}",
                    activity_date="2025-01-01",
                    activity_date_source="journal_api",
                    media_type="image/jpeg",
                    title=None,
                    description=None,
                    filename=str(path),
                    sha256=hashlib.sha256(expected).hexdigest(),
                    size_bytes=len(expected),
                )
            )

    assert valid_archive_source_keys(config) == {"1:image"}


class FakeResponse:
    def __init__(self, status):
        self.status = status
        self.ok = status == 200
        self.url = "https://example.test/media"
        self.headers = {"content-type": "image/jpeg"}


class FakeRequest:
    def __init__(self, statuses):
        self.statuses = iter(statuses)

    def get(self, *_args, **_kwargs):
        return FakeResponse(next(self.statuses))


class FakeBrowser:
    def __init__(self, statuses):
        self.context = type("Context", (), {"request": FakeRequest(statuses)})()


def test_retry_recovers_from_transient_statuses() -> None:
    sleeps = []
    response = request_with_retry(
        FakeBrowser([500, 429, 200]),
        candidate(),
        Config(retry_count=3, retry_delay=0),
        sleep=sleeps.append,
    )
    assert response.status == 200
    assert len(sleeps) == 2


def test_retry_stops_on_permanent_status() -> None:
    with pytest.raises(RuntimeError, match="HTTP 404"):
        request_with_retry(
            FakeBrowser([404]),
            candidate(),
            Config(retry_count=3, retry_delay=0),
            sleep=lambda _delay: None,
        )
