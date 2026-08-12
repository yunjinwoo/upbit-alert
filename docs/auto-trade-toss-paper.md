# 자동매매 2단계 — 토스증권(국내주식) 모의매매(Dry-run) 엔진

- 상태: 로컬 구현·검증 완료(실제 Toss Open API 자격증명으로 시세/캔들 조회·모의 체결 확인) / 서버 PM2 배포는
  아래 "배포" 참고 / 실거래 전환 안 함
- 날짜: 2026-08-12
- 관련 파일: `app/core/toss_client.py`, `app/core/brokers/toss_broker.py`, `app/core/toss_market_analysis.py`,
  `app/core/toss_entry_condition_checker.py`, `app/core/toss_auto_trader.py`, `app/config.py`, `main.py`,
  `app/api/server.py`, `templates/toss_trade.html`, `templates/toss_trade_logs.html`, `templates/_navbar.html`,
  `app/utils/db_manager.py`(브로커 분리 마이그레이션), `tests/test_toss_trade_dry_run.py`,
  `.github/workflows/deploy.yml`
- 이전 단계: [docs/auto-trade-upbit-paper.md](auto-trade-upbit-paper.md)(업비트 1단계) — 이 문서는 그 구조를
  최대한 그대로 재사용한 2단계(국내주식/토스증권) 기록.

## 배경

업비트 1단계 문서에 "최종 목표는 업비트·KIS·토스증권 3개 거래소 지원"이라고 적어둔 대로,
`app/core/brokers/base.py`의 `BrokerClient` 추상 인터페이스와 `app/core/trade_strategy.py`/
`app/core/entry_conditions.py`(둘 다 브로커 비의존 순수 함수)를 그대로 재사용해 토스증권을
2번째 브로커로 추가했다. 업비트와 마찬가지로 **실주문 없는 dry-run(모의매매)만** 구현 — 리스크(실제 돈)가
있는 실거래는 이번에도 범위 밖.

## 왜 KIS가 아니라 토스증권 Open API인가

이 저장소엔 이미 국내주식(KIS) 연동(`app/core/kis_models.py`, `app/core/stock_monitor.py`)이 있지만, 그건
시세/랭킹 "조회" 위주로 짜여 있고 사용자가 이번엔 명시적으로
[토스증권 Open API](https://developers.tossinvest.com/docs)로 만들어달라고 요청함. 두 국내주식 소스는
공존한다 — 스크리닝 유니버스(대상 종목 목록)는 기존 KIS 시가총액 랭킹(`stock_market_cap_daily`)을
그대로 재사용하고, 실제 시세/캔들 조회와 모의 체결 판단은 토스 Open API로 한다(아래 "아키텍처" 참고).

## 아키텍처

```
app/core/toss_client.py       # 토스 Open API 클라이언트 — OAuth2(client_credentials) 토큰 캐시,
                               #   get_current_price/get_candles/get_daily_candles_extended/
                               #   get_candles_resampled(5분봉은 1분봉 리샘플링). 주문 엔드포인트 없음.
app/core/brokers/
└── toss_broker.py            # TossBroker — 시세만 toss_client로 조회, 체결은 DB 가상 원장에만 반영
                               #   (app/core/brokers/paper_broker.py와 동일 패턴, broker_name='toss')
app/core/toss_market_analysis.py       # 스크리닝(coin_screening_daily의 calc_indicators를 일봉으로 이식)
app/core/toss_entry_condition_checker.py  # 정밀 매수조건 검사 루프(entry_condition_checker.py 복제)
app/core/toss_auto_trader.py           # 오케스트레이션(auto_trader.py 복제, broker='toss' 고정)
```

`trade_strategy.py`/`entry_conditions.py`는 **한 줄도 수정하지 않았다** — 둘 다 이미 브로커 비의존
순수 함수였기 때문에 그대로 재사용.

## 실주문 안전 원칙

`grep -rn "api/v1/orders\|buy_market_order\|sell_market_order\|pyupbit\.Upbit(" app/`로 검증 가능 —
매치되는 건 전부 "이 코드가 없다"는 설명 주석뿐이어야 정상이다. 토스 Open API는 계좌·주문·조건주문
엔드포인트(`/api/v1/accounts`, `/api/v1/orders`, `/api/v1/conditional-orders` 등)도 제공하지만 이 저장소
어디에서도 호출하지 않는다 — `toss_client.py`엔 시세/캔들/종목정보 읽기 엔드포인트만 구현돼 있다.

## 스크리닝 유니버스 — 전체 KRX를 매번 스캔하지 않음

`stock_market_cap_daily`(KIS 시가총액 랭킹, `app/core/stock_monitor.run_job_market_cap_and_signal_score`가
채움)의 최신일자 상위 `Config.TOSS_SCREENING_UNIVERSE_SIZE`(기본 200)종목만 대상으로 한다. 이 랭킹 잡이
먼저(또는 같이) 돌고 있어야(`python main.py stock` 또는 `all`) 스크리닝 후보가 채워진다 — 비어 있으면
`toss_market_analysis.run_stock_screening()`이 경고 로그만 남기고 0건 저장한다.

## 스크리닝 지표 — 업비트 것을 일봉으로 이식

`app/core/upbit_market_analysis.py`의 `calc_indicators()`(200이평선 근접/일목구름 위/거래량 급증 돌파)는
OHLCV DataFrame이면 봉 종류와 무관하게 동작하는 로직이라, 4시간봉 대신 **일봉(1d)** 기준으로 그대로
이식했다(`breakout_4h` → `breakout_1d`로 필드명만 변경). 토스 캔들 API는 `count` 상한이 200이라 MA200
계산에 필요한 200개 초과분은 `before` 파라미터로 페이지네이션해서 260개 정도 확보한다
(`toss_client.get_daily_candles_extended()`).

거래량 급증 임계값은 일봉이 4시간봉보다 하루 변동폭이 커서 코인 스크리닝(3.0배)보다 낮은 2.0배로 잡음
(`app/core/toss_market_analysis.py`의 `BREAKOUT_VOL_RATIO_THRESHOLD`) — 대시보드에서 수정 불가(코드
상수), 바꾸려면 코드 수정 후 `toss-analysis-bot` 재시작 필요.

## DB — 브로커별로 완전히 분리되도록 마이그레이션

업비트 1단계 테이블 중 `paper_account`/`paper_positions`/`trade_order_log`/`trade_candidate_approval`/
`trade_condition_status`는 처음부터 `broker` 컬럼이 있어 `broker='toss'`로 그대로 재사용했다. 하지만
`trade_engine_settings`/`trade_strategy_settings`(둘 다 `id=1` 싱글톤)와 `trade_condition_settings`
(`condition_key` 단독 UNIQUE)는 브로커 구분이 없어서, 그대로 뒀다면 **토스 엔진 on/off·손절/익절 기준이
업비트와 뒤섞였을 것**이다. 그래서 `app/utils/db_manager.py`의 `init_db()` 마이그레이션에서 세 테이블을
`broker` 컬럼을 가진 새 스키마로 재생성(RENAME → CREATE → INSERT SELECT → DROP, 전부 idempotent하게
try/except로 감싼 기존 마이그레이션 패턴 그대로)했다:

- `trade_engine_settings`/`trade_strategy_settings`: `id INTEGER PRIMARY KEY CHECK(id=1)` →
  `broker TEXT UNIQUE`(브로커당 1행)로 변경, 기존 값은 `broker='upbit'`로 그대로 이관.
- `trade_condition_settings`: `condition_key UNIQUE` → `UNIQUE(broker, condition_key)`로 변경, 기존
  3개 조건 행은 `broker='upbit'`로 이관 + 토스용 3개 행도 `broker='toss'`로 새로 시딩(둘 다 기본
  `enabled=0`이라 안 건드리면 동작 안 바뀜).
- 관련 CRUD 함수(`get_trade_engine_settings`, `set_trade_engine_enabled`, `get_trade_strategy_settings`,
  `set_trade_strategy_settings`, `get_trade_condition_settings`, `set_trade_condition_setting`)에
  `broker: str = 'upbit'` 파라미터를 추가했다 — 업비트 쪽 기존 호출부는 무수정으로 그대로 동작(회귀 없음,
  로컬에서 마이그레이션 전/후 `get_trade_strategy_settings('upbit')` 값이 동일함을 확인).
- 신규 테이블 `stock_screening_daily` — `coin_screening_daily`와 동일 구조(일봉 버전).

**검증**: 실제 `alerts.db`(백업 복사본으로 먼저 검증 후 실제 DB에도 반영됨 — 마이그레이션이 여러 번
실행돼도 idempotent함을 스크래치 복사본에서 먼저 확인) 기준, 마이그레이션 후에도 업비트의 기존
`trade_order_log`(88건) 등 기존 데이터가 그대로 유지됨을 확인했다.

## 대시보드

업비트 `/auto-trade`와 완전히 동일한 구조의 `/toss-trade`, `/toss-trade/logs` 페이지를 추가했다(업비트
쪽 코드/문구는 전혀 건드리지 않음). API는 `/api/toss-trade/*`로 병렬 추가(`/api/auto-trade/*`와 요청/응답
스키마 동일, `broker='toss'`만 다름). 종목 코드는 6자리 숫자(예: `005930`)이고, 종목명 클릭 시
`https://www.tossinvest.com/stocks/A{code}/order`로 이동한다(`templates/_stock_chart_panel.html`의 기존
토스증권 링크 패턴 재사용).

## 정밀 매수조건 — 5분봉은 1분봉 리샘플링

토스 캔들 API(`GET /api/v1/candles`)는 `interval`이 `1d`/`1m`만 있고 5분봉이 없다. `m5_ma_support`
조건은 1분봉을 넉넉히 받아 pandas `resample('5min')`으로 OHLCV를 합성해서 계산한다
(`toss_client.get_candles_resampled()`). `entry_conditions.py`는 이 사실을 몰라도 되게(콜백 시그니처가
동일) 설계돼 있어 수정이 필요 없었다.

## 실행 방법

```bash
python main.py toss_analysis        # 스크리닝(30분 주기, market_cap 랭킹이 먼저 채워져 있어야 함)
python main.py toss_trade           # 매매 판단/체결 루프
python main.py toss_condition_check # 정밀 매수조건 검사 루프(선택, "정밀검사" 체크한 종목만)
```

업비트와 동일한 이유로 `main.py`의 `start_all()`(`all` 모드)에는 포함하지 않았다 — 매매 엔진 버그가
알림 프로세스를 같이 죽이는 걸 막기 위한 프로세스 격리.

## 사전 준비 (필수)

1. [developers.tossinvest.com](https://developers.tossinvest.com/docs)에서 OAuth Client ID/Secret 발급
   (WTS 콘솔) 후 실행 환경의 `.env`에 추가:
   ```
   TOSS_CLIENT_ID=...
   TOSS_CLIENT_SECRET=...
   ```
2. **WTS 콘솔에 서버 실행 환경의 아웃바운드 IP를 허용 목록에 등록**해야 한다 — 미등록 IP는 403으로
   차단된다. 로컬 개발 PC와 배포 서버(iwinv) 양쪽 IP를 각각 등록해야 함.
3. `TOSS_CLIENT_ID`/`TOSS_CLIENT_SECRET`이 없으면 `toss_client.get_current_price()` 등이 에러 로그만
   남기고 `None`을 반환한다 — 앱이 죽지는 않지만 모든 매매 판단이 "시세 조회 실패"로 SKIP된다.

## 배포

`.github/workflows/deploy.yml`에 `toss-analysis-bot`(`toss_analysis`)/`toss-trade-bot`(`toss_trade`)/
`toss-condition-check-bot`(`toss_condition_check`) 3개 PM2 프로세스를 업비트 3종과 동일한 이유로 추가.
**이 저장소는 `main` 브랜치에 push되는 순간 자동 배포되는 구조**라, 이 변경이 머지되면 서버에도 즉시
이 프로세스들이 뜬다 — 서버 `.env`에 `TOSS_CLIENT_ID`/`TOSS_CLIENT_SECRET`이 없으면 위 3번처럼
안전하게(에러 없이) 전부 SKIP만 쌓인다.

## 검증한 것 (로컬)

- `toss_client.py`: 실제 자격증명으로 `get_current_price('005930')`, `get_candles()`,
  `get_daily_candles_extended(count=260)`(200 초과 페이지네이션, 중복 없이 259개 확보 확인),
  `get_candles_resampled('minute5', ...)`(1분봉→5분봉 리샘플링) 전부 실동작 확인.
  - `before` 파라미터는 타임존 오프셋 포함 ISO8601(`오프셋 포함 isoformat()`)이어야 함을 실측으로 확인
    (날짜만 넣거나 타임존 없이 넣으면 400 `typeMismatch`).
  - 일봉 캔들의 마지막 행이 "오늘 진행 중인 캔들"임을 확인(`get_current_price()`와 거의 같은 값) —
    `calc_indicators()`의 `idx_now = len(df) - 2`(마지막 확정 캔들) 관례가 그대로 성립함.
- `toss_market_analysis.calc_indicators()`: 실제 종목(005930/196170/000660) 캔들로 지표 계산 확인.
- `TossBroker`: 스크래치 DB 복사본에서 매수→매도 가상 체결 라운드트립(현금 차감/환원 정확함) 확인.
- `toss_auto_trader`: `get_dashboard_summary()`/`run_trade_cycle()`/`force_buy()` 스크래치 DB에서 확인.
- `app/api/server.py`: Flask 라우트 12개(`/toss-trade*`, `/api/toss-trade/*`) 등록 확인, 로그인 세션
  주입 후 `/toss-trade`·`/toss-trade/logs` 200 렌더링(템플릿 오류 없음) 확인.
- `grep -rn "api/v1/orders\|buy_market_order\|sell_market_order" app/` — 매치 없음(주석 제외) 확인.

## 알려진 제약 / 다음 단계

- 스크리닝 유니버스가 KIS 시가총액 랭킹에 의존 — 그 랭킹 잡(`python main.py stock`)이 안 떠 있으면
  토스 스크리닝도 후보가 0개가 된다.
- `TOSS_SCREENING_UNIVERSE_SIZE`(기본 200종목) 스크리닝 1회에 종목당 캔들 조회 1~2회(200 초과 페이지네이션
  필요 시 2회) — API 호출 300~400회/사이클, `MARKET_DATA_CHART`(20회/초) 제한 대비 호출 간 0.1초 대기.
- 업비트 1단계처럼 실거래 전환은 이번에도 범위 밖 — 필요해지면 `TossBroker`를 상속/대체하는 실거래
  구현체를 추가하고 `mode='live'`로 분리해야 한다(현재 DB 스키마가 이미 `mode` 컬럼을 갖고 있어 준비돼
  있음).
