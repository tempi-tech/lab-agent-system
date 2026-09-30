import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.services.invite_role_service.config import InviteRoleServiceConfig
from src.services.invite_role_service.join import plan_join_role_ids
from src.services.invite_role_service.service import InviteRoleService

GENERAL_ROLE_ID = 2
REVIEW_ROLE_ID = 3


def _config(tmp_path: Path, **overrides) -> InviteRoleServiceConfig:
    values = dict(
        discord_token="token-123",
        guild_id=1,
        general_role_id=GENERAL_ROLE_ID,
        review_role_id=REVIEW_ROLE_ID,
        log_channel_id=None,
        data_dir=tmp_path,
        member_log_path=tmp_path / "member_log.csv",
        heartbeat_path=tmp_path / "heartbeat.json",
        service_log_path=tmp_path / "service.log",
        baseline_path=tmp_path / "invite_baseline.json",
        excluded_invite_codes={"excluded1"},
    )
    values.update(overrides)
    return InviteRoleServiceConfig(**values)


def test_plan_join_roles_never_contains_general_role() -> None:
    for code in ("abc", "legacy-code", None):
        role_ids = plan_join_role_ids(used_code=code, review_role_id=REVIEW_ROLE_ID)
        assert GENERAL_ROLE_ID not in role_ids


def test_plan_join_roles_adds_review_role_only_when_detection_failed() -> None:
    assert plan_join_role_ids(used_code=None, review_role_id=REVIEW_ROLE_ID) == [
        REVIEW_ROLE_ID
    ]
    assert plan_join_role_ids(used_code="abc", review_role_id=REVIEW_ROLE_ID) == []
    assert plan_join_role_ids(used_code=None, review_role_id=None) == []


def _service(tmp_path: Path, used_code: str | None):
    service = InviteRoleService(_config(tmp_path))
    roles = {
        GENERAL_ROLE_ID: SimpleNamespace(id=GENERAL_ROLE_ID, name="agi-lab"),
        REVIEW_ROLE_ID: SimpleNamespace(id=REVIEW_ROLE_ID, name="要確認"),
    }
    guild = SimpleNamespace(id=1, get_role=lambda role_id: roles.get(role_id))
    service._detect_used_invite_code = AsyncMock(  # type: ignore[method-assign] # noqa: SLF001
        return_value=(used_code, "changes=none")
    )
    service._refresh_invites_baseline = AsyncMock()  # type: ignore[method-assign] # noqa: SLF001
    service._write_heartbeat = AsyncMock()  # type: ignore[method-assign] # noqa: SLF001
    service._log = AsyncMock()  # type: ignore[method-assign] # noqa: SLF001
    service._send_welcome_if_enabled = AsyncMock()  # type: ignore[method-assign] # noqa: SLF001
    member = SimpleNamespace(id=10, guild=guild, add_roles=AsyncMock())
    return service, member, roles


def test_join_with_known_invite_adds_no_roles_and_still_welcomes(
    tmp_path: Path,
) -> None:
    service, member, _ = _service(tmp_path, "abc")

    asyncio.run(service.on_member_join(member))

    member.add_roles.assert_not_awaited()
    service._send_welcome_if_enabled.assert_awaited_once()  # type: ignore[attr-defined] # noqa: SLF001
    kwargs = service._send_welcome_if_enabled.await_args.kwargs  # type: ignore[attr-defined] # noqa: SLF001
    assert kwargs["role_names"] == "none"
    assert kwargs["invite_type"] == "general"
    log_text = (tmp_path / "member_log.csv").read_text(encoding="utf-8")
    assert "agi-lab" not in log_text
    assert ",abc," in log_text


def test_join_with_unknown_invite_adds_only_review_role(tmp_path: Path) -> None:
    service, member, roles = _service(tmp_path, None)

    asyncio.run(service.on_member_join(member))

    member.add_roles.assert_awaited_once()
    assert list(member.add_roles.await_args.args) == [roles[REVIEW_ROLE_ID]]
    service._send_welcome_if_enabled.assert_awaited_once()  # type: ignore[attr-defined] # noqa: SLF001


def test_welcome_continues_when_review_role_assignment_fails(tmp_path: Path) -> None:
    service, member, _ = _service(tmp_path, None)
    member.add_roles.side_effect = RuntimeError("forbidden")

    asyncio.run(service.on_member_join(member))

    service._send_welcome_if_enabled.assert_awaited_once()  # type: ignore[attr-defined] # noqa: SLF001
    kwargs = service._send_welcome_if_enabled.await_args.kwargs  # type: ignore[attr-defined] # noqa: SLF001
    assert kwargs["role_names"] == "none"
    assert "detection_failed" in (tmp_path / "member_log.csv").read_text(
        encoding="utf-8"
    )


def test_excluded_invite_is_logged_without_roles_or_welcome(tmp_path: Path) -> None:
    service, member, _ = _service(tmp_path, "excluded1")

    asyncio.run(service.on_member_join(member))

    member.add_roles.assert_not_awaited()
    service._send_welcome_if_enabled.assert_not_awaited()  # type: ignore[attr-defined] # noqa: SLF001
    assert "none(excluded)" in (tmp_path / "member_log.csv").read_text(
        encoding="utf-8"
    )


def test_general_role_id_is_optional_in_config(tmp_path: Path, monkeypatch) -> None:
    from src.services.invite_role_service.config import load_config

    monkeypatch.setenv("DISCORD_TOKEN", "token-xyz")
    config_path = tmp_path / "svc.json"
    config_path.write_text(
        '{"tokenEnv": "DISCORD_TOKEN", "guild_id": 1, "dataDir": "%s"}'
        % str(tmp_path / "runtime"),
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    assert cfg.general_role_id is None
