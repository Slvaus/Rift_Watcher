import os
import sys
from dotenv import load_dotenv

# --- INITIAL SETTING FUNCTION (対話型初期設定) ---
def setup_env_interactively():
    if os.path.exists(".env"):
        load_dotenv()
        if os.getenv("DISCORD_TOKEN") and os.getenv("RIOT_API_KEY"):
            return

    if not sys.stdin.isatty():
        return

    print("==================================================")
    print("        Rift_Watcher 対話型初期セットアップ")
    print("==================================================")
    print(".env ファイルが見つからないか、トークンが設定されていません。")
    print("以下に設定値を入力してください（Enterキーでスキップできます）。")
    print("※設定値はプロジェクトルートの .env ファイルに保存されます。")
    print("--------------------------------------------------")
    
    try:
        discord_token = input("Discord Bot Token: ").strip()
        riot_api_key = input("Riot API Key: ").strip()
    except KeyboardInterrupt:
        print("\nセットアップがキャンセルされました。")
        return

    if discord_token or riot_api_key:
        with open(".env", "w", encoding="utf-8") as f:
            f.write(f'DISCORD_TOKEN="{discord_token}"\n')
            f.write(f'RIOT_API_KEY="{riot_api_key}"\n')
        print("--------------------------------------------------")
        print("✅ .env ファイルを作成し、設定を保存しました。\n")

# 初期セットアップの実行
setup_env_interactively()
load_dotenv()

# --- ENVIRONMENT VARIABLES ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
RIOT_API_KEY = os.getenv("RIOT_API_KEY")

# --- DATABASE & LOGGING CONFIG ---
DB_FILE = "db.sqlite"  # SQLite への移行を見据えて db.sqlite とする
LOG_FILE = "rift_watcher.log"

# --- INTERVALS & TIMEOUTS (秒) ---
NEW_GAME_CHECK_INTERVAL = 60
FINISHED_GAME_CHECK_INTERVAL = 180
COMMAND_PROCESSOR_INTERVAL = 1

API_TIMEOUT = 10
API_CALL_INTERVAL_NEW_GAME = 2
API_CALL_INTERVAL_FINISHED_GAME = 5

# --- RIOT API & DATA DRAGON MAPPING DATA ---
REGION_MAPPING = {
    "BR1": {"platform": "BR1", "continental": "AMERICAS"},
    "EUN1": {"platform": "EUN1", "continental": "EUROPE"},
    "EUW1": {"platform": "EUW1", "continental": "EUROPE"},
    "JP1": {"platform": "JP1", "continental": "ASIA"},
    "KR": {"platform": "KR", "continental": "ASIA"},
    "LA1": {"platform": "LA1", "continental": "AMERICAS"},
    "LA2": {"platform": "LA2", "continental": "AMERICAS"},
    "NA1": {"platform": "NA1", "continental": "AMERICAS"},
    "OC1": {"platform": "OC1", "continental": "SEA"},
    "TR1": {"platform": "TR1", "continental": "EUROPE"},
    "RU": {"platform": "RU", "continental": "EUROPE"},
    "PH2": {"platform": "PH2", "continental": "SEA"},
    "SG2": {"platform": "SG2", "continental": "SEA"},
    "TH2": {"platform": "TH2", "continental": "SEA"},
    "TW2": {"platform": "TW2", "continental": "SEA"},
    "VN2": {"platform": "VN2", "continental": "SEA"},
}

QUEUE_ID_MAPPING = {
    400: "ノーマル (ドラフト)",
    420: "ランク (ソロ/デュオ)",
    430: "ノーマル (ブラインド)",
    440: "ランク (フレックス)",
    450: "ランダムミッド (ARAM)",
    700: "Clash",
    1700: "アリーナ",
    1900: "URF",
}

DEEPLOL_REGION_MAP = {
    "BR1": "br",
    "EUN1": "eune",
    "EUW1": "euw",
    "JP1": "jp",
    "KR": "kr",
    "LA1": "lan",
    "LA2": "las",
    "NA1": "na",
    "OC1": "oce",
    "TR1": "tr",
    "RU": "ru",
    "PH2": "ph",
    "SG2": "sg",
    "TH2": "th",
    "TW2": "tw",
    "VN2": "vn"
}
