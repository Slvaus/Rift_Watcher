import asyncio
import json
import os
import shutil
import sqlite3
from datetime import datetime

from Python.config import DB_FILE, NEW_GAME_CHECK_INTERVAL
from Python.utils.logger import logger


SCHEMA_VERSION = 3


class DatabaseManager:
    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            self._create_schema(conn)
            self._migrate_schema_if_needed(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
        logger.debug("SQLite database initialized.")

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS summoners (
                puuid TEXT NOT NULL,
                riot_id TEXT NOT NULL,
                region TEXT NOT NULL,
                channel_id INTEGER NOT NULL,
                last_active TEXT NOT NULL,
                next_check TEXT NOT NULL,
                check_interval INTEGER NOT NULL,
                notified_game_id TEXT,
                PRIMARY KEY (puuid, channel_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tracked_games (
                match_id TEXT NOT NULL,
                puuid TEXT NOT NULL,
                region TEXT NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                riot_id TEXT NOT NULL,
                PRIMARY KEY (match_id, puuid, channel_id),
                FOREIGN KEY (puuid, channel_id)
                    REFERENCES summoners (puuid, channel_id)
                    ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_ai_scores (
                match_id TEXT NOT NULL,
                puuid TEXT NOT NULL,
                region TEXT NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                riot_id TEXT NOT NULL,
                match_info_json TEXT NOT NULL,
                participant_info_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (match_id, puuid, channel_id)
            )
            """
        )

    def _primary_key_columns(self, conn: sqlite3.Connection, table_name: str) -> list[str]:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return [row["name"] for row in sorted(rows, key=lambda row: row["pk"]) if row["pk"]]

    def _migrate_schema_if_needed(self, conn: sqlite3.Connection) -> None:
        summoner_pk = self._primary_key_columns(conn, "summoners")
        tracked_pk = self._primary_key_columns(conn, "tracked_games")
        if summoner_pk == ["puuid", "channel_id"] and tracked_pk == ["match_id", "puuid", "channel_id"]:
            return

        logger.info("Migrating SQLite schema for multi-channel tracking support.")
        conn.execute("PRAGMA foreign_keys = OFF")

        conn.execute("ALTER TABLE summoners RENAME TO summoners_old")
        conn.execute("ALTER TABLE tracked_games RENAME TO tracked_games_old")
        self._create_schema(conn)

        conn.execute(
            """
            INSERT OR IGNORE INTO summoners
            (puuid, riot_id, region, channel_id, last_active, next_check, check_interval, notified_game_id)
            SELECT puuid, riot_id, region, channel_id, last_active, next_check, check_interval, notified_game_id
            FROM summoners_old
            WHERE puuid IS NOT NULL AND channel_id IS NOT NULL
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO tracked_games
            (match_id, puuid, region, channel_id, message_id, riot_id)
            SELECT tg.match_id, tg.puuid, tg.region, tg.channel_id, tg.message_id, tg.riot_id
            FROM tracked_games_old tg
            WHERE tg.match_id IS NOT NULL
              AND tg.puuid IS NOT NULL
              AND tg.channel_id IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM summoners s
                  WHERE s.puuid = tg.puuid AND s.channel_id = tg.channel_id
              )
            """
        )

        conn.execute("DROP TABLE tracked_games_old")
        conn.execute("DROP TABLE summoners_old")
        conn.execute("PRAGMA foreign_keys = ON")
        logger.info("SQLite schema migration completed.")

    async def run_migration_if_needed(self, json_path: str = "db.json"):
        if not os.path.exists(json_path):
            return

        logger.info(f"Found legacy database file '{json_path}'. Migrating data to SQLite.")

        def _migrate():
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    old_db = json.load(f)
            except Exception as e:
                logger.error(f"Migration failed while loading json: {e}")
                return False

            summoners = old_db.get("summoners", [])
            notified_games = old_db.get("notified_games", {})
            tracked_games = old_db.get("tracked_games", [])
            now_iso = datetime.now().isoformat()

            try:
                with self._get_connection() as conn:
                    for s in summoners:
                        puuid = s.get("puuid")
                        channel_id = s.get("channel_id")
                        if not puuid or channel_id is None:
                            continue

                        conn.execute(
                            """
                            INSERT INTO summoners
                            (puuid, riot_id, region, channel_id, last_active, next_check, check_interval, notified_game_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(puuid, channel_id) DO UPDATE SET
                                riot_id = excluded.riot_id,
                                region = excluded.region,
                                last_active = excluded.last_active,
                                next_check = excluded.next_check,
                                check_interval = excluded.check_interval,
                                notified_game_id = excluded.notified_game_id
                            """,
                            (
                                puuid,
                                s.get("riot_id"),
                                s.get("region"),
                                channel_id,
                                s.get("last_active", now_iso),
                                s.get("next_check", now_iso),
                                s.get("check_interval", NEW_GAME_CHECK_INTERVAL),
                                notified_games.get(puuid),
                            ),
                        )

                    for tg in tracked_games:
                        match_id = tg.get("match_id")
                        puuid = tg.get("puuid")
                        channel_id = tg.get("channel_id")
                        if not match_id or not puuid or channel_id is None:
                            continue

                        exists = conn.execute(
                            "SELECT 1 FROM summoners WHERE puuid = ? AND channel_id = ?",
                            (puuid, channel_id),
                        ).fetchone()
                        if not exists:
                            continue

                        conn.execute(
                            """
                            INSERT INTO tracked_games
                            (match_id, puuid, region, channel_id, message_id, riot_id)
                            VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT(match_id, puuid, channel_id) DO UPDATE SET
                                region = excluded.region,
                                message_id = excluded.message_id,
                                riot_id = excluded.riot_id
                            """,
                            (
                                match_id,
                                puuid,
                                tg.get("region"),
                                channel_id,
                                tg.get("message_id"),
                                tg.get("riot_id"),
                            ),
                        )

                    conn.commit()

                shutil.move(json_path, f"{json_path}.bak")
                logger.info(f"Legacy data migration completed. Original file moved to '{json_path}.bak'.")
                return True
            except Exception as e:
                logger.error(f"Migration failed while writing SQLite data: {e}")
                return False

        return await asyncio.to_thread(_migrate)

    async def get_all_summoners(self) -> list[dict]:
        def _get():
            with self._get_connection() as conn:
                rows = conn.execute("SELECT * FROM summoners").fetchall()
                return [dict(row) for row in rows]

        return await asyncio.to_thread(_get)

    async def get_summoners_by_channel(self, channel_id: int) -> list[dict]:
        def _get():
            with self._get_connection() as conn:
                rows = conn.execute("SELECT * FROM summoners WHERE channel_id = ?", (channel_id,)).fetchall()
                return [dict(row) for row in rows]

        return await asyncio.to_thread(_get)

    async def add_or_update_summoner(self, summoner_data: dict) -> None:
        def _execute():
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO summoners
                    (puuid, riot_id, region, channel_id, last_active, next_check, check_interval, notified_game_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?,
                        COALESCE(
                            (SELECT notified_game_id FROM summoners WHERE puuid = ? AND channel_id = ?),
                            NULL
                        )
                    )
                    ON CONFLICT(puuid, channel_id) DO UPDATE SET
                        riot_id = excluded.riot_id,
                        region = excluded.region,
                        last_active = excluded.last_active,
                        next_check = excluded.next_check,
                        check_interval = excluded.check_interval,
                        notified_game_id = COALESCE(summoners.notified_game_id, excluded.notified_game_id)
                    """,
                    (
                        summoner_data["puuid"],
                        summoner_data["riot_id"],
                        summoner_data["region"],
                        summoner_data["channel_id"],
                        summoner_data.get("last_active", datetime.now().isoformat()),
                        summoner_data.get("next_check", datetime.now().isoformat()),
                        summoner_data.get("check_interval", NEW_GAME_CHECK_INTERVAL),
                        summoner_data["puuid"],
                        summoner_data["channel_id"],
                    ),
                )
                conn.commit()

        await asyncio.to_thread(_execute)

    async def remove_summoner(self, riot_id: str, channel_id: int) -> bool:
        def _execute():
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT puuid FROM summoners WHERE LOWER(riot_id) = LOWER(?) AND channel_id = ?",
                    (riot_id, channel_id),
                ).fetchone()
                if not row:
                    return False

                conn.execute(
                    "DELETE FROM summoners WHERE puuid = ? AND channel_id = ?",
                    (row["puuid"], channel_id),
                )
                conn.commit()
                return True

        return await asyncio.to_thread(_execute)

    async def update_summoner_check_time(
        self,
        puuid: str,
        last_active: str,
        next_check: str,
        check_interval: int,
        channel_id: int | None = None,
    ) -> None:
        def _execute():
            with self._get_connection() as conn:
                if channel_id is None:
                    conn.execute(
                        """
                        UPDATE summoners
                        SET last_active = ?, next_check = ?, check_interval = ?
                        WHERE puuid = ?
                        """,
                        (last_active, next_check, check_interval, puuid),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE summoners
                        SET last_active = ?, next_check = ?, check_interval = ?
                        WHERE puuid = ? AND channel_id = ?
                        """,
                        (last_active, next_check, check_interval, puuid, channel_id),
                    )
                conn.commit()

        await asyncio.to_thread(_execute)

    async def update_notified_game(self, puuid: str, game_id: str, channel_id: int | None = None) -> None:
        def _execute():
            with self._get_connection() as conn:
                if channel_id is None:
                    conn.execute("UPDATE summoners SET notified_game_id = ? WHERE puuid = ?", (game_id, puuid))
                else:
                    conn.execute(
                        "UPDATE summoners SET notified_game_id = ? WHERE puuid = ? AND channel_id = ?",
                        (game_id, puuid, channel_id),
                    )
                conn.commit()

        await asyncio.to_thread(_execute)

    async def clear_notified_game(self, puuid: str, channel_id: int | None = None) -> None:
        def _execute():
            with self._get_connection() as conn:
                if channel_id is None:
                    conn.execute("UPDATE summoners SET notified_game_id = NULL WHERE puuid = ?", (puuid,))
                else:
                    conn.execute(
                        "UPDATE summoners SET notified_game_id = NULL WHERE puuid = ? AND channel_id = ?",
                        (puuid, channel_id),
                    )
                conn.commit()

        await asyncio.to_thread(_execute)

    async def get_all_tracked_games(self) -> list[dict]:
        def _get():
            with self._get_connection() as conn:
                rows = conn.execute("SELECT * FROM tracked_games").fetchall()
                return [dict(row) for row in rows]

        return await asyncio.to_thread(_get)

    async def add_tracked_game(self, game_data: dict) -> None:
        def _execute():
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO tracked_games
                    (match_id, puuid, region, channel_id, message_id, riot_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(match_id, puuid, channel_id) DO UPDATE SET
                        region = excluded.region,
                        message_id = excluded.message_id,
                        riot_id = excluded.riot_id
                    """,
                    (
                        game_data["match_id"],
                        game_data["puuid"],
                        game_data["region"],
                        game_data["channel_id"],
                        game_data["message_id"],
                        game_data["riot_id"],
                    ),
                )
                conn.commit()

        await asyncio.to_thread(_execute)

    async def remove_tracked_game(self, match_id: str, puuid: str | None = None, channel_id: int | None = None) -> None:
        def _execute():
            with self._get_connection() as conn:
                if puuid is None or channel_id is None:
                    conn.execute("DELETE FROM tracked_games WHERE match_id = ?", (match_id,))
                else:
                    conn.execute(
                        "DELETE FROM tracked_games WHERE match_id = ? AND puuid = ? AND channel_id = ?",
                        (match_id, puuid, channel_id),
                    )
                conn.commit()

        await asyncio.to_thread(_execute)

    async def get_all_pending_ai_scores(self) -> list[dict]:
        def _get():
            with self._get_connection() as conn:
                rows = conn.execute("SELECT * FROM pending_ai_scores").fetchall()
                return [dict(row) for row in rows]

        return await asyncio.to_thread(_get)

    async def add_pending_ai_score(self, game_data: dict, match_info: dict, participant_info: dict) -> None:
        def _execute():
            now_iso = datetime.now().isoformat()
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO pending_ai_scores
                    (
                        match_id, puuid, region, channel_id, message_id, riot_id,
                        match_info_json, participant_info_json, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(match_id, puuid, channel_id) DO UPDATE SET
                        region = excluded.region,
                        message_id = excluded.message_id,
                        riot_id = excluded.riot_id,
                        match_info_json = excluded.match_info_json,
                        participant_info_json = excluded.participant_info_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        game_data["match_id"],
                        game_data["puuid"],
                        game_data["region"],
                        game_data["channel_id"],
                        game_data["message_id"],
                        game_data["riot_id"],
                        json.dumps(match_info, ensure_ascii=False),
                        json.dumps(participant_info, ensure_ascii=False),
                        now_iso,
                        now_iso,
                    ),
                )
                conn.commit()

        await asyncio.to_thread(_execute)

    async def remove_pending_ai_score(
        self,
        match_id: str,
        puuid: str,
        channel_id: int,
    ) -> None:
        def _execute():
            with self._get_connection() as conn:
                conn.execute(
                    "DELETE FROM pending_ai_scores WHERE match_id = ? AND puuid = ? AND channel_id = ?",
                    (match_id, puuid, channel_id),
                )
                conn.commit()

        await asyncio.to_thread(_execute)
