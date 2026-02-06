from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.agents.community_analytics.integrations import (
    load_membership_summary,
    load_question_sla_summary,
)


def test_load_question_sla_summary_reads_open_count_and_median_ttfr(tmp_path: Path) -> None:
    db_path = tmp_path / "state.sqlite"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE questions (
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
            """
        )
        # 1 open, 2 answered (TTFR = 60m and 30m -> median 45m)
        conn.execute(
            """
            INSERT INTO questions VALUES
            (1, 1, 101, 201, '2026-01-01T00:00:00+00:00', NULL, 'open', 0, NULL)
            """
        )
        conn.execute(
            """
            INSERT INTO questions VALUES
            (1, 2, 102, 202, '2026-01-01T00:00:00+00:00', '2026-01-01T01:00:00+00:00', 'answered', 0, NULL)
            """
        )
        conn.execute(
            """
            INSERT INTO questions VALUES
            (1, 3, 103, 203, '2026-01-02T00:00:00+00:00', '2026-01-02T00:30:00+00:00', 'answered', 0, NULL)
            """
        )

    summary = load_question_sla_summary(db_path)
    assert summary is not None
    assert summary["open_count"] == 1
    assert summary["median_ttfr_minutes"] == 45.0


def test_load_membership_summary_reads_counts(tmp_path: Path) -> None:
    status_path = tmp_path / "latest_status.json"
    status_path.write_text(
        json.dumps(
            {
                "members": {
                    "in_server_without_role": [{"x": 1}, {"x": 2}],
                    "not_in_server": [{"y": 1}],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = load_membership_summary(status_path)
    assert summary is not None
    assert summary["in_server_without_role"] == 2
    assert summary["not_in_server"] == 1

