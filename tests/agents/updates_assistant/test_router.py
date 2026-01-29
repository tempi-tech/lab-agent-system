from src.agents.updates_assistant.router import parse_router_decision


def test_parse_router_decision_valid():
    text = '{"action":"log_summary","period":"7d","scope":"guild"}'
    decision = parse_router_decision(text, default_period="24h", default_scope="channel")
    assert decision.action == "log_summary"
    assert decision.period == "7d"
    assert decision.scope == "guild"


def test_parse_router_decision_synonyms():
    text = '{"action":"summary","period":"24h","scope":"channel-only"}'
    decision = parse_router_decision(text, default_period="6h", default_scope="guild")
    assert decision.action == "log_summary"
    assert decision.period == "24h"
    assert decision.scope == "channel"


def test_parse_router_decision_invalid_fallback():
    text = "not json"
    decision = parse_router_decision(text, default_period="6h", default_scope="channel")
    assert decision.action == "chat"
    assert decision.period == "6h"
    assert decision.scope == "channel"
