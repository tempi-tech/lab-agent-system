from pathlib import Path

from src.services.invite_role_service.config import load_config


def test_load_config_reads_roles_and_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "token-123")
    config_path = tmp_path / "invite_role_service.json"
    data_dir = tmp_path / "runtime"
    config_path.write_text(
        """
{
  "tokenEnv": "DISCORD_TOKEN",
  "guild_id": 1,
  "log_channel_id": 2,
  "roles": {
    "general_role_id": 3,
    "review_role_id": 4
  },
  "phases": {
    "legacy": {
      "invite_codes": ["abc"],
      "active": true
    }
  },
  "excludedInviteCodes": ["course123"],
  "coursePass": {
    "roleId": 5,
    "inviteCodes": ["git123"],
    "panelChannelId": 6,
    "panelMessageId": 7,
    "customId": "course_pass_role:claim:test",
    "openChannelUrl": "https://discord.com/channels/1/6"
  },
  "welcome": {
    "enabled": true,
    "channelId": 8,
    "introChannelId": 9,
    "statePath": "%s",
    "agentId": "labochan-public",
    "thinking": "medium",
    "openclawBin": "/opt/homebrew/bin/openclaw",
    "openclawCwd": "/Users/kmini/.openclaw",
    "timeoutSeconds": 12,
    "followup": {
      "enabled": true,
      "delaySeconds": 90000,
      "checkIntervalSeconds": 600,
      "discrawlDbPath": "%s",
      "archiveMaxAgeSeconds": 1200
    }
  },
  "dataDir": "%s"
}
"""
        % (
            str(data_dir / "welcome_state.json"),
            str(data_dir / "discrawl.db"),
            str(data_dir),
        ),
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg.discord_token == "token-123"
    assert cfg.guild_id == 1
    assert cfg.general_role_id == 3
    assert cfg.review_role_id == 4
    assert cfg.log_channel_id == 2
    assert cfg.phase_for_code("abc") == "legacy"
    assert cfg.is_excluded_code("course123")
    assert not cfg.is_excluded_code("abc")
    assert cfg.course_pass is not None
    assert cfg.course_pass.role_id == 5
    assert cfg.course_pass.panel_channel_id == 6
    assert cfg.course_pass.panel_message_id == 7
    assert cfg.course_pass.custom_id == "course_pass_role:claim:test"
    assert cfg.course_pass.invite_codes == {"git123"}
    assert cfg.course_pass.open_channel_url == "https://discord.com/channels/1/6"
    assert cfg.should_grant_course_pass_for_invite("git123")
    assert not cfg.should_grant_course_pass_for_invite("abc")
    assert cfg.welcome is not None
    assert cfg.welcome.channel_id == 8
    assert cfg.welcome.intro_channel_id == 9
    assert cfg.welcome.guide_channel_id is None
    assert cfg.welcome.state_path == data_dir / "welcome_state.json"
    assert cfg.welcome.agent_id == "labochan-public"
    assert cfg.welcome.thinking == "medium"
    assert cfg.welcome.openclaw_bin == "/opt/homebrew/bin/openclaw"
    assert cfg.welcome.timeout_seconds == 12
    assert cfg.welcome.thread_auto_archive_minutes == 10080
    assert cfg.welcome.max_attempts == 2
    assert cfg.welcome.retry_delay_seconds == 2
    assert cfg.welcome.followup is not None
    assert cfg.welcome.followup.enabled is True
    assert cfg.welcome.followup.delay_seconds == 90000
    assert cfg.welcome.followup.check_interval_seconds == 600
    assert cfg.welcome.followup.discrawl_db_path == data_dir / "discrawl.db"
    assert cfg.welcome.followup.archive_max_age_seconds == 1200
    assert cfg.member_log_path == data_dir / "member_log.csv"
