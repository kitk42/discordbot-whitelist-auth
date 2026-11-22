# discordbot-whitelist-auth

Minecraft Java 版サーバーの whitelist 登録を Discord から申請・承認する Bot です。

- 参加者は認証チャンネルで `/whitelist <Minecraft ID>` を実行して申請します。
- 管理者は管理チャンネルのボタンで承認または拒否します。
- 承認されると、Bot が RCON 経由で `whitelist add` を実行します。

詳細は `docs/` を参照してください。

| ファイル | 内容 |
|---|---|
| [docs/requirements.md](docs/requirements.md) | 要件定義 |
| [docs/environment.md](docs/environment.md) | 開発環境・本番環境の構成 |
| [docs/deploy.md](docs/deploy.md) | 本番 VM への配置手順 |

## 開発環境

前提: Ubuntu 24.04（WSL）、Python 3.12、[uv](https://docs.astral.sh/uv/)、OpenJDK 25

```bash
uv sync                      # 依存関係のインストール
cp .env.example .env         # テスト用 Bot の値を記入する
uv run python -m whitelist_bot

uv run ruff check . && uv run ruff format --check .
uv run pytest
```

### テスト用 Minecraft サーバー

`jar/server.jar` を置き、`dev/minecraft/` を作業ディレクトリとして起動します。`jar/` と `dev/minecraft/` はどちらもコミットしません。

```bash
scripts/run-dev-server.sh
```

初回起動後、`dev/minecraft/eula.txt` と `server.properties` を編集してください（[docs/environment.md](docs/environment.md) の 3.3 を参照）。
