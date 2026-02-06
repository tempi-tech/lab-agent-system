from __future__ import annotations

import asyncio
import csv
from pathlib import Path

from src.agents.membership_checker.checker import check_status
from src.agents.membership_checker.config import MembershipCheckerConfig


class DummyRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class DummyMember:
    def __init__(
        self,
        member_id: int,
        *,
        name: str,
        display_name: str,
        global_name: str | None = None,
        roles: list[DummyRole] | None = None,
    ) -> None:
        self.id = member_id
        self.name = name
        self.display_name = display_name
        self.global_name = global_name
        self.roles = roles or []


class DummyGuild:
    def __init__(self, name: str, member_count: int) -> None:
        self.name = name
        self.member_count = member_count


def test_check_status_uses_member_index(tmp_path: Path) -> None:
    csv_path = tmp_path / "note_active_test.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "会員番号",
                "noteID",
                "名前",
                "プラン名",
                "Email",
                "Discord",
                "キャンセル済み",
                "退会日",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "会員番号": "1",
                "noteID": "n1",
                "名前": "Alice",
                "プラン名": "General",
                "Email": "a@example.com",
                "Discord": "123456789012345678",
                "キャンセル済み": "false",
                "退会日": "",
            }
        )
        writer.writerow(
            {
                "会員番号": "2",
                "noteID": "n2",
                "名前": "Bob",
                "プラン名": "General",
                "Email": "b@example.com",
                "Discord": "bob_username",
                "キャンセル済み": "false",
                "退会日": "",
            }
        )

    general_role_id = 111
    config = MembershipCheckerConfig(
        guild_id=999,
        general_role_id=general_role_id,
        review_role_id=0,
        admin_role_id=0,
        log_channel_id=0,
        csv_dir=tmp_path,
    )
    guild = DummyGuild("TestGuild", member_count=2)

    alice = DummyMember(
        123456789012345678,
        name="alice",
        display_name="Alice",
        roles=[DummyRole(general_role_id)],
    )
    bob = DummyMember(
        222,
        name="bob_username",
        display_name="Bob",
        roles=[],
    )

    result = asyncio.run(
        check_status(guild, config, csv_path, members=[alice, bob])  # type: ignore[arg-type]
    )

    assert result["server"]["role_members"] == 1
    assert len(result["members"]["in_server_with_role"]) == 1
    assert len(result["members"]["in_server_without_role"]) == 0
    assert len(result["members"]["not_in_server"]) == 0
    assert len(result["members"]["username_in_server"]) == 1
    assert len(result["members"]["username_not_in_server"]) == 0

