"""Bot 本体。"""

from __future__ import annotations

import logging

import discord

from . import messages
from .commands import register_commands
from .config import Config
from .rcon import RconClient
from .repository import Application, Repository, Status
from .service import WhitelistService
from .tasks import create_expiry_loop
from .views import ApproveButton, RejectButton, pending_view

log = logging.getLogger(__name__)


class WhitelistBot(discord.Client):
    def __init__(self, config: Config) -> None:
        # 特権 Intent（members / message_content）は使わない
        super().__init__(
            intents=discord.Intents.default(),
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
        )
        self.config = config
        self.tree = discord.app_commands.CommandTree(self)
        self.repository = Repository(config.database_path)
        self.service = WhitelistService(
            self.repository,
            RconClient(config.rcon_host, config.rcon_port, config.rcon_password),
        )
        self.expiry_loop = create_expiry_loop(self)

    async def setup_hook(self) -> None:
        await self.repository.open()
        register_commands(self)
        # 再起動前に送った申請メッセージのボタンも、custom_id から申請 ID を取り出して処理する
        self.add_dynamic_items(ApproveButton, RejectButton)
        self.expiry_loop.start()

        # スラッシュコマンドは対象サーバー限定で登録する（変更が即時反映される）
        guild = discord.Object(id=self.config.guild_id)
        try:
            synced = await self.tree.sync(guild=guild)
        except discord.Forbidden as e:
            raise RuntimeError(
                f"Discord サーバー {self.config.guild_id} にスラッシュコマンドを登録できません。"
                "GUILD_ID が正しいか、Bot が bot と applications.commands の両方のスコープで"
                "招待されているかを確認してください。"
            ) from e
        log.info("スラッシュコマンドを %d 件同期しました", len(synced))

    async def on_ready(self) -> None:
        log.info("ログインしました: %s (id=%s)", self.user, self.user and self.user.id)

    async def close(self) -> None:
        self.expiry_loop.cancel()
        await super().close()
        await self.repository.close()

    async def text_channel(self, channel_id: int) -> discord.abc.Messageable:
        channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            raise TypeError(f"チャンネル {channel_id} にはメッセージを送れません")
        return channel

    async def refresh_admin_message(self, app: Application) -> None:
        """管理チャンネルの申請メッセージを最新の状態で上書きする。処理済みならボタンを消す。"""
        if app.admin_message_id is None:
            return
        view = pending_view(app.id) if app.status is Status.PENDING else None
        try:
            channel = await self.text_channel(self.config.admin_channel_id)
            if not isinstance(channel, discord.TextChannel | discord.Thread):
                raise TypeError("管理チャンネルがテキストチャンネルではありません")
            await channel.get_partial_message(app.admin_message_id).edit(
                embed=messages.application_embed(app), view=view
            )
        except (discord.HTTPException, TypeError):
            log.exception("申請メッセージの更新に失敗しました (申請 #%d)", app.id)

    async def notify_applicant(self, text: str) -> None:
        """認証チャンネルにメッセージを送る（申請者へのメンションを含む）。失敗してもログだけ残す。"""
        try:
            channel = await self.text_channel(self.config.auth_channel_id)
            await channel.send(text)
        except (discord.HTTPException, TypeError):
            log.exception("認証チャンネルへの通知に失敗しました: %s", text)
