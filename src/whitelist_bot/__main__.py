"""起動処理: python -m whitelist_bot"""

from __future__ import annotations

import logging
import sys

from dotenv import find_dotenv, load_dotenv

from .bot import WhitelistBot
from .config import ConfigError, load_config


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # 開発用: カレントディレクトリの .env を読む。
    # 既に設定済みの環境変数（本番の EnvironmentFile）は上書きしない。
    load_dotenv(find_dotenv(usecwd=True), override=False)

    try:
        config = load_config()
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 1

    logging.getLogger(__name__).info("設定を読み込みました: %r", config)
    bot = WhitelistBot(config)
    bot.run(config.discord_token, log_handler=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
