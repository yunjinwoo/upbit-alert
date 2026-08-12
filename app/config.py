import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Slack
    SLACK_WEBHOOK_URL = os.getenv("SLACK_TOKEN") # Using SLACK_TOKEN as webhook URL from main.py

    # Database
    DB_NAME = "alerts.db"

    # Upbit Settings — 분봉은 노이즈가 커서 4시간봉 하나만 대표로 남기고, 일봉/주봉을 더해 3개 타임프레임으로 감시
    UPBIT_INTERVALS = {
        "week": "주봉",
        "day": "일봉",
        "minutes240": "4시간봉"
    }
    UPBIT_THRESHOLDS = {
        "week": 2.0,
        "day": 2.5,
        "minutes240": 3.0
    }
    UPBIT_SKIP_DURATION_ALERT = 3600 # seconds (1 hour)
    UPBIT_VOL_AVG_LOOKBACK = 100     # 실시간 감시 거래량 배수 계산에 쓸 평균 봉 개수(20 → 100으로 확대, 주봉/일봉/4시간봉 공통 적용)

    # 코인 스크리닝(매매 후보 필터) — 전부 4시간봉 기준
    COIN_BREAKOUT_VOL_LOOKBACK = 20     # 거래량 평균 계산에 사용할 4시간봉 개수
    COIN_BREAKOUT_RATE_THRESHOLD = 2.0  # 돌파로 인정할 캔들 자체 등락률(%) 기준
    COIN_MA200_NEAR_PCT = 3.0           # 200이평선 "근접"으로 볼 오차 범위(%)

    # KIS (Korean Investment & Securities) Settings
    KIS_APP_KEY = os.getenv("KIS_APP_KEY")
    KIS_APP_SECRET = os.getenv("KIS_APP_SECRET")
    KIS_URL_BASE = "https://openapi.koreainvestment.com:9443"
    STOCK_VOL_AVG_LOOKBACK = 20  # 국내주식 실시간 감시 거래량 배수 계산에 쓸 평균 거래일 수(전일 대비 → 최근 N일 평균 대비)

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

    # 로그인 (잠금 토글 — 켜질 때마다 Slack으로 새 비밀번호 전송, 해제 전까지 재사용 → 세션 쿠키 유지)
    # SECRET_KEY 미설정 시 DB(app_secret 테이블)에 저장된 값을 자동으로 쓴다(없으면 최초 1회 생성) —
    # app/api/server.py 참고. .env에 값을 넣으면 그쪽이 항상 우선한다.
    SECRET_KEY = os.getenv("SECRET_KEY")
    SESSION_LIFETIME_DAYS = 30           # 로그인 유지 기간(일) — 이 기간 안엔 비밀번호 재입력 없이 세션 유지
    # 로그인 비밀번호를 어디서 발급했는지(로컬/서버)는 별도 설정값 없이 발급 시점에
    # platform.system()/platform.node()로 직접 조회함 — app/api/server.py의 _issue_new_password() 참고

    # 자동매매 1단계 — 업비트 모의매매(dry-run) 전용, 실주문 절대 없음.
    # PaperBroker(app/core/brokers/paper_broker.py)는 시세만 공개 API로 조회하고 잔고/포지션은
    # DB 가상 원장으로 시뮬레이션하므로, 이 단계에서는 Upbit access/secret 키가 필요 없음.
    TRADE_INITIAL_CASH_KRW = 1_000_000    # 가상 계좌 초기 자본금
    TRADE_MAX_POSITION_KRW = 100_000      # 1종목당 매수 금액(고정 사이징, 분할매수 없음)
    TRADE_MAX_CONCURRENT_POSITIONS = 5    # 동시 보유 가능 종목 수
    TRADE_STOP_LOSS_PCT = 5.0             # 손절 기준(%)
    TRADE_TAKE_PROFIT_PCT = 10.0          # 익절 기준(%)
    TRADE_LOOP_INTERVAL_SEC = 300         # 매매 판단 루프 주기(초)
    TRADE_SLACK_ALERT = False             # 모의 체결 시 "[모의매매]" 접두어로 Slack 알림 발송 여부 — 우선 비활성화(추후 필요 시 True로 전환)
