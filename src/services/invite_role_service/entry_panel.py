"""Persistent entry panel (discord.ui) for the @everyone entry channel."""

from __future__ import annotations

from typing import Protocol

import discord

from .config import EntryConfig
from .entry import (
    ENTRY_COURSE_CLAIM_CUSTOM_ID,
    ENTRY_COURSE_CLAIM_LABEL,
    ENTRY_LINK_LABEL,
    ENTRY_STATUS_CUSTOM_ID,
    ENTRY_STATUS_LABEL,
    ENTRY_SUPPORT_CUSTOM_ID,
    ENTRY_SUPPORT_LABEL,
    PASSPHRASE_MAX_LENGTH,
    SUPPORT_TEXT_MAX_LENGTH,
)


class EntryPanelHandler(Protocol):
    async def handle_entry_course_claim(
        self, interaction: discord.Interaction, passphrase: str
    ) -> None: ...

    async def handle_entry_status(self, interaction: discord.Interaction) -> None: ...

    async def handle_entry_support(
        self, interaction: discord.Interaction, text: str
    ) -> None: ...


class CourseClaimModal(discord.ui.Modal, title="講座に参加"):
    passphrase: discord.ui.TextInput["CourseClaimModal"] = discord.ui.TextInput(
        label="合言葉",
        placeholder="Luma の申込確認メールにある合言葉",
        max_length=PASSPHRASE_MAX_LENGTH,
        required=True,
    )

    def __init__(self, handler: EntryPanelHandler):
        super().__init__(timeout=600)
        self._handler = handler

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._handler.handle_entry_course_claim(
            interaction, str(self.passphrase.value or "")
        )


class SupportModal(discord.ui.Modal, title="連携できない時はこちら"):
    detail: discord.ui.TextInput["SupportModal"] = discord.ui.TextInput(
        label="お困りの内容",
        style=discord.TextStyle.paragraph,
        placeholder="例: note で入会しましたが、Discord 連携の画面に進めません",
        max_length=SUPPORT_TEXT_MAX_LENGTH,
        required=True,
    )

    def __init__(self, handler: EntryPanelHandler):
        super().__init__(timeout=900)
        self._handler = handler

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._handler.handle_entry_support(
            interaction, str(self.detail.value or "")
        )


class EntryPanelView(discord.ui.View):
    """4 buttons with fixed custom_ids; timeout=None so it survives restarts.

    ``handler`` may be None when the view is only used to post the panel.
    """

    def __init__(self, entry: EntryConfig, handler: EntryPanelHandler | None):
        super().__init__(timeout=None)
        self._handler = handler
        self.add_item(
            discord.ui.Button(
                label=ENTRY_LINK_LABEL,
                style=discord.ButtonStyle.link,
                url=entry.link_url,
                row=0,
            )
        )
        course: discord.ui.Button[EntryPanelView] = discord.ui.Button(
            label=ENTRY_COURSE_CLAIM_LABEL,
            style=discord.ButtonStyle.primary,
            custom_id=ENTRY_COURSE_CLAIM_CUSTOM_ID,
            row=0,
        )
        course.callback = self._open_course_claim  # type: ignore[method-assign]
        self.add_item(course)
        status: discord.ui.Button[EntryPanelView] = discord.ui.Button(
            label=ENTRY_STATUS_LABEL,
            style=discord.ButtonStyle.secondary,
            custom_id=ENTRY_STATUS_CUSTOM_ID,
            row=0,
        )
        status.callback = self._check_status  # type: ignore[method-assign]
        self.add_item(status)
        support: discord.ui.Button[EntryPanelView] = discord.ui.Button(
            label=ENTRY_SUPPORT_LABEL,
            style=discord.ButtonStyle.secondary,
            custom_id=ENTRY_SUPPORT_CUSTOM_ID,
            row=1,
        )
        support.callback = self._open_support  # type: ignore[method-assign]
        self.add_item(support)

    async def _open_course_claim(self, interaction: discord.Interaction) -> None:
        if self._handler is None:
            return
        await interaction.response.send_modal(CourseClaimModal(self._handler))

    async def _check_status(self, interaction: discord.Interaction) -> None:
        if self._handler is None:
            return
        await self._handler.handle_entry_status(interaction)

    async def _open_support(self, interaction: discord.Interaction) -> None:
        if self._handler is None:
            return
        await interaction.response.send_modal(SupportModal(self._handler))
