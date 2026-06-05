import os
import sys
# プロジェクトのルートディレクトリを検索パスの最優先に追加し、Pythonパッケージのインポートを解決します。
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import discord
from discord.ext import commands
from Python.config import DISCORD_TOKEN, RIOT_API_KEY
from Python.database import DatabaseManager
from Python.riot_api import RiotAPIClient
from Python.utils.logger import logger

class RiftWatcherBot(commands.Bot):
    def __init__(self, db_manager: DatabaseManager, riot_client: RiotAPIClient, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.db = db_manager
        self.riot = riot_client

    async def setup_hook(self):
        """ボット起動前のフック。マイグレーションの実行と Cog の登録を行います。"""
        # 旧データファイルからの移行を実行
        await self.db.run_migration_if_needed("db.json")
        
        # 各 Cog のロード
        from Python.cogs.commands import WatcherCommands
        from Python.cogs.watcher import GameWatcher
        
        await self.add_cog(WatcherCommands(self, self.db, self.riot))
        await self.add_cog(GameWatcher(self, self.db, self.riot))
        logger.debug("Cogモジュール（コマンドおよび監視）を登録しました。")

    async def close(self):
        """シャットダウン時のクリーンアップ処理を行います。"""
        logger.info("シャットダウンを開始します。リソースを解放中...")
        await self.riot.close()
        await super().close()
        logger.info("Rift_Watcher は正常に終了しました。")


# --- MAIN EXECUTION ---
if __name__ == "__main__":
    if not DISCORD_TOKEN or not RIOT_API_KEY:
        logger.critical(
            "致命的エラー: 環境変数 'DISCORD_TOKEN' または 'RIOT_API_KEY' が .env ファイルに設定されていません。"
        )
        sys.exit(1)

    logger.info("Rift Watcher を起動しています")

    # データベースマネージャと API クライアントの初期化
    db_manager = DatabaseManager()
    riot_client = RiotAPIClient()

    # インテンツの設定
    intents = discord.Intents.default()
    # ギルド情報、メッセージ送信、メッセージコンテンツなどの取得に必要なインテンツ
    intents.message_content = False  # スラッシュコマンドのみを使用するためFalseでOK
    
    # ボットの初期化
    # スラッシュコマンドをメインとするためコマンドプレフィックスは適当で可
    bot = RiftWatcherBot(
        db_manager=db_manager,
        riot_client=riot_client,
        command_prefix="rw!",
        intents=intents
    )

    @bot.event
    async def on_ready():
        logger.info(f"Discord にログインしました: {bot.user}")
        logger.debug("グローバルスラッシュコマンドを同期中...")
        try:
            synced = await bot.tree.sync()
            logger.info(f"スラッシュコマンド同期完了: {len(synced)} 件")
        except Exception as e:
            logger.error(f"スラッシュコマンドの同期に失敗しました: {e}")

    try:
        bot.run(DISCORD_TOKEN, log_handler=None)  # 既存の標準ロギングとの競合を防ぐため log_handler=None
    except discord.LoginFailure:
        logger.critical("Discord へのログインに失敗しました。トークンが無効である可能性があります。")
    except Exception as e:
        logger.exception(f"ボットの実行中に例外エラーが発生しました: {e}")
