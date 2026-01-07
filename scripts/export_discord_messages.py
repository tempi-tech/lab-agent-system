import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional, TextIO

import discord
from dotenv import load_dotenv


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing env: {name}")
    return value


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _message_to_record(message: discord.Message) -> dict[str, Any]:
    channel = message.channel
    record: dict[str, Any] = {
        "message_id": message.id,
        "created_at": _to_iso(message.created_at),
        "edited_at": _to_iso(message.edited_at),
        "jump_url": message.jump_url,
        "content": message.content,
        "author": {
            "id": getattr(message.author, "id", None),
            "name": getattr(message.author, "name", None),
            "display_name": getattr(message.author, "display_name", None),
            "bot": getattr(message.author, "bot", None),
        },
        "guild": {
            "id": getattr(message.guild, "id", None),
            "name": getattr(message.guild, "name", None),
        },
        "channel": {
            "id": getattr(channel, "id", None),
            "name": getattr(channel, "name", None),
            "type": str(getattr(channel, "type", None)),
        },
        "thread": None,
        "attachments": [
            {
                "id": a.id,
                "filename": a.filename,
                "url": a.url,
                "proxy_url": a.proxy_url,
                "content_type": a.content_type,
                "size": a.size,
            }
            for a in message.attachments
        ],
        "embeds": [e.to_dict() for e in message.embeds],
    }

    if isinstance(channel, discord.Thread):
        parent = channel.parent
        record["thread"] = {
            "id": channel.id,
            "name": channel.name,
            "parent_id": channel.parent_id,
            "parent_name": getattr(parent, "name", None),
            "type": str(channel.type),
            "archived": channel.archived,
            "archive_timestamp": _to_iso(channel.archive_timestamp),
            "locked": channel.locked,
        }
        if parent is not None:
            record["channel"] = {
                "id": parent.id,
                "name": getattr(parent, "name", None),
                "type": str(getattr(parent, "type", None)),
            }

    return record


def _write_jsonl(fp: TextIO, record: dict[str, Any]) -> None:
    fp.write(json.dumps(record, ensure_ascii=False))
    fp.write("\n")


async def _iter_history(
    messageable: discord.abc.Messageable,
    *,
    after: datetime,
    oldest_first: bool,
) -> AsyncIterator[discord.Message]:
    async for message in messageable.history(after=after, limit=None, oldest_first=oldest_first):
        yield message


async def _export_messageable(
    fp: TextIO,
    messageable: discord.abc.Messageable,
    *,
    after: datetime,
    oldest_first: bool,
) -> int:
    count = 0
    async for message in _iter_history(messageable, after=after, oldest_first=oldest_first):
        _write_jsonl(fp, _message_to_record(message))
        count += 1
    return count


async def _iter_archived_threads(
    channel: discord.abc.GuildChannel,
    *,
    after: datetime,
    include_private_threads: bool,
) -> AsyncIterator[discord.Thread]:
    if isinstance(channel, discord.TextChannel):
        async for thread in channel.archived_threads(limit=None):
            if thread.archive_timestamp and thread.archive_timestamp < after:
                break
            yield thread

        if include_private_threads:
            try:
                async for thread in channel.archived_threads(limit=None, private=True):
                    if thread.archive_timestamp and thread.archive_timestamp < after:
                        break
                    yield thread
            except discord.Forbidden:
                print(
                    f"Skip private archived threads (missing manage_threads): "
                    f"#{channel.name} ({channel.id})"
                )
    elif isinstance(channel, discord.ForumChannel):
        async for thread in channel.archived_threads(limit=None):
            if thread.archive_timestamp and thread.archive_timestamp < after:
                break
            yield thread


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Discord message history for the last N days into JSONL."
    )
    parser.add_argument("--guild-id", type=int, help="Target Discord guild/server ID")
    parser.add_argument("--days", type=int, default=30, help="Lookback days (default: 30)")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSONL path (default: data/discord_export_<guild>_<timestamp>.jsonl)",
    )
    parser.add_argument(
        "--archived-threads",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Include archived threads (default: true)",
    )
    parser.add_argument(
        "--private-threads",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Attempt to include private archived threads (default: true, may require manage_threads)",
    )
    parser.add_argument(
        "--oldest-first",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Export messages in chronological order (default: true)",
    )
    parser.add_argument(
        "--list-guilds",
        action="store_true",
        help="List guilds the bot can access and exit",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    token = _require_env("DISCORD_TOKEN")

    args = _parse_args()

    if args.days <= 0:
        raise SystemExit("--days must be >= 1")

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        try:
            if args.list_guilds:
                for g in client.guilds:
                    print(f"{g.id}\t{g.name}")
                return

            if not args.guild_id:
                raise SystemExit("Missing required arg: --guild-id (or use --list-guilds)")

            guild = client.get_guild(int(args.guild_id))
            if guild is None:
                print(f"Guild not found in cache: {args.guild_id}")
                if client.guilds:
                    print("Accessible guilds:")
                    for g in client.guilds:
                        print(f"  - {g.id}\t{g.name}")
                raise SystemExit(2)

            after = _utc_now() - timedelta(days=int(args.days))
            print(f"Exporting messages after: {after.isoformat()}")
            print(f"Guild: {guild.name} ({guild.id})")

            output_path: Path
            if args.output is not None:
                output_path = args.output
            else:
                timestamp = _utc_now().strftime("%Y%m%d_%H%M%S")
                output_path = Path("data") / f"discord_export_{guild.id}_{timestamp}.jsonl"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            print(f"Output: {output_path}")

            channels = await guild.fetch_channels()
            text_channels = [c for c in channels if isinstance(c, discord.TextChannel)]
            forum_channels = [c for c in channels if isinstance(c, discord.ForumChannel)]

            total_messages = 0
            with output_path.open("w", encoding="utf-8") as fp:
                for channel in sorted(text_channels, key=lambda c: (c.position, c.id)):
                    print(f"Channel: #{channel.name} ({channel.id})")
                    try:
                        total_messages += await _export_messageable(
                            fp, channel, after=after, oldest_first=args.oldest_first
                        )
                    except discord.Forbidden:
                        print(f"Skip (forbidden): #{channel.name} ({channel.id})")
                    except discord.HTTPException as e:
                        print(f"Skip (http error): #{channel.name} ({channel.id}) -> {e}")

                try:
                    active_threads = await guild.active_threads()
                except discord.HTTPException as e:
                    print(f"Failed to list active threads: {e}")
                    active_threads = []

                seen_thread_ids: set[int] = set()
                for thread in active_threads:
                    if thread.id in seen_thread_ids:
                        continue
                    seen_thread_ids.add(thread.id)
                    print(f"Thread (active): {thread.name} ({thread.id})")
                    try:
                        total_messages += await _export_messageable(
                            fp, thread, after=after, oldest_first=args.oldest_first
                        )
                    except discord.Forbidden:
                        print(f"Skip thread (forbidden): {thread.name} ({thread.id})")
                    except discord.HTTPException as e:
                        print(f"Skip thread (http error): {thread.name} ({thread.id}) -> {e}")

                if args.archived_threads:
                    for channel in list(text_channels) + list(forum_channels):
                        async for thread in _iter_archived_threads(
                            channel, after=after, include_private_threads=args.private_threads
                        ):
                            if thread.id in seen_thread_ids:
                                continue
                            seen_thread_ids.add(thread.id)
                            print(f"Thread (archived): {thread.name} ({thread.id})")
                            try:
                                total_messages += await _export_messageable(
                                    fp, thread, after=after, oldest_first=args.oldest_first
                                )
                            except discord.Forbidden:
                                print(
                                    f"Skip archived thread (forbidden): {thread.name} ({thread.id})"
                                )
                            except discord.HTTPException as e:
                                print(
                                    f"Skip archived thread (http error): {thread.name} ({thread.id}) -> {e}"
                                )

            print(f"Done. Wrote {total_messages} messages to {output_path}")
        finally:
            await client.close()

    client.run(token)


if __name__ == "__main__":
    main()
