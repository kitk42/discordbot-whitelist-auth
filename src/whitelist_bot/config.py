"""環境変数から設定を読み込む。

開発では __main__ が .env を読み込んでから呼ぶ。本番では systemd の EnvironmentFile で渡される。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    """必須の設定が無い、または値が不正。"""


@dataclass(frozen=True)
class Config:
    discord_token: str
    guild_id: int
    auth_channel_id: int
    admin_channel_id: int
    admin_role_id: int
    rcon_host: str
    rcon_port: int
    rcon_password: str
    database_path: Path

    def __repr__(self) -> str:
        # トークンやパスワードがログに出ないようにする
        return (
            f"Config(guild_id={self.guild_id}, auth_channel_id={self.auth_channel_id}, "
            f"admin_channel_id={self.admin_channel_id}, admin_role_id={self.admin_role_id}, "
            f"rcon={self.rcon_host}:{self.rcon_port}, database_path={self.database_path})"
        )


def load_config(env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    errors: list[str] = []

    def required(name: str) -> str:
        value = env.get(name, "").strip()
        if not value:
            errors.append(f"{name} が設定されていません")
        return value

    def integer(name: str, default: str | None = None) -> int:
        raw = env.get(name, "").strip() or (default or "")
        if not raw:
            errors.append(f"{name} が設定されていません")
            return 0
        try:
            return int(raw)
        except ValueError:
            errors.append(f"{name} は整数で指定してください: {raw!r}")
            return 0

    config = Config(
        discord_token=required("DISCORD_TOKEN"),
        guild_id=integer("GUILD_ID"),
        auth_channel_id=integer("AUTH_CHANNEL_ID"),
        admin_channel_id=integer("ADMIN_CHANNEL_ID"),
        admin_role_id=integer("ADMIN_ROLE_ID"),
        rcon_host=env.get("RCON_HOST", "").strip() or "127.0.0.1",
        rcon_port=integer("RCON_PORT", "25575"),
        rcon_password=required("RCON_PASSWORD"),
        database_path=Path(required("DATABASE_PATH")),
    )
    if errors:
        raise ConfigError("設定エラー:\n  " + "\n  ".join(errors))
    return config
