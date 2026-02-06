from __future__ import annotations

import csv
from pathlib import Path

from src.agents.membership_checker.checker import get_active_members, parse_csv


def test_parse_csv_and_get_active_members(tmp_path: Path) -> None:
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

    users = parse_csv(csv_path)
    valid_ids, usernames = get_active_members(users)
    assert len(valid_ids) == 1
    assert valid_ids[0]["discord_value"] == "123456789012345678"
    assert len(usernames) == 1
    assert usernames[0]["discord_value"] == "bob_username"

