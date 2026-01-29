from __future__ import annotations

import argparse
import asyncio
import os

from src.core.llm import get_llm_client


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LLM smoke test via provider/model")
    parser.add_argument("--provider", type=str, default="openrouter", help="Provider (openrouter/gemini/claude)")
    parser.add_argument("--model", type=str, default="", help="Model id (provider-specific)")
    parser.add_argument("--prompt", type=str, default="こんにちは。自己紹介して。", help="Prompt text")
    return parser


async def main() -> int:
    args = build_parser().parse_args()
    if args.provider.strip().lower() == "openrouter":
        if not os.getenv("OPENROUTER_API_KEY", ""):
            print("OPENROUTER_API_KEY is not set.")
            return 1
    if args.provider.strip().lower() == "claude":
        if not os.getenv("ANTHROPIC_API_KEY", ""):
            print("ANTHROPIC_API_KEY is not set.")
            return 1
    if args.provider.strip().lower() == "gemini":
        if not os.getenv("GOOGLE_API_KEY", "") and not os.getenv("GEMINI_API_KEY", ""):
            print("GOOGLE_API_KEY or GEMINI_API_KEY is not set.")
            return 1

    client = get_llm_client(args.provider, args.model or None)
    result = await client.generate(args.prompt)
    print(result.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
