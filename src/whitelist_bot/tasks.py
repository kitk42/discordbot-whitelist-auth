"""定期処理: 7 日経った承認待ち申請を期限切れにする（FR-4）。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from discord.ext import tasks

from . import messages

if TYPE_CHECKING:
    from .bot import WhitelistBot

log = logging.getLogger(__name__)

CHECK_INTERVAL_MINUTES = 10


def create_expiry_loop(bot: WhitelistBot) -> tasks.Loop:
    @tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
    async def expire_pending() -> None:
        try:
            expired = await bot.service.expire_due()
        except Exception:
            log.exception("期限切れの確認に失敗しました")
            return
        for app in expired:
            log.info("申請 #%d を期限切れにしました", app.id)
            await bot.refresh_admin_message(app)
            await bot.notify_applicant(messages.expired_notice(app))

    @expire_pending.before_loop
    async def before() -> None:
        await bot.wait_until_ready()

    return expire_pending
