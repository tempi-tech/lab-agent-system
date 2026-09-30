from __future__ import annotations

from pathlib import Path

from src.services.invite_role_service.config import load_config
from src.services.invite_role_service.course_pass import (
    CoursePassOutcome,
    evaluate_course_pass_claim,
)


def _call(**overrides):
    kwargs = dict(
        interaction_guild_id=1,
        expected_guild_id=1,
        member_is_resolved=True,
        role_exists=True,
        bot_member_resolved=True,
        bot_has_manage_roles=True,
        bot_top_role_position=10,
        role_position=5,
        member_has_role=False,
    )
    kwargs.update(overrides)
    return evaluate_course_pass_claim(**kwargs)


def test_granted_when_all_preconditions_met() -> None:
    decision = _call()
    assert decision.outcome == CoursePassOutcome.GRANTED
    assert decision.should_add_role is True


def test_wrong_guild_short_circuits_before_role_checks() -> None:
    decision = _call(interaction_guild_id=999, role_exists=False)
    assert decision.outcome == CoursePassOutcome.WRONG_GUILD
    assert decision.should_add_role is False


def test_member_unresolved() -> None:
    decision = _call(member_is_resolved=False)
    assert decision.outcome == CoursePassOutcome.MEMBER_UNRESOLVED
    assert decision.should_add_role is False


def test_role_not_found() -> None:
    decision = _call(role_exists=False)
    assert decision.outcome == CoursePassOutcome.ROLE_NOT_FOUND
    assert decision.should_add_role is False


def test_bot_member_unresolved() -> None:
    decision = _call(bot_member_resolved=False)
    assert decision.outcome == CoursePassOutcome.MEMBER_UNRESOLVED
    assert decision.should_add_role is False


def test_missing_manage_roles() -> None:
    decision = _call(bot_has_manage_roles=False)
    assert decision.outcome == CoursePassOutcome.MISSING_MANAGE_ROLES
    assert decision.should_add_role is False


def test_hierarchy_blocked_when_bot_equal_position() -> None:
    decision = _call(bot_top_role_position=5, role_position=5)
    assert decision.outcome == CoursePassOutcome.HIERARCHY_BLOCKED
    assert decision.should_add_role is False


def test_hierarchy_blocked_when_bot_below_role() -> None:
    decision = _call(bot_top_role_position=3, role_position=5)
    assert decision.outcome == CoursePassOutcome.HIERARCHY_BLOCKED


def test_already_has_role_does_not_reassign() -> None:
    decision = _call(member_has_role=True)
    assert decision.outcome == CoursePassOutcome.ALREADY_HAS_ROLE
    assert decision.should_add_role is False


def test_load_config_parses_course_pass_section(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "token-xyz")
    config_path = tmp_path / "svc.json"
    data_dir = tmp_path / "runtime"
    config_path.write_text(
        """
{
  "tokenEnv": "DISCORD_TOKEN",
  "guild_id": 1,
  "log_channel_id": 2,
  "roles": {"general_role_id": 3, "review_role_id": 4},
  "coursePass": {
    "roleId": 5,
    "inviteCodes": ["git-course"],
    "panelChannelId": 6,
    "panelMessageId": 7,
    "customId": "course_pass_role:claim:2026-04",
    "buttonLabel": "Course Passを受け取る"
  },
  "dataDir": "%s"
}
"""
        % str(data_dir),
        encoding="utf-8",
    )

    cfg = load_config(config_path)
    assert cfg.course_pass is not None
    assert cfg.course_pass.role_id == 5
    assert cfg.course_pass.panel_message_id == 7
    assert cfg.course_pass.custom_id == "course_pass_role:claim:2026-04"
    assert cfg.course_pass.invite_codes == {"git-course"}
    assert cfg.course_pass.button_label == "Course Passを受け取る"
    assert cfg.course_pass.grant_general_role is False


def test_load_config_parses_course_pass_general_role_grant(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "token-xyz")
    config_path = tmp_path / "svc.json"
    data_dir = tmp_path / "runtime"
    config_path.write_text(
        """
{
  "tokenEnv": "DISCORD_TOKEN",
  "guild_id": 1,
  "log_channel_id": 2,
  "roles": {"general_role_id": 3, "review_role_id": 4},
  "coursePass": {
    "roleId": 5,
    "panelChannelId": 6,
    "panelMessageId": 7,
    "customId": "course_pass_role:claim:2026-04",
    "grantGeneralRole": true
  },
  "dataDir": "%s"
}
"""
        % str(data_dir),
        encoding="utf-8",
    )

    cfg = load_config(config_path)
    assert cfg.course_pass is not None
    assert cfg.course_pass.grant_general_role is True


def test_load_config_without_course_pass_leaves_it_none(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "token-xyz")
    config_path = tmp_path / "svc.json"
    data_dir = tmp_path / "runtime"
    config_path.write_text(
        """
{
  "tokenEnv": "DISCORD_TOKEN",
  "guild_id": 1,
  "log_channel_id": 2,
  "roles": {"general_role_id": 3, "review_role_id": 4},
  "dataDir": "%s"
}
"""
        % str(data_dir),
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    assert cfg.course_pass is None
