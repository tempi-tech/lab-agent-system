from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class DailyDigestRecord:
    message_id: int
    channel_id: int
    created_at: str
    content: str
    extracted_channels: List[int]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dump(value: Any) -> str:
    return json.dumps(value or [], ensure_ascii=False)


def _json_load(value: Optional[str]) -> List[int]:
    if not value:
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [int(item) for item in data if str(item).isdigit()]
    return []


class DailyDigestStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path.as_posix(), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS daily_digests (
                message_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                content TEXT NOT NULL,
                extracted_channels_json TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def upsert_digest(
        self,
        *,
        message_id: int,
        channel_id: int,
        created_at: str,
        content: str,
        extracted_channels: List[int],
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO daily_digests (
                message_id, channel_id, created_at, content, extracted_channels_json
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(message_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                created_at = excluded.created_at,
                content = excluded.content,
                extracted_channels_json = excluded.extracted_channels_json
            """,
            (
                message_id,
                channel_id,
                created_at,
                content,
                _json_dump(extracted_channels),
            ),
        )
        self._conn.commit()

    def get_recent_digests(self, days: int) -> List[DailyDigestRecord]:
        threshold = datetime.now(timezone.utc) - timedelta(days=days)
        rows = self._conn.execute(
            "SELECT * FROM daily_digests WHERE created_at >= ? ORDER BY created_at DESC",
            (threshold.isoformat(),),
        ).fetchall()
        results: List[DailyDigestRecord] = []
        for row in rows:
            results.append(
                DailyDigestRecord(
                    message_id=row["message_id"],
                    channel_id=row["channel_id"],
                    created_at=row["created_at"],
                    content=row["content"],
                    extracted_channels=_json_load(row["extracted_channels_json"]),
                )
            )
        return results
