from __future__ import annotations

import csv
import tempfile
from pathlib import Path
from typing import Any

from .checker import get_active_members, parse_csv


def evaluate() -> dict[str, Any]:
    """
    Offline scorecard for membership_checker.
    Focus: deterministic CSV parsing + active-member classification.
    """
    failing: list[str] = []
    notes: list[str] = []

    try:
        with tempfile.TemporaryDirectory() as td:
            csv_path = Path(td) / "note_active_test.csv"
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
            if len(valid_ids) != 1:
                failing.append("active_valid_id_count_mismatch")
            if len(usernames) != 1:
                failing.append("active_username_count_mismatch")
    except Exception as exc:
        failing.append("exception")
        notes.append(f"exception={exc!r}")

    score = 100.0 if not failing else 0.0
    return {
        "feature": "membership",
        "score": score,
        "failing_cases": failing,
        "notes": notes,
    }

