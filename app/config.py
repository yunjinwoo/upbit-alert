import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Slack
    SLACK_WEBHOOK_URL = os.getenv("SLACK_TOKEN") # Using SLACK_TOKEN as webhook URL from main.py

    # Database
    DB_NAME = "alerts.db"

    # Upbit Settings
    UPBIT_INTERVALS = {
        "minutes240": "4시간봉",
        "minutes60": "1시간봉",
        "minutes15": "15분봉",
        "minutes30": "30분봉"
    }
    UPBIT_THRESHOLDS = {
        "minutes5": 10.0,
        "minutes15": 8.0,
        "minutes30": 6.0,
        "minutes60": 4.0,
        "minutes240": 3.0
    }
    UPBIT_SKIP_DURATION_ALERT = 3600 # seconds (1 hour)

    # KIS (Korean Investment & Securities) Settings
    KIS_APP_KEY = os.getenv("KIS_APP_KEY")
    KIS_APP_SECRET = os.getenv("KIS_APP_SECRET")
    KIS_URL_BASE = "https://openapi.koreainvestment.com:9443"

    # Google Sheets
    SHEET_NAME = "py-upbit-alram"
    CREDENTIALS_FILE = "credentials.json"

    # NotebookLM 연동용 별도 스프레드시트 (Signal Score 스냅샷 전용)
    # 사용자 구글 계정에서 이 이름으로 미리 만들어 서비스 계정에 편집자로 공유해둬야 함
    NOTEBOOK_SHEET_NAME = "Signal Score - NotebookLM"

    # API Server
    API_HOST = '0.0.0.0'
    API_PORT = 5000
    DEBUG = True
    APP_ROOT = os.getenv("APP_ROOT", "/")

    # 데이터 동기화 설정
    SYNC_ALLOWED_IPS = [ip.strip() for ip in os.getenv("SYNC_ALLOWED_IPS", "127.0.0.1").split(",") if ip.strip()]
    SYNC_TOKEN_TTL = 600  # 세션 유효시간(초) — 10분
