"""CSV解析・Discord API処理を行うコアロジック"""
import asyncio
import csv
import os
import re
from datetime import datetime
from glob import glob
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import discord

from .config import MembershipCheckerConfig


async def fetch_all_members(guild: discord.Guild) -> list[discord.Member]:
    """
    Fetch all guild members via API for correctness.
    Falls back to cache when API fetch is unavailable.
    """
    try:
        members: list[discord.Member] = []
        async for member in guild.fetch_members(limit=None):
            members.append(member)
        return members
    except Exception:
        return list(getattr(guild, "members", []) or [])


def build_member_maps(
    members: list[discord.Member],
) -> tuple[dict[int, discord.Member], dict[str, discord.Member]]:
    by_id: dict[int, discord.Member] = {}
    by_name: dict[str, discord.Member] = {}
    for m in members:
        by_id[int(m.id)] = m
        by_name[m.name.lower()] = m
        if getattr(m, "global_name", None):
            by_name[str(m.global_name).lower()] = m
        by_name[m.display_name.lower()] = m
    return by_id, by_name


def has_role_id(member: discord.Member, role_id: int) -> bool:
    return any(getattr(role, "id", None) == role_id for role in getattr(member, "roles", []) or [])


def find_latest_csv(csv_dir: Path) -> Optional[Path]:
    """最新のCSVファイルを検索"""
    pattern = str(csv_dir / "note_active_*.csv")
    files = glob(pattern)
    if not files:
        return None
    return Path(max(files, key=os.path.getmtime))


def is_valid_discord_id(value: str) -> bool:
    """Discord IDが有効な数値形式かどうか"""
    if not value:
        return False
    return bool(re.match(r"^\d{15,20}$", value.strip()))


def parse_csv(csv_path: Path) -> List[dict]:
    """CSVからDiscord紐付けユーザーを抽出"""
    discord_users = []

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            discord_value = row.get("Discord", "").strip()
            if discord_value:
                is_valid_id = is_valid_discord_id(discord_value)
                discord_users.append(
                    {
                        "member_no": row.get("会員番号", ""),
                        "note_id": row.get("noteID", ""),
                        "name": row.get("名前", ""),
                        "plan": row.get("プラン名", ""),
                        "email": row.get("Email", ""),
                        "discord_value": discord_value,
                        "is_valid_id": is_valid_id,
                        "cancelled": row.get("キャンセル済み", "false") == "true",
                        "left_date": row.get("退会日", ""),
                    }
                )

    return discord_users


def get_active_members(discord_users: List[dict]) -> Tuple[List[dict], List[dict]]:
    """アクティブ会員を有効ID/ユーザー名のみに分類"""
    valid_ids = [u for u in discord_users if u["is_valid_id"]]
    username_only = [u for u in discord_users if not u["is_valid_id"]]

    active_valid_ids = [
        u for u in valid_ids if not u["left_date"] and not u["cancelled"]
    ]
    active_usernames = [
        u for u in username_only if not u["left_date"] and not u["cancelled"]
    ]

    return active_valid_ids, active_usernames


async def check_status(
    guild: discord.Guild,
    config: MembershipCheckerConfig,
    csv_path: Path,
    *,
    members: list[discord.Member] | None = None,
) -> dict:
    """Discordサーバーの会員状況を確認"""
    discord_users = parse_csv(csv_path)
    active_valid_ids, active_usernames = get_active_members(discord_users)

    member_list = members if members is not None else list(getattr(guild, "members", []) or [])
    member_by_id, member_by_name = build_member_maps(member_list)
    role_member_count = 0
    if config.general_role_id:
        role_member_count = len([m for m in member_list if has_role_id(m, config.general_role_id)])

    result: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "csv_path": str(csv_path),
        "statistics": {
            "total_discord_linked": len(discord_users),
            "valid_ids": len([u for u in discord_users if u["is_valid_id"]]),
            "valid_ids_active": len(active_valid_ids),
            "username_only": len([u for u in discord_users if not u["is_valid_id"]]),
            "username_only_active": len(active_usernames),
        },
        "server": {
            "name": guild.name,
            "member_count": guild.member_count,
            "role_members": role_member_count,
        },
        "members": {
            "in_server_with_role": [],
            "in_server_without_role": [],
            "not_in_server": [],
            "username_in_server": [],
            "username_not_in_server": [],
        },
    }

    # 有効ID会員のチェック
    for user in active_valid_ids:
        discord_id = int(user["discord_value"])
        member = member_by_id.get(discord_id)

        if member:
            has_role = has_role_id(member, config.general_role_id) if config.general_role_id else False
            entry = {
                "note_name": user["name"],
                "plan": user["plan"],
                "discord_id": user["discord_value"],
                "discord_name": member.display_name,
                "email": user["email"],
            }
            if has_role:
                result["members"]["in_server_with_role"].append(entry)
            else:
                result["members"]["in_server_without_role"].append(entry)
        else:
            result["members"]["not_in_server"].append(
                {
                    "note_name": user["name"],
                    "plan": user["plan"],
                    "discord_id": user["discord_value"],
                    "email": user["email"],
                }
            )

    # ユーザー名会員のチェック
    for user in active_usernames:
        target = user["discord_value"].lower()
        member = member_by_name.get(target)

        if member:
            has_role = has_role_id(member, config.general_role_id) if config.general_role_id else False
            result["members"]["username_in_server"].append(
                {
                    "note_name": user["name"],
                    "plan": user["plan"],
                    "discord_username": user["discord_value"],
                    "discord_id": str(member.id),
                    "discord_name": member.display_name,
                    "has_role": has_role,
                    "email": user["email"],
                }
            )
        else:
            result["members"]["username_not_in_server"].append(
                {
                    "note_name": user["name"],
                    "plan": user["plan"],
                    "discord_username": user["discord_value"],
                    "email": user["email"],
                }
            )

    return result


async def assign_roles(
    guild: discord.Guild,
    config: MembershipCheckerConfig,
    csv_path: Path,
    execute: bool = False,
    confirm_usernames: bool = False,
    *,
    members: list[discord.Member] | None = None,
) -> dict:
    """ロールを付与する"""
    discord_users = parse_csv(csv_path)
    active_valid_ids, active_usernames = get_active_members(discord_users)

    agi_lab_role = guild.get_role(config.general_role_id)
    if not agi_lab_role:
        raise RuntimeError(f"Role {config.general_role_id} が見つかりません")

    member_list = members if members is not None else list(getattr(guild, "members", []) or [])
    member_by_id, member_by_name = build_member_maps(member_list)

    result: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "csv_path": str(csv_path),
        "preview": not execute,
        "to_assign_id": [],
        "to_assign_username": [],
        "assigned": [],
        "failed": [],
    }

    # 有効ID会員でロールなしを検出
    for user in active_valid_ids:
        discord_id = int(user["discord_value"])
        member = member_by_id.get(discord_id)

        if member and not has_role_id(member, config.general_role_id):
            entry = {
                "note_name": user["name"],
                "plan": user["plan"],
                "discord_id": user["discord_value"],
                "discord_name": member.display_name,
                "match_type": "id",
            }
            result["to_assign_id"].append(entry)

    # ユーザー名会員でロールなしを検出（confirm_usernames時のみ）
    if confirm_usernames:
        for user in active_usernames:
            target = user["discord_value"].lower()
            member = member_by_name.get(target)

            if member and not has_role_id(member, config.general_role_id):
                entry = {
                    "note_name": user["name"],
                    "plan": user["plan"],
                    "discord_username": user["discord_value"],
                    "discord_id": str(member.id),
                    "discord_name": member.display_name,
                    "match_type": "username",
                }
                result["to_assign_username"].append(entry)

    # 実行
    if execute:
        all_to_assign = result["to_assign_id"] + result["to_assign_username"]
        for entry in all_to_assign:
            discord_id = int(entry["discord_id"])
            member = member_by_id.get(discord_id)
            if not member:
                continue

            try:
                await member.add_roles(
                    agi_lab_role, reason="membership_checker: note会員へのロール付与"
                )
                result["assigned"].append(entry)
                await asyncio.sleep(0.5)
            except Exception as e:
                entry["error"] = str(e)
                result["failed"].append(entry)

    return result


async def export_followup(
    guild: discord.Guild,
    config: MembershipCheckerConfig,
    csv_path: Path,
    include_no_email: bool = False,
    *,
    members: list[discord.Member] | None = None,
) -> dict:
    """未参加者のフォローアップリストを生成"""
    discord_users = parse_csv(csv_path)
    active_valid_ids, active_usernames = get_active_members(discord_users)

    member_list = members if members is not None else list(getattr(guild, "members", []) or [])
    member_by_id, member_by_name = build_member_maps(member_list)

    result: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "csv_path": str(csv_path),
        "followup_list": [],
    }

    # 有効ID会員で未参加
    for user in active_valid_ids:
        discord_id = int(user["discord_value"])
        member = member_by_id.get(discord_id)

        if not member:
            if user["email"] or include_no_email:
                result["followup_list"].append(
                    {
                        "note_name": user["name"],
                        "note_id": user["note_id"],
                        "plan": user["plan"],
                        "email": user["email"],
                        "discord_value": user["discord_value"],
                        "match_type": "id",
                    }
                )

    for user in active_usernames:
        target = user["discord_value"].lower()
        member = member_by_name.get(target)

        if not member:
            if user["email"] or include_no_email:
                result["followup_list"].append(
                    {
                        "note_name": user["name"],
                        "note_id": user["note_id"],
                        "plan": user["plan"],
                        "email": user["email"],
                        "discord_value": user["discord_value"],
                        "match_type": "username",
                    }
                )

    return result


async def sync_roles(
    guild: discord.Guild,
    config: MembershipCheckerConfig,
    csv_path: Path,
    execute: bool = False,
    *,
    members: list[discord.Member] | None = None,
) -> dict:
    """退会者からロールを削除"""
    discord_users = parse_csv(csv_path)

    # アクティブ会員のDiscord IDを抽出
    note_discord_ids: Set[int] = set()
    for user in discord_users:
        if user["left_date"] or user["cancelled"]:
            continue
        if user["is_valid_id"]:
            note_discord_ids.add(int(user["discord_value"]))

    agi_lab_role = guild.get_role(config.general_role_id)
    if not agi_lab_role:
        raise RuntimeError(f"Role {config.general_role_id} が見つかりません")

    member_list = members if members is not None else list(getattr(guild, "members", []) or [])
    member_by_id, _member_by_name = build_member_maps(member_list)
    members_with_role = [m for m in member_list if has_role_id(m, config.general_role_id)]
    discord_ids_with_role = {m.id for m in members_with_role}

    to_remove_ids = discord_ids_with_role - note_discord_ids
    to_keep_ids = discord_ids_with_role & note_discord_ids

    result: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "csv_path": str(csv_path),
        "preview": not execute,
        "note_members": len(note_discord_ids),
        "discord_role_members": len(discord_ids_with_role),
        "to_keep": len(to_keep_ids),
        "to_remove": [],
        "removed": [],
        "failed": [],
    }

    for uid in to_remove_ids:
        member = member_by_id.get(uid)
        if member:
            result["to_remove"].append(
                {
                    "discord_id": str(uid),
                    "discord_name": member.display_name,
                    "username": str(member),
                }
            )

    if execute:
        for entry in result["to_remove"]:
            uid = int(entry["discord_id"])
            member = member_by_id.get(uid)
            if not member:
                continue

            try:
                await member.remove_roles(
                    agi_lab_role, reason="membership_checker sync: not in note members"
                )
                result["removed"].append(entry)
                await asyncio.sleep(0.5)
            except Exception as e:
                entry["error"] = str(e)
                result["failed"].append(entry)

    return result
