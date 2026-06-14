import hashlib
import json
import mimetypes
import os
import random
import re
import sqlite3
import sys
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from rich import filesize
from rich.progress import (
    BarColumn,
    Progress,
    ProgressColumn,
    Task,
    TaskProgressColumn,
    TimeRemainingColumn,
)
from rich.text import Text

from .browser import LillioBrowser, MediaCandidate
from .config import Config
from .logging_config import get_logger
from .manifest import Manifest, MediaRecord
from .metadata import embed_jpeg_metadata, write_sidecar
from .results import RunResult
from .video_metadata import embed_video_metadata

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
TITLE_SEPARATOR = re.compile(r"[-_.\s]+")
TRANSIENT_STATUSES = {408, 429}
logger = get_logger(__name__)


class ResponseLike(Protocol):
    @property
    def status(self) -> int: ...

    @property
    def ok(self) -> bool: ...

    @property
    def url(self) -> str: ...

    @property
    def headers(self) -> Mapping[str, str]: ...

    def body(self) -> bytes: ...


ManifestRow = sqlite3.Row | Mapping[str, str | int]
MediaGetter = Callable[[str, float], ResponseLike]


class ItemCountColumn(ProgressColumn):
    def render(self, task: Task) -> Text:
        total = int(task.total or 0)
        return Text(f"{int(task.completed)}/{total} items")


class DownloadedBytesColumn(ProgressColumn):
    def render(self, task: Task) -> Text:
        downloaded = int(task.fields.get("downloaded_bytes", 0))
        return Text(f"{filesize.decimal(downloaded)} downloaded")


class ByteThroughputColumn(ProgressColumn):
    def render(self, task: Task) -> Text:
        downloaded = int(task.fields.get("downloaded_bytes", 0))
        elapsed = task.elapsed or 0
        if downloaded <= 0 or elapsed <= 0:
            return Text("-- B/s")
        return Text(f"{filesize.decimal(int(downloaded / elapsed))}/s")


def download_progress(*, disable: bool) -> Progress:
    return Progress(
        TaskProgressColumn(),
        BarColumn(),
        ItemCountColumn(),
        DownloadedBytesColumn(),
        ByteThroughputColumn(),
        TimeRemainingColumn(),
        disable=disable,
    )


def filename_for(url: str, digest: str, content_type: str | None) -> str:
    original = SAFE_NAME.sub("_", Path(urlparse(url).path).name).strip("._")
    extension = mimetypes.guess_extension((content_type or "").split(";")[0]) or ""
    if not original:
        original = f"media{extension}"
    elif not Path(original).suffix and extension:
        original += extension
    return f"{digest[:12]}-{original}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def existing_file_valid(row: ManifestRow) -> bool:
    path = Path(str(row["filename"]))
    return (
        path.is_file()
        and path.stat().st_size == int(row["size_bytes"])
        and sha256_file(path) == str(row["sha256"])
    )


def valid_archive_source_keys(config: Config) -> set[str]:
    if not config.manifest_path.exists():
        return set()
    with Manifest(config.manifest_path) as manifest:
        keys = {row["source_key"] for row in manifest.all() if existing_file_valid(row)}
    logger.info(
        "Validated %d archived media item(s) for incremental pagination",
        len(keys),
    )
    return keys


def response_filename(
    response: ResponseLike,
    source_url: str,
    digest: str,
) -> str:
    disposition = response.headers.get("content-disposition", "")
    match = re.search(
        r"""filename\*?=(?:UTF-8''|["']?)([^"';]+)""",
        disposition,
        re.IGNORECASE,
    )
    if match:
        name = SAFE_NAME.sub("_", match.group(1)).strip("._")
        if name:
            return f"{digest[:12]}-{name}"
    return filename_for(source_url, digest, response.headers.get("content-type"))


def title_filename(
    *,
    title: str | None,
    activity_date: str | None,
    activity_id: str,
    fallback_filename: str,
) -> str:
    suffix = Path(fallback_filename).suffix.lower()
    normalized = SAFE_NAME.sub("-", title or "").strip("-._")
    normalized = TITLE_SEPARATOR.sub("-", normalized)[:100].rstrip("-")
    return (
        "_".join(
            [
                activity_date or "unknown-date",
                normalized or "untitled",
                activity_id,
            ]
        )
        + suffix
    )


def candidate_in_range(
    candidate: MediaCandidate,
    *,
    since: date | None,
    until: date | None,
    created_after: datetime | None,
) -> bool:
    activity = (
        date.fromisoformat(candidate.activity_date) if candidate.activity_date else None
    )
    if since and (activity is None or activity < since):
        return False
    if until and (activity is None or activity > until):
        return False
    if created_after:
        if not candidate.created_at:
            return False
        created = datetime.fromisoformat(candidate.created_at)
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if created <= created_after:
            return False
    return True


def request_with_retry(
    get_media: MediaGetter,
    candidate: MediaCandidate,
    config: Config,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> ResponseLike:
    last_error: Exception | None = None
    for attempt in range(config.retry_count + 1):
        try:
            response = get_media(candidate.source_url, 60_000)
            if response.url.endswith("/login") or "text/html" in response.headers.get(
                "content-type", ""
            ):
                raise RuntimeError("Lillio session expired during media download")
            if response.ok:
                return response
            if response.status not in TRANSIENT_STATUSES and response.status < 500:
                raise RuntimeError(f"Media request failed with HTTP {response.status}")
            last_error = RuntimeError(
                f"Transient media response HTTP {response.status}"
            )
        except PlaywrightError as error:
            last_error = error
        if attempt < config.retry_count:
            delay = config.retry_delay * (2**attempt) + random.uniform(0, 0.25)
            logger.warning(
                "Retrying activity %s after attempt %d/%d in %.2fs: %s",
                candidate.activity_id,
                attempt + 1,
                config.retry_count + 1,
                delay,
                last_error,
            )
            sleep(delay)
    raise RuntimeError(
        f"Media request failed after {config.retry_count + 1} attempts: {last_error}"
    )


def _write_media(
    response: ResponseLike,
    candidate: MediaCandidate,
    config: Config,
) -> tuple[Path, int, str]:
    body = response.body()
    if not body:
        raise RuntimeError("Downloaded media response was empty")
    content_type = response.headers.get("content-type", "")
    if not content_type.startswith(("image/", "video/")):
        raise RuntimeError(f"Unexpected media content type: {content_type!r}")
    raw_digest = hashlib.sha256(body).hexdigest()
    fallback = response_filename(response, candidate.source_url, raw_digest)
    filename = title_filename(
        title=candidate.title,
        activity_date=candidate.activity_date,
        activity_id=candidate.activity_id,
        fallback_filename=fallback,
    )
    destination = config.download_dir / (candidate.activity_date or "unknown-date")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.chmod(0o700)
    path = destination / filename
    temporary = destination / f".{Path(filename).stem}.part{Path(filename).suffix}"
    temporary.write_bytes(body)
    temporary.chmod(0o600)
    embedded = embed_jpeg_metadata(
        temporary,
        title=candidate.title,
        description=candidate.description,
        activity_date=candidate.activity_date,
    )
    if candidate.media_type == "video":
        embedded = embed_video_metadata(
            temporary,
            title=candidate.title,
            description=candidate.description,
            activity_date=candidate.activity_date,
        )
    os.replace(temporary, path)
    sidecar = write_sidecar(
        path,
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
    final_size = path.stat().st_size
    final_hash = sha256_file(path)
    logger.debug("Wrote media=%s sidecar=%s", path, sidecar)
    return path, final_size, final_hash


def download_discovered(
    browser: LillioBrowser,
    config: Config,
    *,
    since: date | None = None,
    until: date | None = None,
    new_only: bool = False,
    dry_run: bool = False,
    full_scan: bool = False,
) -> RunResult:
    result = RunResult(
        command="download",
        filters={
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "new": new_only,
            "dry_run": dry_run,
            "full_scan": full_scan,
        },
    )
    with Manifest(config.manifest_path) as manifest:
        created_after = None
        if new_only:
            previous = manifest.latest_successful_run()
            if previous:
                created_after = datetime.fromisoformat(previous)
        candidates = [
            item
            for item in browser.discover_media()
            if candidate_in_range(
                item,
                since=since,
                until=until,
                created_after=created_after,
            )
        ]
        progress = download_progress(disable=not sys.stdout.isatty())
        with progress:
            task = progress.add_task(
                "Archiving",
                total=len(candidates),
                downloaded_bytes=0,
            )
            for candidate in candidates:
                row = manifest.get(candidate.source_key)
                if row and existing_file_valid(row):
                    result.add(
                        source_key=candidate.source_key,
                        status="skipped",
                        filename=row["filename"],
                        bytes=row["size_bytes"],
                    )
                    manifest.update(
                        candidate.source_key,
                        last_seen_at=datetime.now(UTC).isoformat(),
                        failure_details=None,
                    )
                    progress.advance(task)
                    continue
                if dry_run:
                    result.add(
                        source_key=candidate.source_key,
                        status="planned",
                        message="would download or repair",
                    )
                    progress.advance(task)
                    continue
                try:
                    response = request_with_retry(
                        lambda url, timeout: browser.context.request.get(
                            url,
                            timeout=timeout,
                        ),
                        candidate,
                        config,
                    )
                    path, size, digest = _write_media(response, candidate, config)
                    progress.update(
                        task,
                        downloaded_bytes=(
                            progress.tasks[task].fields["downloaded_bytes"] + size
                        ),
                    )
                    if manifest.contains_hash(digest) and not row:
                        result.add(
                            source_key=candidate.source_key,
                            status="duplicate",
                            filename=str(path),
                            bytes=size,
                        )
                        path.unlink(missing_ok=True)
                        path.with_name(f"{path.name}.json").unlink(missing_ok=True)
                    else:
                        manifest.add(
                            MediaRecord(
                                source_key=candidate.source_key,
                                source_url=candidate.source_url,
                                activity_date=candidate.activity_date,
                                activity_date_source=candidate.activity_date_source,
                                media_type=response.headers.get("content-type"),
                                title=candidate.title,
                                description=candidate.description,
                                filename=str(path),
                                sha256=digest,
                                size_bytes=size,
                                list_date=candidate.list_date,
                                created_at=candidate.created_at,
                                updated_at=candidate.updated_at,
                                metadata_fingerprint=candidate.metadata_fingerprint,
                                last_seen_at=datetime.now(UTC).isoformat(),
                                verification_state="verified",
                            )
                        )
                        result.add(
                            source_key=candidate.source_key,
                            status="downloaded" if row is None else "repaired",
                            filename=str(path),
                            bytes=size,
                        )
                except Exception as error:
                    logger.error("Activity %s failed: %s", candidate.activity_id, error)
                    if row:
                        manifest.update(
                            candidate.source_key,
                            failure_details=str(error),
                            verification_state="failed",
                        )
                    result.add(
                        source_key=candidate.source_key,
                        status="failed",
                        message=str(error),
                    )
                progress.advance(task)

        result.finish()
        if not dry_run:
            manifest.record_run(
                command="download",
                started_at=result.started_at,
                finished_at=result.finished_at or result.started_at,
                filters_json=json.dumps(result.filters),
                totals_json=json.dumps(result.counts),
                bytes_count=sum(item.bytes for item in result.items),
                status="failed" if result.failed else "success",
            )
    return result
