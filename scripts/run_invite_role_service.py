from __future__ import annotations

import argparse
from pathlib import Path

from src.services.invite_role_service import InviteRoleService, load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the standalone Discord invite role service")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("operations/invite_role_service.config.example.json"),
        help="Path to the invite role service JSON config",
    )
    args = parser.parse_args()

    service = InviteRoleService(load_config(args.config))
    service.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
