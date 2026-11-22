from pathlib import Path

import pytest

from whitelist_bot.config import ConfigError, load_config

VALID = {
    "DISCORD_TOKEN": "token-secret",
    "GUILD_ID": "1",
    "AUTH_CHANNEL_ID": "2",
    "ADMIN_CHANNEL_ID": "3",
    "ADMIN_ROLE_ID": "4",
    "RCON_PASSWORD": "rcon-secret",
    "DATABASE_PATH": "data/whitelist.db",
}


def test_load_valid_config_with_defaults():
    config = load_config(VALID)
    assert config.guild_id == 1
    assert config.admin_role_id == 4
    assert config.rcon_host == "127.0.0.1"
    assert config.rcon_port == 25575
    assert config.database_path == Path("data/whitelist.db")


def test_missing_and_invalid_values_are_all_reported():
    env = {**VALID, "DISCORD_TOKEN": "", "GUILD_ID": "abc"}
    with pytest.raises(ConfigError) as excinfo:
        load_config(env)
    message = str(excinfo.value)
    assert "DISCORD_TOKEN" in message
    assert "GUILD_ID" in message


def test_repr_does_not_leak_secrets():
    text = repr(load_config(VALID))
    assert "token-secret" not in text
    assert "rcon-secret" not in text
