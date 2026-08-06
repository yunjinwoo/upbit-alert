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
    UPBIT_VOL_AVG_LOOKBACK = 20      # 실시간 감시 거래량 배수 계산에 쓸 평균 봉 개수(직전 1봉 대비 → 최근 N봉 평균 대비)

    # 코인 스크리닝(매매 후보 필터) — 전부 4시간봉 기준
    COIN_BREAKOUT_VOL_LOOKBACK = 20     # 거래량 평균 계산에 사용할 4시간봉 개수
    COIN_BREAKOUT_RATE_THRESHOLD = 2.0  # 돌파로 인정할 캔들 자체 등락률(%) 기준
    COIN_MA200_NEAR_PCT = 3.0           # 200이평선 "근접"으로 볼 오차 범위(%)

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
    SYNC_SERVER_URL = os.getenv("SYNC_SERVER_URL", "http://49.247.202.50/upbit")  # 동기화 관리 페이지 기본값과 동일
    SYNC_AUTO_LIMIT = 7  # 자동 동기화 시 전송할 최근 날짜 수
