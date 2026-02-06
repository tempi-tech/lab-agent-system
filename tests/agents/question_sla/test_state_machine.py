from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.agents.question_sla.logic import QuestionSlaAgent
from src.agents.question_sla import storage


class DummyTextChannel:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, content: str) -> None:
        self.sent.append(content)


class DummyClient:
    def __init__(self, mapping: dict[int, object]) -> None:
        self._mapping = mapping

    def get_channel(self, channel_id: int):
        return self._mapping.get(channel_id)


@pytest.mark.parametrize(
    "age_minutes,expected_stage",
    [
        (10, 0),
        (120, 1),
        (121, 1),
        (1440, 2),
        (2000, 2),
    ],
)
def test_desired_stage_transitions(age_minutes: int, expected_stage: int) -> None:
    from src.agents.question_sla.logic import _desired_stage_minutes

    created = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    now = created + timedelta(minutes=age_minutes)
    stage = _desired_stage_minutes(
        created_at_iso=created.isoformat(),
        now=now,
        first_reminder_minutes=120,
        escalate_minutes=1440,
    )
    assert stage == expected_stage


def test_tick_sends_once_and_updates_stage(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "state.sqlite"

    monkeypatch.setenv("QUESTION_SLA_ENABLED", "true")
    monkeypatch.setenv("QUESTION_SLA_FORUM_CHANNEL_IDS", "123")
    monkeypatch.setenv("QUESTION_SLA_ESCALATION_CHANNEL_ID", "999")
    monkeypatch.setenv("QUESTION_SLA_SQLITE_PATH", str(db))
    monkeypatch.setenv("QUESTION_SLA_FIRST_REMINDER_MINUTES", "120")
    monkeypatch.setenv("QUESTION_SLA_ESCALATE_MINUTES", "1440")
    monkeypatch.setenv("QUESTION_SLA_TICK_SECONDS", "0")

    agent = QuestionSlaAgent()

    # Seed: open question older than first_reminder.
    created = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 1, 1, 3, 0, 0, tzinfo=timezone.utc)
    storage.upsert_open_question(
        db,
        guild_id=1,
        thread_id=2,
        starter_message_id=2,
        starter_author_id=3,
        created_at_iso=created.isoformat(),
    )

    ch = DummyTextChannel()
    agent._client = DummyClient({999: ch})  # type: ignore[attr-defined]

    asyncio.run(agent._run_tick_once(now=now))  # type: ignore[attr-defined]
    assert len(ch.sent) == 1

    row = storage.get_question(db, 2)
    assert row is not None
    assert row.reminded_stage == 1

    # Second tick should not resend.
    asyncio.run(agent._run_tick_once(now=now))  # type: ignore[attr-defined]
    assert len(ch.sent) == 1


def test_tick_scans_history_and_marks_answered_before_notifying(tmp_path: Path, monkeypatch) -> None:
    import src.agents.question_sla.logic as qlogic

    db = tmp_path / "state.sqlite"
    monkeypatch.setenv("QUESTION_SLA_ENABLED", "true")
    monkeypatch.setenv("QUESTION_SLA_FORUM_CHANNEL_IDS", "123")
    monkeypatch.setenv("QUESTION_SLA_ESCALATION_CHANNEL_ID", "999")
    monkeypatch.setenv("QUESTION_SLA_SQLITE_PATH", str(db))
    monkeypatch.setenv("QUESTION_SLA_FIRST_REMINDER_MINUTES", "120")
    monkeypatch.setenv("QUESTION_SLA_ESCALATE_MINUTES", "1440")
    monkeypatch.setenv("QUESTION_SLA_TICK_SECONDS", "0")

    class DummyAuthor:
        def __init__(self, author_id: int) -> None:
            self.id = author_id

    class DummyMsg:
        def __init__(self, author_id: int, created_at: datetime) -> None:
            self.author = DummyAuthor(author_id)
            self.created_at = created_at

    class DummyThread:
        def __init__(self, messages: list[DummyMsg]) -> None:
            self._messages = messages

        async def history(self, *, after: datetime, limit: int, oldest_first: bool):
            for msg in self._messages:
                if msg.created_at > after:
                    yield msg

    # Make isinstance(..., discord.Thread) match our dummy thread.
    monkeypatch.setattr(qlogic.discord, "Thread", DummyThread)

    agent = QuestionSlaAgent()
    created = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 1, 1, 3, 0, 0, tzinfo=timezone.utc)
    storage.upsert_open_question(
        db,
        guild_id=1,
        thread_id=2,
        starter_message_id=2,
        starter_author_id=3,
        created_at_iso=created.isoformat(),
    )

    ch = DummyTextChannel()
    thread = DummyThread([DummyMsg(author_id=3, created_at=created), DummyMsg(author_id=4, created_at=now)])
    agent._client = DummyClient({999: ch, 2: thread})  # type: ignore[attr-defined]

    asyncio.run(agent._run_tick_once(now=now))  # type: ignore[attr-defined]

    # No notification sent because it was already answered in history.
    assert ch.sent == []

    row = storage.get_question(db, 2)
    assert row is not None
    assert row.status == "answered"
