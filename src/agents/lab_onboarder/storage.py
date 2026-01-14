from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ProfileRecord:
    discord_user_id: int
    profile_url: str
    x_profile_url: Optional[str]
    handle: Optional[str]
    display_name: Optional[str]
    one_liner: Optional[str]
    archetype: Optional[str]
    topics: List[str]
    tools: List[str]
    strengths: List[str]
    cautions: List[str]
    looking_for: List[str]
    conversation_starters: List[str]
    recommended_channels: List[str]
    raw_summary_json: str
    forum_thread_id: Optional[int]
    forum_message_id: Optional[int]
    created_at: str
    updated_at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dump(value: Any) -> str:
    return json.dumps(value or [], ensure_ascii=False)


def _json_load(value: Optional[str]) -> List[str]:
    if not value:
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [str(item) for item in data]
    return []


def _row_to_profile(row: sqlite3.Row) -> ProfileRecord:
    return ProfileRecord(
        discord_user_id=row["discord_user_id"],
        profile_url=row["profile_url"],
        x_profile_url=row["x_profile_url"],
        handle=row["handle"],
        display_name=row["display_name"],
        one_liner=row["one_liner"],
        archetype=row["archetype"],
        topics=_json_load(row["topics_json"]),
        tools=_json_load(row["tools_json"]),
        strengths=_json_load(row["strengths_json"]),
        cautions=_json_load(row["cautions_json"]),
        looking_for=_json_load(row["looking_for_json"]),
        conversation_starters=_json_load(row["conversation_starters_json"]),
        recommended_channels=_json_load(row["recommended_channels_json"]),
        raw_summary_json=row["raw_summary_json"],
        forum_thread_id=row["forum_thread_id"],
        forum_message_id=row["forum_message_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class ProfileStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path.as_posix(), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                discord_user_id INTEGER PRIMARY KEY,
                profile_url TEXT NOT NULL,
                x_profile_url TEXT,
                handle TEXT,
                display_name TEXT,
                one_liner TEXT,
                archetype TEXT,
                topics_json TEXT,
                tools_json TEXT,
                strengths_json TEXT,
                cautions_json TEXT,
                looking_for_json TEXT,
                conversation_starters_json TEXT,
                recommended_channels_json TEXT,
                raw_summary_json TEXT,
                forum_thread_id INTEGER,
                forum_message_id INTEGER,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS x_api_cache (
                handle TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            );
            """
        )
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(profiles)").fetchall()
        }
        if "forum_message_id" not in columns:
            self._conn.execute("ALTER TABLE profiles ADD COLUMN forum_message_id INTEGER")
        if "x_profile_url" not in columns:
            self._conn.execute("ALTER TABLE profiles ADD COLUMN x_profile_url TEXT")
        self._conn.commit()

    def upsert_profile(
        self,
        *,
        discord_user_id: int,
        profile_url: str,
        x_profile_url: Optional[str],
        handle: Optional[str],
        display_name: Optional[str],
        one_liner: Optional[str],
        archetype: Optional[str],
        topics: List[str],
        tools: List[str],
        strengths: List[str],
        cautions: List[str],
        looking_for: List[str],
        conversation_starters: List[str],
        recommended_channels: List[str],
        raw_summary_json: str,
        forum_thread_id: Optional[int],
        forum_message_id: Optional[int],
    ) -> None:
        now = _now_iso()
        self._conn.execute(
            """
            INSERT INTO profiles (
                discord_user_id, profile_url, x_profile_url, handle, display_name, one_liner, archetype,
                topics_json, tools_json, strengths_json, cautions_json, looking_for_json,
                conversation_starters_json, recommended_channels_json, raw_summary_json,
                forum_thread_id, forum_message_id, created_at, updated_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?
            )
            ON CONFLICT(discord_user_id) DO UPDATE SET
                profile_url = excluded.profile_url,
                x_profile_url = COALESCE(excluded.x_profile_url, profiles.x_profile_url),
                handle = excluded.handle,
                display_name = excluded.display_name,
                one_liner = excluded.one_liner,
                archetype = excluded.archetype,
                topics_json = excluded.topics_json,
                tools_json = excluded.tools_json,
                strengths_json = excluded.strengths_json,
                cautions_json = excluded.cautions_json,
                looking_for_json = excluded.looking_for_json,
                conversation_starters_json = excluded.conversation_starters_json,
                recommended_channels_json = excluded.recommended_channels_json,
                raw_summary_json = excluded.raw_summary_json,
                forum_thread_id = COALESCE(excluded.forum_thread_id, profiles.forum_thread_id),
                forum_message_id = COALESCE(excluded.forum_message_id, profiles.forum_message_id),
                updated_at = excluded.updated_at
            """,
            (
                discord_user_id,
                profile_url,
                x_profile_url,
                handle,
                display_name,
                one_liner,
                archetype,
                _json_dump(topics),
                _json_dump(tools),
                _json_dump(strengths),
                _json_dump(cautions),
                _json_dump(looking_for),
                _json_dump(conversation_starters),
                _json_dump(recommended_channels),
                raw_summary_json,
                forum_thread_id,
                forum_message_id,
                now,
                now,
            ),
        )
        self._conn.commit()

    def get_profile(self, discord_user_id: int) -> Optional[ProfileRecord]:
        row = self._conn.execute(
            "SELECT * FROM profiles WHERE discord_user_id = ?",
            (discord_user_id,),
        ).fetchone()
        return _row_to_profile(row) if row else None

    def get_all_profiles(self) -> List[ProfileRecord]:
        rows = self._conn.execute("SELECT * FROM profiles").fetchall()
        return [_row_to_profile(row) for row in rows]

    def delete_profile(self, discord_user_id: int) -> None:
        self._conn.execute(
            "DELETE FROM profiles WHERE discord_user_id = ?",
            (discord_user_id,),
        )
        self._conn.commit()

    def get_x_cache(self, handle: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT payload_json, fetched_at FROM x_api_cache WHERE handle = ?",
            (handle,),
        ).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, dict):
            return None
        return {"payload": payload, "fetched_at": row["fetched_at"]}

    def set_x_cache(self, handle: str, payload: Dict[str, Any]) -> None:
        fetched_at = _now_iso()
        self._conn.execute(
            """
            INSERT INTO x_api_cache (handle, payload_json, fetched_at)
            VALUES (?, ?, ?)
            ON CONFLICT(handle) DO UPDATE SET
                payload_json = excluded.payload_json,
                fetched_at = excluded.fetched_at
            """,
            (
                handle,
                json.dumps(payload, ensure_ascii=False),
                fetched_at,
            ),
        )
        self._conn.commit()
