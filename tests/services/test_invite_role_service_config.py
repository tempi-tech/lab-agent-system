from pathlib import Path

from src.services.invite_role_service.config import load_config


def test_load_config_reads_roles_and_paths(tmp_path: Path, monkeypatch) -> None:
    # The legacy coursePass block below must be tolerated (ignored), not rejected.
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
    assert cfg.ignored_legacy_keys == ("coursePass",)
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


def test_legacy_course_pass_block_is_ignored_with_warning(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "token-xyz")
    config_path = tmp_path / "svc.json"
    config_path.write_text(
        """
{
  "tokenEnv": "DISCORD_TOKEN",
  "guild_id": 1,
  "roles": {"general_role_id": 3, "review_role_id": 4},
  "coursePass": {"roleId": 5, "panelMessageId": 7, "customId": ""},
  "dataDir": "%s"
}
""" % str(tmp_path / "runtime"),
        encoding="utf-8",
    )

    with caplog.at_level("WARNING"):
        cfg = load_config(config_path)

    assert cfg.ignored_legacy_keys == ("coursePass",)
    assert not hasattr(cfg, "course_pass")
    assert "coursePass" in caplog.text
    assert "ignored" in caplog.text


def test_config_without_legacy_keys_has_no_warning(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "token-xyz")
    config_path = tmp_path / "svc.json"
    config_path.write_text(
        '{"tokenEnv": "DISCORD_TOKEN", "guild_id": 1, "dataDir": "%s"}'
        % str(tmp_path / "runtime"),
        encoding="utf-8",
    )

    with caplog.at_level("WARNING"):
        cfg = load_config(config_path)

    assert cfg.ignored_legacy_keys == ()
    assert caplog.text == ""
