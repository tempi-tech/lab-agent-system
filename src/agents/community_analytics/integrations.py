from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


def load_question_sla_summary(sqlite_path: Path | None = None) -> dict[str, Any] | None:
    """
    Best-effort read of Question SLA metrics from the SQLite state file.
    This intentionally does NOT import the question_sla agent code.
    """
    resolved_path = sqlite_path or Path(os.getenv("QUESTION_SLA_SQLITE_PATH", "data/question_sla/state.sqlite"))
    if not resolved_path.exists():
        return None

    try:
        with sqlite3.connect(str(resolved_path), timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            open_count = conn.execute(
                "SELECT COUNT(*) AS c FROM questions WHERE status = 'open'"
            ).fetchone()
            open_n = int(open_count["c"]) if open_count else 0

            rows = conn.execute(
                """
                SELECT created_at, first_response_at
                FROM questions
                WHERE first_response_at IS NOT NULL
                """,
            ).fetchall()

        ttfr_minutes: list[float] = []
        for row in rows:
            created_raw = row["created_at"]
            first_raw = row["first_response_at"]
            created = _parse_dt(created_raw)
            first = _parse_dt(first_raw)
            if not created or not first:
                continue
            delta = first - created
            ttfr_minutes.append(delta.total_seconds() / 60.0)

        median_ttfr = _median(ttfr_minutes) if ttfr_minutes else None

        return {
            "sqlite_path": str(resolved_path),
            "open_count": open_n,
            "median_ttfr_minutes": median_ttfr,
        }
    except Exception:
        return None


def load_membership_summary(status_path: Path | None = None) -> dict[str, Any] | None:
    """
    Best-effort read of membership status counts from latest_status.json.
    This intentionally does NOT import the membership_checker agent code.
    """
    resolved_path = status_path or Path("data/membership_checker/latest_status.json")
    if not resolved_path.exists():
        return None
    try:
        raw = json.loads(resolved_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    members = raw.get("members") if isinstance(raw, dict) else None
    if not isinstance(members, dict):
        return None

    def _len(key: str) -> int | None:
        v = members.get(key)
        return len(v) if isinstance(v, list) else None

    return {
        "path": str(resolved_path),
        "in_server_without_role": _len("in_server_without_role"),
        "not_in_server": _len("not_in_server"),
    }


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _median(values: list[float]) -> float:
    """
    Deterministic median for non-empty list.
    """
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return float(ordered[mid])
    return float((ordered[mid - 1] + ordered[mid]) / 2.0)
