"""Minecraft RCON クライアント（asyncio 実装）。

プロトコル: https://minecraft.wiki/w/RCON
パケット = 長さ(int32 LE) + リクエストID(int32 LE) + 種別(int32 LE) + 本文(ASCII) + NUL + NUL

1 コマンドごとに接続 → 認証 → 実行 → 切断する。利用頻度が低いため接続は使い回さない。

長い応答は複数パケットに分割されるため、応答の最初のパケットを受け取った後に種別不明の
パケットを送り、サーバーがそれに返す応答（"Unknown request ..."）が届いた時点を
応答の終わりとみなす。

注意: Vanilla サーバーは 1 回の受信で届いたバイト数がパケット 1 個分と一致しないと接続を切る。
そのため複数のパケットをまとめて送ってはいけない（必ず前のパケットへの応答を受け取ってから送る）。
"""

from __future__ import annotations

import asyncio
import struct
from contextlib import suppress

TYPE_RESPONSE = 0
TYPE_COMMAND = 2
TYPE_LOGIN = 3

_HEADER = struct.Struct("<iii")  # length, request_id, type
_MAX_PACKET = 4096 + 10  # サーバーが送る 1 パケットの上限 + 余裕


class RconError(Exception):
    """RCON の通信に失敗した。"""


class RconConnectionError(RconError):
    """サーバーに接続できない、または通信が途中で切れた（サーバー停止中など）。"""


class RconAuthError(RconError):
    """パスワードが違う。"""


def encode_packet(request_id: int, packet_type: int, body: str) -> bytes:
    payload = body.encode("utf-8") + b"\x00\x00"
    return _HEADER.pack(8 + len(payload), request_id, packet_type) + payload


async def read_packet(reader: asyncio.StreamReader) -> tuple[int, int, str]:
    raw_len = await reader.readexactly(4)
    (length,) = struct.unpack("<i", raw_len)
    if not 10 <= length <= _MAX_PACKET:
        raise RconError(f"不正なパケット長です: {length}")
    data = await reader.readexactly(length)
    request_id, packet_type = struct.unpack("<ii", data[:8])
    body = data[8:-2].decode("utf-8", errors="replace")
    return request_id, packet_type, body


class RconClient:
    def __init__(self, host: str, port: int, password: str, timeout: float = 5.0) -> None:
        self._host = host
        self._port = port
        self._password = password
        self._timeout = timeout

    async def command(self, command: str) -> str:
        """コマンドを実行して応答本文を返す。"""
        try:
            return await asyncio.wait_for(self._command(command), self._timeout)
        except TimeoutError as e:
            raise RconConnectionError("RCON の応答がタイムアウトしました") from e
        except (OSError, asyncio.IncompleteReadError) as e:
            raise RconConnectionError(f"RCON に接続できません: {e}") from e

    async def _command(self, command: str) -> str:
        reader, writer = await asyncio.open_connection(self._host, self._port)
        try:
            writer.write(encode_packet(1, TYPE_LOGIN, self._password))
            await writer.drain()
            request_id, _, _ = await read_packet(reader)
            if request_id == -1:
                raise RconAuthError("RCON のパスワードが違います")

            writer.write(encode_packet(2, TYPE_COMMAND, command))
            await writer.drain()
            request_id, _, body = await read_packet(reader)
            if request_id != 2:
                raise RconError(f"想定外のリクエストIDの応答です: {request_id}")
            parts = [body]

            # 分割された残りの応答を受け取るため、終わりの目印を送る
            writer.write(encode_packet(3, TYPE_RESPONSE, ""))
            await writer.drain()
            while True:
                request_id, _, body = await read_packet(reader)
                if request_id == 3:
                    return "".join(parts)
                if request_id == 2:
                    parts.append(body)
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()
