import sqlite3
import os
import json
import shutil
import asyncio
from datetime import datetime
from Python.config import DB_FILE, NEW_GAME_CHECK_INTERVAL
from Python.utils.logger import logger

class DatabaseManager:
    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self):
        """SQLite コネクションを取得します。"""
        # dict_factoryを使用して、カラム名をキーとする辞書としてレコードを取得できるようにする
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """テーブルの初期化を行います。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # summoners テーブルの作成
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS summoners (
                    puuid TEXT PRIMARY KEY,
                    riot_id TEXT NOT NULL,
                    region TEXT NOT NULL,
                    channel_id INTEGER NOT NULL,
                    last_active TEXT NOT NULL,
                    next_check TEXT NOT NULL,
                    check_interval INTEGER NOT NULL,
                    notified_game_id TEXT
                )
            ''')
            
            # tracked_games テーブルの作成
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tracked_games (
                    match_id TEXT PRIMARY KEY,
                    puuid TEXT NOT NULL,
                    region TEXT NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    riot_id TEXT NOT NULL,
                    FOREIGN KEY (puuid) REFERENCES summoners (puuid) ON DELETE CASCADE
                )
            ''')
            
            conn.commit()
        logger.debug("SQLite データベースが正常に初期化されました。")

    async def run_migration_if_needed(self, json_path: str = "db.json"):
        """既存の db.json がある場合、自動的にデータを SQLite に移行します。"""
        if not os.path.exists(json_path):
            return

        logger.info(f"既存のデータベースファイル '{json_path}' を検出しました。SQLite への移行を開始します。")

        def _migrate():
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    old_db = json.load(f)
            except Exception as e:
                logger.error(f"移行エラー: jsonのロードに失敗しました: {e}")
                return False

            summoners = old_db.get('summoners', [])
            notified_games = old_db.get('notified_games', {})
            tracked_games = old_db.get('tracked_games', [])

            now_iso = datetime.now().isoformat()

            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()

                    # サモナーデータの移行
                    for s in summoners:
                        puuid = s.get('puuid')
                        if not puuid:
                            continue
                        
                        # notified_games からこのサモナーの最後の通知済み試合IDを取得
                        notified_game_id = notified_games.get(puuid)

                        cursor.execute('''
                            INSERT OR REPLACE INTO summoners 
                            (puuid, riot_id, region, channel_id, last_active, next_check, check_interval, notified_game_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            puuid,
                            s.get('riot_id'),
                            s.get('region'),
                            s.get('channel_id'),
                            s.get('last_active', now_iso),
                            s.get('next_check', now_iso),
                            s.get('check_interval', NEW_GAME_CHECK_INTERVAL),
                            notified_game_id
                        ))

                    # 追跡中ゲームデータの移行
                    for tg in tracked_games:
                        match_id = tg.get('match_id')
                        puuid = tg.get('puuid')
                        if not match_id or not puuid:
                            continue

                        cursor.execute('''
                            INSERT OR REPLACE INTO tracked_games 
                            (match_id, puuid, region, channel_id, message_id, riot_id)
                            VALUES (?, ?, ?, ?, ?, ?)
                        ''', (
                            match_id,
                            puuid,
                            tg.get('region'),
                            tg.get('channel_id'),
                            tg.get('message_id'),
                            tg.get('riot_id')
                        ))

                    conn.commit()
                
                # バックアップのためにリネーム
                shutil.move(json_path, f"{json_path}.bak")
                logger.info(f"データの移行が正常に完了しました。元のファイルは '{json_path}.bak' にリネームされました。")
                return True
            except Exception as e:
                logger.error(f"移行処理中にエラーが発生しました: {e}")
                return False

        await asyncio.to_thread(_migrate)

    # --- SUMMONERS CRUD OPERATIONS ---

    async def get_all_summoners(self) -> list[dict]:
        """すべての監視対象サモナーを取得します。"""
        def _get():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM summoners')
                return [dict(row) for row in cursor.fetchall()]
        return await asyncio.to_thread(_get)

    async def get_summoners_by_channel(self, channel_id: int) -> list[dict]:
        """特定のDiscordチャンネルで監視しているすべてのサモナーを取得します。"""
        def _get():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM summoners WHERE channel_id = ?', (channel_id,))
                return [dict(row) for row in cursor.fetchall()]
        return await asyncio.to_thread(_get)

    async def add_or_update_summoner(self, summoner_data: dict) -> None:
        """サモナーをデータベースに追加または更新します。"""
        def _execute():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO summoners 
                    (puuid, riot_id, region, channel_id, last_active, next_check, check_interval, notified_game_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 
                            COALESCE((SELECT notified_game_id FROM summoners WHERE puuid = ?), NULL))
                ''', (
                    summoner_data['puuid'],
                    summoner_data['riot_id'],
                    summoner_data['region'],
                    summoner_data['channel_id'],
                    summoner_data.get('last_active', datetime.now().isoformat()),
                    summoner_data.get('next_check', datetime.now().isoformat()),
                    summoner_data.get('check_interval', NEW_GAME_CHECK_INTERVAL),
                    summoner_data['puuid']
                ))
                conn.commit()
        await asyncio.to_thread(_execute)

    async def remove_summoner(self, riot_id: str, channel_id: int) -> bool:
        """特定のチャンネルの監視リストからサモナーを削除します。"""
        def _execute():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 削除対象の puuid を特定
                cursor.execute('SELECT puuid FROM summoners WHERE LOWER(riot_id) = LOWER(?) AND channel_id = ?', (riot_id, channel_id))
                row = cursor.fetchone()
                if not row:
                    return False
                
                puuid = row['puuid']
                
                # サモナー削除 (CASCADEにより関連するtracked_gamesも自動で削除されます)
                cursor.execute('DELETE FROM summoners WHERE puuid = ? AND channel_id = ?', (puuid, channel_id))
                cursor.execute('DELETE FROM tracked_games WHERE puuid = ?', (puuid,))
                
                conn.commit()
                return True
        return await asyncio.to_thread(_execute)

    async def update_summoner_check_time(self, puuid: str, last_active: str, next_check: str, check_interval: int) -> None:
        """サモナーの監視時間設定を更新します。"""
        def _execute():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE summoners 
                    SET last_active = ?, next_check = ?, check_interval = ? 
                    WHERE puuid = ?
                ''', (last_active, next_check, check_interval, puuid))
                conn.commit()
        await asyncio.to_thread(_execute)

    async def update_notified_game(self, puuid: str, game_id: str) -> None:
        """サモナーの最終通知済みゲームIDを更新します。"""
        def _execute():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('UPDATE summoners SET notified_game_id = ? WHERE puuid = ?', (game_id, puuid))
                conn.commit()
        await asyncio.to_thread(_execute)

    async def clear_notified_game(self, puuid: str) -> None:
        """サモナーの最終通知済みゲームIDをクリアします。"""
        def _execute():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('UPDATE summoners SET notified_game_id = NULL WHERE puuid = ?', (puuid,))
                conn.commit()
        await asyncio.to_thread(_execute)

    # --- TRACKED GAMES OPERATIONS ---

    async def get_all_tracked_games(self) -> list[dict]:
        """追跡中（進行中）のすべての試合を取得します。"""
        def _get():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM tracked_games')
                return [dict(row) for row in cursor.fetchall()]
        return await asyncio.to_thread(_get)

    async def add_tracked_game(self, game_data: dict) -> None:
        """追跡中（進行中）の試合を登録します。"""
        def _execute():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO tracked_games 
                    (match_id, puuid, region, channel_id, message_id, riot_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    game_data['match_id'],
                    game_data['puuid'],
                    game_data['region'],
                    game_data['channel_id'],
                    game_data['message_id'],
                    game_data['riot_id']
                ))
                conn.commit()
        await asyncio.to_thread(_execute)

    async def remove_tracked_game(self, match_id: str) -> None:
        """追跡対象の試合を削除（終了時の更新完了後など）します。"""
        def _execute():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM tracked_games WHERE match_id = ?', (match_id,))
                conn.commit()
        await asyncio.to_thread(_execute)
