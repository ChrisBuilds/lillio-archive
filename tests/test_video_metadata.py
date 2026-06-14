import shutil
import subprocess

import pytest

from lillio_archive.video_metadata import embed_video_metadata, probe_video


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg and ffprobe required",
)
def test_video_metadata_round_trip_without_reencoding(tmp_path) -> None:
    path = tmp_path / "sample.mov"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=16x16:d=0.1",
            "-c:v",
            "mpeg4",
            str(path),
        ],
        check=True,
    )
    assert embed_video_metadata(
        path,
        title="Sample Clip",
        description="A short clip",
        activity_date="2025-12-23",
    )
    tags = probe_video(path)
    assert tags["title"] == "Sample Clip"
    assert (tags.get("description") or tags.get("comment")) == "A short clip"
    assert tags["creation_time"].startswith("2025-12-23")
