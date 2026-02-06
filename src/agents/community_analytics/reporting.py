from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .metrics import JST


def find_latest_weekly_report_path(data_dir: Path) -> Path | None:
    """
    Find the most recent `weekly_*.json` in `data_dir` by filename sort.
    Returns None when none exist.
    """
    if not data_dir.exists():
        return None

    paths = sorted(p for p in data_dir.glob("weekly_*.json") if p.is_file())
    return paths[-1] if paths else None


def load_weekly_payload(path: Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def compute_delta(
    current_metrics: dict[str, Any],
    previous_metrics: dict[str, Any],
) -> dict[str, Any]:
    cur_total = int(current_metrics.get("total_messages", 0) or 0)
    prev_total = int(previous_metrics.get("total_messages", 0) or 0)
    cur_authors = int(current_metrics.get("unique_authors", 0) or 0)
    prev_authors = int(previous_metrics.get("unique_authors", 0) or 0)
    return {
        "total_messages": cur_total - prev_total,
        "unique_authors": cur_authors - prev_authors,
    }


def build_weekly_payload(
    *,
    now: datetime,
    after: datetime,
    days: int,
    metrics: dict[str, Any],
    previous_metrics: dict[str, Any] | None = None,
    integrations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now_utc = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    after_utc = after if after.tzinfo else after.replace(tzinfo=timezone.utc)

    payload: dict[str, Any] = {
        "version": 1,
        "generated_at": now_utc.isoformat(),
        "window": {
            "after": after_utc.isoformat(),
            "before": now_utc.isoformat(),
            "days": int(days),
            "tz": "JST",
        },
        "metrics": metrics,
        "integrations": integrations or {},
    }

    if previous_metrics:
        payload["delta"] = compute_delta(metrics, previous_metrics)

    return payload


def format_weekly_report(payload: dict[str, Any]) -> list[str]:
    """
    Produce a Discord-friendly report as lines (for chunked sending).
    """
    window = payload.get("window", {}) if isinstance(payload.get("window"), dict) else {}
    metrics = payload.get("metrics", {}) if isinstance(payload.get("metrics"), dict) else {}
    delta = payload.get("delta", {}) if isinstance(payload.get("delta"), dict) else None
    integrations = (
        payload.get("integrations", {}) if isinstance(payload.get("integrations"), dict) else {}
    )
    errors = payload.get("errors", []) if isinstance(payload.get("errors"), list) else []

    days = int(window.get("days", 0) or 0) or 7
    generated_at = payload.get("generated_at")
    generated = _parse_dt(generated_at)
    generated_jst = generated.astimezone(JST).strftime("%Y-%m-%d %H:%M") if generated else None

    total = int(metrics.get("total_messages", 0) or 0)
    authors = int(metrics.get("unique_authors", 0) or 0)
    daily_counts = metrics.get("daily_counts", {}) if isinstance(metrics.get("daily_counts"), dict) else {}
    top_channels = metrics.get("top_channels", []) if isinstance(metrics.get("top_channels"), list) else []
    top_threads = metrics.get("top_threads", []) if isinstance(metrics.get("top_threads"), list) else []

    lines: list[str] = []
    title = f"📊 **Community Analytics (last {days} days)**"
    if generated_jst:
        title += f"  `{generated_jst} JST`"
    lines.append(title)
    lines.append(f"- Total messages (non-bot): **{total}**")
    lines.append(f"- Unique authors: **{authors}**")
    if errors:
        sample = ", ".join(str(e) for e in errors[:3] if isinstance(e, str))
        more = f" (+{len(errors) - 3} more)" if len(errors) > 3 else ""
        if sample:
            lines.append(f"- ⚠️ Fetch errors: {sample}{more}")
        else:
            lines.append(f"- ⚠️ Fetch errors: {len(errors)}")

    if delta:
        d_total = int(delta.get("total_messages", 0) or 0)
        d_authors = int(delta.get("unique_authors", 0) or 0)
        lines.append(f"- Delta vs previous: messages {d_total:+d}, authors {d_authors:+d}")

    if daily_counts:
        lines.append("")
        lines.append("**Daily counts (JST)**")

        ordered = sorted(((str(k), int(v or 0)) for k, v in daily_counts.items()), key=lambda kv: kv[0])
        values = [count for _, count in ordered]
        spark = _sparkline(values)
        if spark:
            lines.append(f"`{spark}`  (min={min(values)}, max={max(values)})")

        for day, count in ordered:
            lines.append(f"- {day}: {count}")

    if top_channels:
        lines.append("")
        lines.append("**Top channels**")
        for row in top_channels:
            if not isinstance(row, dict):
                continue
            cid = int(row.get("channel_id", 0) or 0)
            count = int(row.get("count", 0) or 0)
            if cid:
                lines.append(f"- <#{cid}>: {count}")

    if top_threads:
        lines.append("")
        lines.append("**Top threads**")
        for row in top_threads:
            if not isinstance(row, dict):
                continue
            tid = int(row.get("thread_id", 0) or 0)
            count = int(row.get("count", 0) or 0)
            if tid:
                lines.append(f"- <#{tid}>: {count}")

    # Optional operational signals.
    q = integrations.get("question_sla") if isinstance(integrations, dict) else None
    m = integrations.get("membership") if isinstance(integrations, dict) else None
    if q or m:
        lines.append("")
        lines.append("**Ops signals (best-effort)**")
        if isinstance(q, dict):
            open_count = q.get("open_count")
            ttfr_min = q.get("median_ttfr_minutes")
            parts: list[str] = []
            if open_count is not None:
                parts.append(f"open={int(open_count)}")
            if ttfr_min is not None:
                parts.append(f"median_TTFR={float(ttfr_min):.1f}m")
            if parts:
                lines.append(f"- Question SLA: " + ", ".join(parts))
        if isinstance(m, dict):
            missing = m.get("in_server_without_role")
            not_in = m.get("not_in_server")
            parts = []
            if missing is not None:
                parts.append(f"missing_role={int(missing)}")
            if not_in is not None:
                parts.append(f"not_in_server={int(not_in)}")
            if parts:
                lines.append(f"- Membership: " + ", ".join(parts))

    return lines


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


def _sparkline(values: list[int], *, chars: str = "._-~=+*#%@") -> str:
    """
    ASCII-only mini graph for Discord (deterministic, dependency-free).
    """
    if not values:
        return ""
    lo = min(values)
    hi = max(values)
    if hi == lo:
        ch = chars[0] if hi == 0 else chars[-1]
        return ch * len(values)

    span = hi - lo
    steps = len(chars) - 1

    out: list[str] = []
    for v in values:
        idx = int((v - lo) * steps / span)
        if idx < 0:
            idx = 0
        elif idx > steps:
            idx = steps
        out.append(chars[idx])
    return "".join(out)
