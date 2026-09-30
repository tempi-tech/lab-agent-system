"""Post (or update) the entry panel message once.

Uses HTTP only (no gateway session), so it does not disturb the running
invite_role_service. After posting, put the printed message id into
``entry.panelMessageId`` and restart the service.

Usage (Mac mini, from the repo root):

    export PYTHONPATH=.
    set -a; source .env.invite-role-service; set +a
    # preview only (no Discord call)
    ./.venv/bin/python scripts/post_entry_panel.py \\
        --config operations/invite_role_service.config.json --dry-run
    # post a new panel message to entry.channelId
    ./.venv/bin/python scripts/post_entry_panel.py \\
        --config operations/invite_role_service.config.json
    # update the existing panel message (text + buttons)
    ./.venv/bin/python scripts/post_entry_panel.py \\
        --config operations/invite_role_service.config.json --edit
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import discord

from src.services.invite_role_service.config import load_config
from src.services.invite_role_service.entry import (
    ENTRY_COURSE_CLAIM_LABEL,
    ENTRY_LINK_LABEL,
    ENTRY_PANEL_CONTENT,
    ENTRY_STATUS_LABEL,
    ENTRY_SUPPORT_LABEL,
)
from src.services.invite_role_service.entry_panel import EntryPanelView


async def _post(config_path: Path, *, edit: bool, dry_run: bool) -> int:
    config = load_config(config_path)
    entry = config.entry
    if entry is None:
        print("entry block is not configured")
        return 1
    if entry.channel_id is None:
        print("entry.channelId is not set (create the entry channel first)")
        return 1
    if edit and entry.panel_message_id is None:
        print("--edit needs entry.panelMessageId")
        return 1

    if dry_run:
        print(f"channel: {entry.channel_id}")
        print(
            f"buttons: {ENTRY_LINK_LABEL} ({entry.link_url}) / "
            f"{ENTRY_COURSE_CLAIM_LABEL} / {ENTRY_STATUS_LABEL} / {ENTRY_SUPPORT_LABEL}"
        )
        print("---")
        print(ENTRY_PANEL_CONTENT)
        return 0

    client = discord.Client(intents=discord.Intents.none())
    try:
        await client.login(config.discord_token)
        channel = await client.fetch_channel(entry.channel_id)
        if not isinstance(channel, discord.TextChannel):
            print("entry.channelId is not a text channel")
            return 1
        view = EntryPanelView(entry, handler=None)
        allowed = discord.AllowedMentions.none()
        if edit:
            assert entry.panel_message_id is not None
            message = await channel.fetch_message(entry.panel_message_id)
            await message.edit(
                content=ENTRY_PANEL_CONTENT, view=view, allowed_mentions=allowed
            )
            print(f"updated panel message: {message.id}")
        else:
            message = await channel.send(
                ENTRY_PANEL_CONTENT, view=view, allowed_mentions=allowed
            )
            print(f"posted panel message: {message.id}")
            print("set entry.panelMessageId to this id and restart the service")
    finally:
        await client.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Post the entry panel message once")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("operations/invite_role_service.config.json"),
        help="Path to the invite role service JSON config",
    )
    parser.add_argument(
        "--edit",
        action="store_true",
        help="Edit entry.panelMessageId instead of posting",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the panel without calling Discord"
    )
    args = parser.parse_args()
    return asyncio.run(_post(args.config, edit=args.edit, dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
