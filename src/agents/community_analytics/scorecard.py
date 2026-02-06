from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .metrics import AnalyticsEvent, compute_metrics
from .reporting import build_weekly_payload, format_weekly_report


def evaluate() -> dict[str, Any]:
    failing: list[str] = []
    notes: list[str] = []
    try:
        base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        events = [
            AnalyticsEvent(created_at=base, author_id=1, channel_id=10),
            AnalyticsEvent(created_at=base, author_id=2, channel_id=10, thread_id=99),
            AnalyticsEvent(created_at=base, author_id=2, channel_id=11),
        ]
        m = compute_metrics(events, top_n=2)
        if m["total_messages"] != 3:
            failing.append("total_messages_mismatch")
        if m["unique_authors"] != 2:
            failing.append("unique_authors_mismatch")
        if not m["top_channels"]:
            failing.append("top_channels_missing")

        payload = build_weekly_payload(
            now=base,
            after=base,
            days=7,
            metrics=m,
            previous_metrics={"total_messages": 1, "unique_authors": 1},
            integrations={
                "question_sla": {"open_count": 1, "median_ttfr_minutes": 10.0},
                "membership": {"in_server_without_role": 2, "not_in_server": 3},
            },
        )
        if "delta" not in payload:
            failing.append("delta_missing")

        lines = format_weekly_report(payload)
        joined = "\n".join(lines)
        if "Community Analytics" not in joined:
            failing.append("format_missing_title")
        if "Ops signals" not in joined:
            failing.append("format_missing_ops_signals")
    except Exception as exc:
        failing.append("exception")
        notes.append(f"exception={exc!r}")

    score = 100.0 if not failing else 0.0
    return {
        "feature": "analytics",
        "score": score,
        "failing_cases": failing,
        "notes": notes,
    }
