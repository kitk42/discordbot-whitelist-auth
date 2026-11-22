"""申請のルールと状態の管理。Discord には依存しない。

状態を変える処理はすべて 1 つのロックで直列化する。承認ボタンの処理中（RCON 実行中）に
別の管理者が拒否ボタンを押しても、後から来た方は「処理済み」として扱われる。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from .rcon import RconClient, RconError
from .repository import ACTIVE_STATUSES, Application, Repository, Status

log = logging.getLogger(__name__)

MINECRAFT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,16}$")
RATE_LIMIT_COUNT = 5
RATE_LIMIT_WINDOW = 24 * 60 * 60
PENDING_TTL = 7 * 24 * 60 * 60
NOT_FOUND_REASON = "Minecraft ID が存在しません"

# RCON の応答（Vanilla 26.3 で確認。docs/requirements.md 7.1）
_ADDED = re.compile(r"^Added (\S+) to the whitelist$")
_REMOVED = re.compile(r"^Removed (\S+) from the whitelist$")
_ALREADY_WHITELISTED = "Player is already whitelisted"
_NOT_WHITELISTED = "Player is not whitelisted"
_PLAYER_NOT_EXIST = "That player does not exist"
_LIST_EMPTY = "There are no whitelisted players"
_LIST = re.compile(r"^There are \d+ whitelisted player\(s\): (.*)$")


class WhitelistError(Exception):
    """利用者にそのまま見せるメッセージを持つエラー。"""


class ApplyError(WhitelistError):
    """申請を受け付けられない。"""


class NotPendingError(WhitelistError):
    """申請が既に処理済み、または存在しない。"""


class ServerError(WhitelistError):
    """Minecraft サーバーとのやり取りに失敗した。申請の状態は変わっていない。"""


class UnwhitelistError(WhitelistError):
    """削除できない。"""


class ApproveResult(StrEnum):
    ADDED = "added"
    ALREADY_WHITELISTED = "already_whitelisted"
    PLAYER_NOT_FOUND = "player_not_found"  # 自動で拒否済みにした


@dataclass(frozen=True)
class ApproveOutcome:
    result: ApproveResult
    application: Application


@dataclass(frozen=True)
class UnwhitelistOutcome:
    minecraft_id: str
    was_whitelisted: bool
    application: Application | None  # Bot 経由の申請と結び付いていた場合


def is_valid_minecraft_id(minecraft_id: str) -> bool:
    return MINECRAFT_ID_PATTERN.fullmatch(minecraft_id) is not None


def parse_whitelist(response: str) -> set[str] | None:
    """whitelist list の応答から ID の集合（小文字）を返す。形式が想定外なら None。"""
    if response == _LIST_EMPTY:
        return set()
    m = _LIST.fullmatch(response)
    if m is None:
        return None
    return {name.strip().lower() for name in m[1].split(",") if name.strip()}


class WhitelistService:
    def __init__(
        self,
        repository: Repository,
        rcon: RconClient,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repo = repository
        self._rcon = rcon
        self._clock = clock
        self._lock = asyncio.Lock()

    def _now(self) -> int:
        return int(self._clock())

    # --- 申請 (FR-1) ---

    async def apply(self, discord_user_id: int, minecraft_id: str) -> Application:
        minecraft_id = minecraft_id.strip()
        if not is_valid_minecraft_id(minecraft_id):
            raise ApplyError(
                "Minecraft ID の形式が正しくありません。"
                "3〜16 文字の英数字とアンダースコア（_）で入力してください。"
            )

        async with self._lock:
            now = self._now()

            active = await self._repo.find_active_by_user(discord_user_id)
            if active is not None:
                if active.status is Status.PENDING:
                    raise ApplyError(
                        f"申請中の Minecraft ID（`{active.minecraft_id}`）があります。"
                        "結果が出るまでお待ちください。"
                    )
                raise ApplyError(
                    f"既に Minecraft ID `{active.minecraft_id}` が登録されています。"
                    "申請できるのは 1 人 1 つまでです。"
                )

            same_id = await self._repo.find_by_minecraft_id(minecraft_id, ACTIVE_STATUSES)
            if same_id is not None:
                if same_id.status is Status.PENDING:
                    raise ApplyError(f"`{minecraft_id}` は既に申請されていて、承認待ちです。")
                raise ApplyError(f"`{minecraft_id}` は既に whitelist に登録されています。")

            if await self._is_whitelisted(minecraft_id):
                raise ApplyError(f"`{minecraft_id}` は既に whitelist に登録されています。")

            recent = await self._repo.created_at_since(discord_user_id, now - RATE_LIMIT_WINDOW)
            if len(recent) >= RATE_LIMIT_COUNT:
                retry_at = recent[-RATE_LIMIT_COUNT] + RATE_LIMIT_WINDOW
                raise ApplyError(
                    f"申請できるのは 24 時間に {RATE_LIMIT_COUNT} 回までです。"
                    f"<t:{retry_at}:R> 以降に、もう一度申請してください。"
                )

            application_id = await self._repo.insert_application(discord_user_id, minecraft_id, now)
            await self._repo.add_event(
                application_id=application_id,
                minecraft_id=minecraft_id,
                action="applied",
                actor_id=discord_user_id,
                now=now,
            )
            return await self._get(application_id)

    async def _is_whitelisted(self, minecraft_id: str) -> bool:
        """RCON で登録済みか確認する。確認できないときは False（確認を飛ばす。7.2）。"""
        try:
            response = await self._rcon.command("whitelist list")
        except RconError as e:
            log.warning("申請時の whitelist 確認を飛ばしました: %s", e)
            return False
        names = parse_whitelist(response)
        if names is None:
            log.warning("whitelist list の応答形式が想定外です: %r", response)
            return False
        return minecraft_id.lower() in names

    async def set_admin_message(self, application_id: int, message_id: int) -> None:
        await self._repo.set_admin_message(application_id, message_id)

    async def discard(self, application_id: int) -> None:
        """管理チャンネルへの送信に失敗した申請を、無かったことにする。"""
        await self._repo.delete_application(application_id)

    # --- 承認 (FR-2) ---

    async def approve(self, application_id: int, admin_id: int) -> ApproveOutcome:
        async with self._lock:
            app = await self._get_pending(application_id)
            try:
                response = await self._rcon.command(f"whitelist add {app.minecraft_id}")
            except RconError as e:
                log.warning("承認時の RCON 実行に失敗しました (申請 #%d): %s", app.id, e)
                raise ServerError(
                    "Minecraft サーバーに接続できませんでした。申請は承認待ちのままです。"
                    "サーバーの状態を確認してから、もう一度押してください。"
                ) from e

            now = self._now()
            if m := _ADDED.fullmatch(response):
                result, status, reason, canonical = ApproveResult.ADDED, Status.APPROVED, None, m[1]
            elif response == _ALREADY_WHITELISTED:
                result, status, reason, canonical = (
                    ApproveResult.ALREADY_WHITELISTED,
                    Status.APPROVED,
                    None,
                    None,
                )
            elif response == _PLAYER_NOT_EXIST:
                result, status, reason, canonical = (
                    ApproveResult.PLAYER_NOT_FOUND,
                    Status.REJECTED,
                    NOT_FOUND_REASON,
                    None,
                )
            else:
                log.error("whitelist add の応答が想定外です (申請 #%d): %r", app.id, response)
                raise ServerError(
                    f"Minecraft サーバーから想定外の応答がありました: {response}\n"
                    "申請は承認待ちのままです。"
                )

            await self._repo.transition(
                app.id,
                Status.PENDING,
                status,
                actor_id=admin_id,
                now=now,
                reason=reason,
                minecraft_id=canonical,
            )
            await self._repo.add_event(
                application_id=app.id,
                minecraft_id=canonical or app.minecraft_id,
                action=status.value,
                actor_id=admin_id,
                now=now,
                reason=reason,
            )
            return ApproveOutcome(result, await self._get(app.id))

    # --- 拒否 (FR-3) ---

    async def reject(self, application_id: int, admin_id: int, reason: str) -> Application:
        reason = reason.strip()
        async with self._lock:
            app = await self._get_pending(application_id)
            now = self._now()
            await self._repo.transition(
                app.id, Status.PENDING, Status.REJECTED, actor_id=admin_id, now=now, reason=reason
            )
            await self._repo.add_event(
                application_id=app.id,
                minecraft_id=app.minecraft_id,
                action=Status.REJECTED.value,
                actor_id=admin_id,
                now=now,
                reason=reason,
            )
            return await self._get(app.id)

    # --- 期限切れ (FR-4) ---

    async def expire_due(self) -> list[Application]:
        async with self._lock:
            now = self._now()
            expired: list[Application] = []
            for app in await self._repo.list_pending_created_before(now - PENDING_TTL):
                if await self._repo.transition(
                    app.id, Status.PENDING, Status.EXPIRED, actor_id=None, now=now
                ):
                    await self._repo.add_event(
                        application_id=app.id,
                        minecraft_id=app.minecraft_id,
                        action=Status.EXPIRED.value,
                        actor_id=None,
                        now=now,
                    )
                    expired.append(await self._get(app.id))
            return expired

    # --- 削除 (FR-5) ---

    async def unwhitelist(self, minecraft_id: str, admin_id: int) -> UnwhitelistOutcome:
        minecraft_id = minecraft_id.strip()
        if not is_valid_minecraft_id(minecraft_id):
            raise UnwhitelistError("Minecraft ID の形式が正しくありません。")

        async with self._lock:
            try:
                response = await self._rcon.command(f"whitelist remove {minecraft_id}")
            except RconError as e:
                raise ServerError(
                    "Minecraft サーバーに接続できませんでした。時間をおいて再度実行してください。"
                ) from e

            if m := _REMOVED.fullmatch(response):
                was_whitelisted, name = True, m[1]
            elif response == _NOT_WHITELISTED:
                was_whitelisted, name = False, minecraft_id
            elif response == _PLAYER_NOT_EXIST:
                raise UnwhitelistError(f"`{minecraft_id}` という Minecraft ID は存在しません。")
            else:
                log.error("whitelist remove の応答が想定外です: %r", response)
                raise ServerError(f"Minecraft サーバーから想定外の応答がありました: {response}")

            # サーバーのコンソールで先に削除されていた場合も、
            # 申請は削除済みにして再申請できるようにする
            app = await self._repo.find_by_minecraft_id(minecraft_id, (Status.APPROVED,))
            if not was_whitelisted and app is None:
                raise UnwhitelistError(f"`{minecraft_id}` は whitelist に登録されていません。")

            now = self._now()
            if app is not None:
                await self._repo.transition(
                    app.id, Status.APPROVED, Status.REMOVED, actor_id=admin_id, now=now
                )
                app = await self._get(app.id)
            await self._repo.add_event(
                application_id=app.id if app else None,
                minecraft_id=name,
                action=Status.REMOVED.value,
                actor_id=admin_id,
                now=now,
            )
            return UnwhitelistOutcome(name, was_whitelisted, app)

    # --- 共通 ---

    async def get(self, application_id: int) -> Application | None:
        return await self._repo.get(application_id)

    async def _get(self, application_id: int) -> Application:
        app = await self._repo.get(application_id)
        if app is None:
            raise NotPendingError("申請が見つかりません。")
        return app

    async def _get_pending(self, application_id: int) -> Application:
        app = await self._get(application_id)
        if app.status is not Status.PENDING:
            raise NotPendingError(
                f"この申請は既に処理されています（{STATUS_LABELS[app.status]}）。"
            )
        return app


STATUS_LABELS = {
    Status.PENDING: "承認待ち",
    Status.APPROVED: "承認済み",
    Status.REJECTED: "拒否済み",
    Status.EXPIRED: "期限切れ",
    Status.REMOVED: "削除済み",
}
