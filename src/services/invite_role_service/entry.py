"""Entry panel: agi-backend client and pure decision logic.

The bot never decides who holds the general member role. It only relays
requests to agi-backend (``/internal/discord-entry/...``) and turns the
responses into short, polite, ephemeral replies.

Never log the passphrase, the support text, or the API token.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

from .config import EntryConfig

# Fixed custom_ids for the persistent entry panel. Changing them breaks the
# buttons on an already posted panel message.
ENTRY_COURSE_CLAIM_CUSTOM_ID = "entry_panel:course_claim"
ENTRY_STATUS_CUSTOM_ID = "entry_panel:status"
ENTRY_SUPPORT_CUSTOM_ID = "entry_panel:support"

ENTRY_LINK_LABEL = "Discord 連携"
ENTRY_COURSE_CLAIM_LABEL = "講座に参加"
ENTRY_STATUS_LABEL = "参加状態を確認"
ENTRY_SUPPORT_LABEL = "連携できない時はこちら"

PASSPHRASE_MAX_LENGTH = 100
SUPPORT_TEXT_MAX_LENGTH = 1000

ENTRY_PANEL_CONTENT = (
    "**AGIラボへようこそ！**\n"
    "会員向けの部屋は、次の方法で開きます。\n\n"
    "**会員の方**\n"
    "「Discord 連携」を押し、agi-labo で連携してください。その場で部屋が開きます。"
    "連携済みの方がもう一度押しても大丈夫です。\n\n"
    "**講座に申し込んだ方**\n"
    "「講座に参加」を押し、Luma の申込確認メール（またはリマインド）にある合言葉を入力してください。"
    "会員の方は合言葉なしでご参加いただけます。\n\n"
    "**いまの状態を知りたい時**\n"
    "「参加状態を確認」を押すと、あなただけに表示されます。\n\n"
    "**連携がうまくいかない時**\n"
    "「連携できない時はこちら」から運営にお知らせください。"
)

MESSAGE_TEMPORARY_FAILURE = (
    "ただいま確認ができませんでした。少し時間をおいて、もう一度お試しください。"
    "続く場合は「連携できない時はこちら」からお知らせください。"
)
MESSAGE_NOT_READY = (
    "この機能はただいま準備中です。少し時間をおいて、もう一度お試しください。"
    "お急ぎの場合は「連携できない時はこちら」からお知らせください。"
)
MESSAGE_WRONG_GUILD = "この操作は AGIラボのサーバー内でご利用ください。"
MESSAGE_SUPPORT_SENT = "運営にお伝えしました。確認のうえ、運営からご連絡します。"
MESSAGE_SUPPORT_FAILED = (
    "申し訳ありません。ただいまお伝えできませんでした。"
    "少し時間をおいて、もう一度お試しください。"
)
MESSAGE_PASSPHRASE_EMPTY = (
    "合言葉が空のようです。Luma の申込確認メールにある合言葉を入力してください。"
)

_WEEKDAYS = "月火水木金土日"


@dataclass(frozen=True)
class BackendResult:
    """Normalized agi-backend response. ``status_code`` is None on network errors."""

    status_code: int | None
    success: bool = False
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    failure: str | None = None  # short reason for logs (no secrets)

    @property
    def log_label(self) -> str:
        code = self.status_code if self.status_code is not None else "none"
        return f"status={code} error={self.error or self.failure or 'none'}"


def parse_backend_response(status_code: int, body: Any) -> BackendResult:
    if not isinstance(body, dict):
        return BackendResult(status_code=status_code, failure="invalid_json")
    data = body.get("data")
    error = body.get("error")
    return BackendResult(
        status_code=status_code,
        success=bool(body.get("success")) and 200 <= status_code < 300,
        data=data if isinstance(data, dict) else {},
        error=str(error) if error else None,
    )


class EntryBackendClient:
    """Thin async client for agi-backend's discord-entry API."""

    def __init__(
        self,
        config: EntryConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._config = config
        self._transport = transport

    def is_configured(self) -> bool:
        return self._config.backend_token() is not None

    async def claim_course(
        self, *, discord_user_id: int, passphrase: str
    ) -> BackendResult:
        return await self._request(
            "POST",
            "/internal/discord-entry/course-claims",
            json={"discord_user_id": str(discord_user_id), "passphrase": passphrase},
        )

    async def get_status(self, *, discord_user_id: int) -> BackendResult:
        return await self._request(
            "GET", f"/internal/discord-entry/status/{int(discord_user_id)}"
        )

    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> BackendResult:
        token = self._config.backend_token()
        if token is None:
            return BackendResult(status_code=None, failure="token_missing")
        try:
            async with httpx.AsyncClient(
                base_url=self._config.backend_base_url,
                timeout=self._config.request_timeout_seconds,
                transport=self._transport,
                follow_redirects=False,
            ) as client:
                response = await client.request(
                    method,
                    path,
                    json=json,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                )
        except httpx.TimeoutException:
            return BackendResult(status_code=None, failure="timeout")
        except httpx.HTTPError as exc:
            # Only the exception type: messages can include request details.
            return BackendResult(
                status_code=None, failure=f"network:{type(exc).__name__}"
            )
        try:
            body = response.json()
        except ValueError:
            body = None
        return parse_backend_response(response.status_code, body)


def normalize_passphrase(raw: str) -> str:
    """Trim only. agi-backend owns the full normalization (width/case)."""
    return (raw or "").strip()


def format_access_date(raw: Any) -> str | None:
    """'2026-10-12' -> '10/12 (月)'."""
    if not isinstance(raw, str):
        return None
    try:
        day = date.fromisoformat(raw[:10])
    except ValueError:
        return None
    return f"{day.month}/{day.day} ({_WEEKDAYS[day.weekday()]})"


def _course_label(raw: Any) -> str:
    name = str(raw or "").strip()
    return f"「{name}」" if name else "講座"


def course_claim_message(result: BackendResult) -> str:
    """Map a course-claims response to the ephemeral reply."""
    if result.status_code is None:
        if result.failure == "token_missing":
            return MESSAGE_NOT_READY
        return MESSAGE_TEMPORARY_FAILURE

    if result.success:
        course = _course_label(result.data.get("course_name"))
        until = format_access_date(result.data.get("access_until_date"))
        until_text = f"{until} まで" if until else "期限まで"
        if result.data.get("already_claimed"):
            return (
                f"{course}の参加はすでに受け付けています。"
                f"会員向けの部屋は {until_text}開いています（翌朝に閉じます）。"
                "チャンネル一覧からご覧ください。"
            )
        return (
            f"{course}の参加を受け付けました。"
            f"会員向けの部屋を {until_text}開けました（翌朝に閉じます）。"
            "チャンネル一覧に表示された部屋から、講座の案内をご覧ください。"
        )

    error = result.error
    if error == "passphrase_not_found":
        return (
            "合言葉が違うようです。Luma の申込確認メールかリマインドに書いてあります。"
            "講座の冒頭でもお伝えします。確認して、もう一度「講座に参加」から入力してください。"
        )
    if error == "already_member":
        return (
            "会員の方は合言葉なしでご参加いただけます。会員向けの部屋をそのままご利用ください。"
            "部屋が見えない時は「Discord 連携」をもう一度押すと、すぐに開きます。"
        )
    if error == "grant_revoked":
        return (
            "この講座の参加許可は運営で取り消し済みのため、受け付けできませんでした。"
            "お手数ですが「連携できない時はこちら」から運営にお問い合わせください。"
        )
    if error == "invalid_request":
        return (
            "合言葉を読み取れませんでした。Luma のメールにある合言葉を、"
            "そのまま入力してもう一度お試しください。"
        )
    if error == "not_in_guild":
        return (
            "サーバーへの参加を確認できませんでした。AGIラボの Discord サーバーに参加した状態で、"
            "もう一度お試しください。"
        )
    if error == "too_many_attempts":
        seconds = result.data.get("retry_after_seconds")
        minutes = 15
        if isinstance(seconds, (int, float)) and seconds > 0:
            minutes = max(1, math.ceil(seconds / 60))
        return (
            "入力が続いたため、少しお休みしています。"
            f"{minutes} 分ほどたってから、もう一度お試しください。"
        )
    if error == "role_apply_failed":
        return (
            "参加は受け付けましたが、部屋を開く処理が混み合っています。"
            "少し時間をおいて、もう一度「講座に参加」からお試しください（翌朝には自動で開きます）。"
        )
    # unauthorized / 5xx / unknown: a configuration or backend problem.
    return MESSAGE_TEMPORARY_FAILURE


def status_message(result: BackendResult) -> str:
    """Map a status response to the ephemeral reply (shown only to the requester)."""
    if result.status_code is None:
        if result.failure == "token_missing":
            return MESSAGE_NOT_READY
        return MESSAGE_TEMPORARY_FAILURE
    if not result.success:
        return MESSAGE_TEMPORARY_FAILURE

    member_active = bool(result.data.get("member_active"))
    linked = bool(result.data.get("linked"))
    grant = result.data.get("grant")
    lines: list[str] = []

    if member_active and linked:
        lines.append(
            "会員として参加中です。会員向けの部屋をそのままご利用いただけます。"
        )
        lines.append(
            "部屋が見えない時は「Discord 連携」をもう一度押すと、すぐに戻ります。"
        )
    elif member_active:
        lines.append(
            "会員の記録はありますが、この Discord アカウントとの連携がまだです。"
            "「Discord 連携」を押して連携してください。"
        )
    elif linked:
        lines.append(
            "Discord 連携は済んでいますが、いまは有効な会員の記録が見つかりませんでした。"
            "ご入会（再開）いただくと、連携済みのまますぐにご利用いただけます。"
        )

    if isinstance(grant, dict):
        course = _course_label(grant.get("course_name"))
        until = format_access_date(grant.get("access_until_date"))
        if until:
            lines.append(f"{course}の参加許可: {until} まで（翌朝に閉じます）。")
        else:
            lines.append(f"{course}の参加許可があります。")

    if not lines:
        lines.append(
            "会員の方は「Discord 連携」を押してください。"
            "講座に申し込んだ方は「講座に参加」から合言葉を入力してください。"
        )
    return "\n".join(lines)


def support_request_message(
    *, user_id: int, user_mention: str, username: str, text: str
) -> str:
    """Message posted to the ops support channel for "連携できない時"."""
    body = (text or "").strip()[:SUPPORT_TEXT_MAX_LENGTH] or "(本文なし)"
    return (
        "**連携できない時の申し出**\n"
        f"申し出た人: {user_mention} (ID: {user_id} / {username})\n"
        "内容:\n"
        f"{body}"
    )[:1900]
