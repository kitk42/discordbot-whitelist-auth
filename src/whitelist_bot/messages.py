"""Discord に出すメッセージの組み立て。"""

from __future__ import annotations

import discord

from .repository import Application, Status
from .service import PENDING_TTL, STATUS_LABELS

_COLORS = {
    Status.PENDING: discord.Color.gold(),
    Status.APPROVED: discord.Color.green(),
    Status.REJECTED: discord.Color.red(),
    Status.EXPIRED: discord.Color.dark_grey(),
    Status.REMOVED: discord.Color.dark_grey(),
}


def application_embed(app: Application) -> discord.Embed:
    """管理チャンネルに出す申請メッセージ。状態が変わるたびに作り直して上書きする。"""
    embed = discord.Embed(title=f"whitelist 申請 #{app.id}", color=_COLORS[app.status])
    embed.add_field(name="申請者", value=f"<@{app.discord_user_id}>", inline=True)
    embed.add_field(name="Minecraft ID", value=f"`{app.minecraft_id}`", inline=True)
    embed.add_field(name="状態", value=STATUS_LABELS[app.status], inline=True)
    embed.add_field(name="申請日時", value=f"<t:{app.created_at}:f>", inline=True)
    if app.status is Status.PENDING:
        embed.add_field(name="期限", value=f"<t:{app.created_at + PENDING_TTL}:R>", inline=True)
    if app.processed_at is not None:
        processor = f"<@{app.processed_by}>" if app.processed_by else "自動"
        embed.add_field(name="処理", value=f"{processor}（<t:{app.processed_at}:f>）", inline=False)
    if app.reason:
        embed.add_field(name="理由", value=app.reason, inline=False)
    return embed


def mention(app: Application) -> str:
    return f"<@{app.discord_user_id}>"


def approved_notice(app: Application) -> str:
    return f"{mention(app)} Minecraft ID `{app.minecraft_id}` を whitelist に登録しました。"


def rejected_notice(app: Application) -> str:
    return (
        f"{mention(app)} Minecraft ID `{app.minecraft_id}` の申請は拒否されました。\n"
        f"理由: {app.reason}"
    )


def expired_notice(app: Application) -> str:
    return (
        f"{mention(app)} Minecraft ID `{app.minecraft_id}` の申請は、"
        "7 日以内に承認されなかったため無効になりました。もう一度申請できます。"
    )


def removed_notice(app: Application) -> str:
    return f"{mention(app)} Minecraft ID `{app.minecraft_id}` は whitelist から削除されました。"
