#!/usr/bin/env bash
# 開発用 Minecraft サーバーを起動する。
# 本番 minecraft.service の構成を再現している:
#   JAR:              jar/server.jar  (本番: /opt/minecraft/server.jar)
#   作業ディレクトリ:   dev/minecraft/  (本番: /srv/minecraft)
#   JVM オプション:     -Xms512M -Xmx2G
# Ctrl+C で SIGINT を送ると正常停止する（本番の KillSignal=SIGINT と同じ）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JAR="$ROOT/jar/server.jar"
WORKDIR="$ROOT/dev/minecraft"

if [[ ! -f "$JAR" ]]; then
  echo "server.jar が見つかりません: $JAR" >&2
  exit 1
fi

mkdir -p "$WORKDIR"
cd "$WORKDIR"
exec java -Xms512M -Xmx2G -jar "$JAR" nogui
