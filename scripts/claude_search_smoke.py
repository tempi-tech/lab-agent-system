from __future__ import annotations

import argparse
import asyncio
import os
from typing import List

from src.core.discord_search import search_messages_discord
from src.core.llm import get_llm_client
from src.agents.claude_search import prompts


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
    parser = argparse.ArgumentParser(description="Discord search -> LLM answer smoke test")
    parser.add_argument("--guild-id", type=int, default=0, help="Discord guild ID (required)")
    parser.add_argument("--query", type=str, default="", help="Search query/question (required)")
    parser.add_argument(
        "--channel-ids",
        type=str,
        default="",
        help="Comma-separated channel IDs (optional)",
    )
    parser.add_argument("--limit", type=int, default=10, help="Search limit (1-25)")
    parser.add_argument("--provider", type=str, default="openrouter", help="LLM provider")
    parser.add_argument("--model", type=str, default="", help="Model id")
    return parser


async def main() -> int:
    args = build_parser().parse_args()
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
        print("No search results.")
        return 0

    context_lines = []
    for idx, result in enumerate(results, start=1):
        snippet = result.content.strip().replace("\n", " ")[:400]
        context_lines.append(
            f"[{idx}] #{result.channel_name} {result.author_name} ({result.created_at})\n"
            f"{snippet}\n"
            f"{result.jump_url}"
        )
    context = "\n\n".join(context_lines)
    prompt = prompts.ANSWER_PROMPT_TEMPLATE.format(question=args.query, context=context)

    client = get_llm_client(args.provider, args.model or None)
    response = await client.generate(prompt)
    print(response.text)
    print("\nSources:")
    for idx, result in enumerate(results[:3], start=1):
        print(f"{idx}. {result.jump_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
