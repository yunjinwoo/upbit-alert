# 업비트 실거래 자동매매 — 화면(DB)에서 전부 제어

## 개요

기존 모의매매(`python main.py trade`, `PaperBroker`)와 완전히 독립된 실거래 경로.
`UpbitLiveBroker`(app/core/brokers/upbit_live_broker.py)가 진짜 업비트 계좌로 시장가 매수/매도를
낸다. 매매 대상 코인 선택과 실행 on/off는 **`.env`가 아니라 대시보드(`/auto-trade`)의 "🔴 실거래"
패널**에서 전부 제어한다 — DB에 저장되므로 서버 재시작 후에도 유지된다.

## 안전장치

1. **실거래 승인 화이트리스트(opt-in 필수)** — `trade_candidate_approval`(mode='live')에 체크된
   종목이 하나도 없으면, 실거래 실행 스위치가 켜져 있어도 아무 것도 매수하지 않는다. 모의매매는
   "아무것도 체크 안 하면 전체 후보 대상"이지만 실거래는 반대로 안전 기본값을 쓴다
   (app/core/auto_trader.py의 run_trade_cycle 참고).
2. **실거래 실행 스위치(기본 꺼짐)** — `trade_engine_settings`(broker='upbit', mode='live'). 행이
   없으면(최초 실행) 반드시 `enabled=False`로 취급한다(모의매매는 반대로 기본 켜짐 — 실거래만
   예외). 대시보드에서 켜기 전까지 `python main.py live_trade` 프로세스가 떠 있어도 주문이 안 나간다.
3. **최소 주문금액 검증** — `UpbitLiveBroker.MIN_ORDER_KRW`(5,000원) 미만이면 API 호출 전에 차단.
4. **보유 범위 격리** — 계좌에 이 봇과 무관하게 보유 중인 다른 코인이 있어도, "승인했거나 이미 이
   봇이 사서 추적 중인" 종목만 손절/익절 관리 대상이 된다(`_reconcile_live_positions` 참고).
   실거래 스위치를 켜는 순간 무관한 보유 코인이 갑자기 자동매도되는 일은 없다.
5. **출금 권한 없음** — 업비트 API 키는 조회+주문 권한만 발급하고 출금 권한은 부여하지 않는다.
   IP도 실행 서버 IP로 제한 권장.

## 화면 사용법 (`/auto-trade`)

1. "🔴 실거래" 패널의 표에서 매매하고 싶은 종목의 **실거래 승인** 체크박스를 켠다(모의매매 승인
   체크박스와 완전히 별도 저장). 보유 중이면 실제 수량/평단/평가손익이, 미보유면 "미보유(매수판단
   대상)"이 표시된다.
2. **실거래 실행** 스위치를 켠다 — 켤 때 확인 팝업이 뜬다(실주문 가능 경고).
3. `python main.py live_trade`를 별도 프로세스로 띄운다(모의매매 `trade`와 절대 같이 안 씀).
4. 소액 1종목으로 먼저 테스트하고 싶으면 스위치를 안 켜고도 **"⚡ 실거래 강제매수"**로 즉시 1건
   테스트할 수 있다(단, 실거래 실행 스위치가 꺼져 있으면 이것도 차단된다 — 스위치는 모든 실주문의
   공통 관문).
5. "▶ 지금 즉시 실행"으로 다음 사이클을 안 기다리고 판단(청산→진입) 1회를 즉시 돌려볼 수 있다.
6. 매매 기준(포지션당 매수금액/최대 동시보유/손절·익절 %/루프 주기 등)은 위 "⚙️ 매매 기준 설정"을
   모의매매와 **그대로 공유**한다 — 실거래 전용 기준은 따로 없다.
7. 실거래 매매 이력은 [매매 이력](/upbit/auto-trade/logs) 페이지에서 "구분" 드롭다운을 🔴 실거래로
   바꿔 조회한다(모의매매 이력과 DB상 완전히 분리 저장 — mode='live').

## 프로세스 실행

```powershell
python main.py live_trade
```

- `main.py`의 `all`(기본) 모드나 `python main.py trade`(모의)와는 완전히 별도 프로세스다.
- API 키가 없으면 시작 시 바로 에러 로그를 남기고 종료한다.
- 루프 주기는 모의매매와 같은 `trade_strategy_settings.loop_interval_sec`을 공유한다.

## `.env`

```env
UPBIT_ACCESS_KEY=발급받은_access_key
UPBIT_SECRET_KEY=발급받은_secret_key
```

이 두 값만 있으면 된다. 매매 대상 코인 선택, 실행 on/off는 전부 화면(DB)에서 관리하므로 더 이상
`.env`에 넣을 필요가 없다 — `UPBIT_LIVE_TRADING_ENABLED`/`UPBIT_LIVE_TRADING_CONFIRM`은 폐기됐다
(대신 대시보드의 "실거래 실행" 스위치를 쓴다).

`UPBIT_SELECTED_TICKERS`는 `python main.py live_balance`(CLI 진단 전용, 주문 없음)에서만 여전히
쓰인다 — 대시보드/실거래 판단 로직과는 무관하다.

## 아키텍처 메모 (구현 참고용)

- `run_trade_cycle(broker, ...)`(app/core/auto_trader.py)는 브로커 종류를 몰라도 되도록 설계돼
  있었지만, 포지션/현금을 실제로는 `paper_positions`/`paper_account`(모의 가상 원장) 테이블에서
  읽고 있었다. 실거래에서는 이 값 대신 `broker.get_positions()`/`get_cash_balance()`(진짜 잔고)를
  쓰도록 분기했다.
- 트레일링 손절 추적값(peak_price/연속확인카운트)과 물타기 상태는 실거래에도 필요하지만 업비트
  잔고 API에는 없는 값이라, `paper_positions` 테이블을 (broker, mode='live', ticker) 행으로 그대로
  재사용해 추적한다 — `qty`/`avg_buy_price`만 매 사이클 실제 잔고로 덮어쓰고(reconcile), 나머지
  트래킹 필드는 보존한다. 새 테이블을 안 만들어도 되는 이유는 이 테이블이 처음부터 (broker, mode,
  ticker) 단위로 설계돼 있었기 때문.
- 소액 end-to-end 검증, 중복 주문 방지(같은 사이클 내 재매수는 이미 `evaluate_entries`가
  `held_tickers`로 막지만, 사이클 간 레이스는 별도 검증 필요), 체결 재조회(주문 직후 즉시
  `get_positions()`를 다시 불러도 거래소 체결 반영에 지연이 있을 수 있음)는 실거래 규모를 키우기
  전에 계속 눈으로 확인해야 하는 항목이다.

## 공식 API 참고

- [Get Account Balances](https://global-docs.upbit.com/reference/get-balance)
- [Upbit API 사용 시작하기](https://global-docs.upbit.com/docs/first-exchange-api-call)
