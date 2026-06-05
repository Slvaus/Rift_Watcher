import asyncio
from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks

from Python.config import (
    API_CALL_INTERVAL_FINISHED_GAME,
    API_CALL_INTERVAL_NEW_GAME,
    FINISHED_GAME_CHECK_INTERVAL,
    NEW_GAME_CHECK_INTERVAL,
)
from Python.utils.embed_builder import create_game_start_embed, create_match_result_embed
from Python.utils.logger import logger


class GameWatcher(commands.Cog):
    def __init__(self, bot: commands.Bot, db_manager, riot_client):
        self.bot = bot
        self.db = db_manager
        self.riot = riot_client
        self.latest_lol_version = "13.24.1"
        self.champion_data = {}

    async def cog_load(self):
        await self.load_champion_data()
        self.check_new_games_loop.start()
        self.check_finished_games_loop.start()
        logger.info("監視ループを開始しました")

    def cog_unload(self):
        self.check_new_games_loop.cancel()
        self.check_finished_games_loop.cancel()
        logger.info("監視ループを停止しました")

    async def load_champion_data(self):
        version, data = await self.riot.fetch_latest_champion_data()
        if version:
            self.latest_lol_version = version
            self.champion_data = data
            logger.info(f"チャンピオンデータをロード: {version}")
        else:
            logger.warning("チャンピオンデータをロードできませんでした。初期マッピングを使用します。")

    def get_champion_name(self, champion_id: int) -> str:
        return self.champion_data.get(champion_id, f"Unknown Champion (ID: {champion_id})")

    def calculate_decay_interval(self, last_active_str: str) -> int:
        try:
            last_active = datetime.fromisoformat(last_active_str)
        except ValueError:
            return NEW_GAME_CHECK_INTERVAL

        elapsed = (datetime.now() - last_active).total_seconds()
        if elapsed <= 86400:
            return NEW_GAME_CHECK_INTERVAL
        if elapsed <= 259200:
            return 300
        if elapsed <= 604800:
            return 900
        if elapsed <= 2592000:
            return 3600
        return 86400

    async def check_and_notify_single_summoner(self, summoner: dict):
        puuid = summoner["puuid"]
        riot_id = summoner["riot_id"]
        region = summoner["region"]
        channel_id = summoner["channel_id"]

        logger.debug(f"開始監視チェック: {riot_id} ({region})")
        game_info = await self.riot.get_active_game(puuid, region)
        now = datetime.now()

        if not game_info:
            logger.debug(f"オフライン: {riot_id}")
            last_active = summoner.get("last_active") or now.isoformat()
            interval = self.calculate_decay_interval(last_active)
            next_check = (now + timedelta(seconds=interval)).isoformat()
            await self.db.update_summoner_check_time(puuid, last_active, next_check, interval)
            return

        last_active = now.isoformat()
        next_check = (now + timedelta(seconds=NEW_GAME_CHECK_INTERVAL)).isoformat()
        await self.db.update_summoner_check_time(puuid, last_active, next_check, NEW_GAME_CHECK_INTERVAL)

        game_id = str(game_info["gameId"])
        logger.debug(f"試合中: {riot_id} / game_id={game_id}")
        if summoner.get("notified_game_id") == game_id:
            logger.debug(f"通知済み試合をスキップ: {riot_id} / game_id={game_id}")
            return

        logger.info(f"試合開始を検出: {riot_id} ({region})")
        try:
            latest_riot_id, err = await self.riot.get_riot_id_by_puuid(puuid, region)
            if err:
                logger.warning(f"最新 Riot ID の取得に失敗: puuid={puuid} error={err}")
                latest_riot_id = riot_id
            elif latest_riot_id.lower() != riot_id.lower():
                logger.info(f"Riot ID の変更を検出: {riot_id} -> {latest_riot_id}")
                summoner["riot_id"] = latest_riot_id
                await self.db.add_or_update_summoner(summoner)
                riot_id = latest_riot_id

            channel = self.bot.get_channel(channel_id)
            if not channel:
                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except discord.HTTPException:
                    logger.error(f"通知先チャンネルが見つかりません: channel_id={channel_id}")
                    return

            participant_info = next((p for p in game_info["participants"] if p["puuid"] == puuid), None)
            if not participant_info:
                logger.error(f"参加者情報に対象 PUUID が見つかりません: puuid={puuid}")
                return

            champion_name = self.get_champion_name(participant_info["championId"])
            embed = create_game_start_embed(
                riot_id,
                region,
                game_info,
                participant_info,
                self.latest_lol_version,
                champion_name,
            )

            message = await channel.send(embed=embed)
            logger.info(f"試合開始を通知: #{channel.name} / {riot_id}")

            await self.db.update_notified_game(puuid, game_id)
            await self.db.add_tracked_game(
                {
                    "puuid": puuid,
                    "match_id": f"{region.upper()}_{game_id}",
                    "region": region,
                    "channel_id": channel.id,
                    "message_id": message.id,
                    "riot_id": riot_id,
                }
            )

        except discord.Forbidden:
            logger.error(f"Discord 権限エラー: メッセージを送信できません channel_id={channel_id}")
        except Exception as e:
            logger.exception(f"通知送信処理で例外が発生しました: {e}")

    @tasks.loop(seconds=10)
    async def check_new_games_loop(self):
        summoners = await self.db.get_all_summoners()
        if not summoners:
            return

        now = datetime.now()
        to_check = []
        for summoner in summoners:
            next_check_str = summoner.get("next_check")
            try:
                next_check = datetime.fromisoformat(next_check_str) if next_check_str else now
            except ValueError:
                next_check = now

            if now >= next_check:
                to_check.append(summoner)

        if not to_check:
            return

        logger.debug(f"開始監視バッチ: {len(to_check)} / {len(summoners)} 名")
        for summoner in to_check:
            await self.check_and_notify_single_summoner(summoner)
            await asyncio.sleep(API_CALL_INTERVAL_NEW_GAME)

    @tasks.loop(seconds=FINISHED_GAME_CHECK_INTERVAL)
    async def check_finished_games_loop(self):
        tracked_games = await self.db.get_all_tracked_games()
        if not tracked_games:
            return

        logger.debug(f"終了監視バッチ: {len(tracked_games)} 件")
        for game in tracked_games:
            match_details = await self.riot.get_match_details(game["match_id"], game["region"])
            if not match_details:
                await asyncio.sleep(API_CALL_INTERVAL_FINISHED_GAME)
                continue

            logger.debug(f"試合終了を検出: {game['match_id']}")
            try:
                info = match_details.get("info", {})
                participant_info = next(
                    (p for p in info.get("participants", []) if p["puuid"] == game["puuid"]),
                    None,
                )
                if not participant_info:
                    logger.error(f"試合結果に対象プレイヤーが見つかりません: match_id={game['match_id']}")
                    continue

                channel = self.bot.get_channel(game["channel_id"])
                if not channel:
                    try:
                        channel = await self.bot.fetch_channel(game["channel_id"])
                    except discord.HTTPException:
                        logger.error(f"結果更新先チャンネルが見つかりません: channel_id={game['channel_id']}")
                        await self.db.remove_tracked_game(game["match_id"])
                        continue

                try:
                    message = await channel.fetch_message(game["message_id"])
                except discord.NotFound:
                    logger.warning(f"通知メッセージが削除されています: message_id={game['message_id']}")
                    await self.db.remove_tracked_game(game["match_id"])
                    continue
                except discord.HTTPException as e:
                    logger.error(f"通知メッセージの取得に失敗しました: {e}")
                    continue

                champion_name = self.get_champion_name(participant_info.get("championId"))
                new_embed = create_match_result_embed(
                    game,
                    info,
                    participant_info,
                    self.latest_lol_version,
                    champion_name,
                )

                await message.edit(embed=new_embed)
                logger.info(f"試合結果を更新: {game['match_id']}")
                await self.db.remove_tracked_game(game["match_id"])

            except Exception as e:
                logger.exception(f"試合終了通知更新処理で例外が発生しました: {e}")

            await asyncio.sleep(API_CALL_INTERVAL_FINISHED_GAME)

    @check_new_games_loop.before_loop
    @check_finished_games_loop.before_loop
    async def before_loops(self):
        await self.bot.wait_until_ready()
