from pathlib import Path

from lillio_archive.config import Config
from scripts.check_public_tree import scan, tracked_files


def test_public_tree_has_no_private_artifacts_or_secrets() -> None:
    assert scan(tracked_files()) == []


def test_public_defaults_are_generic() -> None:
    config = Config()
    assert config.export_dir == Path("exports/media")
    assert not config.export_dir.is_absolute()
