import aiohttp
import asyncio
from Python.config import RIOT_API_KEY, REGION_MAPPING, API_TIMEOUT
from Python.utils.logger import logger
from urllib.parse import quote

class RiotAPIClient:
    def __init__(self, api_key: str = RIOT_API_KEY):
        self.api_key = api_key
        self.headers = {"X-Riot-Token": api_key}
        self.session = None

    async def init_session(self):
        """非同期セッションを初期化します。"""
        if not self.session:
            self.session = aiohttp.ClientSession(headers=self.headers)

    async def close(self):
        """セッションをクローズします。"""
        if self.session:
            await self.session.close()
            self.session = None

    async def _request(self, url: str, max_retries: int = 3):
        """共通のリクエスト処理（レートリミットハンドリングとリトライ機能付き）"""
        await self.init_session()
        
        for attempt in range(1, max_retries + 1):
            try:
                async with self.session.get(url, timeout=API_TIMEOUT) as response:
                    # 429 Too Many Requests (レートリミット)
                    if response.status == 429:
                        # ヘッダーから再試行までの時間（秒）を取得。デフォルトは5秒
                        retry_after = int(response.headers.get("Retry-After", 5))
                        logger.warning(
                            f"Riot API レートリミットを検知。 {retry_after} 秒待機して再試行します... "
                            f"(試行回数: {attempt}/{max_retries}) - URL: {url}"
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    # 404 Not Found (正常系：データが存在しない場合)
                    if response.status == 404:
                        return None, None

                    # 403 Forbidden (APIキーの無効など)
                    if response.status == 403:
                        logger.error(f"Riot API 403 Forbidden: APIキーが不正または期限切れです。")
                        return None, "APIキーが不正または無効です。(403 Forbidden)"

                    # 5xx サーバーエラー (一時的なエラーとしてリトライ対象とする)
                    if response.status >= 500:
                        logger.warning(
                            f"Riot API サーバーエラー (ステータス: {response.status})。 "
                            f"指数バックオフで待機して再試行します... (試行回数: {attempt}/{max_retries})"
                        )
                        await asyncio.sleep(2 ** attempt)  # 指数バックオフ
                        continue

                    response.raise_for_status()
                    data = await response.json()
                    return data, None

            except asyncio.TimeoutError:
                logger.warning(
                    f"Riot API リクエストタイムアウト。 "
                    f"再試行します... (試行回数: {attempt}/{max_retries}) - URL: {url}"
                )
                await asyncio.sleep(2 ** attempt)
            except aiohttp.ClientError as e:
                logger.warning(
                    f"Riot API クライアントエラー ({e})。 "
                    f"再試行します... (試行回数: {attempt}/{max_retries}) - URL: {url}"
                )
                await asyncio.sleep(2 ** attempt)
            except Exception as e:
                logger.error(f"Riot API リクエスト中に予期せぬエラー: {e}")
                return None, f"Unexpected Error: {e}"

        logger.error(f"Riot API 最大リトライ回数 ({max_retries}) を超えたためリクエストを断念しました。 - URL: {url}")
        return None, "APIリクエストが最大リトライ回数を超えました。"

    async def get_puuid(self, riot_id: str, region: str):
        """Riot ID (GameName#TagLine) から PUUID を取得します。"""
        if "#" not in riot_id:
            return None, "Riot IDは `GameName#TagLine` の形式で入力してください。"
        game_name, tag_line = riot_id.split("#", 1)
        
        continental_routing = REGION_MAPPING.get(region, {}).get("continental")
        if not continental_routing:
            return None, f"無効な地域です: {region}"
            
        url = f"https://{continental_routing}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{quote(game_name)}/{quote(tag_line)}"
        data, err = await self._request(url)
        if err:
            return None, err
        if data and "puuid" in data:
            return data["puuid"], None
        return None, "指定されたRiot IDのプレイヤーが見つかりませんでした。"

    async def get_riot_id_by_puuid(self, puuid: str, region: str):
        """PUUID から最新の Riot ID (GameName#TagLine) を取得します。"""
        continental_routing = REGION_MAPPING.get(region, {}).get("continental")
        if not continental_routing:
            return None, f"無効な地域です: {region}"
            
        url = f"https://{continental_routing}.api.riotgames.com/riot/account/v1/accounts/by-puuid/{puuid}"
        data, err = await self._request(url)
        if err:
            return None, err
        if data and "gameName" in data and "tagLine" in data:
            return f"{data['gameName']}#{data['tagLine']}", None
        return None, "アカウント情報が見つかりませんでした。"

    async def get_active_game(self, puuid: str, region: str):
        """PUUID から現在進行中の試合情報を取得します。"""
        platform_routing = REGION_MAPPING.get(region, {}).get("platform")
        if not platform_routing:
            return None
            
        url = f"https://{platform_routing}.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/{puuid}"
        data, err = await self._request(url)
        if err:
            logger.error(f"試合情報取得失敗 (PUUID: {puuid}): {err}")
        return data

    async def get_match_details(self, match_id: str, region: str):
        """Match ID から試合の詳細な結果を取得します。"""
        continental_routing = REGION_MAPPING.get(region, {}).get("continental")
        if not continental_routing:
            return None
            
        url = f"https://{continental_routing}.api.riotgames.com/lol/match/v5/matches/{match_id}"
        data, err = await self._request(url)
        if err:
            logger.error(f"試合結果取得失敗 (Match ID: {match_id}): {err}")
        return data

    async def get_recent_match_ids(self, puuid: str, region: str, count: int = 1):
        """Fetch recent match IDs for a PUUID."""
        continental_routing = REGION_MAPPING.get(region, {}).get("continental")
        if not continental_routing:
            return None, f"Invalid region: {region}"

        url = (
            f"https://{continental_routing}.api.riotgames.com/lol/match/v5/matches/by-puuid/"
            f"{quote(puuid)}/ids?start=0&count={count}"
        )
        data, err = await self._request(url)
        if err:
            logger.error(f"Recent match ID fetch failed (PUUID: {puuid}): {err}")
            return None, err
        return data or [], None

    async def fetch_latest_champion_data(self):
        """Data Dragon から最新の LoL バージョンとチャンピオンマッピングデータを取得します。"""
        await self.init_session()
        try:
            versions_url = "https://ddragon.leagueoflegends.com/api/versions.json"
            async with self.session.get(versions_url, timeout=API_TIMEOUT) as response:
                response.raise_for_status()
                versions = await response.json()
                latest_version = versions[0]

            champions_url = f"https://ddragon.leagueoflegends.com/cdn/{latest_version}/data/ja_JP/champion.json"
            async with self.session.get(champions_url, timeout=API_TIMEOUT) as response:
                response.raise_for_status()
                champions_data = await response.json()
                
            champion_mapping = {int(info["key"]): info["name"] for champ, info in champions_data["data"].items()}
            return latest_version, champion_mapping
        except Exception as e:
            logger.error(f"Data Dragonからのチャンピオンデータ取得に失敗しました: {e}")
            return None, {}
