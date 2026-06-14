from pathlib import Path

from lillio_archive.cli import _record_result, parser
from lillio_archive.config import Config
from lillio_archive.manifest import Manifest
from lillio_archive.results import RunResult


def test_record_result_persists_run_summary(tmp_path) -> None:
    config = Config(
        download_dir=tmp_path / "downloads",
        artifact_dir=tmp_path / "artifacts",
        export_dir=tmp_path / "exports",
        profile_dir=tmp_path / "profile",
    )
    result = RunResult(command="verify", filters={"strict": True})
    result.add(source_key="one", status="verified", bytes=12)

    _record_result(config, result)

    with Manifest(config.manifest_path) as manifest:
        row = manifest.connection.execute("SELECT * FROM runs").fetchone()
        assert row["command"] == "verify"
        assert row["status"] == "success"
        assert row["bytes"] == 12
        assert row["filters_json"] == '{"strict": true}'


def test_cli_accepts_public_path_and_export_overrides() -> None:
    args = parser().parse_args(
        [
            "export",
            "--base-url",
            "https://example.test/",
            "--profile-dir",
            "profile",
            "--download-dir",
            "archive",
            "--artifact-dir",
            "state",
            "--export-dir",
            "out",
            "--link-mode",
            "copy",
            "--include-sidecars",
        ]
    )

    assert args.base_url == "https://example.test/"
    assert args.profile_dir == Path("profile")
    assert args.download_dir == Path("archive")
    assert args.artifact_dir == Path("state")
    assert args.export_dir == Path("out")
    assert args.link_mode == "copy"
    assert args.include_sidecars
