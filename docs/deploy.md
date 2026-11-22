# 本番 VM への配置手順

Azure VM（Ubuntu 24.04）に Bot を配置する手順です。構成の考え方は `docs/environment.md` の 5 章を参照してください。

- 作業は、特に断りがない限り `azureuser` で SSH 接続して行います。
- `<...>` の部分は実際の値に置き換えてください。

## 全体の流れ

| 手順 | 内容 | Minecraft サーバーの停止 |
|---|---|---|
| 1 | 本番用の Discord Bot を準備する | なし |
| 2 | `server.properties` を変更し、RCON を有効にする | **再起動あり** |
| 3 | RCON が外部から見えないことを確認する | なし |
| 4 | `whitelist-bot` ユーザーを作り、uv をインストールする | なし |
| 5 | コードを配置する | なし |
| 6 | 設定ファイルを作る | なし |
| 7 | systemd に登録して起動する | なし |
| 8 | 動作を確認する | なし |

---

## 1. 本番用の Discord Bot を準備する

テスト用の Bot とは別に、本番用のアプリケーションを Discord Developer Portal で作成します。

1. 本番用のアプリケーションを作り、Bot のトークンを控えます。トークンは手順 6 で使います。
2. 次の URL の `<APPLICATION_ID>` を置き換えて開き、本番の Discord サーバーに招待します。

   ```
   https://discord.com/oauth2/authorize?client_id=<APPLICATION_ID>&scope=bot+applications.commands&permissions=84992
   ```

3. 管理チャンネル（管理者だけが見られるチャンネル）の権限設定で、Bot に「チャンネルを見る」「メッセージを送信」「埋め込みリンク」を許可します。
4. Discord の開発者モードを使って、次の 4 つの ID を控えます。
   - サーバー ID
   - 認証チャンネルの ID
   - 管理チャンネルの ID
   - 管理者ロールの ID

## 2. `server.properties` を変更し、RCON を有効にする

> ⚠ 最後に Minecraft サーバーを再起動します。プレイヤーがいない時間に行ってください。

```bash
# 変更前のバックアップ
sudo cp -p /srv/minecraft/server.properties /srv/minecraft/server.properties.bak-$(date +%Y%m%d)

# 現在の値を確認する
sudo grep -nE '^(white-list|enforce-whitelist|enable-rcon|rcon\.port|rcon\.password)=' /srv/minecraft/server.properties
```

RCON のパスワードを作ります。表示された値は手順 6 でも使うので控えてください。

```bash
openssl rand -base64 32 | tr -d '/+=' | cut -c1-32
```

`server.properties` を書き換えます。`<RCON_PASSWORD>` は、上で作った値に置き換えてください。

```bash
sudo sed -i \
  -e 's/^white-list=.*/white-list=true/' \
  -e 's/^enforce-whitelist=.*/enforce-whitelist=true/' \
  -e 's/^enable-rcon=.*/enable-rcon=true/' \
  -e 's/^rcon\.port=.*/rcon.port=25575/' \
  -e 's/^rcon\.password=.*/rcon.password=<RCON_PASSWORD>/' \
  /srv/minecraft/server.properties
```

書き換えたあとの状態を確認します。

```bash
# 値が反映されたか（パスワードは表示しない）
sudo grep -nE '^(white-list|enforce-whitelist|enable-rcon|rcon\.port)=' /srv/minecraft/server.properties

# 所有者とパーミッションが変わっていないか（minecraft minecraft であること）
sudo ls -l /srv/minecraft/server.properties
```

Minecraft サーバーを再起動し、RCON が起動したことを確認します。

```bash
sudo systemctl restart minecraft
sudo journalctl -u minecraft -n 60 --no-pager | grep -E 'RCON running|Done \('
# → "RCON running on 0.0.0.0:25575" が出ていれば成功
```

## 3. RCON が外部から見えないことを確認する

Vanilla の RCON は、すべての接続元（`0.0.0.0`）からの接続を待ち受けます。外部から接続できないようにしているのは、NSG だけです。そのため、実際に外から接続できないことを確かめます。

**自宅の Windows（PowerShell）から**実行します。

```powershell
Test-NetConnection <VMのパブリックIP> -Port 25575   # → TcpTestSucceeded : False であること
Test-NetConnection <VMのパブリックIP> -Port 25565   # → True（ゲーム用のポートは開いている）
```

## 4. `whitelist-bot` ユーザーを作り、uv をインストールする

```bash
# ログインできず、sudo も使えない、Bot の実行専用のシステムユーザー
sudo useradd --system --no-create-home --home-dir /nonexistent \
  --shell /usr/sbin/nologin whitelist-bot
id whitelist-bot

# uv（azureuser の ~/.local/bin にインストールされる）
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.profile
uv --version
```

## 5. コードを配置する

```bash
sudo install -d -m 0750 -o azureuser -g whitelist-bot /opt/discordbot-whitelist-auth
git clone https://github.com/<GitHubユーザー名>/discordbot-whitelist-auth.git /opt/discordbot-whitelist-auth
cd /opt/discordbot-whitelist-auth
uv sync --frozen --no-dev
```

OS 標準の Python で仮想環境が作られたことを確認します。

```bash
readlink -f .venv/bin/python   # → /usr/bin/python3.12 であること
```

`uv` が自分で取得した Python（`~/.local/share/uv/...`）を使っていると、本番では起動できません。本番のサービスには `ProtectHome=true` を設定していて、Bot からはホームディレクトリが見えないためです。

## 6. 設定ファイルを作る

```bash
sudo install -d -m 0750 -o root -g whitelist-bot /etc/whitelist-bot
sudo install -m 0640 -o root -g whitelist-bot deploy/env.example /etc/whitelist-bot/env
sudoedit /etc/whitelist-bot/env
```

`sudoedit` で開いたファイルに、次の値を記入します。

| 項目 | 値 |
|---|---|
| `DISCORD_TOKEN` | 手順 1 で控えた本番用 Bot のトークン |
| `GUILD_ID` | 手順 1 で控えた ID |
| `AUTH_CHANNEL_ID` | 手順 1 で控えた ID |
| `ADMIN_CHANNEL_ID` | 手順 1 で控えた ID |
| `ADMIN_ROLE_ID` | 手順 1 で控えた ID |
| `RCON_PASSWORD` | 手順 2 で作ったパスワード |

`RCON_HOST`、`RCON_PORT`、`DATABASE_PATH` は、見本のままで構いません。

記入したら、所有者と権限を確認します。

```bash
sudo ls -l /etc/whitelist-bot/env   # → -rw-r----- root whitelist-bot
```

## 7. systemd に登録して起動する

```bash
sudo cp deploy/whitelist-bot.service /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/whitelist-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now whitelist-bot
```

起動したかを確認します。

```bash
sudo systemctl status whitelist-bot --no-pager
sudo journalctl -u whitelist-bot -n 30 --no-pager
# → "スラッシュコマンドを 2 件同期しました" と "ログインしました" が出ていれば成功
```

## 8. 動作を確認する

| 確認すること | 方法 |
|---|---|
| 申請から承認まで | 認証チャンネルで `/whitelist <ID>` を実行し、管理チャンネルで承認する |
| サーバーへの反映 | `sudo journalctl -u minecraft -n 20 --no-pager` に `[Rcon: Added <ID> to the whitelist]` が出ている |
| データベースの場所 | `sudo ls -l /var/lib/whitelist-bot/` に `whitelist.db` がある |
| OS 再起動後の自動起動 | `sudo reboot` のあと、`systemctl is-active minecraft whitelist-bot` が両方 `active` になる |
| サンドボックスの評価（任意） | `sudo systemd-analyze security whitelist-bot`。手元の評価では 3.5 OK でした |

---

## 2 回目以降のコード更新

GitHub に push したあと、VM で次のスクリプトを実行します。

```bash
/opt/discordbot-whitelist-auth/deploy/deploy.sh
```

スクリプトが行うこと:

- `git pull`
- `uv sync`
- ユニットファイルに変更があれば、`/etc/systemd/system/` にコピーして `daemon-reload` する
- サービスを再起動して、ログを表示する

## よくあるトラブル

| 症状（`journalctl -u whitelist-bot` に出るもの） | 原因と対処 |
|---|---|
| `設定エラー: ... が設定されていません` | `/etc/whitelist-bot/env` の記入漏れです。 |
| `スラッシュコマンドを登録できません` | `GUILD_ID` が間違っているか、Bot が `applications.commands` のスコープ付きで招待されていません。 |
| 承認時に「Minecraft サーバーに接続できませんでした」 | Minecraft サーバーが止まっているか、RCON が無効です。手順 2 を確認してください。 |
| ログに `RCON のパスワードが違います` | `server.properties` の `rcon.password` と、env ファイルの `RCON_PASSWORD` が一致していません。 |
| `status=203/EXEC` で起動しない | `.venv` がない、または `whitelist-bot` ユーザーから読めません。手順 5 の権限を確認してください。 |

## データのバックアップ

申請と処理の履歴は `/var/lib/whitelist-bot/whitelist.db` に保存されています。必要に応じて、Bot を止めてからコピーしてください。

```bash
sudo systemctl stop whitelist-bot
sudo cp -p /var/lib/whitelist-bot/whitelist.db ~/whitelist-$(date +%Y%m%d).db
sudo systemctl start whitelist-bot
```
