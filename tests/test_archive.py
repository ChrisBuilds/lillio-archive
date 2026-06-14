import hashlib
import csv
import json
import os

import pytest

from lillio_archive.archive import archive_report, export_archive, verify_archive
from lillio_archive.config import Config
from lillio_archive.manifest import Manifest, MediaRecord
from lillio_archive.metadata import write_sidecar


def build_archive(tmp_path):
    config = Config(
        download_dir=tmp_path / "downloads",
        artifact_dir=tmp_path / "artifacts",
        export_dir=tmp_path / "exports" / "media",
        profile_dir=tmp_path / "profile",
    )
    media = config.download_dir / "2025-01-01" / "photo.jpg"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"not-a-real-jpeg")
    write_sidecar(
        media,
        {"activity_id": "123", "activity_date": "2025-01-01"},
    )
    with Manifest(config.manifest_path) as manifest:
        manifest.add(
            MediaRecord(
                source_key="123:image",
                source_url="https://example.test/123.image",
                activity_date="2025-01-01",
                activity_date_source="journal_api",
                media_type="application/octet-stream",
                title="Title",
                description="Description",
                filename=str(media),
                sha256=hashlib.sha256(media.read_bytes()).hexdigest(),
                size_bytes=media.stat().st_size,
                verification_state="verified",
            )
        )
    return config, media


def test_report_reads_manifest(tmp_path) -> None:
    config, _media = build_archive(tmp_path)
    result = archive_report(config)
    assert result.counts == {"verified": 1}


def test_export_is_incremental_and_media_only(tmp_path) -> None:
    config, media = build_archive(tmp_path)
    # Skip strict JPEG metadata checks for this fixture.
    media.rename(media.with_suffix(".bin"))
    new_media = media.with_suffix(".bin")
    side = media.with_name(f"{media.name}.json")
    side.rename(new_media.with_name(f"{new_media.name}.json"))
    with Manifest(config.manifest_path) as manifest:
        manifest.update(
            "123:image",
            filename=str(new_media),
            sha256=hashlib.sha256(new_media.read_bytes()).hexdigest(),
        )
    first = export_archive(config)
    second = export_archive(config)
    assert first.counts == {"exported": 1}
    assert second.counts == {"skipped": 1}
    first_batch = config.export_dir / first.filters["batch"]
    second_batch = config.export_dir / second.filters["batch"]
    exported = first_batch / new_media.name
    assert exported.exists()
    assert first_batch != second_batch
    assert not list(second_batch.iterdir())
    assert (config.export_dir / "latest").is_symlink()
    assert (config.export_dir / "latest").resolve() == second_batch.resolve()
    assert not list(first_batch.glob("*.json"))
    assert not list(first_batch.glob("*.csv"))
    assert (
        config.report_dir
        / f"media-export-{second.filters['batch']}.csv"
    ).exists()


def test_export_recognizes_legacy_flat_files(tmp_path) -> None:
    config, media = build_archive(tmp_path)
    media.rename(media.with_suffix(".bin"))
    new_media = media.with_suffix(".bin")
    side = media.with_name(f"{media.name}.json")
    side.rename(new_media.with_name(f"{new_media.name}.json"))
    with Manifest(config.manifest_path) as manifest:
        manifest.update(
            "123:image",
            filename=str(new_media),
            sha256=hashlib.sha256(new_media.read_bytes()).hexdigest(),
        )
    config.export_dir.mkdir(parents=True)
    legacy = config.export_dir / new_media.name
    os.link(new_media, legacy)

    result = export_archive(config)

    assert result.counts == {"skipped": 1}
    assert not list((config.export_dir / result.filters["batch"]).iterdir())
    assert (config.export_dir / "latest").resolve() == (
        config.export_dir / result.filters["batch"]
    ).resolve()


def test_export_copy_mode_includes_sidecars_and_generic_report(tmp_path) -> None:
    config, media = build_archive(tmp_path)
    media.rename(media.with_suffix(".bin"))
    source = media.with_suffix(".bin")
    original_sidecar = media.with_name(f"{media.name}.json")
    original_sidecar.rename(source.with_name(f"{source.name}.json"))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with Manifest(config.manifest_path) as manifest:
        manifest.update("123:image", filename=str(source), sha256=digest)

    result = export_archive(
        config,
        link_mode="copy",
        include_sidecars=True,
    )

    batch = config.export_dir / result.filters["batch"]
    exported = batch / source.name
    assert exported.exists()
    assert exported.stat().st_ino != source.stat().st_ino
    assert exported.with_name(f"{exported.name}.json").exists()
    report = (
        config.report_dir
        / f"media-export-{result.filters['batch']}.csv"
    )
    row = next(csv.DictReader(report.open()))
    assert row["filename"] == f"{result.filters['batch']}/{source.name}"
    assert row["sha256"] == digest
    assert row["transfer_method"] == "copy"
    assert row["activity_date"] == "2025-01-01"
    assert "metadata" in row["metadata_limitations"]


def test_export_hardlink_mode_does_not_fall_back(tmp_path, monkeypatch) -> None:
    config, media = build_archive(tmp_path)
    media.rename(media.with_suffix(".bin"))
    source = media.with_suffix(".bin")
    side = media.with_name(f"{media.name}.json")
    side.rename(source.with_name(f"{source.name}.json"))
    with Manifest(config.manifest_path) as manifest:
        manifest.update(
            "123:image",
            filename=str(source),
            sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        )
    monkeypatch.setattr(os, "link", lambda *_args: (_ for _ in ()).throw(OSError()))

    with pytest.raises(OSError):
        export_archive(config, link_mode="hardlink")


def test_export_auto_mode_falls_back_to_copy(tmp_path, monkeypatch) -> None:
    config, media = build_archive(tmp_path)
    media.rename(media.with_suffix(".bin"))
    source = media.with_suffix(".bin")
    side = media.with_name(f"{media.name}.json")
    side.rename(source.with_name(f"{source.name}.json"))
    with Manifest(config.manifest_path) as manifest:
        manifest.update(
            "123:image",
            filename=str(source),
            sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        )
    monkeypatch.setattr(os, "link", lambda *_args: (_ for _ in ()).throw(OSError()))

    result = export_archive(config, link_mode="auto")

    assert result.items[0].message == "copy"


def test_changed_media_is_exported_in_a_new_batch(tmp_path) -> None:
    config, media = build_archive(tmp_path)
    media.rename(media.with_suffix(".bin"))
    source = media.with_suffix(".bin")
    side = media.with_name(f"{media.name}.json")
    side.rename(source.with_name(f"{source.name}.json"))
    with Manifest(config.manifest_path) as manifest:
        manifest.update(
            "123:image",
            filename=str(source),
            sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        )
    first = export_archive(config)
    source.write_bytes(b"changed media")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with Manifest(config.manifest_path) as manifest:
        manifest.update(
            "123:image",
            sha256=digest,
            size_bytes=source.stat().st_size,
        )

    second = export_archive(config)

    assert first.counts == {"exported": 1}
    assert second.counts == {"exported": 1}
    assert first.filters["batch"] != second.filters["batch"]


def test_new_export_root_creates_a_fresh_export(tmp_path) -> None:
    config, media = build_archive(tmp_path)
    media.rename(media.with_suffix(".bin"))
    source = media.with_suffix(".bin")
    side = media.with_name(f"{media.name}.json")
    side.rename(source.with_name(f"{source.name}.json"))
    with Manifest(config.manifest_path) as manifest:
        manifest.update(
            "123:image",
            filename=str(source),
            sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        )
    first = export_archive(config)
    other_config = Config(
        download_dir=config.download_dir,
        artifact_dir=config.artifact_dir,
        export_dir=tmp_path / "other-exports",
        profile_dir=config.profile_dir,
    )

    second = export_archive(other_config)

    assert first.counts == {"exported": 1}
    assert second.counts == {"exported": 1}
