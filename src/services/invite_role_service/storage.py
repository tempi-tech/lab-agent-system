from __future__ import annotations

import csv
import fcntl
import json
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class MemberJoinRecord:
    timestamp: str
    discord_id: int
    username: str
    invite_code: str
    phase: str
    role_assigned: str
    detection_method: str

    @classmethod
    def create(
        cls,
        *,
        discord_id: int,
        username: str,
        invite_code: str,
        phase: str,
        role_assigned: str,
        detection_method: str,
    ) -> "MemberJoinRecord":
        return cls(
            timestamp=datetime.now(timezone.utc).isoformat(),
            discord_id=discord_id,
            username=username,
            invite_code=invite_code,
            phase=phase,
            role_assigned=role_assigned,
            detection_method=detection_method,
        )


class CsvLogStore:
    FIELDNAMES = [f.name for f in fields(MemberJoinRecord)]

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with self.path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=self.FIELDNAMES)
                writer.writeheader()

    def append(self, record: MemberJoinRecord) -> None:
        with self.path.open("a", newline="", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                writer = csv.DictWriter(handle, fieldnames=self.FIELDNAMES)
                writer.writerow(asdict(record))
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class JsonStateFile:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def read(self, default: Any) -> Any:
        if not self.path.exists():
            return default
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def write(self, payload: Any) -> None:
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)
