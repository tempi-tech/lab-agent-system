from __future__ import annotations

from datetime import datetime, timezone

from src.agents.community_analytics.metrics import AnalyticsEvent, compute_metrics


def test_compute_metrics_counts_and_tops() -> None:
    base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    events = [
        AnalyticsEvent(created_at=base, author_id=1, channel_id=10),
        AnalyticsEvent(created_at=base, author_id=1, channel_id=10),
        AnalyticsEvent(created_at=base, author_id=2, channel_id=11, thread_id=99),
    ]
    m = compute_metrics(events, top_n=2)
    assert m["total_messages"] == 3
    assert m["unique_authors"] == 2
    assert m["top_channels"][0]["channel_id"] == 10
    assert m["top_channels"][0]["count"] == 2
    assert m["top_threads"][0]["thread_id"] == 99
    assert m["top_threads"][0]["count"] == 1

