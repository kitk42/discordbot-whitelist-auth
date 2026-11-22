#!/usr/bin/env bash
# VM 上で Bot を最新のコードに更新する。azureuser で実行する（sudo はサービス再起動にだけ使う）。
#   /opt/discordbot-whitelist-auth/deploy/deploy.sh
set -euo pipefail

APP_DIR=/opt/discordbot-whitelist-auth
UNIT_SRC="$APP_DIR/deploy/whitelist-bot.service"
UNIT_DST=/etc/systemd/system/whitelist-bot.service
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"

cd "$APP_DIR"

echo "==> git pull"
git pull --ff-only

echo "==> 依存関係の同期"
"$UV" sync --frozen --no-dev

if ! cmp -s "$UNIT_SRC" "$UNIT_DST"; then
  echo "==> ユニットファイルが変更されているため更新します"
  sudo cp "$UNIT_SRC" "$UNIT_DST"
  sudo systemctl daemon-reload
fi

echo "==> 再起動"
sudo systemctl restart whitelist-bot
sleep 5

if systemctl is-active --quiet whitelist-bot; then
  echo "==> 起動しました"
else
  echo "==> 起動に失敗しました" >&2
fi
sudo journalctl -u whitelist-bot -n 20 --no-pager
systemctl is-active --quiet whitelist-bot
