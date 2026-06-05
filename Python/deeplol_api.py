import aiohttp
import urllib.parse
from Python.utils.logger import logger

class DeepLoLClient:
    def __init__(self):
        self.session = None

    async def init_session(self):
        """HTTPセッションの初期化を行います。"""
        if not self.session:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/json',
                'Origin': 'https://www.deeplol.gg',
                'Referer': 'https://www.deeplol.gg/'
            }
            self.session = aiohttp.ClientSession(headers=headers)

    async def close(self):
        """セッションをクローズします。"""
        if self.session:
            await self.session.close()
            self.session = None

    def _normalize_platform_id(self, region: str) -> str:
        """region名からDeepLoLのplatform_id（サーバー名）へ変換します。 (例: jp -> JP1)"""
        r_upper = region.upper()
        if r_upper in ("JP1", "JP"):
            return "JP1"
        if r_upper == "KR":
            return "KR"
            
        # 一般的なマッピング
        mapping = {
            "NA": "NA1",
            "EUW": "EUW1",
            "EUNE": "EUN1",
            "BR": "BR1",
            "TR": "TR1",
            "OCE": "OC1",
            "LAN": "LA1",
            "LAS": "LA2",
            "RU": "RU1",
            "PH": "PH2",
            "SG": "SG2",
            "TH": "TH2",
            "TW": "TW2",
            "VN": "VN2"
        }
        return mapping.get(r_upper, r_upper)

    async def refresh_matches(self, puuid: str, region: str) -> bool:
        """DeepLoL側に対戦履歴の最新同期を要求します。"""
        await self.init_session()
        platform_id = self._normalize_platform_id(region)
        url = "https://renew.deeplol.gg/match/refresh-matches"
        payload = {
            "puu_id": puuid,
            "platform_id": platform_id,
            "queue_type": "ALL",
            "start_idx": 0,
            "count": 20
        }
        try:
            async with self.session.post(url, json=payload, timeout=30) as response:
                if response.status in (200, 201):
                    text = await response.text()
                    if "completed" in text:
                        logger.debug(f"DeepLoL refresh accepted: puuid={puuid}, region={region}")
                        return True
                    else:
                        logger.debug(f"DeepLoL refresh returned unexpected body: {text}")
                else:
                    logger.debug(f"DeepLoL refresh returned status={response.status}")
        except Exception as e:
            logger.error(f"DeepLoL更新要求中に例外が発生しました: {e}")
        return False

    async def get_match_ai_score_result(
        self,
        match_id: str,
        region: str,
        riot_id: str,
        champion_id: int,
    ) -> dict | None:
        """DeepLoLの内部APIから対象プレイヤーのAIスコアと順位を取得します。"""
        await self.init_session()
        platform_id = self._normalize_platform_id(region)
        url = f"https://b2c-api-cdn.deeplol.gg/match/match-cached?match_id={match_id}&platform_id={platform_id}"
        
        target_name = ""
        target_tag = ""
        if "#" in riot_id:
            target_name, target_tag = riot_id.split("#", 1)
            target_name = target_name.lower()
            target_tag = target_tag.lower()
            
        try:
            async with self.session.get(url, timeout=15) as response:
                if response.status == 200:
                    data = await response.json()
                    participants = data.get("participants_list", [])

                    scored_participants = []
                    for p in participants:
                        ai_score = p.get("ai_score")
                        if ai_score is None:
                            continue
                        try:
                            scored_participants.append((p, float(ai_score)))
                        except (TypeError, ValueError):
                            continue

                    score_rank_by_participant_id = {}
                    if len(scored_participants) == len(participants) and scored_participants:
                        sorted_scores = sorted(scored_participants, key=lambda item: item[1], reverse=True)
                        score_rank_by_participant_id = {
                            id(participant): rank
                            for rank, (participant, _score) in enumerate(sorted_scores, 1)
                        }

                    for p in participants:
                        p_name = (
                            p.get("riot_id_game_name")
                            or p.get("riot_id_name")
                            or p.get("game_name")
                            or p.get("summoner_name")
                            or ""
                        ).lower()
                        p_tag = (p.get("riot_id_tag_line") or "").lower()
                        p_champ_id = p.get("champion_id") or p.get("championId")
                        try:
                            p_champ_id = int(p_champ_id)
                        except (TypeError, ValueError):
                            p_champ_id = None
                        
                        name_matches = not p_name or p_name == target_name
                        if name_matches and p_tag == target_tag and p_champ_id == champion_id:
                            ai_score = p.get("ai_score")
                            if ai_score is not None:
                                return {
                                    "score": float(ai_score),
                                    "rank": score_rank_by_participant_id.get(id(p)),
                                }
                                
                    logger.debug(
                        f"DeepLoL score not ready: tag={target_tag}, champion_id={champion_id}, match_id={match_id}"
                    )
                else:
                    logger.debug(f"DeepLoL match cache returned status={response.status}: match_id={match_id}")
        except Exception as e:
            logger.error(f"DeepLoL AIスコア取得中に例外が発生しました: {e}")
        return None

    async def get_match_ai_score(self, match_id: str, region: str, riot_id: str, champion_id: int) -> float | None:
        result = await self.get_match_ai_score_result(match_id, region, riot_id, champion_id)
        if result is None:
            return None
        return result["score"]

    async def ensure_summoner_exists(self, riot_id: str, region: str) -> bool:
        """指定したRiot IDのサモナーがDeepLoLに登録されているか確認し、無ければ登録（ロード）を要求します。"""
        if "#" not in riot_id:
            return False
        name, tag = riot_id.split("#", 1)
        await self.init_session()
        platform_id = self._normalize_platform_id(region)
        
        encoded_name = urllib.parse.quote(name)
        url = f"https://b2c-api-cdn.deeplol.gg/summoner/summoner?riot_id_name={encoded_name}&riot_id_tag_line={tag}&platform_id={platform_id}"
        try:
            async with self.session.get(url, timeout=15) as response:
                if response.status == 200:
                    data = await response.json()
                    if isinstance(data, dict) and "summoner_basic_info_dict" in data:
                        logger.debug(f"DeepLoL summoner ready: {riot_id}")
                        return True
                    else:
                        logger.debug(f"DeepLoL summoner response shape unexpected: {data}")
                else:
                    logger.debug(f"DeepLoL summoner lookup returned status={response.status}: {riot_id}")
        except Exception as e:
            logger.error(f"DeepLoLサモナー存在確認中に例外が発生しました: {e}")
        return False
