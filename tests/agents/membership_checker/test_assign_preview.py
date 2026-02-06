from __future__ import annotations

import asyncio
import csv
from pathlib import Path

from src.agents.membership_checker.checker import assign_roles
from src.agents.membership_checker.config import MembershipCheckerConfig


class DummyRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class DummyMember:
    def __init__(self, member_id: int, *, display_name: str, roles: list[DummyRole]) -> None:
        self.id = member_id
        self.display_name = display_name
        self.name = display_name.lower()
        self.global_name = None
        self.roles = roles


class DummyGuild:
    def __init__(self, role: DummyRole) -> None:
        self._role = role

    def get_role(self, role_id: int):
        return self._role if self._role.id == role_id else None


def test_assign_roles_preview_detects_missing_role(tmp_path: Path) -> None:
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

    general_role_id = 111
    config = MembershipCheckerConfig(
        guild_id=999,
        general_role_id=general_role_id,
        review_role_id=0,
        admin_role_id=0,
        log_channel_id=0,
        csv_dir=tmp_path,
    )

    role = DummyRole(general_role_id)
    guild = DummyGuild(role)
    member = DummyMember(123456789012345678, display_name="Alice", roles=[])

    result = asyncio.run(
        assign_roles(guild, config, csv_path, execute=False, confirm_usernames=False, members=[member])  # type: ignore[arg-type]
    )
    assert result["preview"] is True
    assert len(result["to_assign_id"]) == 1

