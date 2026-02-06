from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Iterable, List, Optional, Type

import pytest

from src.core.discord_history import (
    iter_forum_messages,
    iter_text_channel_messages,
    iter_text_channel_thread_messages,
)


@dataclass(frozen=True)
class DummyMessage:
    label: str


class DummyThread:
    def __init__(
        self,
        thread_id: int,
        *,
        messages: Iterable[DummyMessage],
        archive_timestamp: Optional[datetime] = None,
    ) -> None:
        self.id = int(thread_id)
        self.archive_timestamp = archive_timestamp
        self._messages = list(messages)

    async def history(self, *, after: datetime, limit: Any = None) -> AsyncIterator[DummyMessage]:
        # `after`/`limit` are accepted to match discord.py's signature.
        for msg in self._messages:
            yield msg


class DummyForumChannel:
    def __init__(
        self,
        *,
        threads: List[DummyThread],
        archived: List[DummyThread],
    ) -> None:
        self.threads = threads
        self._archived = archived

    async def archived_threads(self, *, limit: Any = None) -> AsyncIterator[DummyThread]:
        for thread in self._archived:
            yield thread


class DummyTextChannel:
    def __init__(
        self,
        *,
        messages: Iterable[DummyMessage],
        threads: List[DummyThread],
        archived_public: List[DummyThread],
        archived_private: Optional[List[DummyThread]] = None,
        private_forbidden: bool = False,
        forbidden_exc: Type[BaseException] = Exception,
    ) -> None:
        self._messages = list(messages)
        self.threads = threads
        self._archived_public = archived_public
        self._archived_private = archived_private or []
        self._private_forbidden = private_forbidden
        self._forbidden_exc = forbidden_exc

    async def history(self, *, after: datetime, limit: Any = None) -> AsyncIterator[DummyMessage]:
        for msg in self._messages:
            yield msg

    async def archived_threads(
        self,
        *,
        limit: Any = None,
        private: bool = False,
    ) -> AsyncIterator[DummyThread]:
        if private and self._private_forbidden:
            raise self._forbidden_exc("forbidden")
        threads = self._archived_private if private else self._archived_public
        for thread in threads:
            yield thread


async def _collect(ait: AsyncIterator[Any]) -> list[Any]:
    out: list[Any] = []
    async for item in ait:
        out.append(item)
    return out


def test_iter_text_channel_messages_yields_all() -> None:
    after = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ch = DummyTextChannel(
        messages=[DummyMessage("a"), DummyMessage("b")],
        threads=[],
        archived_public=[],
    )
    got = asyncio.run(_collect(iter_text_channel_messages(ch, after=after)))  # type: ignore[arg-type]
    assert [m.label for m in got] == ["a", "b"]


def test_iter_forum_messages_includes_archived_and_dedupes() -> None:
    after = datetime(2026, 1, 1, tzinfo=timezone.utc)
    newer = after + timedelta(hours=1)
    older = after - timedelta(hours=1)

    active = DummyThread(1, messages=[DummyMessage("active-1")])
    archived_dup = DummyThread(1, messages=[DummyMessage("archived-dup")], archive_timestamp=newer)
    archived_ok = DummyThread(2, messages=[DummyMessage("archived-2")], archive_timestamp=newer)
    archived_old = DummyThread(3, messages=[DummyMessage("archived-old")], archive_timestamp=older)
    archived_should_not_reach = DummyThread(4, messages=[DummyMessage("archived-4")], archive_timestamp=newer)

    forum = DummyForumChannel(
        threads=[active],
        archived=[archived_dup, archived_ok, archived_old, archived_should_not_reach],
    )

    got = asyncio.run(_collect(iter_forum_messages(forum, after=after)))  # type: ignore[arg-type]
    assert [m.label for m in got] == ["active-1", "archived-2"]


def test_iter_text_channel_thread_messages_includes_archived_and_handles_private_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.core.discord_history as dh

    class DummyForbidden(Exception):
        pass

    # Make the production code catch our dummy exception without requiring a real discord.Forbidden.
    monkeypatch.setattr(dh.discord, "Forbidden", DummyForbidden, raising=True)

    after = datetime(2026, 1, 1, tzinfo=timezone.utc)
    newer = after + timedelta(hours=1)
    older = after - timedelta(hours=1)

    active = DummyThread(10, messages=[DummyMessage("active-thread")])
    archived_ok = DummyThread(11, messages=[DummyMessage("archived-public")], archive_timestamp=newer)
    archived_old = DummyThread(12, messages=[DummyMessage("archived-old")], archive_timestamp=older)

    ch = DummyTextChannel(
        messages=[],
        threads=[active],
        archived_public=[archived_ok, archived_old],
        archived_private=[DummyThread(99, messages=[DummyMessage("private")], archive_timestamp=newer)],
        private_forbidden=True,
        forbidden_exc=DummyForbidden,
    )

    got = asyncio.run(
        _collect(iter_text_channel_thread_messages(ch, after=after, include_private_threads=True))  # type: ignore[arg-type]
    )
    assert [m.label for m in got] == ["active-thread", "archived-public"]

