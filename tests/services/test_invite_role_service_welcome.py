import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from src.services.invite_role_service.config import (
    InviteRoleServiceConfig,
    WelcomeConfig,
    WelcomeFollowupConfig,
)
from src.services.invite_role_service.service import (
    InviteRoleService,
    WelcomeDeliveryError,
)


def _config(tmp_path: Path) -> InviteRoleServiceConfig:
    return InviteRoleServiceConfig(
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
            agent_id="labochan-public",
            thinking="low",
            state_path=tmp_path / "welcome_state.json",
            openclaw_bin="openclaw",
            openclaw_cwd=tmp_path,
            timeout_seconds=5,
            retry_delay_seconds=0,
        ),
    )


def _followup_config(tmp_path: Path) -> InviteRoleServiceConfig:
    base = _config(tmp_path)
    assert base.welcome is not None
    return InviteRoleServiceConfig(
        **{
            **base.__dict__,
            "welcome": WelcomeConfig(
                **{
                    **base.welcome.__dict__,
                    "guide_channel_id": 12,
                    "threaded": True,
                    "followup": WelcomeFollowupConfig(
                        enabled=True,
                        delay_seconds=60,
                        check_interval_seconds=60,
                        discrawl_db_path=tmp_path / "discrawl.db",
                        archive_max_age_seconds=3600,
                    ),
                }
            ),
        }
    )


def _seed_welcomed(
    service: InviteRoleService,
    *,
    member_id: int = 123,
    thread_id: int = 99,
    age_seconds: int = 120,
) -> None:
    assert service.welcome_state is not None
    service.welcome_state.write(
        {
            "welcomed": {
                str(member_id): {
                    "welcomedAt": (
                        datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
                    ).isoformat(),
                    "username": "member",
                    "inviteType": "general",
                    "roles": "agi-lab",
                    "channelId": 10,
                    "threadId": thread_id,
                }
            },
            "pending": {},
            "followups": {},
        }
    )


def test_welcome_message_mentions_member_and_intro_channel(tmp_path: Path) -> None:
    service = InviteRoleService(_config(tmp_path))
    member = SimpleNamespace(id=123, bot=False, __str__=lambda: "user")

    message = service._build_welcome_agent_prompt(
        member=member, invite_type="general", role_names="agi-lab"
    )  # noqa: SLF001

    assert "<@123>" in message
    assert "<#11>" in message
    assert "Discord文脈検索" in message
    assert "最終出力はそのままDiscordに投稿される本文だけ" in message
    assert "見つかった事実だけを使い" in message
    assert "自己紹介済みだと分かったら、自己紹介を促さない" in message
    assert "未済と断定せず" in message
    assert "対象者以外のユーザー、ロール、@everyone、@hereをmentionしない" in message
    assert "全体案内と自己紹介の2チャンネルは必ず実リンクで案内" in message
    assert "直近24時間" in message
    assert "3〜6文程度" in message
    assert "join判定" not in message
    assert "付与ロール" not in message
    assert "Discord user id" not in message


def test_followup_prompt_keeps_low_pressure_channel_guidance(tmp_path: Path) -> None:
    service = InviteRoleService(_followup_config(tmp_path))
    member = SimpleNamespace(id=123)

    message = service._build_followup_agent_prompt(  # noqa: SLF001
        member=member, destination_channel_id=99
    )

    assert "<@123>" in message
    assert "<#11>" in message
    assert "<#12>" in message
    assert "軽い生意気さ" in message
    assert "閲覧だけでも歓迎" in message
    assert "1〜3文" in message
    assert "罪悪感" in message


def test_discrawl_intro_lookup_includes_public_forum_threads(tmp_path: Path) -> None:
    config = _followup_config(tmp_path)
    service = InviteRoleService(config)
    assert config.welcome is not None and config.welcome.followup is not None
    db_path = config.welcome.followup.discrawl_db_path
    with sqlite3.connect(db_path) as connection:
        connection.executescript("""
            CREATE TABLE channels (
                id TEXT PRIMARY KEY,
                parent_id TEXT,
                thread_parent_id TEXT,
                is_private_thread INTEGER
            );
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                author_id TEXT,
                deleted_at TEXT
            );
            """)
        connection.execute(
            "INSERT INTO channels VALUES (?, ?, ?, 0)",
            ("77", "11", "11"),
        )
        connection.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?, NULL)",
            ("88", "1", "77", "123"),
        )

    assert service._discrawl_member_has_intro(123) is True  # noqa: SLF001
    assert service._discrawl_member_has_intro(456) is False  # noqa: SLF001


def test_welcome_send_is_deduplicated(tmp_path: Path) -> None:
    service = InviteRoleService(_config(tmp_path))
    member = SimpleNamespace(id=123, bot=False, __str__=lambda: "user")
    sent: list[str] = []
    logged: list[str] = []

    async def fake_destination(
        *, member: object, invite_type: str, role_names: str
    ) -> int:
        return 12

    async def fake_send(
        *, prompt: str, member_id: int, destination_channel_id: int
    ) -> None:
        sent.append(f"{member_id}:{destination_channel_id}:{prompt}")

    async def fake_log(message: str) -> None:
        logged.append(message)

    service._create_welcome_destination = fake_destination  # type: ignore[method-assign] # noqa: SLF001
    service._run_openclaw_agent_welcome = fake_send  # type: ignore[method-assign] # noqa: SLF001
    service._log = fake_log  # type: ignore[method-assign] # noqa: SLF001

    asyncio.run(
        service._send_welcome_if_enabled(
            member=member, invite_type="general", role_names="agi-lab"
        )
    )  # noqa: SLF001
    asyncio.run(
        service._send_welcome_if_enabled(
            member=member, invite_type="general", role_names="agi-lab"
        )
    )  # noqa: SLF001

    assert len(sent) == 1
    assert ":12:" in sent[0]
    assert "welcome sent member=123" in logged[0]
    assert "already_sent" in logged[1]


def test_threaded_welcome_creates_starter_and_public_thread(tmp_path: Path) -> None:
    base = _config(tmp_path)
    threaded_welcome = WelcomeConfig(
        **{
            **base.welcome.__dict__,
            "threaded": True,
            "thread_auto_archive_minutes": 1440,
        }
    )
    service = InviteRoleService(
        InviteRoleServiceConfig(
            **{
                **base.__dict__,
                "welcome": threaded_welcome,
            }
        )
    )
    thread = SimpleNamespace(id=99)
    starter = SimpleNamespace(id=55, create_thread=AsyncMock(return_value=thread))
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock(return_value=starter)
    service.get_channel = MagicMock(return_value=channel)  # type: ignore[method-assign]
    member = SimpleNamespace(
        id=123,
        mention="<@123>",
        display_name="nobu",
        bot=False,
    )

    destination_id = asyncio.run(
        service._create_welcome_destination(member=member)
    )  # noqa: SLF001

    assert destination_id == 99
    channel.send.assert_awaited_once()
    send_args, send_kwargs = channel.send.await_args
    assert send_args == ("👋 <@123>センパイ、AGIラボへようこそ！",)
    allowed_mentions = send_kwargs["allowed_mentions"]
    assert allowed_mentions.everyone is False
    assert allowed_mentions.roles is False
    assert allowed_mentions.users == [member]
    starter.create_thread.assert_awaited_once_with(
        name="👋-nobuセンパイ、ようこそ！",
        auto_archive_duration=1440,
        reason="新規メンバーのオンボーディング",
    )


def test_welcome_thread_name_normalizes_untrusted_display_name(tmp_path: Path) -> None:
    service = InviteRoleService(_config(tmp_path))
    member = SimpleNamespace(
        id=123,
        display_name="  long\n\x00\u202e   name  " + "x" * 200,
    )

    name = service._welcome_thread_name(member)  # noqa: SLF001

    assert name.startswith("👋-long name ")
    assert name.endswith("センパイ、ようこそ！")
    assert "\n" not in name
    assert "\x00" not in name
    assert "\u202e" not in name
    assert len(name) == 100


def test_thread_creation_retry_reuses_starter_message(tmp_path: Path) -> None:
    base = _config(tmp_path)
    service = InviteRoleService(
        InviteRoleServiceConfig(
            **{
                **base.__dict__,
                "welcome": WelcomeConfig(**{**base.welcome.__dict__, "threaded": True}),
            }
        )
    )
    thread = SimpleNamespace(id=99)
    starter = SimpleNamespace(
        id=55,
        create_thread=AsyncMock(side_effect=[RuntimeError("transient"), thread]),
    )
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock(return_value=starter)
    channel.fetch_message = AsyncMock(return_value=starter)
    service.get_channel = MagicMock(return_value=channel)  # type: ignore[method-assign]
    member = SimpleNamespace(
        id=123,
        mention="<@123>",
        display_name="nobu",
        bot=False,
    )

    with pytest.raises(RuntimeError, match="transient"):
        asyncio.run(service._create_welcome_destination(member=member))  # noqa: SLF001
    destination_id = asyncio.run(
        service._create_welcome_destination(member=member)
    )  # noqa: SLF001

    assert destination_id == 99
    channel.send.assert_awaited_once()
    channel.fetch_message.assert_awaited_once_with(55)
    assert starter.create_thread.await_count == 2


def test_retry_safe_delivery_failure_retries_same_destination(tmp_path: Path) -> None:
    service = InviteRoleService(_config(tmp_path))
    member = SimpleNamespace(id=123, bot=False)
    destinations: list[int] = []
    attempts = 0

    async def fake_destination(
        *, member: object, invite_type: str, role_names: str
    ) -> int:
        destinations.append(12)
        return 12

    async def fake_send(
        *, prompt: str, member_id: int, destination_channel_id: int
    ) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise WelcomeDeliveryError("not delivered", retry_safe=True)

    service._create_welcome_destination = fake_destination  # type: ignore[method-assign] # noqa: SLF001
    service._run_openclaw_agent_welcome = fake_send  # type: ignore[method-assign] # noqa: SLF001
    service._log = AsyncMock()  # type: ignore[method-assign] # noqa: SLF001
    service._welcome_message_exists_since = AsyncMock(return_value=False)  # type: ignore[method-assign] # noqa: SLF001

    asyncio.run(
        service._send_welcome_if_enabled(
            member=member, invite_type="general", role_names="agi-lab"
        )
    )  # noqa: SLF001

    assert destinations == [12]
    assert attempts == 2
    assert service._welcome_record_for(123) is not None  # noqa: SLF001
    assert service._pending_welcome_for(123) is None  # noqa: SLF001


def test_missing_ack_is_reconciled_without_duplicate_retry(tmp_path: Path) -> None:
    service = InviteRoleService(_config(tmp_path))
    member = SimpleNamespace(id=123, bot=False)
    attempts = 0

    async def fake_destination(
        *, member: object, invite_type: str, role_names: str
    ) -> int:
        return 12

    async def fake_send(
        *, prompt: str, member_id: int, destination_channel_id: int
    ) -> None:
        nonlocal attempts
        attempts += 1
        raise WelcomeDeliveryError("missing ack", retry_safe=True)

    service._create_welcome_destination = fake_destination  # type: ignore[method-assign] # noqa: SLF001
    service._run_openclaw_agent_welcome = fake_send  # type: ignore[method-assign] # noqa: SLF001
    service._welcome_message_exists_since = AsyncMock(return_value=True)  # type: ignore[method-assign] # noqa: SLF001
    service._log = AsyncMock()  # type: ignore[method-assign] # noqa: SLF001

    asyncio.run(
        service._send_welcome_if_enabled(
            member=member, invite_type="general", role_names="agi-lab"
        )
    )  # noqa: SLF001

    assert attempts == 1
    assert service._welcome_record_for(123) is not None  # noqa: SLF001
    assert service._pending_welcome_for(123) is None  # noqa: SLF001


def test_uncertain_delivery_is_not_retried(tmp_path: Path) -> None:
    service = InviteRoleService(_config(tmp_path))
    member = SimpleNamespace(id=123, bot=False)
    attempts = 0

    async def fake_destination(
        *, member: object, invite_type: str, role_names: str
    ) -> int:
        return 12

    async def fake_send(
        *, prompt: str, member_id: int, destination_channel_id: int
    ) -> None:
        nonlocal attempts
        attempts += 1
        raise WelcomeDeliveryError("unknown", retry_safe=False)

    service._create_welcome_destination = fake_destination  # type: ignore[method-assign] # noqa: SLF001
    service._run_openclaw_agent_welcome = fake_send  # type: ignore[method-assign] # noqa: SLF001
    service._log = AsyncMock()  # type: ignore[method-assign] # noqa: SLF001

    asyncio.run(
        service._send_welcome_if_enabled(
            member=member, invite_type="general", role_names="agi-lab"
        )
    )  # noqa: SLF001
    asyncio.run(
        service._send_welcome_if_enabled(
            member=member, invite_type="general", role_names="agi-lab"
        )
    )  # noqa: SLF001

    assert attempts == 1
    pending = service._pending_welcome_for(123)  # noqa: SLF001
    assert pending is not None
    assert pending["status"] == "delivery_uncertain"


def test_openclaw_delivery_status_requires_confirmed_send() -> None:
    sent = b'{"deliveryStatus":{"status":"sent","resultCount":1}}'
    failed = b'{"result":{"deliveryStatus":{"status":"failed","resultCount":0}}}'

    assert InviteRoleService._openclaw_delivery_status(sent) == {  # noqa: SLF001
        "status": "sent",
        "resultCount": 1,
    }
    assert InviteRoleService._openclaw_delivery_status(failed) == {  # noqa: SLF001
        "status": "failed",
        "resultCount": 0,
    }
    assert (
        InviteRoleService._openclaw_delivery_status(b"not-json") is None
    )  # noqa: SLF001


def test_openclaw_runner_requires_delivery_ack(tmp_path: Path, monkeypatch) -> None:
    service = InviteRoleService(_config(tmp_path))
    command: tuple[object, ...] | None = None

    class FakeProcess:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b'{"deliveryStatus":{"status":"sent","resultCount":1}}', b""

    async def fake_subprocess(*args, **kwargs):
        nonlocal command
        command = args
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)

    asyncio.run(
        service._run_openclaw_agent_welcome(  # noqa: SLF001
            prompt="hello",
            member_id=123,
            destination_channel_id=99,
        )
    )

    assert command is not None
    assert "--timeout" in command
    assert "--deliver" in command
    assert "channel:99" in command


def test_openclaw_runner_terminates_process_on_timeout(
    tmp_path: Path, monkeypatch
) -> None:
    base = _config(tmp_path)
    service = InviteRoleService(
        InviteRoleServiceConfig(
            **{
                **base.__dict__,
                "welcome": WelcomeConfig(
                    **{**base.welcome.__dict__, "timeout_seconds": 0.01}
                ),
            }
        )
    )

    class FakeProcess:
        returncode = None
        terminated = False
        killed = False

        def __init__(self) -> None:
            self.done = asyncio.Event()

        async def communicate(self) -> tuple[bytes, bytes]:
            await self.done.wait()
            return b"", b""

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15
            self.done.set()

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9
            self.done.set()

    proc = FakeProcess()

    async def fake_subprocess(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)

    with pytest.raises(WelcomeDeliveryError, match="timed out") as caught:
        asyncio.run(
            service._run_openclaw_agent_welcome(  # noqa: SLF001
                prompt="hello",
                member_id=123,
                destination_channel_id=99,
            )
        )

    assert caught.value.retry_safe is False
    assert proc.terminated is True


def test_pending_destination_recovers_after_service_restart(
    tmp_path: Path, monkeypatch
) -> None:
    service = InviteRoleService(_config(tmp_path))
    member = SimpleNamespace(id=123, bot=False)
    service._mark_welcome_pending(  # noqa: SLF001
        member=member,
        invite_type="general",
        role_names="agi-lab",
        destination_channel_id=12,
        status="destination_created",
    )
    service._run_openclaw_agent_welcome = AsyncMock()  # type: ignore[method-assign] # noqa: SLF001
    service._log = AsyncMock()  # type: ignore[method-assign] # noqa: SLF001
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    guild = SimpleNamespace(get_member=lambda member_id: member)

    asyncio.run(service._recover_pending_welcomes(guild))  # noqa: SLF001

    service._run_openclaw_agent_welcome.assert_awaited_once()  # type: ignore[attr-defined] # noqa: SLF001
    assert service._welcome_record_for(123) is not None  # noqa: SLF001
    assert service._pending_welcome_for(123) is None  # noqa: SLF001


def test_due_followup_sends_once_and_records_completion(tmp_path: Path) -> None:
    service = InviteRoleService(_followup_config(tmp_path))
    _seed_welcomed(service)
    member = SimpleNamespace(id=123, bot=False)
    guild = SimpleNamespace(get_member=lambda member_id: member)
    service._discrawl_member_has_intro = MagicMock(return_value=False)  # type: ignore[method-assign] # noqa: SLF001
    service._run_openclaw_agent_welcome = AsyncMock()  # type: ignore[method-assign] # noqa: SLF001
    service._log = AsyncMock()  # type: ignore[method-assign] # noqa: SLF001

    asyncio.run(service._process_due_welcome_followups(guild))  # noqa: SLF001
    asyncio.run(service._process_due_welcome_followups(guild))  # noqa: SLF001

    service._run_openclaw_agent_welcome.assert_awaited_once()  # type: ignore[attr-defined] # noqa: SLF001
    call = service._run_openclaw_agent_welcome.await_args  # type: ignore[attr-defined] # noqa: SLF001
    assert call.kwargs["destination_channel_id"] == 99
    assert call.kwargs["session_scope"] == "welcome-followup"
    record = service._followup_record_for(123)  # noqa: SLF001
    assert record is not None
    assert record["status"] == "sent"
    assert record["attempts"] == 1


def test_due_followup_skips_member_with_introduction(tmp_path: Path) -> None:
    service = InviteRoleService(_followup_config(tmp_path))
    _seed_welcomed(service)
    member = SimpleNamespace(id=123, bot=False)
    guild = SimpleNamespace(get_member=lambda member_id: member)
    service._discrawl_member_has_intro = MagicMock(return_value=True)  # type: ignore[method-assign] # noqa: SLF001
    service._run_openclaw_agent_welcome = AsyncMock()  # type: ignore[method-assign] # noqa: SLF001
    service._log = AsyncMock()  # type: ignore[method-assign] # noqa: SLF001

    asyncio.run(service._process_due_welcome_followups(guild))  # noqa: SLF001

    service._run_openclaw_agent_welcome.assert_not_awaited()  # type: ignore[attr-defined] # noqa: SLF001
    record = service._followup_record_for(123)  # noqa: SLF001
    assert record is not None
    assert record["status"] == "skipped_intro_present"


def test_followup_waits_until_delay_has_elapsed(tmp_path: Path) -> None:
    service = InviteRoleService(_followup_config(tmp_path))
    _seed_welcomed(service, age_seconds=10)
    member = SimpleNamespace(id=123, bot=False)
    guild = SimpleNamespace(get_member=lambda member_id: member)
    service._discrawl_member_has_intro = MagicMock(return_value=False)  # type: ignore[method-assign] # noqa: SLF001
    service._run_openclaw_agent_welcome = AsyncMock()  # type: ignore[method-assign] # noqa: SLF001

    asyncio.run(service._process_due_welcome_followups(guild))  # noqa: SLF001

    service._run_openclaw_agent_welcome.assert_not_awaited()  # type: ignore[attr-defined] # noqa: SLF001
    assert service._followup_record_for(123) is None  # noqa: SLF001


def test_followup_defers_when_discrawl_is_unavailable(tmp_path: Path) -> None:
    service = InviteRoleService(_followup_config(tmp_path))
    _seed_welcomed(service)
    member = SimpleNamespace(id=123, bot=False)
    guild = SimpleNamespace(get_member=lambda member_id: member)
    service._discrawl_member_has_intro = MagicMock(return_value=None)  # type: ignore[method-assign] # noqa: SLF001
    service._run_openclaw_agent_welcome = AsyncMock()  # type: ignore[method-assign] # noqa: SLF001
    service._log = AsyncMock()  # type: ignore[method-assign] # noqa: SLF001

    asyncio.run(service._process_due_welcome_followups(guild))  # noqa: SLF001

    service._run_openclaw_agent_welcome.assert_not_awaited()  # type: ignore[attr-defined] # noqa: SLF001
    assert service._followup_record_for(123) is None  # noqa: SLF001


def test_existing_followup_record_prevents_duplicate_send(tmp_path: Path) -> None:
    service = InviteRoleService(_followup_config(tmp_path))
    _seed_welcomed(service)
    service._mark_followup(  # noqa: SLF001
        member_id=123, status="sent_manual", thread_id=99
    )
    member = SimpleNamespace(id=123, bot=False)
    guild = SimpleNamespace(get_member=lambda member_id: member)
    service._run_openclaw_agent_welcome = AsyncMock()  # type: ignore[method-assign] # noqa: SLF001

    asyncio.run(service._process_due_welcome_followups(guild))  # noqa: SLF001

    service._run_openclaw_agent_welcome.assert_not_awaited()  # type: ignore[attr-defined] # noqa: SLF001


def test_welcome_does_not_hold_invite_detection_lock(tmp_path: Path) -> None:
    service = InviteRoleService(_config(tmp_path))
    role = SimpleNamespace(name="agi-lab")
    guild = SimpleNamespace(id=1, get_role=lambda role_id: role)
    detection_count = 0

    async def scenario() -> None:
        first_welcome_started = asyncio.Event()
        release_first_welcome = asyncio.Event()
        second_detection_finished = asyncio.Event()

        async def fake_detect(member_guild) -> tuple[str, str]:
            nonlocal detection_count
            detection_count += 1
            if detection_count == 2:
                second_detection_finished.set()
            return "abc", "changes=abc:0->1"

        async def fake_welcome(*, member, invite_type: str, role_names: str) -> None:
            if member.id == 1:
                first_welcome_started.set()
                await release_first_welcome.wait()

        service._detect_used_invite_code = fake_detect  # type: ignore[method-assign] # noqa: SLF001
        service._send_welcome_if_enabled = fake_welcome  # type: ignore[method-assign] # noqa: SLF001
        service._write_heartbeat = AsyncMock()  # type: ignore[method-assign] # noqa: SLF001
        service._log = AsyncMock()  # type: ignore[method-assign] # noqa: SLF001
        first = SimpleNamespace(id=1, guild=guild, add_roles=AsyncMock())
        second = SimpleNamespace(id=2, guild=guild, add_roles=AsyncMock())

        first_task = asyncio.create_task(service.on_member_join(first))
        await asyncio.wait_for(first_welcome_started.wait(), timeout=1)
        second_task = asyncio.create_task(service.on_member_join(second))
        await asyncio.wait_for(second_detection_finished.wait(), timeout=1)
        release_first_welcome.set()
        await asyncio.gather(first_task, second_task)

    asyncio.run(scenario())

    assert detection_count == 2
