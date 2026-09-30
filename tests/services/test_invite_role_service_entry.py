import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import httpx
import pytest

from src.services.invite_role_service.config import (
    EntryConfig,
    InviteRoleServiceConfig,
    WelcomeConfig,
    load_config,
)
from src.services.invite_role_service.entry import (
    ENTRY_COURSE_CLAIM_CUSTOM_ID,
    ENTRY_STATUS_CUSTOM_ID,
    ENTRY_SUPPORT_CUSTOM_ID,
    MESSAGE_NOT_READY,
    MESSAGE_SUPPORT_FAILED,
    MESSAGE_SUPPORT_SENT,
    MESSAGE_TEMPORARY_FAILURE,
    MESSAGE_WRONG_GUILD,
    BackendResult,
    EntryBackendClient,
    course_claim_message,
    format_access_date,
    parse_backend_response,
    status_message,
    support_request_message,
)
from src.services.invite_role_service.entry_panel import EntryPanelView
from src.services.invite_role_service.service import InviteRoleService

TOKEN_ENV = "TEST_DISCORD_ENTRY_API_TOKEN"
SECRET_TOKEN = "entry-secret-token-value"
PASSPHRASE = "orange-river-42"


# --- config -----------------------------------------------------------------


def _write_config(tmp_path: Path, extra: dict) -> Path:
    raw = {"tokenEnv": "DISCORD_TOKEN", "guild_id": 1, "dataDir": str(tmp_path / "rt")}
    raw.update(extra)
    path = tmp_path / "svc.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def test_entry_block_missing_turns_feature_off(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "token-xyz")
    cfg = load_config(_write_config(tmp_path, {}))
    assert cfg.entry is None


def test_entry_block_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "token-xyz")
    cfg = load_config(_write_config(tmp_path, {"entry": {"channelId": 0}}))
    assert cfg.entry == EntryConfig()
    assert cfg.entry.channel_id is None  # 0 is a placeholder
    assert cfg.entry.link_url == "https://agi-labo.com/settings"
    assert cfg.entry.backend_base_url == "https://api.agi-labo.com"
    assert cfg.entry.backend_token_env == "DISCORD_ENTRY_API_TOKEN"
    assert cfg.entry.request_timeout_seconds == 10


def test_entry_block_full(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "token-xyz")
    cfg = load_config(
        _write_config(
            tmp_path,
            {
                "entry": {
                    "channelId": "20",
                    "panelMessageId": 21,
                    "linkUrl": "https://example.com/link",
                    "backendBaseUrl": "https://api.example.com/",
                    "backendTokenEnv": TOKEN_ENV,
                    "supportChannelId": 22,
                }
            },
        )
    )
    assert cfg.entry is not None
    assert cfg.entry.channel_id == 20
    assert cfg.entry.panel_message_id == 21
    assert cfg.entry.link_url == "https://example.com/link"
    assert cfg.entry.backend_base_url == "https://api.example.com"
    assert cfg.entry.backend_token_env == TOKEN_ENV
    assert cfg.entry.support_channel_id == 22


def test_entry_block_rejects_non_https_backend(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "token-xyz")
    with pytest.raises(ValueError, match="backendBaseUrl"):
        load_config(
            _write_config(tmp_path, {"entry": {"backendBaseUrl": "http://api"}})
        )


def test_example_config_loads(monkeypatch) -> None:
    monkeypatch.setenv("INVITE_ROLE_DISCORD_TOKEN", "token-xyz")
    cfg = load_config(
        Path(__file__).resolve().parents[2]
        / "operations"
        / "invite_role_service.config.example.json"
    )
    assert cfg.entry is not None
    assert cfg.ignored_legacy_keys == ()


# --- message mapping (pure) -------------------------------------------------


def _ok(data: dict) -> BackendResult:
    return parse_backend_response(200, {"success": True, "data": data})


def _err(status: int, error: str, data: dict | None = None) -> BackendResult:
    body: dict = {"success": False, "error": error}
    if data is not None:
        body["data"] = data
    return parse_backend_response(status, body)


def test_format_access_date() -> None:
    assert format_access_date("2026-10-12") == "10/12 (月)"
    assert format_access_date("2026-10-11T00:00:00Z") == "10/11 (日)"
    assert format_access_date("bad") is None
    assert format_access_date(None) is None


def test_course_claim_success_message() -> None:
    message = course_claim_message(
        _ok(
            {
                "course_name": "Git講座",
                "access_until_date": "2026-10-12",
                "expires_at": "2026-10-13T00:00:00Z",
                "already_claimed": False,
            }
        )
    )
    assert "「Git講座」の参加を受け付けました" in message
    assert "10/12 (月) まで開けました" in message
    assert "翌朝に閉じます" in message


def test_course_claim_already_claimed_message() -> None:
    message = course_claim_message(
        _ok(
            {
                "course_name": "Git講座",
                "access_until_date": "2026-10-12",
                "already_claimed": True,
            }
        )
    )
    assert "すでに受け付けています" in message
    assert "10/12 (月) まで" in message


@pytest.mark.parametrize(
    ("status", "error", "expected"),
    [
        (
            404,
            "passphrase_not_found",
            "合言葉が違うようです。Luma の申込確認メールかリマインド",
        ),
        (409, "grant_revoked", "運営にお問い合わせください"),
        (409, "already_member", "会員の方は合言葉なしでご参加いただけます"),
        (400, "invalid_request", "もう一度お試しください"),
        (422, "not_in_guild", "サーバーに参加した状態で"),
        (502, "role_apply_failed", "少し時間をおいて"),
    ],
)
def test_course_claim_error_messages(status: int, error: str, expected: str) -> None:
    assert expected in course_claim_message(_err(status, error))


def test_course_claim_rate_limit_rounds_up_minutes() -> None:
    message = course_claim_message(
        _err(429, "too_many_attempts", {"retry_after_seconds": 61})
    )
    assert "2 分ほど" in message


@pytest.mark.parametrize(
    "result",
    [
        _err(401, "unauthorized"),
        parse_backend_response(500, None),
        parse_backend_response(503, {"success": False, "error": "internal"}),
        BackendResult(status_code=None, failure="timeout"),
    ],
)
def test_course_claim_backend_failure_is_polite(result: BackendResult) -> None:
    assert course_claim_message(result) == MESSAGE_TEMPORARY_FAILURE


def test_missing_token_says_not_ready() -> None:
    result = BackendResult(status_code=None, failure="token_missing")
    assert course_claim_message(result) == MESSAGE_NOT_READY
    assert status_message(result) == MESSAGE_NOT_READY


def test_status_member_linked() -> None:
    message = status_message(
        _ok({"member_active": True, "linked": True, "grant": None})
    )
    assert "会員として参加中です" in message
    assert "Discord 連携" in message


def test_status_with_grant() -> None:
    message = status_message(
        _ok(
            {
                "member_active": False,
                "linked": False,
                "grant": {
                    "course_name": "Git講座",
                    "access_until_date": "2026-10-12",
                    "expires_at": "2026-10-13T00:00:00Z",
                },
            }
        )
    )
    assert message == "「Git講座」の参加許可: 10/12 (月) まで（翌朝に閉じます）。"


def test_status_neither_member_nor_grant() -> None:
    message = status_message(
        _ok({"member_active": False, "linked": False, "grant": None})
    )
    assert "会員の方は「Discord 連携」を押してください" in message
    assert "「講座に参加」から合言葉を入力してください" in message


def test_status_active_but_not_linked_asks_to_link() -> None:
    message = status_message(_ok({"member_active": True, "linked": False}))
    assert "連携がまだです" in message


def test_status_failure_is_polite() -> None:
    assert (
        status_message(parse_backend_response(500, None)) == MESSAGE_TEMPORARY_FAILURE
    )


def test_support_request_message_contains_requester_and_text() -> None:
    message = support_request_message(
        user_id=123,
        user_mention="<@123>",
        username="nobu",
        text="  note で入会しました  ",
    )
    assert "<@123>" in message
    assert "ID: 123" in message
    assert "note で入会しました" in message
    long_message = support_request_message(
        user_id=1, user_mention="<@1>", username="x", text="あ" * 5000
    )
    assert len(long_message) <= 1900


# --- HTTP client (mocked transport, no network) ------------------------------


def _entry(**overrides) -> EntryConfig:
    values = dict(
        channel_id=20,
        backend_base_url="https://api.example.test",
        backend_token_env=TOKEN_ENV,
        support_channel_id=22,
    )
    values.update(overrides)
    return EntryConfig(**values)


def test_client_claim_sends_contract_request(monkeypatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, SECRET_TOKEN)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "course_name": "Git講座",
                    "access_until_date": "2026-10-12",
                    "already_claimed": False,
                },
            },
        )

    client = EntryBackendClient(_entry(), transport=httpx.MockTransport(handler))
    result = asyncio.run(
        client.claim_course(discord_user_id=123, passphrase=PASSPHRASE)
    )

    assert result.success is True
    assert result.data["course_name"] == "Git講座"
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == (
        "https://api.example.test/internal/discord-entry/course-claims"
    )
    assert request.headers["Authorization"] == f"Bearer {SECRET_TOKEN}"
    assert json.loads(request.content) == {
        "discord_user_id": "123",
        "passphrase": PASSPHRASE,
    }


def test_client_status_uses_get_path(monkeypatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, SECRET_TOKEN)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"success": True, "data": {"member_active": True, "linked": True}},
        )

    client = EntryBackendClient(_entry(), transport=httpx.MockTransport(handler))
    result = asyncio.run(client.get_status(discord_user_id=123))

    assert result.success is True
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/internal/discord-entry/status/123"


def test_client_without_token_does_not_call_backend(monkeypatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    handler = MagicMock()
    client = EntryBackendClient(_entry(), transport=httpx.MockTransport(handler))

    result = asyncio.run(client.get_status(discord_user_id=123))

    handler.assert_not_called()
    assert result.failure == "token_missing"
    assert client.is_configured() is False


def test_client_timeout_and_non_json(monkeypatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, SECRET_TOKEN)

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    result = asyncio.run(
        EntryBackendClient(
            _entry(), transport=httpx.MockTransport(timeout_handler)
        ).get_status(discord_user_id=1)
    )
    assert result.status_code is None
    assert result.failure == "timeout"

    result = asyncio.run(
        EntryBackendClient(
            _entry(),
            transport=httpx.MockTransport(
                lambda request: httpx.Response(502, text="<html>")
            ),
        ).get_status(discord_user_id=1)
    )
    assert result.status_code == 502
    assert result.success is False
    assert SECRET_TOKEN not in result.log_label


# --- service handlers (mocked Discord) ---------------------------------------


def _service(tmp_path: Path, *, entry: EntryConfig | None = None) -> InviteRoleService:
    return InviteRoleService(
        InviteRoleServiceConfig(
            discord_token="token-123",
            guild_id=1,
            general_role_id=2,
            review_role_id=None,
            log_channel_id=None,
            data_dir=tmp_path,
            member_log_path=tmp_path / "member_log.csv",
            heartbeat_path=tmp_path / "heartbeat.json",
            service_log_path=tmp_path / "service.log",
            baseline_path=tmp_path / "invite_baseline.json",
            welcome=WelcomeConfig(
                enabled=True,
                channel_id=10,
                intro_channel_id=11,
                state_path=tmp_path / "welcome_state.json",
                openclaw_cwd=tmp_path,
                retry_delay_seconds=0,
            ),
            entry=entry if entry is not None else _entry(),
        )
    )


def _interaction(guild_id: int = 1):
    is_done = MagicMock(return_value=False)

    async def defer(**kwargs) -> None:
        is_done.return_value = True

    response = SimpleNamespace(
        is_done=is_done,
        defer=AsyncMock(side_effect=defer),
        send_message=AsyncMock(),
        send_modal=AsyncMock(),
    )
    return SimpleNamespace(
        guild=SimpleNamespace(id=guild_id),
        user=SimpleNamespace(id=123, mention="<@123>", __str__=lambda self: "nobu"),
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
    )


def _replies(interaction) -> list[str]:
    texts = [c.args[0] for c in interaction.response.send_message.await_args_list]
    texts += [c.args[0] for c in interaction.followup.send.await_args_list]
    return texts


def test_course_claim_handler_relays_and_never_logs_passphrase(
    tmp_path: Path, caplog
) -> None:
    service = _service(tmp_path)
    logged: list[str] = []
    service._log = AsyncMock(side_effect=lambda text: logged.append(text))  # type: ignore[method-assign] # noqa: SLF001
    service.entry_backend = SimpleNamespace(  # type: ignore[assignment]
        claim_course=AsyncMock(return_value=_err(404, "passphrase_not_found"))
    )
    interaction = _interaction()

    with caplog.at_level(logging.DEBUG):
        asyncio.run(service.handle_entry_course_claim(interaction, f"  {PASSPHRASE} "))

    service.entry_backend.claim_course.assert_awaited_once_with(
        discord_user_id=123, passphrase=PASSPHRASE
    )
    interaction.response.defer.assert_awaited_once()
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    assert "合言葉が違うようです" in _replies(interaction)[0]
    assert logged and "passphrase_not_found" in logged[0]
    assert all(PASSPHRASE not in line for line in logged)
    assert PASSPHRASE not in caplog.text
    assert PASSPHRASE not in (tmp_path / "service.log").read_text(encoding="utf-8")


def test_course_claim_handler_rejects_empty_passphrase(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.entry_backend = SimpleNamespace(claim_course=AsyncMock())  # type: ignore[assignment]
    interaction = _interaction()

    asyncio.run(service.handle_entry_course_claim(interaction, "   "))

    service.entry_backend.claim_course.assert_not_awaited()
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


def test_handlers_reject_other_guilds(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.entry_backend = SimpleNamespace(get_status=AsyncMock())  # type: ignore[assignment]
    interaction = _interaction(guild_id=999)

    asyncio.run(service.handle_entry_status(interaction))

    service.entry_backend.get_status.assert_not_awaited()
    assert _replies(interaction) == [MESSAGE_WRONG_GUILD]


def test_status_handler_replies_ephemeral(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._log = AsyncMock()  # type: ignore[method-assign] # noqa: SLF001
    service.entry_backend = SimpleNamespace(  # type: ignore[assignment]
        get_status=AsyncMock(
            return_value=_ok({"member_active": True, "linked": True, "grant": None})
        )
    )
    interaction = _interaction()

    asyncio.run(service.handle_entry_status(interaction))

    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    assert "会員として参加中です" in _replies(interaction)[0]


def test_support_handler_posts_to_ops_channel_without_logging_text(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    logged: list[str] = []
    service._log = AsyncMock(side_effect=lambda text: logged.append(text))  # type: ignore[method-assign] # noqa: SLF001
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    service.get_channel = MagicMock(return_value=channel)  # type: ignore[method-assign]
    interaction = _interaction()
    text = "note で入会しましたが連携できません。連絡先 test@example.com"

    asyncio.run(service.handle_entry_support(interaction, text))

    service.get_channel.assert_called_once_with(22)
    posted = channel.send.await_args
    assert "<@123>" in posted.args[0]
    assert text in posted.args[0]
    mentions = posted.kwargs["allowed_mentions"]
    assert (
        mentions.everyone is False
        and mentions.users is False
        and mentions.roles is False
    )
    assert _replies(interaction) == [MESSAGE_SUPPORT_SENT]
    assert all("test@example.com" not in line for line in logged)


def test_support_handler_without_channel_replies_politely(tmp_path: Path) -> None:
    service = _service(tmp_path, entry=_entry(support_channel_id=None))
    service._log = AsyncMock()  # type: ignore[method-assign] # noqa: SLF001
    interaction = _interaction()

    asyncio.run(service.handle_entry_support(interaction, "help"))

    assert _replies(interaction) == [MESSAGE_SUPPORT_FAILED]


def test_support_handler_send_failure_replies_politely(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._log = AsyncMock()  # type: ignore[method-assign] # noqa: SLF001
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock(side_effect=RuntimeError("boom"))
    service.get_channel = MagicMock(return_value=channel)  # type: ignore[method-assign]
    interaction = _interaction()

    asyncio.run(service.handle_entry_support(interaction, "help"))

    assert _replies(interaction) == [MESSAGE_SUPPORT_FAILED]


# --- panel view / welcome routing --------------------------------------------


def test_entry_panel_view_has_fixed_custom_ids_and_link() -> None:
    async def build() -> EntryPanelView:
        return EntryPanelView(_entry(link_url="https://agi-labo.com/settings"), None)

    view = asyncio.run(build())

    assert view.timeout is None
    assert view.is_persistent()
    custom_ids = [item.custom_id for item in view.children if item.custom_id]
    assert custom_ids == [
        ENTRY_COURSE_CLAIM_CUSTOM_ID,
        ENTRY_STATUS_CUSTOM_ID,
        ENTRY_SUPPORT_CUSTOM_ID,
    ]
    links = [item for item in view.children if getattr(item, "url", None)]
    assert [(item.label, item.url) for item in links] == [
        ("Discord 連携", "https://agi-labo.com/settings")
    ]


def test_welcome_goes_to_entry_channel_when_configured(tmp_path: Path) -> None:
    service = _service(tmp_path)
    member = SimpleNamespace(id=123)

    assert service._welcome_channel_id() == 20  # noqa: SLF001
    prompt = service._build_welcome_agent_prompt(  # noqa: SLF001
        member=member, invite_type="general", role_names="none"
    )
    assert "<#20>" in prompt
    assert "講座に参加" in prompt


def test_welcome_falls_back_to_welcome_channel_without_entry_channel(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, entry=_entry(channel_id=None))
    assert service._welcome_channel_id() == 10  # noqa: SLF001
