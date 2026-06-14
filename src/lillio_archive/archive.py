import csv
import json
import os
import shutil
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import piexif

from .browser import MediaCandidate
from .config import Config
from .downloader import sha256_file, title_filename
from .logging_config import get_logger
from .manifest import Manifest
from .metadata import embed_jpeg_metadata, sidecar_path, write_sidecar
from .results import RunResult
from .video_metadata import embed_video_metadata, probe_video, tools_available

logger = get_logger(__name__)


def _create_export_batch(root: Path) -> tuple[str, Path]:
    stem = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%fZ")
    for suffix in range(1000):
        name = stem if suffix == 0 else f"{stem}-{suffix}"
        path = root / name
        try:
            path.mkdir(mode=0o700)
            return name, path
        except FileExistsError:
            continue
    raise RuntimeError("Could not allocate a unique media export batch")


def _transfer_file(source: Path, target: Path, link_mode: str) -> str:
    if link_mode == "copy":
        shutil.copy2(source, target)
        return "copy"
    if link_mode == "hardlink":
        os.link(source, target)
        return "hardlink"
    try:
        os.link(source, target)
        return "hardlink"
    except OSError:
        shutil.copy2(source, target)
        return "copy"


def _metadata_limitations(path: Path) -> str:
    if path.suffix.lower() in {".jpg", ".jpeg", ".mov", ".mp4", ".m4v"}:
        return ""
    return "embedded metadata support varies; canonical metadata is in the sidecar"


def verify_archive(config: Config) -> RunResult:
    result = RunResult(command="verify")
    referenced = set()
    with Manifest(config.manifest_path) as manifest:
        integrity = manifest.integrity_check()
        if integrity != "ok":
            result.add(source_key="manifest", status="corrupt", message=integrity)
        for row in manifest.all():
            path = Path(row["filename"])
            referenced.add(path)
            referenced.add(sidecar_path(path))
            errors = []
            if not path.is_file():
                errors.append("media missing")
            else:
                if path.stat().st_size != row["size_bytes"]:
                    errors.append("size mismatch")
                if sha256_file(path) != row["sha256"]:
                    errors.append("hash mismatch")
            sidecar = sidecar_path(path)
            if not sidecar.is_file():
                errors.append("sidecar missing")
            else:
                try:
                    metadata = json.loads(sidecar.read_text())
                    if metadata.get("activity_id") != row["source_key"].split(":")[0]:
                        errors.append("sidecar activity mismatch")
                except (OSError, ValueError):
                    errors.append("sidecar invalid")
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg"}:
                try:
                    exif = piexif.load(str(path))
                    if row["activity_date"] and not exif["Exif"].get(
                        piexif.ExifIFD.DateTimeOriginal
                    ):
                        errors.append("JPEG date metadata missing")
                except (ValueError, piexif.InvalidImageDataError):
                    errors.append("JPEG metadata invalid")
            if path.is_file() and path.suffix.lower() in {".mov", ".mp4", ".m4v"}:
                if not tools_available():
                    errors.append("ffmpeg/ffprobe unavailable")
                else:
                    tags = probe_video(path)
                    if not tags:
                        errors.append("video metadata missing")
                    elif row["activity_date"] and not str(
                        tags.get("creation_time", "")
                    ).startswith(row["activity_date"]):
                        errors.append("video creation date mismatch")
            status = "corrupt" if errors else "verified"
            result.add(
                source_key=row["source_key"],
                status=status,
                filename=str(path),
                bytes=path.stat().st_size if path.is_file() else 0,
                message="; ".join(errors) or None,
            )
            manifest.update(
                row["source_key"],
                verification_state=status,
                failure_details="; ".join(errors) or None,
            )

    if config.download_dir.exists():
        for path in config.download_dir.rglob("*"):
            if not path.is_file() or path == config.manifest_path:
                continue
            if path.name == ".DS_Store":
                continue
            if path not in referenced:
                result.add(
                    source_key=f"orphan:{path}",
                    status="corrupt",
                    filename=str(path),
                    message="orphaned file",
                )
    result.finish()
    return result


def reconcile_archive(
    config: Config,
    candidates: Iterable[MediaCandidate],
    *,
    apply: bool,
) -> RunResult:
    result = RunResult(command="reconcile", filters={"apply": apply})
    upstream = {item.source_key: item for item in candidates}
    with Manifest(config.manifest_path) as manifest:
        existing = {row["source_key"]: row for row in manifest.all()}
        for source_key, row in existing.items():
            candidate = upstream.get(source_key)
            if candidate is None:
                result.add(
                    source_key=source_key,
                    status="missing_upstream",
                    filename=row["filename"],
                )
                continue
            if candidate.activity_date_source != "journal_api":
                result.add(
                    source_key=source_key,
                    status="failed",
                    filename=row["filename"],
                    message="authoritative journal API date unavailable",
                )
                continue
            changes = {}
            comparisons = {
                "source_url": candidate.source_url,
                "activity_date": candidate.activity_date,
                "activity_date_source": candidate.activity_date_source,
                "title": candidate.title,
                "description": candidate.description,
                "media_type": row["media_type"],
                "list_date": candidate.list_date,
                "created_at": candidate.created_at,
                "updated_at": candidate.updated_at,
                "metadata_fingerprint": candidate.metadata_fingerprint,
            }
            for name, value in comparisons.items():
                if name in row and row[name] != value:
                    changes[name] = value
            if not changes:
                if apply:
                    manifest.update(
                        source_key,
                        last_seen_at=datetime.now(UTC).isoformat(),
                        failure_details=None,
                    )
                result.add(
                    source_key=source_key,
                    status="unchanged",
                    filename=row["filename"],
                )
                continue
            if apply:
                old_path = Path(row["filename"])
                new_name = title_filename(
                    title=candidate.title,
                    activity_date=candidate.activity_date,
                    activity_id=candidate.activity_id,
                    fallback_filename=old_path.name,
                )
                new_path = (
                    config.download_dir
                    / (candidate.activity_date or "unknown-date")
                    / new_name
                )
                new_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if new_path != old_path:
                    if new_path.exists():
                        raise RuntimeError(f"Reconcile destination exists: {new_path}")
                    old_path.replace(new_path)
                    old_sidecar = sidecar_path(old_path)
                    if old_sidecar.exists():
                        old_sidecar.replace(sidecar_path(new_path))
                embedded = embed_jpeg_metadata(
                    new_path,
                    title=candidate.title,
                    description=candidate.description,
                    activity_date=candidate.activity_date,
                )
                if candidate.media_type == "video":
                    embedded = embed_video_metadata(
                        new_path,
                        title=candidate.title,
                        description=candidate.description,
                        activity_date=candidate.activity_date,
                    )
                write_sidecar(
                    new_path,
                    {
                        "activity_date": candidate.activity_date,
                        "activity_date_source": candidate.activity_date_source,
                        "activity_id": candidate.activity_id,
                        "created_at": candidate.created_at,
                        "description": candidate.description,
                        "embedded_in_media": embedded,
                        "list_date": candidate.list_date,
                        "media_type": candidate.media_type,
                        "source_url": candidate.source_url,
                        "title": candidate.title,
                        "updated_at": candidate.updated_at,
                    },
                )
                changes.update(
                    {
                        "filename": str(new_path),
                        "sha256": sha256_file(new_path),
                        "size_bytes": new_path.stat().st_size,
                        "last_seen_at": datetime.now(UTC).isoformat(),
                        "failure_details": None,
                    }
                )
                manifest.update(source_key, **changes)
                status = "reconciled"
            else:
                status = "drift"
            result.add(
                source_key=source_key,
                status=status,
                filename=changes.get("filename", row["filename"]),
                message=", ".join(sorted(changes)),
            )
        for source_key in upstream.keys() - existing.keys():
            result.add(source_key=source_key, status="new_upstream")
    result.finish()
    return result


def export_archive(
    config: Config,
    *,
    link_mode: str = "auto",
    include_sidecars: bool = False,
) -> RunResult:
    if link_mode not in {"auto", "hardlink", "copy"}:
        raise ValueError("link_mode must be auto, hardlink, or copy")
    verification = verify_archive(config)
    result = RunResult(
        command="export",
        filters={
            "link_mode": link_mode,
            "include_sidecars": include_sidecars,
        },
    )
    if verification.failed:
        result.add(
            source_key="archive",
            status="failed",
            message="archive verification failed; export not created",
        )
        result.finish()
        return result
    config.export_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    config.export_dir.chmod(0o700)
    batch_name, batch_dir = _create_export_batch(config.export_dir)
    result.filters["batch"] = batch_name
    report_rows = []
    with Manifest(config.manifest_path) as manifest:
        for row in manifest.all():
            source = Path(row["filename"])
            previous = manifest.get_export(row["source_key"])
            if previous and previous["sha256"] == row["sha256"]:
                previous_path = Path(previous["filename"])
                if (
                    previous_path.is_relative_to(config.export_dir)
                    and previous_path.is_file()
                    and previous_path.stat().st_size == source.stat().st_size
                    and sha256_file(previous_path) == row["sha256"]
                ):
                    result.add(
                        source_key=row["source_key"],
                        status="skipped",
                        filename=str(previous_path),
                        bytes=previous_path.stat().st_size,
                    )
                    report_rows.append(
                        {
                            "source_key": row["source_key"],
                            "status": "skipped",
                            "filename": os.path.relpath(
                                previous_path,
                                config.export_dir,
                            ),
                            "bytes": previous_path.stat().st_size,
                            "sha256": row["sha256"],
                            "mime_type": row["media_type"] or "",
                            "activity_date": row["list_date"]
                            or row["activity_date"]
                            or "",
                            "transfer_method": "",
                            "metadata_limitations": _metadata_limitations(source),
                        }
                    )
                    continue

            legacy = config.export_dir / source.name
            if (
                legacy.is_file()
                and legacy.stat().st_size == source.stat().st_size
                and sha256_file(legacy) == row["sha256"]
            ):
                manifest.record_export(
                    source_key=row["source_key"],
                    sha256=row["sha256"],
                    filename=str(legacy),
                    batch="legacy",
                )
                result.add(
                    source_key=row["source_key"],
                    status="skipped",
                    filename=str(legacy),
                    bytes=legacy.stat().st_size,
                    message="legacy export",
                )
                report_rows.append(
                    {
                        "source_key": row["source_key"],
                        "status": "skipped",
                        "filename": os.path.relpath(legacy, config.export_dir),
                        "bytes": legacy.stat().st_size,
                        "sha256": row["sha256"],
                        "mime_type": row["media_type"] or "",
                        "activity_date": row["list_date"] or row["activity_date"] or "",
                        "transfer_method": "legacy",
                        "metadata_limitations": _metadata_limitations(source),
                    }
                )
                continue

            target = batch_dir / source.name
            if target.exists():
                target = batch_dir / (
                    f"{source.stem}-{row['source_key'].split(':')[0]}{source.suffix}"
                )
            target_sidecar = sidecar_path(target)
            try:
                method = _transfer_file(source, target, link_mode)
                if include_sidecars:
                    _transfer_file(
                        sidecar_path(source),
                        target_sidecar,
                        link_mode,
                    )
            except OSError:
                target.unlink(missing_ok=True)
                target_sidecar.unlink(missing_ok=True)
                raise
            result.add(
                source_key=row["source_key"],
                status="exported",
                filename=str(target),
                bytes=target.stat().st_size,
                message=method,
            )
            report_rows.append(
                {
                    "source_key": row["source_key"],
                    "status": "exported",
                    "filename": os.path.relpath(target, config.export_dir),
                    "bytes": target.stat().st_size,
                    "sha256": row["sha256"],
                    "mime_type": row["media_type"] or "",
                    "activity_date": row["list_date"] or row["activity_date"] or "",
                    "transfer_method": method,
                    "metadata_limitations": _metadata_limitations(source),
                }
            )
            manifest.record_export(
                source_key=row["source_key"],
                sha256=row["sha256"],
                filename=str(target),
                batch=batch_name,
            )

    latest = config.export_dir / "latest"
    temporary_latest = config.export_dir / f".latest-{batch_name}"
    temporary_latest.symlink_to(batch_name, target_is_directory=True)
    if latest.exists() and not latest.is_symlink():
        temporary_latest.unlink()
        raise RuntimeError(f"Cannot replace non-symlink export path: {latest}")
    os.replace(temporary_latest, latest)
    logger.info(
        "Media export batch ready: %s -> %s",
        latest,
        batch_name,
    )

    report = config.report_dir / f"media-export-{batch_name}.csv"
    report.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with report.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "source_key",
                "status",
                "filename",
                "bytes",
                "sha256",
                "mime_type",
                "activity_date",
                "transfer_method",
                "metadata_limitations",
            ],
        )
        writer.writeheader()
        writer.writerows(report_rows)
    report.chmod(0o600)
    result.finish()
    return result


def archive_report(config: Config) -> RunResult:
    result = RunResult(command="report")
    with Manifest(config.manifest_path) as manifest:
        for row in manifest.all():
            result.add(
                source_key=row["source_key"],
                status=row["verification_state"] or "unverified",
                filename=row["filename"],
                bytes=row["size_bytes"],
                message=row["failure_details"],
            )
    result.finish()
    return result
