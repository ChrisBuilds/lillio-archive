from lillio_archive.downloader import (
    ByteThroughputColumn,
    DownloadedBytesColumn,
    ItemCountColumn,
    download_progress,
    filename_for,
    title_filename,
)


def test_filename_uses_url_name_and_digest_prefix() -> None:
    assert (
        filename_for(
            "https://cdn.example.test/My%20Photo.jpg?token=private",
            "abcdef1234567890",
            "image/jpeg",
        )
        == "abcdef123456-My_20Photo.jpg"
    )


def test_filename_falls_back_to_content_type() -> None:
    assert (
        filename_for(
            "https://cdn.example.test/download",
            "abcdef1234567890",
            "video/mp4",
        )
        == "abcdef123456-download.mp4"
    )


def test_title_filename_includes_date_title_and_activity_id() -> None:
    assert (
        title_filename(
            title="Art Project",
            activity_date="2026-06-09",
            activity_id="100001",
            fallback_filename="abcdef-photo.JPG",
        )
        == "2026-06-09_Art-Project_100001.jpg"
    )


def test_title_filename_handles_missing_title_and_date() -> None:
    assert (
        title_filename(
            title=None,
            activity_date=None,
            activity_id="123",
            fallback_filename="video.mov",
        )
        == "unknown-date_untitled_123.mov"
    )


def test_progress_distinguishes_items_from_bytes() -> None:
    progress = download_progress(disable=True)
    task_id = progress.add_task(
        "Archiving",
        total=10,
        completed=2,
        downloaded_bytes=1_500_000,
    )
    task = progress.tasks[task_id]

    assert ItemCountColumn().render(task).plain == "2/10 items"
    assert DownloadedBytesColumn().render(task).plain == "1.5 MB downloaded"


def test_progress_byte_throughput_uses_downloaded_bytes() -> None:
    progress = download_progress(disable=True)
    task_id = progress.add_task(
        "Archiving",
        total=10,
        completed=2,
        downloaded_bytes=1_500_000,
    )
    task = progress.tasks[task_id]
    task.start_time = 1.0
    task._get_time = lambda: 2.0

    assert ByteThroughputColumn().render(task).plain == "1.5 MB/s"
