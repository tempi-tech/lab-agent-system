from __future__ import annotations

import argparse
import asyncio
from typing import List

from src.core.llm import get_llm_client
from src.agents.updates_assistant import prompts
from src.agents.updates_assistant.config import load_config
from src.agents.updates_assistant.router import parse_router_decision
from src.agents.updates_assistant.logic import PERIOD_OPTIONS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="UpdatesAssistant router + chat smoke test")
    parser.add_argument(
        "--message",
        action="append",
        default=[],
        help="Message text to route (can be used multiple times)",
    )
    parser.add_argument(
        "--default",
        action="store_true",
        help="Use a default message set",
    )
    return parser


async def _route_message(message: str) -> dict[str, str]:
    config = load_config()
    router = get_llm_client(config.router_llm_provider, config.router_llm_model)
    default_period = config.default_period if config.default_period in PERIOD_OPTIONS else "24h"
    default_scope = config.router_default_scope if config.router_default_scope in {"channel", "guild"} else "channel"
    prompt = prompts.ROUTER_PROMPT_TEMPLATE.format(
        message=message,
        period_options=sorted(PERIOD_OPTIONS),
        default_period=default_period,
        default_scope=default_scope,
    )
    result = await asyncio.wait_for(
        router.generate(
            prompt,
            temperature=config.router_llm_temperature,
            max_output_tokens=config.router_llm_max_output_tokens,
        ),
        timeout=20,
    )
    decision = parse_router_decision(
        result.text,
        default_period=default_period,
        default_scope=default_scope,
    )
    return {"action": decision.action, "period": decision.period, "scope": decision.scope}


async def _chat_reply(message: str) -> str:
    config = load_config()
    client = get_llm_client(config.llm_provider, config.llm_model)
    prompt = prompts.CHAT_PROMPT_TEMPLATE.format(
        persona=prompts.BASE_PERSONA,
        context="（なし）",
        message=message,
    )
    result = await asyncio.wait_for(
        client.generate(prompt, temperature=0.2, max_output_tokens=400),
        timeout=30,
    )
    return result.text.strip()


async def main() -> int:
    args = build_parser().parse_args()
    messages: List[str] = args.message
    if args.default or not messages:
        messages = [
            "自己紹介してくれる？",
            "今日サーバーでどんな会話があった？",
            "今日の要約お願い",
            "最近の更新を教えて",
            "このチャンネルで何が話題？",
        ]

    chat_only = load_config().chat_only
    for message in messages:
        print(f"Message: {message}")
        try:
            decision = await _route_message(message)
            print(
                "Route: action={action} period={period} scope={scope}".format(
                    action=decision["action"],
                    period=decision["period"],
                    scope=decision["scope"],
                )
            )
            if chat_only:
                reply = await _chat_reply(message)
                print("Reply:")
                print(reply or "(empty)")
                print("Note: chat_only=true, router decision ignored.")
            elif decision["action"] == "chat":
                reply = await _chat_reply(message)
                print("Reply:")
                print(reply or "(empty)")
            else:
                print("Reply: (log fetch required)")
        except Exception as exc:
            print(f"Error: {type(exc).__name__}: {exc}")
        print("-" * 40)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
