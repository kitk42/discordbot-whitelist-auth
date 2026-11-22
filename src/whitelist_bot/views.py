"""承認・拒否ボタンと拒否理由の Modal。

ボタンは DynamicItem で実装する。custom_id に申請 ID を埋め込んでおき、Bot の再起動後も
custom_id から申請 ID を取り出して処理できるようにする（FR-7 の persistent view）。
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, cast

import discord

from . import messages
from .service import ApproveResult, WhitelistError

if TYPE_CHECKING:
    from .bot import WhitelistBot

log = logging.getLogger(__name__)


def is_admin(interaction: discord.Interaction, admin_role_id: int) -> bool:
    user = interaction.user
    return isinstance(user, discord.Member) and any(r.id == admin_role_id for r in user.roles)


def pending_view(application_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ApproveButton(application_id))
    view.add_item(RejectButton(application_id))
    return view


async def _ensure_admin(interaction: discord.Interaction, bot: WhitelistBot) -> bool:
    if is_admin(interaction, bot.config.admin_role_id):
        return True
    await interaction.response.send_message("この操作は管理者だけが行えます。", ephemeral=True)
    return False


class ApproveButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"wl:approve:(?P<id>[0-9]+)",
):
    def __init__(self, application_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="承認",
                style=discord.ButtonStyle.success,
                custom_id=f"wl:approve:{application_id}",
            )
        )
        self.application_id = application_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[discord.ui.View],
        match: re.Match[str],
    ) -> ApproveButton:
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = cast("WhitelistBot", interaction.client)
        if not await _ensure_admin(interaction, bot):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            outcome = await bot.service.approve(self.application_id, interaction.user.id)
        except WhitelistError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return

        app = outcome.application
        await bot.refresh_admin_message(app)
        if outcome.result is ApproveResult.PLAYER_NOT_FOUND:
            await bot.notify_applicant(messages.rejected_notice(app))
            await interaction.followup.send(
                f"`{app.minecraft_id}` は存在しない Minecraft ID だったため、自動で拒否しました。",
                ephemeral=True,
            )
            return
        await bot.notify_applicant(messages.approved_notice(app))
        note = (
            "（既に whitelist に登録されていました）"
            if outcome.result is ApproveResult.ALREADY_WHITELISTED
            else ""
        )
        await interaction.followup.send(
            f"`{app.minecraft_id}` を承認しました。{note}", ephemeral=True
        )


class RejectButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"wl:reject:(?P<id>[0-9]+)",
):
    def __init__(self, application_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="拒否",
                style=discord.ButtonStyle.danger,
                custom_id=f"wl:reject:{application_id}",
            )
        )
        self.application_id = application_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[discord.ui.View],
        match: re.Match[str],
    ) -> RejectButton:
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = cast("WhitelistBot", interaction.client)
        if not await _ensure_admin(interaction, bot):
            return
        await interaction.response.send_modal(RejectModal(self.application_id))


class RejectModal(discord.ui.Modal, title="申請を拒否"):
    reason: discord.ui.TextInput[RejectModal] = discord.ui.TextInput(
        label="拒否の理由（認証チャンネルで申請者に表示されます）",
        style=discord.TextStyle.paragraph,
        max_length=500,
    )

    def __init__(self, application_id: int) -> None:
        super().__init__(timeout=600)
        self.application_id = application_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot = cast("WhitelistBot", interaction.client)
        if not await _ensure_admin(interaction, bot):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            app = await bot.service.reject(
                self.application_id, interaction.user.id, self.reason.value
            )
        except WhitelistError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        await bot.refresh_admin_message(app)
        await bot.notify_applicant(messages.rejected_notice(app))
        await interaction.followup.send(f"`{app.minecraft_id}` を拒否しました。", ephemeral=True)
