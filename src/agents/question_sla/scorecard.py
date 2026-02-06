from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import storage


def evaluate() -> dict[str, Any]:
    """
    Offline scorecard for the question_sla feature.
    This is intentionally deterministic and does not require Discord API access.
    """
    failing: list[str] = []
    notes: list[str] = []

    try:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "state.sqlite"
            storage.ensure_schema(db)
            now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat()
            storage.upsert_open_question(
                db,
                guild_id=1,
                thread_id=2,
                starter_message_id=2,
                starter_author_id=3,
                created_at_iso=now,
            )
            rows = storage.list_questions(db, status="open")
            if len(rows) != 1:
                failing.append("insert_or_list_failed")
            else:
                row = rows[0]
                if row.status != "open" or row.thread_id != 2 or row.starter_author_id != 3:
                    failing.append("stored_row_mismatch")
    except Exception as exc:
        failing.append("exception")
        notes.append(f"exception={exc!r}")

    score = 100.0 if not failing else 0.0
    return {
        "feature": "question_sla",
        "score": score,
        "failing_cases": failing,
        "notes": notes,
    }

