from pathlib import Path

from lillio_archive.config import Config, load_config


def test_config_precedence_cli_over_env_over_toml(tmp_path) -> None:
    path = tmp_path / "lillio-archive.toml"
    path.write_text(
        """
[lillio-archive]
retry-count = 1
retry-delay = 2.5
download-dir = "from-file"
"""
    )
    config = load_config(
        path=path,
        environ={
            "LILLIO_ARCHIVE_RETRY_COUNT": "2",
            "LILLIO_ARCHIVE_DOWNLOAD_DIR": "from-env",
        },
        overrides={"retry_count": 4},
    )
    assert config.retry_count == 4
    assert config.retry_delay == 2.5
    assert config.download_dir == (Path.cwd() / "from-env").resolve()


def test_toml_paths_are_relative_to_config_file(tmp_path) -> None:
    config_dir = tmp_path / "configuration"
    config_dir.mkdir()
    path = config_dir / "lillio-archive.toml"
    path.write_text(
        """
[lillio-archive]
download-dir = "private/downloads"
export-dir = "~/lillio-exports"
"""
    )

    config = load_config(path=path, environ={})

    assert config.download_dir == (config_dir / "private" / "downloads").resolve()
    assert config.export_dir == Path("~/lillio-exports").expanduser().resolve()


def test_default_paths_are_generic_and_absolute() -> None:
    config = load_config(path=Path("does-not-exist.toml"), environ={})
    assert config.profile_dir == (Path.cwd() / ".lillio-profile").resolve()
    assert config.download_dir == (Path.cwd() / "downloads").resolve()
    assert config.artifact_dir == (Path.cwd() / "artifacts").resolve()
    assert config.export_dir == (Path.cwd() / "exports" / "media").resolve()
    assert not hasattr(Config(), "action_delay_ms")
