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

    # 코인 스크리닝 — 최근 N개 4시간봉 중 RSI가 임계값을 넘은 적 있는지(모멘텀 과열 감지, 표시/필터
    # 전용 — 자동매매 진입 조건에는 포함하지 않음. coin_screening.html 필터로만 사용)
    COIN_RSI_BREAKOUT_THRESHOLD = 70.0  # 이 값을 넘으면 "돌파"로 인정
    COIN_RSI_BREAKOUT_LOOKBACK = 10     # 판정에 볼 4시간봉 개수(최근 확정 캔들 기준)

    # 코인 스크리닝 — 돌파(breakout_4h)와 같은 로직을 일봉으로도 병행 계산(breakout_1d).
    # 거래량 배수 임계값은 실시간 감시 일봉 임계값(UPBIT_THRESHOLDS["day"])을 재사용하고,
    # lookback/등락률만 4시간봉과 독립적으로 튜닝할 수 있게 별도 값을 둔다.
    COIN_BREAKOUT_VOL_LOOKBACK_1D = 20     # 거래량 평균 계산에 사용할 일봉 개수
    COIN_BREAKOUT_RATE_THRESHOLD_1D = 2.0  # 돌파(일봉)로 인정할 캔들 자체 등락률(%) 기준

    # KIS (Korean Investment & Securities) Settings
    KIS_APP_KEY = os.getenv("KIS_APP_KEY")
    KIS_APP_SECRET = os.getenv("KIS_APP_SECRET")
    KIS_URL_BASE = "https://openapi.koreainvestment.com:9443"
    STOCK_VOL_AVG_LOOKBACK = 20  # 국내주식 실시간 감시 거래량 배수 계산에 쓸 평균 거래일 수(전일 대비 → 최근 N일 평균 대비)

    # 토스증권(Toss Invest) Open API Settings — 자동매매(모의) 2단계용. WTS 콘솔(developers.tossinvest.com)에서
    # 발급받은 OAuth2 Client Credentials를 .env에 TOSS_CLIENT_ID/TOSS_CLIENT_SECRET로 넣어야 동작한다.
    # (서버 실행 환경의 아웃바운드 IP를 WTS 콘솔의 허용 IP 목록에 등록해둬야 함 — 미등록 시 403)
    TOSS_CLIENT_ID = os.getenv("TOSS_CLIENT_ID")
    TOSS_CLIENT_SECRET = os.getenv("TOSS_CLIENT_SECRET")
    TOSS_API_BASE = "https://openapi.tossinvest.com"
    TOSS_SCREENING_UNIVERSE_SIZE = 200  # 스크리닝 대상 종목 수(시가총액 상위 N, stock_market_cap_daily 재사용)

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

    # 업비트 실계좌 연결(잔고 조회 + 실거래). 이 키로는 잔고 조회(GET)와 시장가 매수/매도 주문만
    # 호출하며, 출금 등 다른 권한은 코드 어디에서도 쓰지 않는다 — 발급 시 출금 권한은 부여하지 말 것.
    # 자세한 내용은 docs/auto-trade-upbit-live.md 참고.
    UPBIT_ACCESS_KEY = os.getenv("UPBIT_ACCESS_KEY")
    UPBIT_SECRET_KEY = os.getenv("UPBIT_SECRET_KEY")
    # `python main.py live_balance`(CLI 진단용)에서만 쓰는 코인 필터. 대시보드(/auto-trade)의 실거래
    # 매매 대상/실행 여부는 이제 전부 화면(DB — trade_candidate_approval mode='live',
    # trade_engine_settings mode='live')에서 제어하며 이 값을 쓰지 않는다.
    # 'BTC,ETH,SOL'처럼 마켓 접두어 없이 적어도 'KRW-BTC,KRW-ETH,KRW-SOL'로 정규화한다.
    UPBIT_SELECTED_TICKERS = [
        t if t.startswith("KRW-") else f"KRW-{t}"
        for t in (raw.strip().upper() for raw in os.getenv("UPBIT_SELECTED_TICKERS", "").split(","))
        if t
    ]

    # 자동매매 1단계 — 업비트 모의매매(dry-run) 전용, 실주문 절대 없음.
    # PaperBroker(app/core/brokers/paper_broker.py)는 시세만 공개 API로 조회하고 잔고/포지션은
    # DB 가상 원장으로 시뮬레이션하므로, 매매 판단/체결 자체에는 Upbit access/secret 키가 필요 없음.
    TRADE_INITIAL_CASH_KRW = 1_000_000    # 가상 계좌 초기 자본금
    TRADE_MAX_POSITION_KRW = 100_000      # 1종목당 매수 금액(고정 사이징, 분할매수 없음)
    TRADE_MAX_CONCURRENT_POSITIONS = 5    # 동시 보유 가능 종목 수
    TRADE_STOP_LOSS_PCT = 5.0             # 트레일링 손절 기준(%) — 진입가가 아닌 "보유 중 최고가" 대비 하락률
    TRADE_TAKE_PROFIT_PCT = 10.0          # 익절 기준(%, 평단 대비)
    TRADE_LOOP_INTERVAL_SEC = 300         # 매매 판단 루프 주기(초)
    TRADE_STOP_LOSS_CONFIRM_CYCLES = 1    # 트레일링 손절 조건이 몇 사이클 연속 유지돼야 실제 매도할지(1=즉시)
    TRADE_DCA_TRIGGER_PCT = 10.0          # 물타기(추가매수, 포지션별 체크박스로 켠 경우) 트리거 — 평단 대비 하락률(%)
    TRADE_DCA_MAX_COUNT = 2                # 포지션당 물타기(추가매수) 최대 허용 횟수(무제한 방지 안전장치)
    TRADE_PER_POSITION_CAP_KRW = 300_000   # 1종목당 총 투입원금(평단×수량) 상한 — 현재는 대시보드에 "투입/상한"
                                            # 게이지로 표시만 함(가드레일 1단계). 자동 차단은 아직 안 함.
                                            # 여유돈 생길 때마다 무한정 물타서 한 종목이 비대해지는 걸 눈으로 잡기 위함.
    TRADE_CONDITION_CHECK_INTERVAL_SEC = 60  # 정밀 매수조건(일봉/5분봉/1분봉) 검사 루프 주기(초) —
                                              # 매매 루프(TRADE_LOOP_INTERVAL_SEC)와 별개로, 대시보드에서
                                              # "정밀검사" 체크한 종목만 대상으로 이 주기로 캔들을 재조회함
    TRADE_SLACK_ALERT = True               # 체결(매수/매도/물타기) 시 Slack 알림 발송 여부 — "[🔴 실거래]"/"[모의매매]"
                                            # 라벨로 구분해서 보냄(app/core/auto_trader.py의 _execute 참고). SLACK_TOKEN
                                            # 미설정 시 send_slack_msg가 조용히 스킵하므로 켜둬도 안전함.
    TRADE_RSI_EXIT_ENABLED = False          # RSI 과매수 매도조건 on/off (기본 비활성화 — 켜기 전까진 동작 안 바뀜)
    TRADE_RSI_EXIT_PERIOD = 14              # RSI 계산 기간
    TRADE_RSI_EXIT_OVERBOUGHT = 80.0        # 이 값 이상이면 손익/트레일링과 무관하게 즉시 매도(15분봉 기준,
                                            # app/core/exit_conditions.py)

    # ── 회복형 분할 물타기(recovery DCA) — docs/auto-trade-recovery-dca.md
    # 깊은 하락에서 소액으로 나눠 물타고, 새 평단 조금 위에서 소폭 익절로 빠져나오는 걸 반복하는
    # 청산 모드. 켜면 이 포지션들에 대해 트레일링 손절(TRADE_STOP_LOSS_PCT)을 쓰지 않고 아래
    # 파라미터로만 판단한다(app/core/trade_strategy.py의 evaluate_exits 참고).
    # 기본값은 꺼짐 — 켜기 전까지 기존 동작은 전혀 바뀌지 않는다.
    TRADE_RECOVERY_DCA_ENABLED = False       # 회복형 모드 on/off (꺼져 있으면 기존 트레일링 손절/물타기 로직 그대로)
    TRADE_RECOVERY_DCA_TRIGGER_PCT = 20.0    # 평단 대비 이 % 이상 하락하면 물타기 후보(기준은 항상 "현재 평단")
    TRADE_RECOVERY_DCA_AMOUNT_KRW = 50_000   # 1회 물타기 금액(소액). 최초 매수금액보다 작게 두는 게 취지에 맞음
    TRADE_RECOVERY_DCA_COOLDOWN_MIN = 60     # 직전 물타기(없으면 최초 진입)로부터 최소 경과 시간(분) — "시간이 조금 지나서"
    TRADE_RECOVERY_TAKE_PROFIT_PCT = 5.0     # 평단 대비 이 % 이상이면 전량 매도(반등 익절). 기존 익절 기준보다 낮게 둠
    TRADE_RECOVERY_DCA_MAX_COUNT = 3         # 포지션당 회복형 물타기 최대 횟수(기존 dca_count와 별개로 셈)
    TRADE_RECOVERY_MAX_INVESTED_KRW = 250_000  # 포지션당 총 투입액(매수 누적액) 상한 — 넘기는 물타기는 실행 안 함.
                                               # "우하향 종목에 계속 사들이는" 최악의 경우 손실 원금을 여기서 끊는다.
    TRADE_RECOVERY_TIME_STOP_DAYS = 7        # 물타기 상한 소진 후 이 일수가 지나도 익절 못 하면 전량 정리(0=비활성).
                                             # "+5% 반등이 영영 안 오는 종목"에 자본이 무기한 묶이는 걸 막는 유일한 출구.
    TRADE_RECOVERY_PARTIAL_STOP_PCT = 30.0   # 물타기를 더 못 하게 된 뒤, 평단 대비 이 % 이상 하락하면 보유량 일부 매도(0=비활성)
    TRADE_RECOVERY_PARTIAL_STOP_RATIO = 20.0 # 위 조건에서 한 번에 덜어낼 보유 수량 비율(%)
    TRADE_RECOVERY_PARTIAL_STOP_COOLDOWN_MIN = 360  # 소액 손절 반복 최소 간격(분)
    TRADE_MIN_ORDER_KRW = 5_000              # 거래소 최소 주문금액 — 부분 매도 금액이 이 밑이면 쪼개지 말고 전량 매도한다
                                             # (업비트 실주문 검증값과 동일: app/core/brokers/upbit_live_broker.py)
