import asyncio

import pytest

from src.agents.operator.logic import OperatorAgent
from src.core.action_registry import ActionRegistry


class DummyChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


class DummyAuthor:
    def __init__(self, user_id: int, *, bot: bool = False) -> None:
        self.id = user_id
        self.bot = bot


class DummyMessage:
    def __init__(self, content: str, user_id: int, channel_id: int = 1) -> None:
        self.content = content
        self.author = DummyAuthor(user_id)
        self.channel = DummyChannel(channel_id)


def test_operator_rejects_unauthorized_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPERATOR_ADMIN_USER_IDS", "")
    monkeypatch.setenv("OPERATOR_ADMIN_ROLE_IDS", "")
    monkeypatch.setenv("OPERATOR_ALLOWED_CHANNEL_IDS", "")

    agent = OperatorAgent()
    agent._actions = ActionRegistry()
    agent._actions.register("daily_reporter", {"run": lambda *_: None})

    msg = DummyMessage("!agent daily_reporter run here", user_id=42)
    asyncio.run(agent.on_message(msg))

    assert msg.channel.sent == ["You are not authorized to run operator actions."]


def test_operator_allows_authorized_user_and_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPERATOR_ADMIN_USER_IDS", "42")
    monkeypatch.setenv("OPERATOR_ADMIN_ROLE_IDS", "")
    monkeypatch.setenv("OPERATOR_ALLOWED_CHANNEL_IDS", "")

    called: list[list[str]] = []

    async def action(_message, args: list[str]) -> None:
        called.append(args)

    agent = OperatorAgent()
    agent._actions = ActionRegistry()
    agent._actions.register("daily_reporter", {"run": action})

    msg = DummyMessage("!agent daily_reporter run here", user_id=42)
    asyncio.run(agent.on_message(msg))

    assert called == [["here"]]
    assert msg.channel.sent == []
