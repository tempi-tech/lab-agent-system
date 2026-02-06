import asyncio

from src.core.discord_text import send_chunked


class DummyChannel:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, content: str) -> None:
        self.sent.append(content)


def test_send_chunked_sends_single_message_under_limit():
    ch = DummyChannel()
    asyncio.run(send_chunked(ch, "hello", limit=10))
    assert ch.sent == ["hello"]


def test_send_chunked_splits_overlong_line():
    ch = DummyChannel()
    asyncio.run(send_chunked(ch, "abcdefghijk", limit=5))
    assert ch.sent == ["abcde", "fghij", "k"]


def test_send_chunked_buffers_multiple_lines():
    ch = DummyChannel()
    asyncio.run(send_chunked(ch, ["aaa", "bbb", "ccc"], limit=8))
    # "aaa\nbbb" fits, adding "\nccc" would exceed 8 -> split into 2 sends
    assert ch.sent == ["aaa\nbbb", "ccc"]

