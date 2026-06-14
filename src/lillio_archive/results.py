import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ItemResult:
    source_key: str
    status: str
    filename: Optional[str] = None
    bytes: int = 0
    message: Optional[str] = None


@dataclass
class RunResult:
    command: str
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    finished_at: Optional[str] = None
    filters: Dict[str, Any] = field(default_factory=dict)
    items: List[ItemResult] = field(default_factory=list)

    def add(self, **values: Any) -> None:
        self.items.append(ItemResult(**values))

    @property
    def counts(self) -> Dict[str, int]:
        result: Dict[str, int] = {}
        for item in self.items:
            result[item.status] = result.get(item.status, 0) + 1
        return result

    @property
    def failed(self) -> bool:
        return any(
            item.status in {"failed", "corrupt"} for item in self.items
        )

    def finish(self) -> None:
        self.finished_at = datetime.now(timezone.utc).isoformat()

    def write(self, directory: Path) -> tuple[Path, Path]:
        self.finish()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = self.started_at.replace(":", "").replace("+00:00", "Z")
        stem = f"{stamp}-{self.command}"
        json_path = directory / f"{stem}.json"
        csv_path = directory / f"{stem}.csv"
        payload = asdict(self)
        payload["counts"] = self.counts
        json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        with csv_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=[
                    "source_key",
                    "status",
                    "filename",
                    "bytes",
                    "message",
                ],
            )
            writer.writeheader()
            writer.writerows(asdict(item) for item in self.items)
        json_path.chmod(0o600)
        csv_path.chmod(0o600)
        return json_path, csv_path
