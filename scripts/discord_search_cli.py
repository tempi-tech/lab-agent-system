from __future__ import annotations

import argparse
import asyncio
import os
from typing import List

from src.core.discord_search import search_messages_discord


def _parse_int_csv(value: str) -> List[int]:
    if not value:
        return []
    out: List[int] = []
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            out.append(int(token))
        except ValueError:
            continue
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discord REST search smoke test")
    parser.add_argument("--guild-id", type=int, default=0, help="Discord guild ID (required)")
    parser.add_argument("--query", type=str, default="", help="Search query (required)")
    parser.add_argument(
        "--channel-ids",
        type=str,
        default="",
        help="Comma-separated channel IDs (optional)",
    )
    parser.add_argument("--limit", type=int, default=10, help="Search limit (1-25)")
    return parser


async def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        print("DISCORD_TOKEN is not set.")
        return 1
    if not args.guild_id:
        print("--guild-id is required.")
        return 1
    if not args.query:
        print("--query is required.")
        return 1

    channel_ids = _parse_int_csv(args.channel_ids)

    results = await search_messages_discord(
        guild_id=args.guild_id,
        query=args.query,
        bot_token=token,
        channel_ids=channel_ids if channel_ids else None,
        author_ids=None,
        limit=args.limit,
    )

    if not results:
        print("No results.")
        return 0

    for idx, result in enumerate(results, start=1):
        print(f"{idx}. #{result.channel_name} {result.author_name}: {result.content[:120]}")
        print(result.jump_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
