import os
import re
from dataclasses import dataclass
from typing import Optional, Set

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

@dataclass(frozen=True)
class InviteRoleAssignerConfig:
    guild_id: int
    general_role_id: int      # agi-lab
    review_role_id: int       # 要確認
    admin_role_id: int
    log_channel_id: int       # #member-log

    invited_codes: Set[str]   # 招待枠のコード

    debug: bool = False

def load_config() -> InviteRoleAssignerConfig:
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

    # 招待枠のコード（空でもOK - generate後に設定）
    invited_codes = _parse_codes_csv(os.getenv("INVROLE_INVITED_CODES", ""))

    debug = os.getenv("INVROLE_DEBUG", "").strip() in {"1", "true", "True", "yes", "YES"}

    return InviteRoleAssignerConfig(
        guild_id=guild_id,
        general_role_id=general_role_id,
        review_role_id=review_role_id,
        admin_role_id=admin_role_id,
        log_channel_id=log_channel_id,
        invited_codes=invited_codes,
        debug=debug,
    )
