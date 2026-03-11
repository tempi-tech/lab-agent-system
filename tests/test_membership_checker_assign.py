import asyncio
from pathlib import Path
from types import SimpleNamespace

from src.agents.membership_checker import checker


class DummyMember:
    def __init__(self, member_id: int, name: str, *, global_name=None, display_name=None):
        self.id = member_id
        self.name = name
        self.global_name = global_name
        self.display_name = display_name or name
        self.roles = []
        self.added_roles = []

    async def add_roles(self, role, reason=None):
        self.roles.append(role)
        self.added_roles.append((role, reason))


class DummyGuild:
    def __init__(self, members, role):
        self.members = members
        self._role = role

    def get_role(self, role_id):
        if role_id == self._role.id:
            return self._role
        return None

    def get_member(self, member_id):
        for member in self.members:
            if member.id == member_id:
                return member
        return None


def test_assign_roles_does_not_grant_role_from_username_match(monkeypatch, tmp_path: Path):
    role = SimpleNamespace(id=999)
    attacker = DummyMember(member_id=111, name="attacker", display_name="victim")
    guild = DummyGuild(members=[attacker], role=role)
    config = SimpleNamespace(general_role_id=999)

    monkeypatch.setattr(
        checker,
        "parse_csv",
        lambda _csv_path: [
            {
                "member_no": "1",
                "note_id": "n1",
                "name": "Victim Name",
                "plan": "basic",
                "email": "victim@example.com",
                "discord_value": "victim",
                "is_valid_id": False,
                "cancelled": False,
                "left_date": "",
            }
        ],
    )

    result = asyncio.run(
        checker.assign_roles(
            guild,
            config,
            tmp_path / "dummy.csv",
            execute=True,
            confirm_usernames=True,
        )
    )

    assert len(result["to_assign_username"]) == 1
    assert result["assigned"] == []
    assert attacker.added_roles == []
