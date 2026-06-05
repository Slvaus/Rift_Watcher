from urllib.parse import quote

import discord

from Python.config import DEEPLOL_REGION_MAP, GAME_MODE_MAPPING, QUEUE_ID_MAPPING

COLUMN_PAD = "\u2800"
FIRST_COLUMN_TITLE_SUFFIX = COLUMN_PAD * 2


def _deeplol_region(region: str) -> str:
    return DEEPLOL_REGION_MAP.get(region, region.lower().rstrip("1234567890"))


def _deeplol_riot_id(riot_id: str) -> str:
    return quote(riot_id.replace("#", "-"))


def _riot_id_name(riot_id: str) -> str:
    return riot_id.split("#", 1)[0]


def _champion_icon_url(participant_info: dict, latest_lol_version: str) -> str | None:
    champion_slug = participant_info.get("championName")
    if not champion_slug:
        return None
    return f"https://ddragon.leagueoflegends.com/cdn/{latest_lol_version}/img/champion/{champion_slug}.png"


def _profile_icon_url(participant_info: dict, latest_lol_version: str) -> str:
    profile_icon_id = participant_info.get("profileIcon") or participant_info.get("profileIconId") or 0
    return f"https://ddragon.leagueoflegends.com/cdn/{latest_lol_version}/img/profileicon/{profile_icon_id}.png"


def _format_game_duration(match_info: dict) -> str:
    duration = match_info.get("gameDuration")
    if duration is None:
        return "不明"

    try:
        duration = int(duration)
    except (TypeError, ValueError):
        return "不明"

    if duration > 10000:
        duration //= 1000

    minutes, seconds = divmod(duration, 60)
    return f"{minutes:02d}:{seconds:02d}"


def _format_kill_participation(match_info: dict, participant_info: dict) -> str:
    team_id = participant_info.get("teamId")
    team_kills = sum(
        participant.get("kills", 0)
        for participant in match_info.get("participants", [])
        if participant.get("teamId") == team_id
    )
    if team_kills <= 0:
        return "0%"

    participation = participant_info.get("kills", 0) + participant_info.get("assists", 0)
    return f"{round(participation / team_kills * 100)}%"


def _match_summary_text(match_info: dict, participant_info: dict) -> str:
    return "　|　".join(
        [
            f"結果: {'勝利' if participant_info.get('win') else '敗北'}",
            f"試合時間: {_format_game_duration(match_info)}",
            f"キル関与: {_format_kill_participation(match_info, participant_info)}",
        ]
    )


def get_game_mode_name_jp(game_info: dict) -> str:
    raw_game_mode = game_info.get("gameMode")
    queue_id = game_info.get("gameQueueConfigId")
    if queue_id is None:
        queue_id = game_info.get("queueId")
    return QUEUE_ID_MAPPING.get(
        queue_id,
        GAME_MODE_MAPPING.get(raw_game_mode, raw_game_mode or "不明なモード"),
    )


def create_game_start_embed(
    riot_id: str,
    region: str,
    game_info: dict,
    participant_info: dict,
    latest_lol_version: str,
    champion_name: str,
) -> discord.Embed:
    deeplol_url = (
        f"https://www.deeplol.gg/summoner/"
        f"{_deeplol_region(region)}/{_deeplol_riot_id(riot_id)}/ingame"
    )

    embed = discord.Embed(
        title=f"⚔️ {_riot_id_name(riot_id)} が試合を開始",
        url=deeplol_url,
        color=discord.Color.blue(),
    )
    embed.set_thumbnail(url=_profile_icon_url(participant_info, latest_lol_version))
    embed.add_field(
        name=f"ゲームモード{FIRST_COLUMN_TITLE_SUFFIX}",
        value=get_game_mode_name_jp(game_info),
        inline=True,
    )
    embed.add_field(
        name="チャンピオン",
        value=champion_name,
        inline=True,
    )
    return embed


def create_match_result_embed(
    game_track_info: dict,
    match_info: dict,
    participant_info: dict,
    latest_lol_version: str,
    champion_name: str,
    ai_score: float | None = None,
    ai_rank: int | None = None,
) -> discord.Embed:
    won = bool(participant_info.get("win"))

    # 基本の絵文字と色の設定
    title_icon = "🏆" if won else "🔥"
    color = discord.Color.green() if won else discord.Color.red()

    # AIスコアの値を取得して整数にする
    score_val = round(ai_score) if ai_score is not None else None

    # 特定条件でカラーと絵文字を変更
    if score_val is not None:
        if won:
            if score_val >= 75:
                color = discord.Color.gold()
            if ai_rank == 1:
                title_icon = "👑"
        else:
            if score_val <= 25:
                color = discord.Color.light_gray()
            if ai_rank == 10:
                title_icon = "💀"

    game_mode = get_game_mode_name_jp(match_info)
    kda = (
        f"{participant_info.get('kills', 0)}/"
        f"{participant_info.get('deaths', 0)}/"
        f"{participant_info.get('assists', 0)}"
    )

    match_url = (
        f"https://www.deeplol.gg/summoner/"
        f"{_deeplol_region(game_track_info['region'])}/"
        f"{_deeplol_riot_id(game_track_info['riot_id'])}/matches/{game_track_info['match_id']}"
    )

    embed = discord.Embed(
        title=f"{title_icon} {_riot_id_name(game_track_info['riot_id'])} の試合が終了",
        url=match_url,
        color=color,
    )
    embed.set_thumbnail(
        url=_champion_icon_url(participant_info, latest_lol_version)
        or _profile_icon_url(participant_info, latest_lol_version)
    )
    embed.add_field(name=f"ゲームモード{FIRST_COLUMN_TITLE_SUFFIX}", value=game_mode, inline=True)
    embed.add_field(
        name="チャンピオン",
        value=champion_name,
        inline=True,
    )
    embed.add_field(name="\u200b", value="\u200b", inline=True)

    # AIスコアを表示 (0-100に丸めて四捨五入、マイナス値も許容)
    if score_val is not None:
        score_text = f"**{score_val}**"
    else:
        score_text = "**--**"

    embed.add_field(name="AIスコア", value=score_text, inline=True)
    embed.add_field(
        name="KDA",
        value=kda,
        inline=True,
    )
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    embed.set_footer(text=_match_summary_text(match_info, participant_info))
    return embed
