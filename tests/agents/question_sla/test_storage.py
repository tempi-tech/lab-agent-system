from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.agents.question_sla import storage


def test_upsert_and_list_open_questions(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite"
    now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()
    storage.upsert_open_question(
        db,
        guild_id=10,
        thread_id=20,
        starter_message_id=20,
        starter_author_id=30,
        created_at_iso=now,
    )
    rows = storage.list_questions(db, status="open")
    assert len(rows) == 1
    assert rows[0].guild_id == 10
    assert rows[0].thread_id == 20
    assert rows[0].starter_author_id == 30


def test_mark_answered_is_idempotent_first_response_time(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite"
    created = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()
    storage.upsert_open_question(
        db,
        guild_id=10,
        thread_id=20,
        starter_message_id=20,
        starter_author_id=30,
        created_at_iso=created,
    )
    first = datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc).isoformat()
    later = datetime(2026, 1, 1, 2, 0, 0, tzinfo=timezone.utc).isoformat()

    storage.mark_answered(db, 20, first)
    storage.mark_answered(db, 20, later)

    row = storage.get_question(db, 20)
    assert row is not None
    assert row.status == "answered"
    assert row.first_response_at == first
