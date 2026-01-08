import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Set

_INVITE_CODE_RE = re.compile(r"(?:discord\.gg/|discord(?:app)?\.com/invite/)([A-Za-z0-9-]+)")


def _normalize_invite_code(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    m = _INVITE_CODE_RE.search(v)
    return m.group(1) if m else v


def _parse_codes_csv(csv: str) -> Set[str]:
    if not csv:
        return set()
    codes = set()
    for part in csv.split(","):
        code = _normalize_invite_code(part)
        if code:
            codes.add(code)
    return codes


def _get_int_env(name: str, default: Optional[int] = None) -> Optional[int]:
    v = os.getenv(name, "").strip()
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        raise ValueError(f"Env {name} must be an integer, got: {v!r}")


@dataclass
class Phase:
    """招待フェーズ（時期ごとの招待コードグループ）"""
    name: str
    description: str
    invite_codes: Set[str]
    active: bool = True

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "Phase":
        return cls(
            name=name,
            description=data.get("description", ""),
            invite_codes=set(data.get("invite_codes", [])),
            active=data.get("active", True),
        )


@dataclass
class InviteRoleAssignerConfig:
    guild_id: int
    general_role_id: int      # agi-lab
    review_role_id: int       # 要確認
    admin_role_id: int
    log_channel_id: int       # #member-log

    phases: Dict[str, Phase] = field(default_factory=dict)
    debug: bool = False
    data_dir: Path = field(default_factory=lambda: Path("data/invite_role_assigner"))

    # 後方互換性のため、invited_codes も保持
    @property
    def invited_codes(self) -> Set[str]:
        """全フェーズの招待コードを統合して返す（後方互換性）"""
        codes: Set[str] = set()
        for phase in self.phases.values():
            if phase.active:
                codes.update(phase.invite_codes)
        return codes

    def get_phase_for_code(self, code: str) -> Optional[str]:
        """招待コードが属するフェーズ名を返す"""
        if not code:
            return None
        for phase_name, phase in self.phases.items():
            if phase.active and code in phase.invite_codes:
                return phase_name
        return None

    def is_invited_code(self, code: str) -> bool:
        """招待コードがいずれかのフェーズに属するか"""
        return self.get_phase_for_code(code) is not None


def _load_from_json(config_path: str) -> InviteRoleAssignerConfig:
    """JSONファイルから設定を読み込み"""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # フェーズをパース
    phases: Dict[str, Phase] = {}
    for phase_name, phase_data in data.get("phases", {}).items():
        phases[phase_name] = Phase.from_dict(phase_name, phase_data)

    roles = data.get("roles", {})

    return InviteRoleAssignerConfig(
        guild_id=data["guild_id"],
        general_role_id=roles.get("general_role_id", data.get("general_role_id")),
        review_role_id=roles.get("review_role_id", data.get("review_role_id")),
        admin_role_id=roles.get("admin_role_id", data.get("admin_role_id")),
        log_channel_id=data["log_channel_id"],
        phases=phases,
        debug=data.get("debug", False),
        data_dir=Path(data.get("data_dir", "data/invite_role_assigner")),
    )


def _load_from_env() -> InviteRoleAssignerConfig:
    """環境変数から設定を読み込み（後方互換性）"""
    guild_id = _get_int_env("INVROLE_GUILD_ID")
    general_role_id = _get_int_env("INVROLE_GENERAL_ROLE_ID")
    review_role_id = _get_int_env("INVROLE_REVIEW_ROLE_ID")
    admin_role_id = _get_int_env("INVROLE_ADMIN_ROLE_ID")
    log_channel_id = _get_int_env("INVROLE_LOG_CHANNEL_ID")

    missing = [k for k, v in {
        "INVROLE_GUILD_ID": guild_id,
        "INVROLE_GENERAL_ROLE_ID": general_role_id,
        "INVROLE_REVIEW_ROLE_ID": review_role_id,
        "INVROLE_ADMIN_ROLE_ID": admin_role_id,
        "INVROLE_LOG_CHANNEL_ID": log_channel_id,
    }.items() if v is None]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

    # 招待枠のコードを "legacy" フェーズとして登録
    invited_codes = _parse_codes_csv(os.getenv("INVROLE_INVITED_CODES", ""))
    phases: Dict[str, Phase] = {}
    if invited_codes:
        phases["legacy"] = Phase(
            name="legacy",
            description="Legacy invited codes from env var",
            invite_codes=invited_codes,
            active=True,
        )

    debug = os.getenv("INVROLE_DEBUG", "").strip() in {"1", "true", "True", "yes", "YES"}

    return InviteRoleAssignerConfig(
        guild_id=guild_id,
        general_role_id=general_role_id,
        review_role_id=review_role_id,
        admin_role_id=admin_role_id,
        log_channel_id=log_channel_id,
        phases=phases,
        debug=debug,
    )


def load_config() -> InviteRoleAssignerConfig:
    """設定を読み込み（JSON優先、なければ環境変数）"""
    config_path = os.getenv("INVROLE_CONFIG", "").strip()

    if config_path:
        return _load_from_json(config_path)

    # 後方互換性: 環境変数から読み込み
    print("[InviteRoleAssigner] WARNING: Using legacy env vars. "
          "Please migrate to JSON config (set INVROLE_CONFIG)")
    return _load_from_env()
