import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Set


def _get_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def _get_int_env(*names: str, required: bool = False) -> Optional[int]:
    for name in names:
        value = os.getenv(name, "").strip()
        if not value:
            continue
        try:
            return int(value)
        except ValueError:
            raise ValueError(f"Env {name} must be an integer, got: {value!r}")
    if required:
        raise RuntimeError(f"Missing required env vars: {', '.join(names)}")
    return None


def _parse_int_csv(value: str) -> Set[int]:
    if not value:
        return set()
    items = set()
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            items.add(int(token))
        except ValueError:
            raise ValueError(f"Role ID must be integer, got: {token!r}")
    return items


def _parse_str_csv_lower(value: str) -> Set[str]:
    if not value:
        return set()
    items: Set[str] = set()
    for part in value.split(","):
        token = part.strip().lower()
        if token:
            items.add(token)
    return items


def _collect_allowed_role_ids() -> Set[int]:
    role_ids: Set[int] = set()

    csv = _get_env("LAB_ONBOARDER_ALLOWED_ROLE_IDS", "ALLOWED_ROLE_IDS")
    role_ids.update(_parse_int_csv(csv))

    for name in (
        "LAB_ONBOARDER_MEMBER_ROLE_ID",
        "LAB_ONBOARDER_ADMIN_ROLE_ID",
        "MEMBER_ROLE_ID",
        "ADMIN_ROLE_ID",
    ):
        value = _get_int_env(name)
        if value is not None:
            role_ids.add(value)

    return role_ids


def _collect_admin_role_ids() -> Set[int]:
    role_ids: Set[int] = set()

    csv = _get_env("LAB_ONBOARDER_ADMIN_ROLE_IDS", "ADMIN_ROLE_IDS")
    role_ids.update(_parse_int_csv(csv))

    for name in (
        "LAB_ONBOARDER_ADMIN_ROLE_ID",
        "ADMIN_ROLE_ID",
    ):
        value = _get_int_env(name)
        if value is not None:
            role_ids.add(value)

    return role_ids


@dataclass(frozen=True)
class LabOnboarderConfig:
    """Simplified configuration for Lab Onboarder agent."""

    guild_id: int
    profile_forum_channel_id: int
    llm_model: Optional[str]
    allowed_role_ids: Set[int]
    admin_role_ids: Set[int]
    sqlite_path: Path
    log_path: Path
    log_channel_id: Optional[int]
    dm_allowlist_user_ids: Optional[Set[int]]
    dm_allowlist_usernames: Optional[Set[str]]
    debug: bool


def load_config() -> LabOnboarderConfig:
    guild_id = _get_int_env("LAB_ONBOARDER_GUILD_ID", "GUILD_ID", required=True)
    profile_forum_channel_id = _get_int_env(
        "LAB_ONBOARDER_PROFILE_FORUM_CHANNEL_ID",
        "PROFILE_FORUM_CHANNEL_ID",
        required=True,
    )

    llm_model = _get_env("LAB_ONBOARDER_LLM_MODEL") or None

    sqlite_path = Path(
        _get_env("LAB_ONBOARDER_SQLITE_PATH", default="data/lab_onboarder/profiles.sqlite")
    )
    log_path = Path(
        _get_env("LAB_ONBOARDER_LOG_PATH", default="data/lab_onboarder/lab_onboarder.log")
    )
    log_channel_id = _get_int_env("LAB_ONBOARDER_LOG_CHANNEL_ID")
    allowed_role_ids = _collect_allowed_role_ids()
    admin_role_ids = _collect_admin_role_ids()

    if not allowed_role_ids:
        print("[LabOnboarder] WARNING: allowed role IDs not set; everyone can use /onboard")

    raw_allowlist = os.getenv("LAB_ONBOARDER_DM_ALLOWLIST_USER_IDS")
    dm_allowlist_user_ids: Optional[Set[int]]
    if raw_allowlist is None:
        dm_allowlist_user_ids = None
    else:
        dm_allowlist_user_ids = _parse_int_csv(raw_allowlist)

    raw_name_allowlist = os.getenv("LAB_ONBOARDER_DM_ALLOWLIST_USERNAMES")
    dm_allowlist_usernames: Optional[Set[str]]
    if raw_name_allowlist is None:
        dm_allowlist_usernames = None
    else:
        dm_allowlist_usernames = _parse_str_csv_lower(raw_name_allowlist)

    debug = os.getenv("LAB_ONBOARDER_DEBUG", "").strip().lower() in {"1", "true", "yes"}

    return LabOnboarderConfig(
        guild_id=guild_id,
        profile_forum_channel_id=profile_forum_channel_id,
        llm_model=llm_model,
        allowed_role_ids=allowed_role_ids,
        admin_role_ids=admin_role_ids,
        sqlite_path=sqlite_path,
        log_path=log_path,
        log_channel_id=log_channel_id,
        dm_allowlist_user_ids=dm_allowlist_user_ids,
        dm_allowlist_usernames=dm_allowlist_usernames,
        debug=debug,
    )
