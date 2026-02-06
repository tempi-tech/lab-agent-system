from __future__ import annotations

from typing import Iterable, List

import discord


async def send_chunked(
    channel: discord.abc.Messageable,
    text_or_lines: str | Iterable[str],
    *,
    limit: int = 1900,
) -> None:
    """
    Send text while respecting Discord's 2000-char limit.

    - Accepts a single string or an iterable of strings.
    - Splits overly long lines into chunks.
    - Buffers lines until adding the next chunk would exceed `limit`.
    """

    def split_text(text: str, max_len: int) -> List[str]:
        if len(text) <= max_len:
            return [text]
        return [text[i : i + max_len] for i in range(0, len(text), max_len)]

    lines = [text_or_lines] if isinstance(text_or_lines, str) else list(text_or_lines)

    buffer = ""
    for line in lines:
        for chunk in split_text(line, limit):
            candidate = f"{buffer}\n{chunk}" if buffer else chunk
            if len(candidate) > limit:
                if buffer:
                    await channel.send(buffer)
                buffer = chunk
            else:
                buffer = candidate

    if buffer:
        await channel.send(buffer)

