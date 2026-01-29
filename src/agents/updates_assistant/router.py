from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.core.llm import safe_json_loads


ALLOWED_ACTIONS = {"chat", "log_summary", "log_qa"}
ALLOWED_PERIODS = {"1h", "6h", "24h", "7d"}
ALLOWED_SCOPES = {"channel", "guild"}


@dataclass(frozen=True)
class RouterDecision:
    action: str
    period: str
    scope: str


def _normalize_action(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in ALLOWED_ACTIONS:
        return raw
    if raw in {"summary", "summarize", "updates"}:
        return "log_summary"
    if raw in {"qa", "ask", "question"}:
        return "log_qa"
    if raw in {"talk", "chatting"}:
        return "chat"
    return "chat"


def _normalize_period(value: Any, default_period: str) -> str:
    raw = str(value or "").strip().lower()
    if raw in ALLOWED_PERIODS:
        return raw
    return default_period if default_period in ALLOWED_PERIODS else "24h"


def _normalize_scope(value: Any, default_scope: str) -> str:
    raw = str(value or "").strip().lower()
    if raw in ALLOWED_SCOPES:
        return raw
    if raw in {"channel-only", "channel_only", "local"}:
        return "channel"
    if raw in {"server", "guild-wide", "guild_wide", "global"}:
        return "guild"
    return default_scope if default_scope in ALLOWED_SCOPES else "channel"


def parse_router_decision(
    text: str,
    *,
    default_period: str,
    default_scope: str,
) -> RouterDecision:
    try:
        payload = safe_json_loads(text)
    except Exception:
        return RouterDecision(action="chat", period=_normalize_period("", default_period), scope=_normalize_scope("", default_scope))

    if not isinstance(payload, dict):
        return RouterDecision(action="chat", period=_normalize_period("", default_period), scope=_normalize_scope("", default_scope))

    action = _normalize_action(payload.get("action"))
    period = _normalize_period(payload.get("period"), default_period)
    scope = _normalize_scope(payload.get("scope"), default_scope)
    return RouterDecision(action=action, period=period, scope=scope)
