from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional


@dataclass(frozen=True)
class QuestionRow:
    guild_id: int
    thread_id: int
    starter_message_id: int
    starter_author_id: int
    created_at: str
    first_response_at: str | None
    status: str
    reminded_stage: int
    last_notified_at: str | None


SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
  guild_id INTEGER NOT NULL,
  thread_id INTEGER NOT NULL,
  starter_message_id INTEGER PRIMARY KEY,
  starter_author_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  first_response_at TEXT,
  status TEXT NOT NULL,
  reminded_stage INTEGER NOT NULL,
  last_notified_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_questions_status_created_at ON questions(status, created_at);
"""


def ensure_schema(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path), timeout=30) as conn:
        conn.executescript(SCHEMA)


def upsert_open_question(
    path: Path,
    *,
    guild_id: int,
    thread_id: int,
    starter_message_id: int,
    starter_author_id: int,
    created_at_iso: str,
) -> None:
    ensure_schema(path)
    with sqlite3.connect(str(path), timeout=30) as conn:
        conn.execute(
            """
            INSERT INTO questions (
              guild_id, thread_id, starter_message_id, starter_author_id,
              created_at, first_response_at, status, reminded_stage, last_notified_at
            )
            VALUES (?, ?, ?, ?, ?, NULL, 'open', 0, NULL)
            ON CONFLICT(starter_message_id) DO UPDATE SET
              guild_id=excluded.guild_id,
              thread_id=excluded.thread_id,
              starter_author_id=excluded.starter_author_id,
              created_at=excluded.created_at
            """,
            (guild_id, thread_id, starter_message_id, starter_author_id, created_at_iso),
        )


def list_questions(
    path: Path,
    *,
    status: str | None = None,
    limit: int = 200,
) -> list[QuestionRow]:
    ensure_schema(path)
    with sqlite3.connect(str(path), timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        if status:
            rows = conn.execute(
                """
                SELECT * FROM questions
                WHERE status = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (status, int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM questions
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
    out: list[QuestionRow] = []
    for row in rows:
        out.append(
            QuestionRow(
                guild_id=int(row["guild_id"]),
                thread_id=int(row["thread_id"]),
                starter_message_id=int(row["starter_message_id"]),
                starter_author_id=int(row["starter_author_id"]),
                created_at=str(row["created_at"]),
                first_response_at=row["first_response_at"],
                status=str(row["status"]),
                reminded_stage=int(row["reminded_stage"]),
                last_notified_at=row["last_notified_at"],
            )
        )
    return out


def get_question(path: Path, starter_message_id: int) -> QuestionRow | None:
    ensure_schema(path)
    with sqlite3.connect(str(path), timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM questions WHERE starter_message_id = ?",
            (int(starter_message_id),),
        ).fetchone()
    if not row:
        return None
    return QuestionRow(
        guild_id=int(row["guild_id"]),
        thread_id=int(row["thread_id"]),
        starter_message_id=int(row["starter_message_id"]),
        starter_author_id=int(row["starter_author_id"]),
        created_at=str(row["created_at"]),
        first_response_at=row["first_response_at"],
        status=str(row["status"]),
        reminded_stage=int(row["reminded_stage"]),
        last_notified_at=row["last_notified_at"],
    )


def mark_closed(path: Path, starter_message_id: int) -> None:
    ensure_schema(path)
    with sqlite3.connect(str(path), timeout=30) as conn:
        conn.execute(
            "UPDATE questions SET status='closed' WHERE starter_message_id = ?",
            (int(starter_message_id),),
        )


def mark_answered(path: Path, starter_message_id: int, first_response_at_iso: str) -> None:
    ensure_schema(path)
    with sqlite3.connect(str(path), timeout=30) as conn:
        conn.execute(
            """
            UPDATE questions
            SET status='answered',
                first_response_at=COALESCE(first_response_at, ?)
            WHERE starter_message_id = ?
            """,
            (first_response_at_iso, int(starter_message_id)),
        )


def update_notification_state(
    path: Path,
    starter_message_id: int,
    *,
    reminded_stage: int,
    last_notified_at_iso: str,
) -> None:
    ensure_schema(path)
    with sqlite3.connect(str(path), timeout=30) as conn:
        conn.execute(
            """
            UPDATE questions
            SET reminded_stage = ?,
                last_notified_at = ?
            WHERE starter_message_id = ?
            """,
            (int(reminded_stage), last_notified_at_iso, int(starter_message_id)),
        )

