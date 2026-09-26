# 📖 코드 공부 가이드 — 처음 읽는 사람을 위한 안내서

> 이 문서는 upbit-alert 코드를 **처음 공부하는 사람**을 위한 길잡이다.
> 모든 걸 설명하지 않고 "어디부터, 어떤 순서로 읽으면 되는지"를 알려준다.
> 더 딱딱한 구조 설명은 [../code.md](../code.md), 서버 운영은 [../upbit.md](../upbit.md),
> 기능별 설계 기록은 [README.md](README.md)에 있다.
>
> 줄 번호(`파일:줄`)는 2026-09-26 기준이다. 코드가 바뀌면 조금씩 어긋날 수 있으니
> 함수 이름으로 검색하면 된다.

---

## 0. 한 문장 요약

**"여러 개의 작은 봇이 각자 할 일을 하고, 서로는 DB(SQLite)로만 대화한다."**

- 분석 봇은 "살 만한 코인 후보"를 DB에 적는다.
- 사람은 대시보드(웹 화면)에서 후보 중 살 코인을 체크한다. 체크 결과도 DB에 적힌다.
- 매매 봇은 5분마다 DB를 읽고 "팔까? 살까?"를 판단해 업비트에 주문한다. 결과는 다시 DB에 적는다.
- 화면은 DB를 읽어서 보여준다.

봇끼리 직접 함수를 부르지 않는다. 그래서 봇 하나가 죽어도 나머지는 계속 돈다.

---

## 1. 폴더 지도

```
main.py                ← 시작점. "어떤 봇으로 실행할지" 고르는 스위치
app/
  config.py            ← 기본 설정값(손절 %, 매수 금액 등). 실제 값은 대시보드에서 DB에 저장된 게 우선
  api/server.py        ← 웹 서버(Flask). 화면과 버튼 API 전부
  core/                ← 실제 로직
    upbit_market_analysis.py   ← 분석 봇: 코인 지표 계산 → 후보 저장
    auto_trader.py             ← 매매 봇의 지휘자: 한 사이클의 순서를 정함 ★
    trade_strategy.py          ← 매매 판단의 두뇌: "팔까/살까"만 계산(주문 안 함) ★
    brokers/                   ← 실제 주문을 내는 손발(업비트/토스/모의)
    entry_conditions.py        ← 정밀 매수조건(일봉·5분봉·1분봉 검사) 계산식
    entry_condition_checker.py ← 정밀조건 검사 봇(결과를 DB에 캐시)
    convergence_buy.py         ← 수렴 자동 매수 계산식
    market_regime.py           ← 시장 판단(좋음/애매/나쁨) 봇
  utils/
    db_manager.py      ← DB의 모든 읽기/쓰기. 테이블 생성도 여기 ★
    slack.py           ← 슬랙 알림
  backtest/            ← 과거 캔들로 전략을 돌려보는 백테스트
templates/             ← 화면 HTML(auto_trade.html이 자동매매 화면)
.github/workflows/deploy.yml ← main에 머지되면 서버에 자동 배포
```

★ 표시 세 파일이 핵심이다. 나머지는 필요할 때 찾아보면 된다.

---

## 2. 프로그램이 켜지는 방법

`main.py`는 명령어 뒤에 붙은 단어를 보고 어떤 봇을 실행할지 고른다.

```bash
python main.py api          # 웹 서버
python main.py live_trade   # 업비트 실거래 봇
python main.py coin_analysis  # 코인 후보 분석 봇
```

서버에서는 PM2라는 프로그램이 이 봇들을 **각각 따로** 띄워 둔다.
`deploy.yml`을 보면 `pm2 start main.py --name upbit-live-trade-bot ... -- live_trade` 같은 줄이 봇마다 있다.

| PM2 이름 | 실행 모드 | 하는 일 |
|---|---|---|
| `upbit-api` | `api` | 웹 화면 + 버튼 처리 |
| `coin-analysis-bot` | `coin_analysis` | 30분마다 코인 지표 계산 → 후보 저장 |
| `condition-check-bot` | `condition_check` | 정밀 매수조건 검사 결과 저장 |
| `upbit-live-trade-bot` | `live_trade` | **5분마다 매매 사이클 실행** |
| `market-regime-bot` | `market_regime` | 30분마다 시장 판단 + 슬랙 알림 |
| `coin-ranking-bot` | `coin_ranking` | 매시 코인 순위 저장 |

📌 **읽어볼 곳**: `main.py` 전체(짧다) → `.github/workflows/deploy.yml`의 `pm2 start` 줄들

---

## 3. 핵심: 매매 사이클 한 바퀴 따라가기

가장 중요한 흐름이다. 실거래 봇이 5분마다 하는 일을 순서대로 따라가 보자.

### 3-1. 루프: 5분마다 깨어난다

`app/core/auto_trader.py:1039` `run_live_trade_loop()`

```
while True:
    스위치 꺼져 있으면 → 쉬고 다음 바퀴
    run_trade_cycle() 한 번 실행
    loop_interval_sec(기본 300초)만큼 잠
```

대시보드의 **"실거래 실행" 스위치**를 끄면 여기서 그냥 건너뛴다.

### 3-2. 한 사이클: `run_trade_cycle()`

`app/core/auto_trader.py:349` — 이 함수 하나만 제대로 읽어도 봇의 절반을 이해한 것이다.

```
[준비]
  락 잡기            ← 같은 사이클이 두 번 동시에 돌지 않게(이중 매수 방지)
  설정 읽기          ← 대시보드에서 바꾼 값을 매번 새로 읽음
  실제 잔고 → DB 동기화 (_reconcile_live_positions)

① 청산 판단 (팔까?)
  보유 코인마다 evaluate_exits() → SELL / HOLD / DCA_BUY(물타기)
  _execute()로 실행

  ※ 이번 사이클에 하나라도 팔았으면 ②~④는 건너뛴다
    (판 돈으로 바로 다른 걸 사지 않게 — 다음 사이클에 다시 판단)

② 진입 판단 (살까?)
  후보 목록 = 분석 봇이 적어둔 후보
            ∩ 사람이 "매매 대상" 체크한 것
            ∩ 사람이 "실거래 승인" 체크한 것
  evaluate_entries() → BUY / SKIP

③ 모아가기 (등록 코인 정액 매수)
④ 수렴 자동 매수 (봇이 알아서 고른 새 코인)

[마무리]
  잔고 → DB 다시 동기화
  "마지막 실행 시각" 기록, 락 풀기
```

### 3-3. 두뇌와 손발이 나뉘어 있다

이 설계가 이 코드의 가장 좋은 점이니 꼭 이해하고 넘어가자.

| 역할 | 파일 | 하는 일 | 하지 않는 일 |
|---|---|---|---|
| 🧠 두뇌 | `trade_strategy.py` | "이 코인은 SELL" 같은 **결정 목록**만 돌려준다 | DB 접근, 주문, 네트워크 |
| 🎬 지휘자 | `auto_trader.py` | DB에서 재료를 모아 두뇌에 넘기고, 결정을 손발에 넘긴다 | 판단 계산 |
| ✋ 손발 | `brokers/*.py` | 실제 주문(`buy_market`, `sell_market`) | 판단 |

두뇌가 순수 함수(입력 → 출력만 있는 함수)라서 좋은 점:
- 같은 두뇌를 **실거래·모의매매·백테스트**가 그대로 같이 쓴다(`app/backtest/engine.py`도 `evaluate_exits`를 부른다).
- 시세도 직접 조회하지 않고 `get_price_fn`이라는 함수를 받아서 쓴다. 그래서 백테스트는 "과거 가격을 돌려주는 함수"만 넘기면 된다.

결정 하나는 `TradeDecision`(`trade_strategy.py:49`)이라는 작은 상자다: `ticker`, `action`(BUY/SELL/HOLD/SKIP/DCA_BUY), `reason`, 금액 등.
`_execute()`(`auto_trader.py:159`)가 이 상자를 받아 주문하고 `trade_order_log` 테이블에 기록한다. 매매 기록 화면은 이 테이블을 보여주는 것이다.

---

## 4. "팔까?" — `evaluate_exits()`

`app/core/trade_strategy.py:354`. 파일 맨 위 설명(1~43줄)에 순서가 정리돼 있다. 보유 코인마다 위에서부터 확인하고, 먼저 걸리는 규칙이 이긴다.

1. **익절** — 평단 대비 +10%(기본) 이상이면 바로 판다.
2. **RSI 과매수 매도**(기본 꺼짐) — 너무 과열됐고 수익 중이면 판다.
3. **되돌림 익절**(기본 꺼짐) — +3%까지 갔다가 +2%로 내려오면 이익을 지키려고 판다.
4. **트레일링 손절** — "산 가격"이 아니라 **"보유 중 가장 높았던 가격(peak_price)"** 대비 5% 떨어지면 손절.
5. **물타기** — 손절 직전에 한 번 더 기다렸다가 -10%에서 추가매수해 평단을 낮춘다(최대 횟수 제한 있음).

용어 정리:
- **평단(avg_buy_price)**: 평균 매수 가격.
- **peak_price**: 산 뒤로 본 최고가. 가격이 오를수록 손절선도 같이 올라간다(그래서 "트레일링").
- **물타기(DCA)**: 떨어졌을 때 더 사서 평균 단가를 낮추는 것.

설정에 따라 4~5번이 다른 모드로 통째로 바뀐다: 짧은 손절·긴 수익(`_evaluate_exit_tight_stop`), 회복형 분할 물타기(`_evaluate_exit_recovery`). 둘 다 기본 꺼짐.

---

## 5. "살까?" — `evaluate_entries()`

`app/core/trade_strategy.py:519`. 후보마다 아래 관문을 차례로 통과해야 BUY가 된다. 하나라도 걸리면 SKIP과 이유가 남는다.

```
이미 보유 중?           → SKIP
최근에 판 코인?          → SKIP (재매수 대기, 켰을 때만)
보유 종목 수 꽉 참?      → SKIP
현금 부족?              → SKIP
정밀조건 켰는데 불합격?   → SKIP
시세 조회 실패?          → SKIP
통과                    → BUY (정해진 금액만큼)
```

후보는 어디서 오나? 분석 봇(`upbit_market_analysis.py:221` `run_coin_screening`)이 코인마다 지표(돌파, 200일선 근처, 일목 구름 위 등)를 계산해서 `coin_screening_daily` 테이블에 저장한다. `db_manager.py:3989` `get_coin_screening_candidates()`가 그중 신호가 켜진 것만 꺼낸다.

### 실거래의 안전장치 3겹

실제 돈이 나가므로 여러 겹으로 막혀 있다.

1. **실거래 실행 스위치** — 꺼져 있으면 루프가 쉬고, 주문 직전에도 한 번 더 확인한다(`brokers/upbit_live_broker.py:123` `_blocked_reason`).
2. **매매 대상 체크(1단계)** — 사람이 관심 등록한 코인만.
3. **실거래 승인 체크(2단계)** — 승인한 코인이 **하나도 없으면 아무것도 안 산다**(`auto_trader.py`의 `elif is_live: candidates = []`).

※ 예외: ③ 모아가기와 ④ 수렴 자동 매수는 승인 체크를 쓰지 않는 별도 경로다(아래 6장).

---

## 6. 최근에 추가된 기능은 코드 어디에?

2026-09-26에 머지된 네 기능이다. 각각 "어디를 고쳤나"를 보면 코드 구조를 익히기에 좋다.

### 🔁 매도 후 재매수 대기 (PR #95) — 기본 꺼짐

손절한 코인을 5분 뒤 또 사서 또 손절하는 반복을 막는다.

- 설정 기본값: `app/config.py:183` `TRADE_REENTRY_BLOCK_HOURS = 0.0` (0 = 꺼짐)
- 지휘자: `run_trade_cycle()`의 ② 안에서 `get_last_sell_times()`로 "최근 판 시각"을 모아서 넘긴다.
- 두뇌: `trade_strategy.py:551` 부근 — 대기 시간 안이면 SKIP.
- 👉 **배울 점**: 두뇌에 필요한 재료(`last_sell_at`)는 지휘자가 DB에서 모아서 넘기고, 두뇌는 판단만 한다. 3-3의 구조 그대로다.

### 🪙 모아가기 (PR #96) — 기본 꺼짐

BTC·ETH 같은 등록 코인은 정밀조건을 통과하면 정해둔 금액만큼 사고, **자동으로 팔지 않는다**.

- 두뇌: `trade_strategy.py:615` `evaluate_accumulation()`
- 지휘자: `run_trade_cycle()`의 ③. 그리고 ①·②에서 모아가기 코인을 **목록에서 빼는 줄**(`if p['ticker'] not in accumulate_tickers`)이 "자동 매도 제외"를 만든다.
- 설정 저장: `db_manager.py:5067` `get_accumulate_settings()`
- 문서: [auto-trade-accumulate.md](auto-trade-accumulate.md)

### 🛑 강제매도 시 실거래 승인 자동 해제 (PR #97)

화면에서 "강제 매도"로 손수 판 코인을 봇이 다음 사이클에 다시 사지 않게, 승인 체크를 같이 끈다.

- 웹 API: `app/api/server.py:1024` `force_sell_live_api()` — 매도 성공이면 `set_candidate_approval(..., False)` 호출.
- 실제 매도: `auto_trader.py:960` `force_sell()`
- 👉 **배울 점**: 버튼 하나가 "화면 → server.py 라우트 → auto_trader 함수 → broker 주문 → DB"로 흘러가는 전형적인 경로다.

### 🎯 수렴 자동 매수 (PR #98) — **기본 켜짐**, 하루 1종목

사람 승인 없이 봇이 직접 고른다. 24시간 거래대금 400억 이상인 "새 코인" 중 5분봉의 가격·구름·80선·120선이 1.5% 안에 모여 있고 일봉이 구름·기준선 위면 산다. 산 뒤에는 일반 청산 규칙(4장)을 따른다.

- 계산식: `app/core/convergence_buy.py` — `measure_convergence()`(얼마나 모였나), `measure_daily_trend()`(일봉 추세), `judge_row()`(합격/불합격 이유), `pick_buys()`(이격 작은 순으로 고르기)
- 지휘자: `auto_trader.py:309` `_run_convergence_buy()` — 오늘 몇 개 샀는지 세고, "새 코인이 아닌 것"(보유 중, 최근 7일 매매, 모아가기)을 빼고 시장을 훑는다.
- 설정: `app/config.py`의 `CONVERGENCE_*`, DB는 `db_manager.py:5367` `get_convergence_settings()`
- 문서: [auto-trade-convergence.md](auto-trade-convergence.md)

---

## 7. 화면 버튼은 어떻게 동작하나

예: 자동매매 화면에서 "지금 즉시 실행"을 누르면

```
templates/auto_trade.html   fetch('api/auto-trade/live/run-now', POST)
        ↓
app/api/server.py:975       라우트 함수 → run_trade_cycle(broker=UpbitLiveBroker(), trigger_type='manual_live')
        ↓
auto_trader.py:349          3장의 사이클 한 바퀴 (봇 루프와 같은 함수!)
```

화면은 빌드 도구 없는 순수 HTML + JavaScript다. `fetch()`로 JSON을 받아 표를 그린다. 새 화면을 만드는 순서는 [../code.md](../code.md) 4장 "확장 가이드"에 있다.

---

## 8. DB 이야기

- 파일 하나짜리 SQLite다. 모든 접근은 `app/utils/db_manager.py`를 거친다.
- 테이블 생성·컬럼 추가도 코드(`db_manager.py:44` `init_db()`)가 한다. 그래서 배포만 하면 DB 구조가 자동으로 맞춰진다.
- 매매 테이블은 `(broker, mode)`로 나뉜다: 업비트/토스 × paper(모의)/live(실거래).

자주 만나는 테이블:

| 테이블 | 내용 |
|---|---|
| `coin_screening_daily` | 분석 봇이 적은 코인 후보와 신호 |
| `paper_positions` | 봇이 추적 중인 보유 코인(이름은 paper지만 `mode='live'` 행이 실거래) |
| `trade_order_log` | 모든 매매 판단 기록(BUY/SELL/SKIP + 이유) |
| `trade_strategy_settings` | 대시보드에서 바꾼 매매 기준 |
| `trade_engine_settings` | 실거래 실행 스위치 |

---

## 9. 추천 읽기 순서

한 번에 다 읽으려 하지 말고, 아래 순서로 **하루에 한 단계씩** 읽어보자.

1. `main.py` — 전체 봇 목록 파악 (10분)
2. `auto_trader.py:349` `run_trade_cycle()` — 주석까지 천천히. 모르는 함수는 이름만 보고 넘어가기 (30분)
3. `trade_strategy.py` 맨 위 설명 → `evaluate_entries()` (짧다) → `evaluate_exits()` (길다)
4. `brokers/base.py` — 손발이 가져야 할 5개 기능. 그다음 `upbit_live_broker.py`의 `buy_market`
5. 최근 기능 하나를 골라 6장의 위치를 직접 따라가 보기. `git show 0af7f46`(재매수 대기)처럼 **커밋 하나를 통째로 보면** "기능 하나 추가에 어디어디를 고치는지"가 한눈에 보인다.

| 기능 | 커밋 보기 |
|---|---|
| 재매수 대기 | `git show 0af7f46` |
| 모아가기 | `git show 6cd8452` |
| 강제매도 승인 해제 | `git show 88f12e2` (가장 작다 — 첫 번째로 추천) |
| 수렴 자동 매수 | `git show ba4d8f7` |

### 스스로 확인해보기

읽고 나서 아래 질문에 답할 수 있으면 핵심은 이해한 것이다.

- 실거래 승인 체크를 하나도 안 했을 때 봇은 무엇을 사나? (→ 5장, 단 ③④는 예외)
- 같은 사이클에 손절과 새 매수가 동시에 일어날 수 있나? (→ 3-2의 ※)
- 백테스트가 실제 매매와 같은 판단을 할 수 있는 이유는? (→ 3-3)
- 모아가기 코인이 자동으로 팔리지 않는 건 어느 줄 때문인가? (→ 6장)
