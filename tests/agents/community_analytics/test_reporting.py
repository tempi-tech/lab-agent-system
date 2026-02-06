from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.agents.community_analytics.reporting import (
    build_weekly_payload,
    compute_delta,
    find_latest_weekly_report_path,
    format_weekly_report,
)


def test_compute_delta_basic() -> None:
    cur = {"total_messages": 10, "unique_authors": 5}
    prev = {"total_messages": 7, "unique_authors": 6}
    delta = compute_delta(cur, prev)
    assert delta["total_messages"] == 3
    assert delta["unique_authors"] == -1


def test_build_weekly_payload_includes_delta_when_previous_metrics_present() -> None:
    now = datetime(2026, 2, 1, 0, 0, tzinfo=timezone.utc)
    after = datetime(2026, 1, 25, 0, 0, tzinfo=timezone.utc)
    metrics = {"total_messages": 10, "unique_authors": 5, "daily_counts": {}, "top_channels": [], "top_threads": []}
    prev = {"total_messages": 7, "unique_authors": 6}
    payload = build_weekly_payload(now=now, after=after, days=7, metrics=metrics, previous_metrics=prev)
    assert "delta" in payload
    assert payload["delta"]["total_messages"] == 3


def test_format_weekly_report_mentions_channels_and_delta() -> None:
    payload = {
        "generated_at": "2026-02-01T00:00:00+00:00",
        "window": {"days": 7},
        "metrics": {
            "total_messages": 10,
            "unique_authors": 5,
            "daily_counts": {"2026-02-01": 10},
            "top_channels": [{"channel_id": 123, "count": 9}],
            "top_threads": [{"thread_id": 456, "count": 4}],
        },
        "delta": {"total_messages": 3, "unique_authors": -1},
        "integrations": {},
    }
    lines = format_weekly_report(payload)
    joined = "\n".join(lines)
    assert "Delta vs previous" in joined
    assert "<#123>" in joined
    assert "<#456>" in joined


def test_find_latest_weekly_report_path_uses_filename_sort(tmp_path: Path) -> None:
    d = tmp_path / "reports"
    d.mkdir()
    (d / "weekly_20260101.json").write_text("{}", encoding="utf-8")
    (d / "weekly_20260108.json").write_text("{}", encoding="utf-8")
    latest = find_latest_weekly_report_path(d)
    assert latest is not None
    assert latest.name == "weekly_20260108.json"

