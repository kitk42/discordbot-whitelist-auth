"""スラッシュコマンド: /whitelist（申請）と /unwhitelist（削除）。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from . import messages
from .service import WhitelistError
from .views import is_admin, pending_view

if TYPE_CHECKING:
    from .bot import WhitelistBot

log = logging.getLogger(__name__)


def register_commands(bot: WhitelistBot) -> None:
    guild = discord.Object(id=bot.config.guild_id)
    config = bot.config

    @bot.tree.command(
        name="whitelist",
        description="Minecraft サーバーの whitelist への登録を申請します",
        guild=guild,
    )
    @app_commands.describe(minecraft_id="Minecraft（Java 版）のプレイヤー ID")
    async def whitelist(interaction: discord.Interaction, minecraft_id: str) -> None:
        if interaction.channel_id != config.auth_channel_id:
            await interaction.response.send_message(
                f"このコマンドは <#{config.auth_channel_id}> でだけ使えます。", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            app = await bot.service.apply(interaction.user.id, minecraft_id)
        except WhitelistError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return

        try:
            channel = await bot.text_channel(config.admin_channel_id)
            message = await channel.send(
                embed=messages.application_embed(app), view=pending_view(app.id)
            )
        except discord.HTTPException:
            log.exception("管理チャンネルへの送信に失敗しました (申請 #%d)", app.id)
            await bot.service.discard(app.id)
            await interaction.followup.send(
                "申請の送信に失敗しました。時間をおいて、もう一度お試しください。", ephemeral=True
            )
            return

        await bot.service.set_admin_message(app.id, message.id)
        log.info(
            "申請 #%d を受け付けました: user=%d id=%s",
            app.id,
            app.discord_user_id,
            app.minecraft_id,
        )
        await interaction.followup.send(
            f"`{app.minecraft_id}` の申請を受け付けました。"
            "管理者が確認したら、このチャンネルでお知らせします。",
            ephemeral=True,
        )

    @bot.tree.command(
        name="unwhitelist",
        description="【管理者用】Minecraft ID を whitelist から削除します",
        guild=guild,
    )
    @app_commands.describe(minecraft_id="削除する Minecraft のプレイヤー ID")
    async def unwhitelist(interaction: discord.Interaction, minecraft_id: str) -> None:
        if interaction.channel_id != config.admin_channel_id or not is_admin(
            interaction, config.admin_role_id
        ):
            await interaction.response.send_message(
                "このコマンドは、管理者が管理チャンネルでだけ使えます。", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True)
        try:
            outcome = await bot.service.unwhitelist(minecraft_id, interaction.user.id)
        except WhitelistError as e:
            await interaction.followup.send(str(e))
            return

        lines = [
            f"`{outcome.minecraft_id}` を whitelist から削除しました。"
            if outcome.was_whitelisted
            else f"`{outcome.minecraft_id}` は既に whitelist にありませんでした。"
        ]
        if outcome.application is not None:
            app = outcome.application
            lines.append(f"申請 #{app.id}（<@{app.discord_user_id}>）を削除済みにしました。")
            await bot.refresh_admin_message(app)
            await bot.notify_applicant(messages.removed_notice(app))
        else:
            lines.append("Bot 経由の申請と結び付いていない ID のため、通知は行いません。")
        await interaction.followup.send(
            "\n".join(lines), allowed_mentions=discord.AllowedMentions.none()
        )
