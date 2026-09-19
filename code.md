# 💻 시스템 코드 설계 및 작동 원리 (Code Architecture)

이 문서는 알림·자동매매 시스템의 데이터 흐름과 핵심 로직을 코드 관점에서 설명한다.
운영(설치·배포·프로세스)은 [upbit.md](upbit.md), 기능별 설계 기록은 [docs/README.md](docs/README.md).

---

## 1. 데이터 흐름도 (Data Flow)

### 알림 경로 (시작점)
1. **감시 봇** (`app/core/upbit_monitor.py`, `app/core/stock_monitor.py`) — API로 시세/거래량을
   받아 급증 조건을 검사.
2. **저장** (`app/utils/db_manager.py`) — 조건 만족 시 SQLite(`alerts.db`)에 알림 기록.
3. **외부 알림** (`app/utils/slack.py`, `app/utils/google_sheets.py`) — 슬랙 전송 + 구글 시트 기록.
4. **대시보드** (`app/api/server.py`) — Flask가 DB를 JSON으로 제공, `templates/`가 렌더링.

### 자동매매 경로 (현재 주력)
1. **스크리닝** (`upbit_market_analysis.py` / `toss_market_analysis.py`) — 캔들에서 지표를 계산해
   신호 플래그와 함께 `coin_screening_daily` / `stock_screening_daily`에 적재.
2. **후보 선별** (`db_manager.get_coin_screening_candidates()`) — 신호가 하나라도 켜진 종목.
3. **사람의 승인** — 대시보드에서 관심등록(1단계) + 실거래 승인(2단계) 체크. 전부 DB에 저장.
4. **매매 사이클** (`auto_trader.run_trade_cycle()` / `toss_auto_trader`) — 청산 판단 → 진입 판단 →
   주문 실행 → 감사로그(`trade_order_log`) 기록. 루프 주기는 `trade_strategy_settings`.
5. **주문** (`app/core/brokers/*`) — 실거래는 실제 계좌, 모의는 가상 원장.
6. **성과 확인** (`app/core/trade_performance.py`) — `trade_order_log`의 매수→매도를 한 사이클로
   묶어 승률·누적손익·진입 신호별 기여도를 집계(`/auto-trade/performance`). 읽기 전용이라 매매
   판단에는 영향이 없다([docs/auto-trade-performance.md](docs/auto-trade-performance.md)).

---

## 2. 핵심 모듈 상세 분석

### ① `app/utils/db_manager.py` (데이터의 심장)
- 스키마 생성과 컬럼 추가(마이그레이션)를 코드에서 직접 처리 — 수동 DDL 없이 배포만으로 반영된다.
- 매매 관련 테이블은 전부 `(broker, mode)` 키로 분리 — 업비트/토스 × 모의/실거래가 한 테이블 안에서
  섞이지 않는다.
- `ENTRY_SIGNALS` / `DOWNSIDE_SIGNALS`가 신호 정의의 **유일한 소스** — WHERE 절, 대시보드 요약,
  템플릿의 필터 체크박스·뱃지 라벨이 전부 이 목록 하나를 참조한다.

### ② `app/api/server.py` (데이터 중계소)
- 화면 라우트(`/`, `/auto-trade`, `/toss-trade`, `/auto-trade/logs` …)와 JSON API를 전부 담당.
- 실거래 관련 API(`/api/*-trade/live/*`)는 스위치 토글·승인 체크·즉시 실행·강제매수로 나뉘고,
  즉시 실행/강제매수에는 동시성 락이 걸려 있다(중복 요청 시 409 — 이중 매수 방지).
- 로그인은 잠금 토글 방식 — 켤 때마다 새 비밀번호를 슬랙으로 보낸다(`docs/slack-login.md`).

### ③ `app/core/trade_strategy.py` (매매 판단의 핵심, 순수 함수)
- DB도 네트워크도 건드리지 않는다 — 시세는 `get_price_fn` 콜백으로 주입받는다. 덕분에 브로커와
  무관하게 같은 로직이 모의/실거래에 그대로 쓰이고 테스트가 쉽다.
- 청산 우선순위: ①익절 → ②RSI 과매수 매도 → ③트레일링 손절(연속 확인) → ④물타기.
- 손절은 진입가가 아니라 **보유 중 최고가(peak_price)** 대비 하락률 기준(트레일링).

### ④ `app/core/brokers/` (거래소 차이를 가두는 곳)
- `base.py`의 `BrokerClient` 인터페이스 — `get_current_price` / `get_cash_balance` /
  `get_positions` / `buy_market` / `sell_market`.
- `upbit_live_broker.py`: 최소 주문금액(5,000원) 검증, 소수점 수량 가능.
- `toss_live_broker.py`: 정수 주 단위, 주문 후 체결 폴링, 고액주문 확인 플래그.
- 실주문 직전에 "실거래 실행 스위치"를 DB에서 다시 읽어 차단 여부를 판단한다 — 프로세스가 떠
  있어도 스위치가 꺼져 있으면 API 호출 자체를 안 한다.

### ⑤ `app/core/stock_monitor.py` (주식 감시 + 수집)
- 한국투자증권 OAuth2 토큰을 자동 발급받아 세션 유지.
- 순위 API로 거래량 순위를 단일 호출로 획득하고, Signal Score 재료(수급·시총·업종지수)도 적재.
- 평일 장 운영 시간(09:00~15:30)에만 루프를 돌린다.

---

## 3. 프론트엔드

- 서버 렌더링된 템플릿 + `fetch()`로 JSON을 받아 그리는 구조. 빌드 도구 없음.
- 공통 상단 메뉴는 `templates/_navbar.html` — 화면을 추가하면 여기에 링크를 넣는다.
- 표는 DataTables로 정렬/검색을 붙인다(갱신 시 `destroy()` 후 재초기화).
- `/auto-trade`, `/toss-trade`는 신호 필터 체크박스(AND) + 뱃지 UX를 공유한다.

---

## 4. 확장 가이드 (코드 수정 팁)
- **알림 조건 변경**: `app/config.py`의 `UPBIT_THRESHOLDS` 등 임계값.
- **매매 기준 변경**: 대시보드의 "⚙️ 매매 기준 설정"(DB에 저장) — `app/config.py` 값은 초기 기본값일 뿐.
- **신호 추가**: 지표 계산(`*_market_analysis.py`) → 컬럼 마이그레이션 + `ENTRY_SIGNALS`/
  `DOWNSIDE_SIGNALS`에 한 줄 추가(나머지 소비처는 자동으로 따라온다).
- **새 화면 추가**: `db_manager`에 조회 함수 → `server.py`에 라우트 → `templates/`에 페이지 →
  `_navbar.html`에 링크.
- **새 거래소 추가**: `BrokerClient`를 구현하면 매매 판단 로직은 손대지 않아도 된다.

---

===========

# 리펙토링 20260409

> 아래는 2026-04-09 당시의 작업 기록(히스토리)이다. 현재 구조 설명은 위 1~4장을 본다 —
> 이후에 자동매매·실거래가 추가되면서 실행 모드와 프로세스 구성은 더 늘어났다.

전체적인 리펙토링과 테스트 파일 정리를 완료했습니다. 주요 변경 사항은 다음과 같습니다:

   1. 프로젝트 구조 모듈화: app 폴더를 생성하여 코드를 역할별로 분리했습니다.
       * app/api/: Flask 서버 관련 코드 (server.py)
       * app/core/: 업비트 및 주식 감시 핵심 로직 (upbit_monitor.py, stock_monitor.py, kis_models.py)
       * app/utils/: 데이터베이스, 로깅, 구글 시트 등 유틸리티 (db_manager.py, logger.py, google_sheets.py)
   2. 설정 중앙 집중화: app/config.py를 생성하여 환경 변수와 주요 설정값들을 한 곳에서 관리하도록 했습니다.
   3. 테스트 파일 분리: test_insert.py를 tests/ 폴더로 이동하고, 새로운 구조에서도 작동하도록 업데이트했습니다.
   4. 통합 엔트리 포인트: 루트의 main.py를 수정하여 인자값(api, upbit, stock, all)에 따라 원하는 서비스를 실행할 수 있도록 통합했습니다.
   5. 배포 워크플로우 업데이트: .github/workflows/deploy.yml을 새로운 구조와 실행 방식에 맞게 수정했습니다.
   6. 코드 정리: 루트 디렉토리에 흩어져 있던 기존 파일들을 정리하여 프로젝트가 훨씬 깔끔해졌습니다.
---
  이제 서비스를 실행하려면 다음과 같이 사용할 수 있습니다:
   * 전체 실행: .venv\Scripts\python.exe main.py
   * 특정 기능만 실행: .venv\Scripts\python.exe main.py api (또는 upbit, stock)
   * 테스트 데이터 입력: .venv\Scripts\python.exe tests\test_insert.py

 서버 부하를 줄이기 위해 감시 로직의 딜레이 시간을 기존보다 4배 늘렸습니다.

  변경 사항 요약

  1. 업비트 감시 (app/core/upbit_monitor.py)
   * 개별 코인 체크 사이: 0.5초 → 2.0초
   * 분봉 데이터 조회 사이: 0.1초 → 0.4초
   * 전체 코인 1회 순회 후 대기: 15분 → 1시간

  2. 주식 감시 (app/core/stock_monitor.py)
   * 실시간 순위 조회 간격: 30초 → 120초 (2분)
   * 장 종료 후 대기 간격: 1분 → 4분
   * 주말 대기 간격: 1시간 → 4시간
   * 에러 발생 시 재시도: 10초 → 40초

  이 설정을 통해 API 호출 횟수가 대폭 줄어들어 서버 리소스를 아끼고, API 제공처(업비트, 한국투자증권)의 호출 제한(Rate Limit)에 걸릴 위험도
  낮아졌습니다.

  만약 감시가 너무 느리다고 느껴지시면 다시 조정해 드릴 수 있습니다. 다른 필요한 사항이 있으시면 말씀해 주세요!