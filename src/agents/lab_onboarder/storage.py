from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


@dataclass
class ProfileRecord:
    """Simplified profile record for storage."""

    discord_user_id: int
    display_name: str
    introduction: str  # New field: user's introduction text
    archetype: Optional[str]
    x_profile_url: Optional[str]
    forum_thread_id: Optional[int]
    forum_message_id: Optional[int]
    created_at: str
    updated_at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProfileStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path.as_posix(), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        # Create table with new simplified schema
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                discord_user_id INTEGER PRIMARY KEY,
                display_name TEXT,
                introduction TEXT,
                archetype TEXT,
                x_profile_url TEXT,
                forum_thread_id INTEGER,
                forum_message_id INTEGER,
                created_at TEXT,
                updated_at TEXT,
                -- Legacy columns kept for backward compatibility
                profile_url TEXT,
                handle TEXT,
                one_liner TEXT,
                topics_json TEXT,
                tools_json TEXT,
                strengths_json TEXT,
                cautions_json TEXT,
                looking_for_json TEXT,
                conversation_starters_json TEXT,
                recommended_channels_json TEXT,
                raw_summary_json TEXT
            );
            """
        )

        # Add new columns if they don't exist (migration)
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(profiles)").fetchall()
        }

        if "introduction" not in columns:
            self._conn.execute("ALTER TABLE profiles ADD COLUMN introduction TEXT")
        if "forum_message_id" not in columns:
            self._conn.execute("ALTER TABLE profiles ADD COLUMN forum_message_id INTEGER")
        if "x_profile_url" not in columns:
            self._conn.execute("ALTER TABLE profiles ADD COLUMN x_profile_url TEXT")

        self._conn.commit()

    def upsert_profile(
        self,
        *,
        discord_user_id: int,
        display_name: str,
        introduction: str,
        archetype: Optional[str],
        x_profile_url: Optional[str],
        forum_thread_id: Optional[int],
        forum_message_id: Optional[int],
    ) -> None:
        """Insert or update a profile with the simplified schema."""
        now = _now_iso()
        self._conn.execute(
            """
            INSERT INTO profiles (
                discord_user_id, display_name, introduction, archetype,
                x_profile_url, forum_thread_id, forum_message_id,
                created_at, updated_at, profile_url
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(discord_user_id) DO UPDATE SET
                display_name = excluded.display_name,
                introduction = excluded.introduction,
                archetype = excluded.archetype,
                x_profile_url = COALESCE(excluded.x_profile_url, profiles.x_profile_url),
                forum_thread_id = COALESCE(excluded.forum_thread_id, profiles.forum_thread_id),
                forum_message_id = COALESCE(excluded.forum_message_id, profiles.forum_message_id),
                updated_at = excluded.updated_at
            """,
            (
                discord_user_id,
                display_name,
                introduction,
                archetype,
                x_profile_url,
                forum_thread_id,
                forum_message_id,
                now,
                now,
                "",  # legacy profile_url placeholder for NOT NULL constraint
            ),
        )
        self._conn.commit()

    def get_profile(self, discord_user_id: int) -> Optional[ProfileRecord]:
        row = self._conn.execute(
            "SELECT * FROM profiles WHERE discord_user_id = ?",
            (discord_user_id,),
        ).fetchone()
        return self._row_to_profile(row) if row else None

    def get_all_profiles(self) -> List[ProfileRecord]:
        rows = self._conn.execute("SELECT * FROM profiles").fetchall()
        return [self._row_to_profile(row) for row in rows]

    def delete_profile(self, discord_user_id: int) -> None:
        self._conn.execute(
            "DELETE FROM profiles WHERE discord_user_id = ?",
            (discord_user_id,),
        )
        self._conn.commit()

    def _row_to_profile(self, row: sqlite3.Row) -> ProfileRecord:
        """Convert a database row to ProfileRecord with backward compatibility."""
        row_dict = dict(row)

        # Backward compatibility: use one_liner if introduction is empty
        introduction = row_dict.get("introduction") or row_dict.get("one_liner") or ""

        return ProfileRecord(
            discord_user_id=row_dict["discord_user_id"],
            display_name=row_dict.get("display_name") or "",
            introduction=introduction,
            archetype=row_dict.get("archetype"),
            x_profile_url=row_dict.get("x_profile_url"),
            forum_thread_id=row_dict.get("forum_thread_id"),
            forum_message_id=row_dict.get("forum_message_id"),
            created_at=row_dict.get("created_at") or "",
            updated_at=row_dict.get("updated_at") or "",
        )
