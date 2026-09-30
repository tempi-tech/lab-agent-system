from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast, overload

ThreadAutoArchiveMinutes = Literal[60, 1440, 4320, 10080]


@dataclass(frozen=True)
class InvitePhase:
    name: str
    invite_codes: set[str]
    active: bool = True


@dataclass(frozen=True)
class CoursePassConfig:
    role_id: int
    panel_message_id: int
    custom_id: str
    invite_codes: set[str] = field(default_factory=set)
    panel_channel_id: int | None = None
    button_label: str = "Course Passを受け取る"
    open_channel_label: str = "講座チャンネルを開く"
    open_channel_url: str | None = None
    grant_general_role: bool = False


@dataclass(frozen=True)
class WelcomeFollowupConfig:
    enabled: bool = False
    delay_seconds: int = 86400
    check_interval_seconds: int = 900
    discrawl_db_path: Path = Path("/Users/kmini/.discrawl/discrawl.db")
    archive_max_age_seconds: int = 3600


@dataclass(frozen=True)
class WelcomeConfig:
    enabled: bool
    channel_id: int
    state_path: Path
    intro_channel_id: int | None = None
    guide_channel_id: int | None = None
    agent_id: str = "labochan-public"
    thinking: str = "low"
    openclaw_bin: str = "openclaw"
    openclaw_cwd: Path = Path("/Users/kmini/.openclaw")
    timeout_seconds: int = 180
    skip_bots: bool = True
    threaded: bool = False
    thread_auto_archive_minutes: ThreadAutoArchiveMinutes = 10080
    node_bin_dir: Path | None = None
    max_attempts: int = 2
    retry_delay_seconds: float = 2.0
    followup: WelcomeFollowupConfig | None = None


@dataclass(frozen=True)
class InviteRoleServiceConfig:
    discord_token: str
    guild_id: int
    general_role_id: int
    review_role_id: int | None
    log_channel_id: int | None
    data_dir: Path
    member_log_path: Path
    heartbeat_path: Path
    service_log_path: Path
    baseline_path: Path
    heartbeat_interval_seconds: int = 30
    baseline_refresh_seconds: int = 300
    phases: dict[str, InvitePhase] = field(default_factory=dict)
    excluded_invite_codes: set[str] = field(default_factory=set)
    course_pass: CoursePassConfig | None = None
    welcome: WelcomeConfig | None = None
    debug: bool = False

    def phase_for_code(self, code: str | None) -> str | None:
        if not code:
            return None
        for name, phase in self.phases.items():
            if phase.active and code in phase.invite_codes:
                return name
        return None

    def is_excluded_code(self, code: str | None) -> bool:
        return bool(code and code in self.excluded_invite_codes)

    def should_grant_course_pass_for_invite(self, code: str | None) -> bool:
        return bool(code and self.course_pass and code in self.course_pass.invite_codes)


@overload
def _int_value(raw: Any, field_name: str, *, required: Literal[True] = True) -> int: ...


@overload
def _int_value(
    raw: Any, field_name: str, *, required: Literal[False]
) -> int | None: ...


def _int_value(raw: Any, field_name: str, *, required: bool = True) -> int | None:
    if raw is None or str(raw).strip() == "":
        if required:
            raise ValueError(f"{field_name} is required")
        return None
    text = str(raw).strip()
    if not text.isdigit():
        raise ValueError(f"{field_name} must be an integer")
    return int(text)


def _resolve_token(raw: dict[str, Any]) -> str:
    token_env = str(raw.get("tokenEnv") or "DISCORD_TOKEN").strip()
    token = os.environ.get(token_env) or raw.get("token") or ""
    token = str(token).strip()
    if not token:
        raise ValueError(f"Discord token is not configured (env: {token_env})")
    return token


def load_config(path: str | Path) -> InviteRoleServiceConfig:
    config_path = Path(path).expanduser().resolve()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    data_dir = Path(raw.get("dataDir") or config_path.parent / "runtime").expanduser()
    member_log_path = Path(
        raw.get("memberLogPath") or data_dir / "member_log.csv"
    ).expanduser()
    heartbeat_path = Path(
        raw.get("heartbeatPath") or data_dir / "heartbeat.json"
    ).expanduser()
    service_log_path = Path(
        raw.get("serviceLogPath") or data_dir / "service.log"
    ).expanduser()
    baseline_path = Path(
        raw.get("baselinePath") or data_dir / "invite_baseline.json"
    ).expanduser()

    roles = raw.get("roles") or {}
    phases: dict[str, InvitePhase] = {}
    for name, phase_raw in (raw.get("phases") or {}).items():
        phases[name] = InvitePhase(
            name=name,
            invite_codes={
                str(code).strip()
                for code in (phase_raw or {}).get("invite_codes", [])
                if str(code).strip()
            },
            active=bool((phase_raw or {}).get("active", True)),
        )
    excluded_invite_codes = {
        str(code).strip()
        for code in raw.get("excludedInviteCodes", raw.get("excluded_invite_codes", []))
        if str(code).strip()
    }
    course_pass_raw = raw.get("coursePass") or raw.get("course_pass")
    course_pass = None
    if course_pass_raw:
        course_pass = CoursePassConfig(
            role_id=_int_value(
                course_pass_raw.get("roleId", course_pass_raw.get("role_id")),
                "coursePass.roleId",
            ),
            invite_codes={
                str(code).strip()
                for code in course_pass_raw.get(
                    "inviteCodes", course_pass_raw.get("invite_codes", [])
                )
                if str(code).strip()
            },
            panel_channel_id=_int_value(
                course_pass_raw.get(
                    "panelChannelId", course_pass_raw.get("panel_channel_id")
                ),
                "coursePass.panelChannelId",
                required=False,
            ),
            panel_message_id=_int_value(
                course_pass_raw.get(
                    "panelMessageId", course_pass_raw.get("panel_message_id")
                ),
                "coursePass.panelMessageId",
            ),
            custom_id=str(
                course_pass_raw.get("customId", course_pass_raw.get("custom_id")) or ""
            ).strip(),
            button_label=str(
                course_pass_raw.get("buttonLabel", "Course Passを受け取る")
            ).strip()
            or "Course Passを受け取る",
            open_channel_label=str(
                course_pass_raw.get("openChannelLabel", "講座チャンネルを開く")
            ).strip()
            or "講座チャンネルを開く",
            open_channel_url=str(
                course_pass_raw.get("openChannelUrl")
                or course_pass_raw.get("open_channel_url")
                or ""
            ).strip()
            or None,
            grant_general_role=bool(
                course_pass_raw.get(
                    "grantGeneralRole", course_pass_raw.get("grant_general_role", False)
                )
            ),
        )
        if not course_pass.custom_id:
            raise ValueError("coursePass.customId is required")

    welcome_raw = raw.get("welcome") or raw.get("labochanWelcome")
    welcome = None
    if welcome_raw:
        welcome_enabled = bool(welcome_raw.get("enabled", True))
        welcome_state_path = Path(
            welcome_raw.get("statePath") or data_dir / "welcome_state.json"
        ).expanduser()
        followup_raw = welcome_raw.get("followup") or {}
        followup = None
        if followup_raw:
            followup = WelcomeFollowupConfig(
                enabled=bool(followup_raw.get("enabled", False)),
                delay_seconds=max(int(followup_raw.get("delaySeconds", 86400)), 60),
                check_interval_seconds=max(
                    int(followup_raw.get("checkIntervalSeconds", 900)), 60
                ),
                discrawl_db_path=Path(
                    followup_raw.get("discrawlDbPath")
                    or "/Users/kmini/.discrawl/discrawl.db"
                ).expanduser(),
                archive_max_age_seconds=max(
                    int(followup_raw.get("archiveMaxAgeSeconds", 3600)), 60
                ),
            )
        welcome = WelcomeConfig(
            enabled=welcome_enabled,
            channel_id=_int_value(
                welcome_raw.get("channelId", welcome_raw.get("channel_id")),
                "welcome.channelId",
            ),
            state_path=welcome_state_path,
            intro_channel_id=_int_value(
                welcome_raw.get("introChannelId", welcome_raw.get("intro_channel_id")),
                "welcome.introChannelId",
                required=False,
            ),
            guide_channel_id=_int_value(
                welcome_raw.get("guideChannelId", welcome_raw.get("guide_channel_id")),
                "welcome.guideChannelId",
                required=False,
            ),
            agent_id=str(
                welcome_raw.get(
                    "agentId", welcome_raw.get("agent_id", "labochan-public")
                )
            ).strip()
            or "labochan-public",
            thinking=str(welcome_raw.get("thinking", "low")).strip() or "low",
            openclaw_bin=str(welcome_raw.get("openclawBin", "openclaw")).strip()
            or "openclaw",
            openclaw_cwd=Path(
                welcome_raw.get("openclawCwd") or "/Users/kmini/.openclaw"
            ).expanduser(),
            timeout_seconds=max(int(welcome_raw.get("timeoutSeconds", 180)), 5),
            skip_bots=bool(welcome_raw.get("skipBots", True)),
            threaded=bool(welcome_raw.get("threaded", False)),
            thread_auto_archive_minutes=cast(
                ThreadAutoArchiveMinutes,
                int(welcome_raw.get("threadAutoArchiveMinutes", 10080)),
            ),
            node_bin_dir=(
                Path(welcome_raw.get("nodeBinDir")).expanduser()
                if welcome_raw.get("nodeBinDir")
                else None
            ),
            max_attempts=min(
                max(int(welcome_raw.get("maxAttempts", 2)), 1),
                5,
            ),
            retry_delay_seconds=min(
                max(float(welcome_raw.get("retryDelaySeconds", 2)), 0),
                60,
            ),
            followup=followup,
        )
        if welcome.thread_auto_archive_minutes not in {60, 1440, 4320, 10080}:
            raise ValueError(
                "welcome.threadAutoArchiveMinutes must be one of 60, 1440, 4320, 10080"
            )
        if welcome.thinking not in {
            "off",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "adaptive",
            "max",
            "ultra",
        }:
            raise ValueError("welcome.thinking is invalid")
        if (
            welcome.followup
            and welcome.followup.enabled
            and not welcome.intro_channel_id
        ):
            raise ValueError(
                "welcome.introChannelId is required when welcome.followup is enabled"
            )

    return InviteRoleServiceConfig(
        discord_token=_resolve_token(raw),
        guild_id=_int_value(raw.get("guild_id"), "guild_id"),
        general_role_id=_int_value(
            roles.get("general_role_id", raw.get("general_role_id")), "general_role_id"
        ),
        review_role_id=_int_value(
            roles.get("review_role_id", raw.get("review_role_id")),
            "review_role_id",
            required=False,
        ),
        log_channel_id=_int_value(
            raw.get("log_channel_id"), "log_channel_id", required=False
        ),
        data_dir=data_dir,
        member_log_path=member_log_path,
        heartbeat_path=heartbeat_path,
        service_log_path=service_log_path,
        baseline_path=baseline_path,
        heartbeat_interval_seconds=max(
            int(raw.get("heartbeatIntervalSeconds", 30)), 10
        ),
        baseline_refresh_seconds=max(int(raw.get("baselineRefreshSeconds", 300)), 30),
        phases=phases,
        excluded_invite_codes=excluded_invite_codes,
        course_pass=course_pass,
        welcome=welcome,
        debug=bool(raw.get("debug", False)),
    )
