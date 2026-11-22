"""RconClient のテスト。Vanilla の挙動を真似た偽サーバーを使う。"""

from __future__ import annotations

import asyncio
import socket

import pytest

from whitelist_bot.rcon import (
    TYPE_COMMAND,
    TYPE_LOGIN,
    RconAuthError,
    RconClient,
    RconConnectionError,
    encode_packet,
    read_packet,
)

PASSWORD = "secret"


async def start_fake_server(responses: dict[str, list[str]]) -> tuple[asyncio.Server, int]:
    """responses: コマンド → 応答パケット本文のリスト（2 個以上なら分割応答）。"""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        authed = False
        try:
            while True:
                request_id, packet_type, body = await read_packet(reader)
                if packet_type == TYPE_LOGIN:
                    authed = body == PASSWORD
                    writer.write(encode_packet(request_id if authed else -1, 2, ""))
                elif packet_type == TYPE_COMMAND and authed:
                    for part in responses.get(body, ["Unknown command"]):
                        writer.write(encode_packet(request_id, 0, part))
                else:
                    writer.write(encode_packet(request_id, 0, f"Unknown request {packet_type:x}"))
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


async def test_command_returns_response():
    responses = {"whitelist add Notch": ["Added Notch to the whitelist"]}
    server, port = await start_fake_server(responses)
    async with server:
        client = RconClient("127.0.0.1", port, PASSWORD)
        assert await client.command("whitelist add Notch") == "Added Notch to the whitelist"


async def test_split_response_is_joined():
    server, port = await start_fake_server({"help": ["a" * 4096, "b" * 100]})
    async with server:
        client = RconClient("127.0.0.1", port, PASSWORD)
        assert await client.command("help") == "a" * 4096 + "b" * 100


async def test_wrong_password_raises_auth_error():
    server, port = await start_fake_server({})
    async with server:
        client = RconClient("127.0.0.1", port, "wrong")
        with pytest.raises(RconAuthError):
            await client.command("list")


async def test_server_down_raises_connection_error():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # 確保してすぐ閉じるので、誰も待ち受けていないポートになる
    client = RconClient("127.0.0.1", port, PASSWORD, timeout=2)
    with pytest.raises(RconConnectionError):
        await client.command("list")


def test_encode_and_read_roundtrip():
    async def roundtrip() -> tuple[int, int, str]:
        reader = asyncio.StreamReader()
        reader.feed_data(encode_packet(7, TYPE_COMMAND, "whitelist list"))
        reader.feed_eof()
        return await read_packet(reader)

    assert asyncio.run(roundtrip()) == (7, TYPE_COMMAND, "whitelist list")
