from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Dict, Iterable, List, Optional, Tuple


JST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class AnalyticsEvent:
    created_at: datetime
    author_id: int
    channel_id: int
    thread_id: int | None = None


def compute_metrics(
    events: Iterable[AnalyticsEvent],
    *,
    tz: tzinfo = JST,
    top_n: int = 5,
) -> dict[str, Any]:
    # Normalize and sort for deterministic output.
    normalized: list[AnalyticsEvent] = []
    for e in events:
        created = e.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        normalized.append(
            AnalyticsEvent(
                created_at=created.astimezone(timezone.utc),
                author_id=int(e.author_id),
                channel_id=int(e.channel_id),
                thread_id=int(e.thread_id) if e.thread_id is not None else None,
            )
        )

    total = len(normalized)
    unique_authors = len({e.author_id for e in normalized})

    daily: dict[str, int] = {}
    by_channel: dict[int, int] = {}
    by_thread: dict[int, int] = {}

    for e in normalized:
        day = e.created_at.astimezone(tz).strftime("%Y-%m-%d")
        daily[day] = daily.get(day, 0) + 1
        by_channel[e.channel_id] = by_channel.get(e.channel_id, 0) + 1
        if e.thread_id is not None:
            by_thread[e.thread_id] = by_thread.get(e.thread_id, 0) + 1

    top_channels = sorted(by_channel.items(), key=lambda kv: (-kv[1], kv[0]))[: int(top_n)]
    top_threads = sorted(by_thread.items(), key=lambda kv: (-kv[1], kv[0]))[: int(top_n)]

    return {
        "total_messages": total,
        "unique_authors": unique_authors,
        "daily_counts": dict(sorted(daily.items())),
        "top_channels": [{"channel_id": cid, "count": count} for cid, count in top_channels],
        "top_threads": [{"thread_id": tid, "count": count} for tid, count in top_threads],
    }

