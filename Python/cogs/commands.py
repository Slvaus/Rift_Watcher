from datetime import datetime
import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from Python.config import NEW_GAME_CHECK_INTERVAL, REGION_MAPPING
from Python.utils.embed_builder import create_match_result_embed
from Python.utils.logger import logger


def create_list_embed(summoners: list[dict], channel: discord.TextChannel) -> discord.Embed:
    embed = discord.Embed(
        title="監視サモナー一覧",
        description=f"#{channel.name} で監視中のサモナーです。",
        color=discord.Color.blue(),
    )

    for index, summoner in enumerate(summoners, 1):
        last_active = summoner.get("last_active") or "未チェック"
        try:
            last_active = datetime.fromisoformat(last_active).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

        embed.add_field(
            name=f"{index}. {summoner['riot_id']} ({summoner['region']})",
            value=f"チェック間隔: {summoner['check_interval']}秒 | 最終検出: {last_active}",
            inline=False,
        )

    embed.set_footer(text=f"合計 {len(summoners)} 名")
    embed.timestamp = discord.utils.utcnow()
    return embed


class SummonerDropdown(discord.ui.Select):
    def __init__(self, summoners: list[dict], db_manager):
        self.db_manager = db_manager
        options = [
            discord.SelectOption(
                label=summoner["riot_id"],
                description=f"地域: {summoner['region']} | 間隔: {summoner['check_interval']}s",
                value=summoner["riot_id"],
            )
            for summoner in summoners[:25]
        ]
        super().__init__(
            placeholder="監視を解除するサモナーを選択...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        riot_id = self.values[0]
        await interaction.response.defer(ephemeral=True)

        success = await self.db_manager.remove_summoner(riot_id, interaction.channel_id)
        if not success:
            await interaction.followup.send(f"❌ `{riot_id}` の解除に失敗しました。", ephemeral=True)
            return

        logger.info(f"監視対象を削除: {riot_id}")
        updated_summoners = await self.db_manager.get_summoners_by_channel(interaction.channel_id)
        if updated_summoners:
            embed = create_list_embed(updated_summoners, interaction.channel)
            view = SummonerListView(updated_summoners, self.db_manager)
            await interaction.message.edit(embed=embed, view=view)
        else:
            embed = discord.Embed(
                title="監視サモナー一覧",
                description="このチャンネルには監視対象のサモナーが登録されていません。",
                color=discord.Color.orange(),
            )
            await interaction.message.edit(embed=embed, view=None)

        await interaction.followup.send(f"✅ `{riot_id}` の監視を解除しました。", ephemeral=True)


class SummonerListView(discord.ui.View):
    def __init__(self, summoners: list[dict], db_manager, timeout=120):
        super().__init__(timeout=timeout)
        self.message = None
        self.add_item(SummonerDropdown(summoners, db_manager))

    async def on_timeout(self):
        if not self.message:
            return

        try:
            for item in self.children:
                item.disabled = True
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass


class WatcherCommands(commands.Cog):
    def __init__(self, bot: commands.Bot, db_manager, riot_client):
        self.bot = bot
        self.db = db_manager
        self.riot = riot_client

    def _get_champion_display_context(self, champion_id: int) -> tuple[str, str]:
        watcher_cog = self.bot.get_cog("GameWatcher")
        if watcher_cog:
            return watcher_cog.latest_lol_version, watcher_cog.get_champion_name(champion_id)
        return "13.24.1", f"Unknown Champion (ID: {champion_id})"

    @app_commands.command(name="debuglatestmatch", description="指定したRiot IDの直近の試合結果メッセージを表示します。")
    @app_commands.describe(
        riot_id="確認するRiot ID (例: Faker#KR1)",
        region="対象アカウントの地域コード",
        public="True にするとチャンネルへ公開表示します。未指定では自分だけに表示します。",
    )
    @app_commands.choices(region=[app_commands.Choice(name=key, value=key) for key in REGION_MAPPING.keys()])
    async def debug_latest_match(
        self,
        interaction: discord.Interaction,
        riot_id: str,
        region: str,
        public: bool = False,
    ):
        ephemeral = not public
        await interaction.response.defer(ephemeral=ephemeral)
        logger.debug(f"/debuglatestmatch riot_id={riot_id} region={region} public={public} user={interaction.user}")

        puuid, error_message = await self.riot.get_puuid(riot_id, region)
        if error_message:
            await interaction.followup.send(f"❌ エラー: {error_message}", ephemeral=ephemeral)
            return

        match_ids, error_message = await self.riot.get_recent_match_ids(puuid, region, count=1)
        if error_message:
            await interaction.followup.send(f"❌ 直近の対戦履歴を取得できませんでした: {error_message}", ephemeral=ephemeral)
            return
        if not match_ids:
            await interaction.followup.send(f"⚠️ `{riot_id}` の直近の対戦履歴が見つかりませんでした。", ephemeral=ephemeral)
            return

        match_id = match_ids[0]
        match_details = await self.riot.get_match_details(match_id, region)
        if not match_details:
            await interaction.followup.send(f"❌ 試合詳細を取得できませんでした: `{match_id}`", ephemeral=ephemeral)
            return

        match_info = match_details.get("info", {})
        participant_info = next(
            (p for p in match_info.get("participants", []) if p.get("puuid") == puuid),
            None,
        )
        if not participant_info:
            await interaction.followup.send(
                f"❌ 試合詳細内に `{riot_id}` の参加者情報が見つかりませんでした: `{match_id}`",
                ephemeral=ephemeral,
            )
            return

        champion_id = participant_info.get("championId", 0)
        latest_lol_version, champion_name = self._get_champion_display_context(champion_id)

        # GameWatcherコグからdeeplolクライアントを利用してAIスコアを取得
        ai_score = None
        ai_rank = None
        watcher_cog = self.bot.get_cog("GameWatcher")
        if watcher_cog and hasattr(watcher_cog, "deeplol"):
            # キャッシュから取得を試みる
            ai_result = await watcher_cog.deeplol.get_match_ai_score_result(match_id, region, riot_id, champion_id)
            if ai_result is not None:
                ai_score = ai_result["score"]
                ai_rank = ai_result.get("rank")

            if ai_score is None:
                # 同期（更新）を要求し、待機して再取得
                logger.debug(f"DeepLoL AI score cache miss: {riot_id}")
                await watcher_cog.deeplol.ensure_summoner_exists(riot_id, region)
                await watcher_cog.deeplol.refresh_matches(puuid, region)
                await asyncio.sleep(4)
                ai_result = await watcher_cog.deeplol.get_match_ai_score_result(match_id, region, riot_id, champion_id)
                if ai_result is not None:
                    ai_score = ai_result["score"]
                    ai_rank = ai_result.get("rank")

        embed = create_match_result_embed(
            {"riot_id": riot_id, "region": region, "match_id": match_id},
            match_info,
            participant_info,
            latest_lol_version,
            champion_name,
            ai_score,
            ai_rank,
        )

        await interaction.followup.send(
            content=f"Debug latest match: `{match_id}`",
            embed=embed,
            ephemeral=ephemeral,
        )

    @app_commands.command(name="summonerset", description="監視対象のサモナーを登録・更新します。")
    @app_commands.describe(riot_id="監視するRiot ID (例: Faker#KR1)", region="対象アカウントの地域コード")
    @app_commands.choices(region=[app_commands.Choice(name=key, value=key) for key in REGION_MAPPING.keys()])
    async def summoner_set(self, interaction: discord.Interaction, riot_id: str, region: str):
        await interaction.response.defer(ephemeral=True)
        logger.debug(f"/summonerset riot_id={riot_id} region={region} user={interaction.user}")

        puuid, error_message = await self.riot.get_puuid(riot_id, region)
        if error_message:
            await interaction.followup.send(f"❌ エラー: {error_message}", ephemeral=True)
            return

        now_iso = datetime.now().isoformat()
        summoner_entry = {
            "riot_id": riot_id,
            "puuid": puuid,
            "region": region,
            "channel_id": interaction.channel_id,
            "last_active": now_iso,
            "next_check": now_iso,
            "check_interval": NEW_GAME_CHECK_INTERVAL,
        }
        await self.db.add_or_update_summoner(summoner_entry)
        logger.info(f"監視対象を登録: {riot_id} ({region})")

        await interaction.followup.send(
            f"✅ `{riot_id}` を監視リストに追加しました。\n"
            f"間隔: {NEW_GAME_CHECK_INTERVAL} 秒で試合開始を監視します。",
            ephemeral=True,
        )

        watcher_cog = self.bot.get_cog("GameWatcher")
        if watcher_cog:
            logger.debug(f"登録直後チェックを予約: {riot_id}")
            self.bot.loop.create_task(watcher_cog.check_and_notify_single_summoner(summoner_entry))

    async def summoner_remove_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        summoners = await self.db.get_summoners_by_channel(interaction.channel_id)
        choices = [
            app_commands.Choice(name=s["riot_id"], value=s["riot_id"])
            for s in summoners
            if current.lower() in s["riot_id"].lower()
        ]
        return choices[:25]

    @app_commands.command(name="summonerremove", description="このチャンネルの監視リストからサモナーを削除します。")
    @app_commands.autocomplete(riot_id=summoner_remove_autocomplete)
    @app_commands.describe(riot_id="削除するRiot ID")
    async def summoner_remove(self, interaction: discord.Interaction, riot_id: str):
        logger.debug(f"/summonerremove riot_id={riot_id} user={interaction.user}")

        success = await self.db.remove_summoner(riot_id, interaction.channel_id)
        if success:
            logger.info(f"監視対象を削除: {riot_id}")
            await interaction.response.send_message(f"✅ `{riot_id}` を監視リストから削除しました。", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"❌ `{riot_id}` はこのチャンネルの監視リストに見つかりませんでした。",
                ephemeral=True,
            )

    @app_commands.command(name="summonerslist", description="このチャンネルで監視中のサモナー一覧を表示します。")
    async def summoners_list(self, interaction: discord.Interaction):
        logger.debug(f"/summonerslist user={interaction.user}")

        summoners = await self.db.get_summoners_by_channel(interaction.channel_id)
        if not summoners:
            await interaction.response.send_message(
                "このチャンネルには監視対象のサモナーが登録されていません。",
                ephemeral=True,
            )
            return

        view = SummonerListView(summoners, self.db)
        await interaction.response.send_message(
            embed=create_list_embed(summoners, interaction.channel),
            view=view,
            ephemeral=False,
        )
        view.message = await interaction.original_response()


async def setup(bot: commands.Bot):
    pass
