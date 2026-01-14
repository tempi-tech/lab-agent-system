"""membership_checker 設定管理"""
from dataclasses import dataclass, field
from pathlib import Path
import json
import os


@dataclass
class MembershipCheckerConfig:
    """会員管理エージェントの設定"""

    guild_id: int
    general_role_id: int
    review_role_id: int
    admin_role_id: int
    log_channel_id: int
    csv_dir: Path
    data_dir: Path = field(default_factory=lambda: Path("data/membership_checker"))
    confirm_usernames: bool = False
    debug: bool = False


def load_config() -> MembershipCheckerConfig:
    """設定を読み込む

    1. invite_role_assigner の config.json からギルド・ロールIDを取得
    2. 環境変数から membership_checker 固有の設定を取得
    """
    # invite_role_assigner の config.json を参照
    invrole_config_path = os.getenv(
        "INVROLE_CONFIG",
        str(Path("data/invite_role_assigner/config.json")),
    )

    guild_id = 0
    general_role_id = 0
    review_role_id = 0
    admin_role_id = 0

    if Path(invrole_config_path).exists():
        with open(invrole_config_path, "r", encoding="utf-8") as f:
            invrole_config = json.load(f)
            guild_id = invrole_config.get("guild_id", 0)
            roles = invrole_config.get("roles", {})
            general_role_id = roles.get("general_role_id", 0)
            review_role_id = roles.get("review_role_id", 0)
            admin_role_id = roles.get("admin_role_id", 0)

    # 環境変数でオーバーライド可能
    guild_id = int(os.getenv("MEMBERSHIP_CHECKER_GUILD_ID", str(guild_id)))
    general_role_id = int(
        os.getenv("MEMBERSHIP_CHECKER_GENERAL_ROLE_ID", str(general_role_id))
    )
    review_role_id = int(
        os.getenv("MEMBERSHIP_CHECKER_REVIEW_ROLE_ID", str(review_role_id))
    )
    admin_role_id = int(
        os.getenv("MEMBERSHIP_CHECKER_ADMIN_ROLE_ID", str(admin_role_id))
    )

    # membership_checker 固有の設定
    log_channel_id = int(os.getenv("MEMBERSHIP_CHECKER_LOG_CHANNEL_ID", "0"))

    csv_dir_str = os.getenv(
        "MEMBERSHIP_CHECKER_CSV_DIR",
        str(Path.home() / "Develop/note-extract-auto/data/output/active"),
    )
    csv_dir = Path(csv_dir_str)

    data_dir_str = os.getenv(
        "MEMBERSHIP_CHECKER_DATA_DIR",
        "data/membership_checker",
    )
    data_dir = Path(data_dir_str)

    confirm_usernames = os.getenv(
        "MEMBERSHIP_CHECKER_CONFIRM_USERNAMES", "false"
    ).lower() in {"true", "1", "yes"}

    debug = os.getenv("MEMBERSHIP_CHECKER_DEBUG", "false").lower() in {
        "true",
        "1",
        "yes",
    }

    return MembershipCheckerConfig(
        guild_id=guild_id,
        general_role_id=general_role_id,
        review_role_id=review_role_id,
        admin_role_id=admin_role_id,
        log_channel_id=log_channel_id,
        csv_dir=csv_dir,
        data_dir=data_dir,
        confirm_usernames=confirm_usernames,
        debug=debug,
    )
