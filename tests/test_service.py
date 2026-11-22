from __future__ import annotations

import pytest

from whitelist_bot.rcon import RconConnectionError
from whitelist_bot.repository import Repository, Status
from whitelist_bot.service import (
    NOT_FOUND_REASON,
    PENDING_TTL,
    RATE_LIMIT_WINDOW,
    ApplyError,
    ApproveResult,
    NotPendingError,
    ServerError,
    UnwhitelistError,
    WhitelistService,
    parse_whitelist,
)

USER = 100
OTHER = 200
ADMIN = 900


class FakeRcon:
    """Vanilla 26.3 の応答を真似る。exists に無い ID は「存在しない」扱い。"""

    def __init__(self) -> None:
        self.exists = {"notch": "Notch", "jeb_": "jeb_", "dinnerbone": "Dinnerbone"}
        self.whitelist: set[str] = set()
        self.down = False
        self.commands: list[str] = []

    async def command(self, command: str) -> str:
        self.commands.append(command)
        if self.down:
            raise RconConnectionError("down")
        parts = command.split()
        if parts[:2] == ["whitelist", "list"]:
            if not self.whitelist:
                return "There are no whitelisted players"
            names = ", ".join(sorted(self.whitelist))
            return f"There are {len(self.whitelist)} whitelisted player(s): {names}"
        action, name = parts[1], parts[2]
        canonical = self.exists.get(name.lower())
        if canonical is None:
            return "That player does not exist"
        if action == "add":
            if canonical in self.whitelist:
                return "Player is already whitelisted"
            self.whitelist.add(canonical)
            return f"Added {canonical} to the whitelist"
        if canonical not in self.whitelist:
            return "Player is not whitelisted"
        self.whitelist.remove(canonical)
        return f"Removed {canonical} from the whitelist"


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000

    def __call__(self) -> float:
        return self.now


@pytest.fixture
async def env():
    repo = Repository(":memory:")
    await repo.open()
    rcon = FakeRcon()
    clock = Clock()
    service = WhitelistService(repo, rcon, clock)  # type: ignore[arg-type]
    yield service, repo, rcon, clock
    await repo.close()


# --- 申請 ---


@pytest.mark.parametrize("bad", ["ab", "a" * 17, "no-hyphen", "日本語id", "space id", ""])
async def test_apply_rejects_invalid_format(env, bad):
    service, *_ = env
    with pytest.raises(ApplyError, match="形式"):
        await service.apply(USER, bad)


async def test_apply_creates_pending_application_and_event(env):
    service, repo, *_ = env
    app = await service.apply(USER, " Notch ")
    assert app.status is Status.PENDING
    assert app.minecraft_id == "Notch"
    events = await repo.list_events()
    assert [(e["action"], e["actor_id"]) for e in events] == [("applied", USER)]


async def test_apply_one_active_application_per_user(env):
    service, *_ = env
    await service.apply(USER, "Notch")
    with pytest.raises(ApplyError, match="申請中"):
        await service.apply(USER, "jeb_")


async def test_apply_rejects_same_id_pending_case_insensitive(env):
    service, *_ = env
    await service.apply(USER, "Notch")
    with pytest.raises(ApplyError, match="承認待ち"):
        await service.apply(OTHER, "NOTCH")


async def test_apply_rejects_id_already_on_server_whitelist(env):
    service, _, rcon, _ = env
    rcon.whitelist.add("Notch")
    with pytest.raises(ApplyError, match="既に whitelist"):
        await service.apply(USER, "notch")


async def test_apply_skips_whitelist_check_when_server_down(env):
    service, _, rcon, _ = env
    rcon.down = True
    app = await service.apply(USER, "Notch")
    assert app.status is Status.PENDING


async def test_rate_limit_rolling_24h(env):
    service, _, _, clock = env
    for _ in range(5):
        app = await service.apply(USER, "Notch")
        await service.reject(app.id, ADMIN, "test")
        clock.now += 60
    with pytest.raises(ApplyError, match="24 時間に 5 回"):
        await service.apply(USER, "Notch")
    # 最初の申請から 24 時間経てば 1 枠空く
    clock.now = 1_800_000_000 + RATE_LIMIT_WINDOW + 1
    assert (await service.apply(USER, "Notch")).status is Status.PENDING


async def test_errors_do_not_count_toward_rate_limit(env):
    service, *_ = env
    for _ in range(10):
        with pytest.raises(ApplyError):
            await service.apply(USER, "x")
    assert (await service.apply(USER, "Notch")).status is Status.PENDING


async def test_discard_removes_application(env):
    service, repo, *_ = env
    app = await service.apply(USER, "Notch")
    await service.discard(app.id)
    assert await repo.get(app.id) is None
    assert (await service.apply(USER, "Notch")).status is Status.PENDING


# --- 承認 ---


async def test_approve_adds_to_whitelist_with_canonical_name(env):
    service, repo, rcon, _ = env
    app = await service.apply(USER, "notch")
    outcome = await service.approve(app.id, ADMIN)
    assert outcome.result is ApproveResult.ADDED
    assert outcome.application.status is Status.APPROVED
    assert outcome.application.minecraft_id == "Notch"
    assert outcome.application.processed_by == ADMIN
    assert "Notch" in rcon.whitelist
    assert [e["action"] for e in await repo.list_events()] == ["applied", "approved"]


async def test_approve_already_whitelisted_counts_as_approved(env):
    service, _, rcon, _ = env
    rcon.down = True  # 申請時は確認を飛ばす
    app = await service.apply(USER, "Notch")
    rcon.down = False
    rcon.whitelist.add("Notch")
    outcome = await service.approve(app.id, ADMIN)
    assert outcome.result is ApproveResult.ALREADY_WHITELISTED
    assert outcome.application.status is Status.APPROVED


async def test_approve_nonexistent_player_auto_rejects(env):
    service, *_ = env
    app = await service.apply(USER, "NoSuchPlayer")
    outcome = await service.approve(app.id, ADMIN)
    assert outcome.result is ApproveResult.PLAYER_NOT_FOUND
    assert outcome.application.status is Status.REJECTED
    assert outcome.application.reason == NOT_FOUND_REASON
    # 拒否後は再申請できる
    assert (await service.apply(USER, "Notch")).status is Status.PENDING


async def test_approve_server_down_keeps_pending(env):
    service, repo, rcon, _ = env
    app = await service.apply(USER, "Notch")
    rcon.down = True
    with pytest.raises(ServerError):
        await service.approve(app.id, ADMIN)
    assert (await repo.get(app.id)).status is Status.PENDING
    rcon.down = False
    assert (await service.approve(app.id, ADMIN)).result is ApproveResult.ADDED


async def test_second_action_on_processed_application_fails(env):
    service, *_ = env
    app = await service.apply(USER, "Notch")
    await service.approve(app.id, ADMIN)
    with pytest.raises(NotPendingError, match="承認済み"):
        await service.reject(app.id, ADMIN, "late")
    with pytest.raises(NotPendingError):
        await service.approve(app.id, ADMIN)


# --- 拒否 ---


async def test_reject_records_reason_and_allows_reapply(env):
    service, *_ = env
    app = await service.apply(USER, "Notch")
    rejected = await service.reject(app.id, ADMIN, "  知らない人です ")
    assert rejected.status is Status.REJECTED
    assert rejected.reason == "知らない人です"
    assert rejected.processed_by == ADMIN
    assert (await service.apply(USER, "Notch")).status is Status.PENDING


# --- 期限切れ ---


async def test_expire_due_only_expires_old_pending(env):
    service, _, _, clock = env
    old = await service.apply(USER, "Notch")
    clock.now += 60
    new = await service.apply(OTHER, "jeb_")
    clock.now = old.created_at + PENDING_TTL
    expired = await service.expire_due()
    assert [a.id for a in expired] == [old.id]
    assert expired[0].status is Status.EXPIRED
    assert (await service.get(new.id)).status is Status.PENDING
    assert await service.expire_due() == []
    # 期限切れ後は再申請できる
    clock.now += 1
    assert (await service.apply(USER, "Notch")).status is Status.PENDING


# --- 削除 ---


async def test_unwhitelist_bot_registered_id(env):
    service, _, rcon, _ = env
    app = await service.apply(USER, "Notch")
    await service.approve(app.id, ADMIN)
    outcome = await service.unwhitelist("notch", ADMIN)
    assert outcome.was_whitelisted
    assert outcome.minecraft_id == "Notch"
    assert outcome.application is not None
    assert outcome.application.status is Status.REMOVED
    assert "Notch" not in rcon.whitelist
    assert (await service.apply(USER, "Notch")).status is Status.PENDING


async def test_unwhitelist_id_not_from_bot(env):
    service, repo, rcon, _ = env
    rcon.whitelist.add("Dinnerbone")
    outcome = await service.unwhitelist("Dinnerbone", ADMIN)
    assert outcome.was_whitelisted
    assert outcome.application is None
    events = await repo.list_events()
    assert [(e["action"], e["application_id"]) for e in events] == [("removed", None)]


async def test_unwhitelist_already_removed_on_console_frees_application(env):
    service, _, rcon, _ = env
    app = await service.apply(USER, "Notch")
    await service.approve(app.id, ADMIN)
    rcon.whitelist.clear()  # コンソールで先に削除された
    outcome = await service.unwhitelist("Notch", ADMIN)
    assert not outcome.was_whitelisted
    assert outcome.application.status is Status.REMOVED


async def test_unwhitelist_errors(env):
    service, _, rcon, _ = env
    with pytest.raises(UnwhitelistError, match="登録されていません"):
        await service.unwhitelist("Notch", ADMIN)
    with pytest.raises(UnwhitelistError, match="存在しません"):
        await service.unwhitelist("NoSuchPlayer", ADMIN)
    with pytest.raises(UnwhitelistError, match="形式"):
        await service.unwhitelist("a-b", ADMIN)
    rcon.down = True
    with pytest.raises(ServerError):
        await service.unwhitelist("Notch", ADMIN)


def test_parse_whitelist():
    assert parse_whitelist("There are no whitelisted players") == set()
    assert parse_whitelist("There are 2 whitelisted player(s): jeb_, Notch") == {"jeb_", "notch"}
    assert parse_whitelist("something else") is None
