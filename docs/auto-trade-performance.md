# 매매 성과 화면 (Trade Performance) — 설계/구현 문서

> 상태: **구현 완료**. `/auto-trade/performance`(업비트), `/toss-trade/performance`(토스증권).
> 읽기 전용 화면 — 매매 판단/체결 로직은 한 줄도 건드리지 않았다.

## 1. 배경 — 왜 만드나

매매 이력은 `trade_order_log`에 계속 쌓이고 [`/auto-trade/logs`](../templates/auto_trade_logs.html)에서
한 건씩 볼 수 있지만, 그 화면은 **판단 1건 = 1행**이라 "그래서 벌었나?"에 답하지 못한다.
운영자가 실제로 알고 싶은 건 판단 하나하나가 아니라:

- 지금까지 승률과 누적 실현손익이 얼마인가
- **어떤 진입 신호로 들어간 매매가 실제로 돈을 벌었나** (`breakout_4h`가 `near_ma200+above_cloud`보다 나은가)
- 익절/손절/RSI 매도 중 무엇이 성과를 끌고 가는가
- "무조건 한 번은 물타 본다"는 현재 정책이 손익에 도움이 되는가

이 화면은 그 네 가지에 답하는 것이 목적이다.

## 2. 원본 데이터의 두 가지 함정

`trade_order_log`([db_manager.py](../app/utils/db_manager.py))는 한 행이 곧 판단 1건이다. 성과를
집계할 때 반드시 조심해야 하는 게 둘 있다.

### ① HOLD 행에도 `pnl_krw`가 들어있다 — 그건 미실현이다

[`evaluate_exits()`](../app/core/trade_strategy.py)는 HOLD 판단에도 그 시점의 평가손익을 채워 넣는다
(대시보드가 보여주려고). 실현손익을 세면서 이걸 같이 더하면 손익이 통째로 뻥튀기된다.
→ **`decision='SELL'` 행만 실현손익으로 센다.** `get_trade_fill_rows()`가 SQL에서 이미 걸러내고,
`build_trade_cycles()`도 SELL이 아니면 손익을 읽지 않는다. 회귀 테스트로 고정해뒀다
([tests/test_trade_performance.py](../tests/test_trade_performance.py)의 "HOLD 행의 평가손익은 절대 섞이지 않는다").

### ② 진입 신호는 BUY 행에, 손익은 SELL 행에 따로 있다

`reason` 컬럼은 진입이면 진입 근거, 청산이면 청산 사유다. "breakout_4h로 들어간 매매의 승률"을
내려면 같은 종목의 BUY와 SELL을 이어붙여야 한다 — 그 묶음을 **매매 사이클**이라 부른다.

## 3. 매매 사이클 재구성 규칙

[app/core/trade_performance.py](../app/core/trade_performance.py)의 `build_trade_cycles()`.
종목별로 체결 행을 시간순으로 훑는다.

```
BUY ─────────▶ (DCA_BUY …) ─────────▶ SELL
진입 신호 확정    매수금액/물타기 횟수에 합산    실현손익 확정 → 사이클 종료
```

| 상황 | 처리 |
|---|---|
| `BUY` (열린 사이클 없음) | 새 사이클을 열고 그 reason을 진입 신호로 확정 |
| `BUY` (이미 열려 있음) | 새 사이클을 만들지 않고 추가매수로 합산. 정상 루프에선 `이미 보유 중` SKIP으로 막히지만 수동 강제매수로는 생길 수 있고, 계좌 상으로도 한 포지션이 맞다 |
| `DCA_BUY` | 물타기 횟수 +1, 매수금액 합산 |
| `SELL` | 사이클 종료. 이 행의 `pnl_krw`/`pnl_pct`가 그 사이클의 실현손익 |
| `SELL` (짝 BUY 없음) | 기록 이전부터 들고 있던 포지션. 진입 신호를 **`미확인`** 버킷으로 따로 센다 — 임의의 신호에 손익을 떠넘기지 않는다 |
| 청산 안 된 사이클 | 실현손익 집계에서 제외하고 "보유 중 N건"으로 건수만 표시 |

진입 신호는 `+정밀조건충족` 접미사를 떼고 같은 신호로 묶는다. 접미사까지 다른 신호로 세면 표본이
쪼개져 승률이 의미를 잃기 때문이고, 대신 "정밀조건" 열에 그 건수를 따로 보여준다.

현재 매매 로직은 전량매도라 분할청산이 없어 BUY:SELL = 1:1로 가정한다. 분할매도가 생기면 이 가정을
먼저 고쳐야 한다.

## 4. 화면 구성

| 영역 | 내용 |
|---|---|
| 기간 필터 | 청산일 기준. 7일/30일/90일/전체 빠른 선택 + 수수료 반영 토글 |
| 요약 타일 | 청산 건수 · 승률 · 누적 실현손익 · 평균 수익률 · 손익비 · Profit Factor · 최대 낙폭(MDD) · 최대 연승/연패 · 평균 보유시간 · 최고/최대 손실 거래 |
| 차트 | 막대 = 그날 실현손익, 선 = 누적 실현손익 (Chart.js) |
| 진입 신호별 성과 | 건수/승/패/승률/누적손익/평균수익률/평균보유/정밀조건 건수 |
| 청산 사유별 성과 | 익절·손절은 정의상 승률이 100%/0%이라 **비중** 열을 같이 본다 |
| 종목별 성과 | 어떤 종목에서 벌고 잃는지 |
| 물타기 여부별 성과 | 물탄 사이클이 실제로 회복해서 나왔는지 — 현재 물타기 정책의 성적표 |
| 청산 사이클 목록 | 진입/청산 시각, 신호, 사유, 보유시간, 금액, 손익 + CSV 내보내기 |

**지표 정의**
- 승 = `pnl_krw > 0`. 표본이 0건이면 승률은 0%가 아니라 `—`(없음)으로 표시한다.
- 손익비 = 평균이익 ÷ 평균손실, Profit Factor = 총이익 ÷ 총손실 (1 미만이면 손실 구간).
- 최대 낙폭(MDD) = 누적 실현손익 곡선의 고점 대비 최대 하락폭. 승률만 보면 놓치는
  "연속 손실로 얼마나 깎였나"를 잡는 값.

## 5. 수수료는 추정치다

`pnl_krw`는 체결가 단순 차액이라 수수료·세금이 빠져 있고, 원본 어디에도 실제 부과액이 없다.
그래서 요율 가정으로 **추정만** 하고, 화면에 요율을 같이 적어 추정임을 밝힌다.
기본값은 [app/config.py](../app/config.py)의 `TRADE_FEE_RATE_*`:

| | 매수 | 매도 |
|---|---|---|
| 업비트 원화마켓 | 0.05% | 0.05% |
| 국내주식(토스증권) | 위탁수수료 0.015% | 위탁수수료 0.015% + 증권거래세 0.18% |

세율/요율은 제도와 계좌 조건에 따라 달라지니 실제와 다르면 이 상수만 고치면 된다. 매매 판단에는
전혀 쓰이지 않는 표시 전용 값이다.

기본 화면은 **수수료 미반영**(원본 그대로)이고, 체크박스를 켜면 재조회 없이 순손익으로 다시 그린다.

## 6. 업비트/토스 차이는 어떻게 다루나

집계 로직과 화면 구성이 완전히 같아서 템플릿([trade_performance.html](../templates/trade_performance.html))과
집계 모듈을 그대로 공유하고, 다른 것만 서버에서 주입한다
([server.py](../app/api/server.py)의 `TRADE_PERFORMANCE_VIEWS`).

| | 업비트 | 토스증권 |
|---|---|---|
| 종목 링크 | upbit.com 거래소 | tossinvest.com 주문 화면 |
| 수량 표기 | 소수점 6자리 | 정수(주) |
| 수수료 요율 | 매수/매도 대칭 | 매도에 증권거래세 추가 |

브로커가 늘면 이 표에 한 줄 추가하고 라우트 두 개(뷰/API)만 더 만들면 된다.

## 7. 성능

`trade_order_log`는 HOLD/SKIP까지 매 사이클 쌓여 계속 커진다. 성과 집계는 체결 행
(BUY/DCA_BUY/SELL)만 필요하고 그건 전체의 아주 일부라, 그 부분만 꺼내 오도록 인덱스를 뒀다:

```sql
CREATE INDEX IF NOT EXISTS idx_trade_order_log_fills
    ON trade_order_log(broker, mode, decision, id)
```

기간 필터를 SQL로 걸지 않는 이유: 사이클은 기간 밖에서 산 종목이 기간 안에서 팔리는 식으로 걸쳐
있을 수 있어, 기간 안 행만 읽으면 진입 신호를 잃어버린다. 재구성을 끝낸 뒤 청산일로 거른다.

## 8. 파일

| 파일 | 역할 |
|---|---|
| [app/core/trade_performance.py](../app/core/trade_performance.py) | 사이클 재구성 + 집계 (순수 함수, DB/네트워크 접근 없음) |
| [app/utils/db_manager.py](../app/utils/db_manager.py) | `get_trade_fill_rows()` + 인덱스 마이그레이션 |
| [app/api/server.py](../app/api/server.py) | 뷰/API 라우트 4개 + 브로커별 차이 표 |
| [templates/trade_performance.html](../templates/trade_performance.html) | 화면 (업비트/토스 공용) |
| [tests/test_trade_performance.py](../tests/test_trade_performance.py) | 단위 테스트 (`python tests/test_trade_performance.py`) |
