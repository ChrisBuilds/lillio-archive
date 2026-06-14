import os
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, Dict, Optional

import tomllib


@dataclass(frozen=True)
class Config:
    base_url: str = "https://app.lillio.com/"
    profile_dir: Path = Path(".lillio-profile")
    download_dir: Path = Path("downloads")
    artifact_dir: Path = Path("artifacts")
    export_dir: Path = Path("exports/media")
    browser_mode: str = "auto"
    max_expand_actions: int = 500
    retry_count: int = 3
    retry_delay: float = 1.0

    @property
    def manifest_path(self) -> Path:
        return self.download_dir / "manifest.sqlite3"

    @property
    def log_path(self) -> Path:
        return self.artifact_dir / "lillio-archive.log"

    @property
    def report_dir(self) -> Path:
        return self.artifact_dir / "reports"

    @property
    def headless(self) -> bool:
        return self.browser_mode == "headless"


PATH_FIELDS = {"profile_dir", "download_dir", "artifact_dir", "export_dir"}
INT_FIELDS = {"max_expand_actions", "retry_count"}
FLOAT_FIELDS = {"retry_delay"}


def _coerce(name: str, value: Any, *, base_dir: Path) -> Any:
    if name in PATH_FIELDS:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = base_dir / path
        return path.resolve()
    if name in INT_FIELDS:
        return int(value)
    if name in FLOAT_FIELDS:
        return float(value)
    return value


def load_config(
    *,
    path: Path = Path("lillio-archive.toml"),
    overrides: Optional[Dict[str, Any]] = None,
    environ: Optional[Dict[str, str]] = None,
) -> Config:
    config_path = path.expanduser()
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path
    config_path = config_path.resolve()
    cwd = Path.cwd().resolve()
    known = {field.name for field in fields(Config)}
    defaults = Config()
    values: Dict[str, Any] = {
        name: _coerce(name, getattr(defaults, name), base_dir=cwd)
        for name in known
    }
    if config_path.exists():
        data = tomllib.loads(config_path.read_text())
        section = data.get("lillio-archive", data)
        for key, value in section.items():
            name = key.replace("-", "_")
            if name in known:
                values[name] = _coerce(
                    name,
                    value,
                    base_dir=config_path.parent,
                )

    env = os.environ if environ is None else environ
    for name in known:
        key = f"LILLIO_ARCHIVE_{name.upper()}"
        if key in env:
            values[name] = _coerce(name, env[key], base_dir=cwd)

    for name, value in (overrides or {}).items():
        if value is not None and name in known:
            values[name] = _coerce(name, value, base_dir=cwd)

    config = replace(Config(), **values)
    if config.browser_mode not in {"auto", "visible", "headless"}:
        raise ValueError("browser_mode must be auto, visible, or headless")
    if config.retry_count < 0 or config.retry_delay < 0:
        raise ValueError("retry settings cannot be negative")
    return config
