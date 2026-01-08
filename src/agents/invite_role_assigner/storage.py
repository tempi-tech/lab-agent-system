"""
永続化ストレージ for InviteRoleAssignerAgent

- MemberLogStore: 入室記録をCSVに保存
"""
import csv
import fcntl
from dataclasses import dataclass, asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Set


@dataclass
class MemberJoinRecord:
    """入室記録"""
    timestamp: str           # ISO 8601 (UTC)
    discord_id: int          # Discord ID
    username: str            # Discord username
    invite_code: str         # 使用した招待コード
    phase: str               # フェーズ名
    role_assigned: str       # 付与したロール（カンマ区切り）
    detection_method: str    # 検出方法

    @classmethod
    def create(
        cls,
        discord_id: int,
        username: str,
        invite_code: str,
        phase: str,
        role_assigned: str,
        detection_method: str,
    ) -> "MemberJoinRecord":
        """現在時刻でレコードを作成"""
        return cls(
            timestamp=datetime.now(timezone.utc).isoformat(),
            discord_id=discord_id,
            username=username,
            invite_code=invite_code,
            phase=phase,
            role_assigned=role_assigned,
            detection_method=detection_method,
        )


class MemberLogStore:
    """入室記録のCSV永続化"""

    FIELDNAMES = [f.name for f in fields(MemberJoinRecord)]

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_header()

    def _ensure_header(self) -> None:
        """ファイルが存在しない場合、ヘッダーを作成"""
        if not self.path.exists():
            with open(self.path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self.FIELDNAMES)
                writer.writeheader()

    def append(self, record: MemberJoinRecord) -> None:
        """レコードを追加（ファイルロック付き）"""
        with open(self.path, "a", newline="", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                writer = csv.DictWriter(f, fieldnames=self.FIELDNAMES)
                writer.writerow(asdict(record))
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def read_all(self) -> List[MemberJoinRecord]:
        """全レコードを読み込み"""
        if not self.path.exists():
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            records = []
            for row in reader:
                # discord_id を int に変換
                row["discord_id"] = int(row["discord_id"])
                records.append(MemberJoinRecord(**row))
            return records

    def get_discord_ids(self) -> Set[int]:
        """記録されている全Discord IDを取得"""
        return {r.discord_id for r in self.read_all()}

    def get_by_phase(self, phase: str) -> List[MemberJoinRecord]:
        """フェーズで絞り込み"""
        return [r for r in self.read_all() if r.phase == phase]

    def get_by_discord_id(self, discord_id: int) -> Optional[MemberJoinRecord]:
        """Discord IDで検索（最新の1件）"""
        records = [r for r in self.read_all() if r.discord_id == discord_id]
        return records[-1] if records else None

    def count_by_phase(self) -> dict:
        """フェーズごとの人数を集計"""
        counts: dict = {}
        for r in self.read_all():
            phase = r.phase or "(unknown)"
            counts[phase] = counts.get(phase, 0) + 1
        return counts
