# 開発環境・本番環境の構成案

`docs/requirements.md` の「8. 開発と運用の方針」を具体化した案です。本番 VM の現状（既存の `minecraft.service` の構成など）は、非公開の構成メモをもとにしています。

## 1. 全体像

```
┌─ 開発: WSL (Ubuntu 24.04) ── 自分のユーザーで実行 ─┐
│  Bot (テスト用トークン, .env)                      │
│    │ RCON 127.0.0.1:25575                       │
│    ▼                                            │
│  テスト用 Minecraft 26.3 (dev/minecraft/, Java 25)│
└──────────────┬─────────────────────────────────┘
               ▼ Discord API
        テスト用 Discord サーバー

        git push → GitHub (公開) → git pull

┌─ 本番: Azure VM (Ubuntu 24.04) ─────────────────┐
│  whitelist-bot.service  (User=whitelist-bot)     │
│    │ RCON 127.0.0.1:25575 (NSG で外部非公開)       │
│    ▼                                            │
│  minecraft.service      (User=minecraft, 既存)   │
│    /opt/minecraft/server.jar, /srv/minecraft     │
└──────────────┬─────────────────────────────────┘
               ▼
        本番 Discord サーバー
```

**Bot と Minecraft サーバーのやり取りは RCON だけにします。**

本番の `/srv/minecraft` は、所有者が `minecraft:minecraft` で、他のユーザーからのアクセスを禁止しています（`o-rwx`）。そのため、Bot は `whitelist.json` などのファイルを直接読みません。whitelist に登録済みかどうかも、RCON の `whitelist list` で確認します。

## 2. 本番 VM との対応（WSL で再現するもの・しないもの）

WSL ではユーザー構成を再現しません。Bot の動作確認に必要な機能だけを再現します。

| 本番 VM | WSL での扱い |
|---|---|
| OpenJDK 25（`/usr/lib/jvm/java-25-openjdk-amd64`） | **再現します。** apt で `openjdk-25-jre-headless` を入れます。`25.0.5.1+2-1~24.04.1` がインストールできることを確認済みです。 |
| 起動オプション `-Xms512M -Xmx2G -jar server.jar nogui` | **再現します。** 起動スクリプトで同じオプションを使います。 |
| `KillSignal=SIGINT` による正常な停止 | **再現します。** 起動スクリプトで java を `exec` で起動するので、Ctrl+C で同じように停止します。 |
| `server.properties`（whitelist と RCON） | **再現します。** RCON の設定は本番と同じ値にします。パスワードだけは開発用のものを使います。 |
| `/opt/minecraft` と `/srv/minecraft` の分離 | **再現します。** `jar/server.jar` と `dev/minecraft/` に分けて置きます。 |
| `minecraft` ユーザーと `whitelist-bot` ユーザー | 再現しません。自分のユーザーで実行します。 |
| `minecraft.service` と `whitelist-bot.service` | 再現しません。WSL でも systemd は動いていますが、サービスとしては登録しません。代わりに、作成した Bot のユニットファイルを `systemd-analyze verify` で検証します。 |
| fail2ban、AMA（Azure Monitor Agent）、SSH の設定 | 対象外です。Bot とは関係がありません。 |

## 3. 開発環境（WSL）

### 3.1 現状（確認済み）

| 項目 | 状態 |
|---|---|
| OS | Ubuntu 24.04.5 LTS。本番 VM と同じです。 |
| Python | 3.12.3。Ubuntu 標準の Python で、本番 VM でも同じものを使います。 |
| git / gh | インストール済み |
| uv | 未インストール |
| Java | 未インストール。apt から OpenJDK 25 を入れられます。 |
| メモリ | 7.4GB（空き 5.7GB）。テスト用サーバーに最大 2GB を割り当てても足ります。 |

Dev Container は使いません。WSL と本番 VM は OS も Python のバージョンも同じなので、コンテナで環境をそろえる利点が小さいためです。

### 3.2 ツールとライブラリ

| 用途 | 採用するもの |
|---|---|
| Python の依存関係と仮想環境の管理 | uv（`pyproject.toml` と `uv.lock` で管理。Python 本体は OS 標準のものを使う。理由は 5.2 を参照） |
| リンターとフォーマッター | ruff |
| テスト | pytest、pytest-asyncio |
| Bot の実行時に使うライブラリ | discord.py、aiosqlite、python-dotenv |
| RCON クライアント | 外部ライブラリを使わず、`asyncio` で自前実装する（50行程度） |

### 3.3 テスト用 Minecraft サーバー

本番と同じく、JAR とサーバーの作業ディレクトリを分けて置きます。`jar/` と `dev/minecraft/` は、どちらも git の管理対象から外します。

```
jar/server.jar        ← 本番の /opt/minecraft/server.jar に相当。あなたが置いたファイル（Claude はダウンロードしない）
dev/minecraft/        ← 本番の /srv/minecraft に相当
  eula.txt            ← 利用規約への同意なので、あなたが eula=true に書き換える
  server.properties
  world/ ...
```

`server.properties` の主な設定:

```
online-mode=true
white-list=true
enforce-whitelist=true
enable-rcon=true
rcon.port=25575
rcon.password=<開発用のパスワード>
```

`scripts/run-dev-server.sh` は、`dev/minecraft` に移動してから次のコマンドを実行するだけのスクリプトです。

```
exec java -Xms512M -Xmx2G -jar ../../jar/server.jar nogui
```

### 3.4 テスト用 Discord

- Discord Developer Portal で、本番とは別のテスト用アプリケーション（Bot）を作成してください。
- テスト用の Discord サーバーに、認証チャンネル、管理チャンネル、管理者ロールを作成してください。
- スラッシュコマンドは、特定の Discord サーバーだけで使えるように登録します。全体に登録する方式と違って、変更がすぐに反映されます。

## 4. リポジトリの構成

```
discordbot-whitelist-auth/
├── src/whitelist_bot/
│   ├── __main__.py      # 起動処理
│   ├── config.py        # 環境変数の読み込みと値の検証
│   ├── bot.py           # Bot 本体と persistent view の登録
│   ├── commands.py      # /whitelist と /unwhitelist
│   ├── views.py         # 承認・拒否ボタンと拒否理由の Modal
│   ├── service.py       # 申請のルールと状態の管理（Discord に依存しない部分）
│   ├── repository.py    # SQLite の読み書き
│   ├── rcon.py          # RCON クライアント
│   └── tasks.py         # 期限切れを定期的に確認する処理
├── tests/
├── deploy/
│   ├── whitelist-bot.service   # systemd のユニットファイル
│   ├── env.example             # 本番用の環境変数ファイルの見本
│   └── deploy.sh               # VM 上でコードを更新するスクリプト
├── scripts/
│   └── run-dev-server.sh
├── dev/minecraft/       # git の管理対象外
├── docs/
├── .env.example         # 開発用の設定の見本
├── .gitignore
├── pyproject.toml
├── uv.lock
└── README.md
```

### 4.1 設定項目（環境変数）

```
DISCORD_TOKEN=
GUILD_ID=
AUTH_CHANNEL_ID=
ADMIN_CHANNEL_ID=
ADMIN_ROLE_ID=
RCON_HOST=127.0.0.1
RCON_PORT=25575
RCON_PASSWORD=
DATABASE_PATH=
```

| 環境 | 設定の読み込み元 | `DATABASE_PATH` |
|---|---|---|
| 開発 | リポジトリ直下の `.env`（python-dotenv で読み込む） | `data/whitelist.db` |
| 本番 | `/etc/whitelist-bot/env`（systemd の `EnvironmentFile` で読み込む） | `/var/lib/whitelist-bot/whitelist.db` |

本番の秘密情報をリポジトリの外に置くことで、`git pull` で上書きされたり、誤ってコミットしたりする心配がなくなります。

### 4.2 git の管理対象から外すもの

`.env`、`data/`、`*.db`、`dev/minecraft/`、`server.jar`、`.venv/`

## 5. 本番環境（Azure VM）

### 5.1 配置

| 項目 | 内容 |
|---|---|
| コード | `/opt/discordbot-whitelist-auth`。`git clone` で配置し、所有者は `azureuser:whitelist-bot`、権限は `0750` にする |
| 実行ユーザー | `whitelist-bot`。ログインできないシステムユーザーにし、sudo の権限は与えない。`minecraft` ユーザーと同じ作り方にする |
| 環境変数ファイル | `/etc/whitelist-bot/env`。所有者は `root:whitelist-bot`、権限は `0640` |
| データベース | `/var/lib/whitelist-bot/`。systemd の `StateDirectory` で作成させる |
| ログ | journald に記録し、`journalctl -u whitelist-bot` で確認する |

### 5.2 Python 本体は OS 標準のものを使う

uv は、自分で取得した Python をホームディレクトリの下に置くことがあります。一方、Bot のユニットファイルには `ProtectHome=true` を設定するので、Bot からはホームディレクトリが見えません。そのため、`pyproject.toml` で `python-preference = "only-system"` を指定し、`/usr/bin/python3.12` を使って仮想環境を作るようにします。

### 5.3 systemd のユニットファイル

ユニットファイルは `deploy/whitelist-bot.service` です。既存の `minecraft.service` と同じ水準の制限に加えて、`PrivateDevices` や `RestrictNamespaces` などの制限もかけています。

| ユニット | `systemd-analyze security` の評価（手元でオフライン評価） |
|---|---|
| `minecraft.service`（既存） | 5.8 MEDIUM |
| `whitelist-bot.service` | 3.5 OK |

- `After=minecraft.service` は起動の順番を指定するだけで、依存関係は持たせません（`Requires` は使わない）。そのため、Minecraft サーバーが止まっていても、Bot は申請の受け付けを続けられます。
- `ProtectSystem=strict` により、Bot が書き込めるのは `StateDirectory` で作られる `/var/lib/whitelist-bot` だけになります。

### 5.4 配置とデプロイの手順

VM での作業手順は `docs/deploy.md` にまとめています。2 回目以降の更新は `deploy/deploy.sh` で行います。

### 5.5 RCON の公開範囲

RCON のポートは Azure の NSG で拒否されています（受信を許可しているのは 25565 と、自宅 IP からの 22 だけ）。

VM のファイアウォール（ufw）は現在無効です。ufw も有効にすれば二重の防御になりますが、設定を誤ると SSH で接続できなくなる危険があります。また、Bot の開発とは別の作業なので、このプロジェクトの対象外にします。

## 6. CI（GitHub Actions）

push のたびに、ruff と pytest だけを実行します。自動デプロイと違って VM には接続しないので、SSH のポート 22 を開ける必要はありません。

## 7. 構築の手順

| 順番 | 作業 | 担当 |
|---|---|---|
| 1 | uv をインストールし、`pyproject.toml`、`.gitignore`、`.env.example` を用意する | Claude |
| 2 | 中身の処理を入れずに、Bot の骨組みと ruff・pytest の設定を作る | Claude |
| 3 | テスト用の Discord アプリケーションとサーバーを作成する | あなた |
| 4 | OpenJDK 25 をインストールする（`sudo apt install openjdk-25-jre-headless`） | あなた（sudo が必要なため。`! コマンド` で実行できます） |
| 5 | `server.jar` を `jar/` に置き（済み）、`eula.txt` に同意する | あなた |
| 6 | テスト用 Minecraft サーバーを起動し、RCON の応答を確認する（requirements.md の「7. 実装時に検証する事項」） | Claude |
| 7 | 要件定義の機能を実装する | Claude |
| 8 | CI を設定し、GitHub に公開リポジトリを作る | Claude（リポジトリの作成前に確認を取る） |
| 9 | VM の事前作業（5.4）を行い、Bot を配置して systemd に登録する | あなた（手順書は Claude が用意する） |
