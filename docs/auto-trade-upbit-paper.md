# 자동매매 1단계 — 업비트 모의매매(Dry-run) 엔진

> **2026-08-21 기준 중단됨.** 실거래(live) 기능(`docs/auto-trade-upbit-live.md`)이 자리잡으면서
> 모의매매는 더 이상 화면에서 쓰지 않기로 했다 — `/auto-trade` 페이지의 모의매매 전용 섹션(가상
> 계좌/보유 포지션/승인 표/정밀 매수조건)을 걷어냈고, 배포 PM2 목록에서도 `trade-bot`/
> `condition-check-bot`을 뺐다. 백엔드 코드(`PaperBroker`, `app/core/auto_trader.py`의 모의매매
> 함수, DB 테이블)는 회귀 위험을 줄이기 위해 지우지 않고 그대로 남겨뒀다 — `python main.py trade`로
> 수동 실행하면 여전히 동작하지만, 화면(대시보드)에서는 더 이상 노출되지 않는다. 아래 내용은
> 그 이전 시점의 설계 기록으로만 참고할 것.

- 상태: 로컬 구현·검증 완료 / 서버 배포(PM2) 등록은 PR로 다시 올리는 중(아래 "알아둘 점" 참고) / 실거래 전환 안 함
- 날짜: 2026-08-12
- 관련 파일: `app/core/brokers/`, `app/core/trade_strategy.py`, `app/core/auto_trader.py`,
  `app/core/entry_conditions.py`, `app/core/entry_condition_checker.py`,
  `app/utils/db_manager.py`, `app/config.py`, `main.py`, `app/api/server.py`,
  `templates/auto_trade.html`, `templates/auto_trade_logs.html`, `templates/_navbar.html`,
  `tests/test_trade_dry_run.py`,
  `.github/workflows/deploy.yml`

## 배경

자동매매를 새 프로젝트로 분리할지 이 저장소에 붙일지 검토한 결과, 업비트 연동
(`app/core/upbit_monitor.py`, `app/core/upbit_market_analysis.py`)과 국내주식(KIS)
연동(`app/core/kis_models.py`)이 이미 있어 재사용 가치가 커서 **이 저장소에 통합**하기로
결정. 최종 목표는 업비트·KIS·토스증권 3개 거래소 지원이지만, 리스크(실제 돈)가 큰 작업이라
**1단계는 업비트만, 그것도 실주문 없는 dry-run(모의매매)만** 구현하기로 범위를 좁힘.

## 아키텍처 — 브로커 추상화

향후 KIS/토스 브로커를 추가해도 매매 판단 로직(`auto_trader.py`)을 그대로 재사용할 수 있도록
`BrokerClient` 추상 인터페이스를 뒀다. 지금은 업비트 모의매매 구현체(`PaperBroker`)만 존재.

```
app/core/brokers/
├── base.py            # BrokerClient 추상 인터페이스 (get_current_price/get_cash_balance/
│                       #   get_positions/buy_market/sell_market), Position·OrderResult dataclass
└── paper_broker.py     # PaperBroker — 시세만 pyupbit 공개 API로 조회(인증 불필요),
                         #   체결은 DB 가상 원장(paper_account/paper_positions)에만 반영
app/core/trade_strategy.py  # 순수 함수(evaluate_entries/evaluate_exits) — DB/네트워크 의존 없음
app/core/auto_trader.py     # 오케스트레이션: run_trade_cycle(1회) / run_auto_trade_loop(상시)
                             # / get_dashboard_summary(대시보드용 읽기 전용 요약)
```

**중요**: 이 저장소 전체에 `pyupbit.Upbit(access, secret)` 인스턴스 생성이나
`buy_market_order`/`sell_market_order`(실주문 메서드) 호출이 단 한 곳도 없다. 검증:
```bash
grep -rn "buy_market_order\|sell_market_order\|pyupbit\.Upbit(" app/
```
→ `paper_broker.py` 상단 주석(이 코드가 없다는 설명)만 매칭되어야 정상.

## 매매 기준 (1단계 최소 규칙)

**진입 후보 소스**: 기존 `coin_screening_daily` 테이블(`app/core/upbit_market_analysis.py`가
30분 주기로 채움) 중 `breakout_4h=1 OR (near_ma200=1 AND above_cloud=1)`인 종목만
(`get_coin_screening_candidates()`). 이 테이블이 비어 있으면 진입 후보가 없다 —
`coin_analysis` 모드(또는 `all`)가 함께 돌고 있어야 함. 이후 대시보드에서 수동으로 체크한
종목이 있으면(`trade_candidate_approval`) 그 종목만으로 한 번 더 좁혀짐 — 아래 "매매 대상
코인 수동 승인" 참고.

| 항목 | 기본값 | 의미 | DB 설정 가능 여부 |
|---|---|---|---|
| `TRADE_INITIAL_CASH_KRW` | 1,000,000원 | 가상 계좌 초기 자본(계좌 최초 생성 시 1회만 사용) | ❌ Config 고정 |
| `TRADE_MAX_POSITION_KRW` | 100,000원 | 1종목당 매수 금액(고정, 분할매수 없음) | ✅ `trade_strategy_settings` |
| `TRADE_MAX_CONCURRENT_POSITIONS` | 5종목 | 동시 보유 한도 | ✅ `trade_strategy_settings` |
| `TRADE_STOP_LOSS_PCT` | 5.0% | 손절 기준 | ✅ `trade_strategy_settings` |
| `TRADE_TAKE_PROFIT_PCT` | 10.0% | 익절 기준 | ✅ `trade_strategy_settings` |
| `TRADE_LOOP_INTERVAL_SEC` | 300초 | 매매 판단 루프 주기 | ✅ `trade_strategy_settings` |

값 근거는 백테스트가 아니라 손익비 1:2(승률 40%대에서도 기대값 플러스) 정도의 상식적인 기본값이며,
1단계라 일부러 단순하게(트레일링스탑·ATR 기반 조정 없이 고정 %) 잡은 것 — 대시보드에서
자유롭게 조정하도록 DB화했다(아래 "매매 기준 설정 (DB 저장)" 참고).

- **진입(BUY)**: 후보 중 ①미보유 ②열린 포지션 < `max_concurrent_positions` ③가상 현금 ≥ `max_position_krw`
  → 고정 금액어치 시장가 매수 시뮬레이션. 한 사이클에 여러 종목을 순차 매수 가능(현금 소진 시 이후 후보는 스킵).
- **청산(SELL)**: 보유 포지션마다 현재가 재조회 → 손익률이 `-stop_loss_pct` 이하면 손절, `+take_profit_pct`
  이상이면 익절, 그 사이면 HOLD. 부분매도·트레일링스탑·보유기간 제한은 1단계 범위 밖.
- **리스크 한도**: 총 노출액 자연 상한 = `max_position_krw × max_concurrent_positions`(기본값 기준 50만원, 초기자본의 50%).
- **감사로그**: BUY/SELL/HOLD/SKIP 판단 전부(`trade_order_log`)를 근거(`reason`: `breakout_4h`,
  `near_ma200+above_cloud`, `stop_loss(-x.xx%)`, `take_profit(+x.xx%)`, `이미 보유 중`,
  `최대 동시보유 종목 수 도달`, `가상 현금 부족`, `시세 조회 실패` 등)와 함께 기록.

Config 상수는 [app/config.py](../app/config.py)에 있음.

## DB 스키마

`app/utils/db_manager.py`의 `init_db()`에 추가된 신규 테이블 6개(계좌/포지션/로그는 `broker`/`mode`
컬럼을 가져 향후 KIS/토스·실거래(live) 데이터도 같은 구조로 얹을 수 있게 설계):

- `paper_account` — 가상 계좌(broker+mode 조합당 1행): `cash_balance`, `initial_balance`
- `paper_positions` — 가상 보유 포지션(종목당 1행, 완전 청산 시 행 삭제)
- `trade_order_log` — 매매 판단/체결 감사로그(BUY/SELL/HOLD/SKIP 전부)
- `trade_engine_settings` — 엔진 실행/일시중지 토글(싱글톤 1행)
- `trade_candidate_approval` — 종목별 매매 승인 체크박스 상태(broker+mode+ticker당 1행, opt-in 화이트리스트)
- `trade_strategy_settings` — 매매 전략 파라미터(포지션당 금액/최대 동시보유/손절·익절 기준/루프 주기, 싱글톤 1행)

CRUD 함수는 `coin_screening_daily` 섹션 뒤에 있음(`get_or_create_paper_account`,
`upsert_paper_position`, `save_trade_order_log`, `get_trade_engine_settings` 등).

## 실행 방법 — 왜 따로 실행해야 하는가

```bash
python main.py trade
```

`main.py`의 `start_all()`(`all` 모드가 api/upbit/stock/coin_analysis 4개를 한 프로세스에서
멀티프로세싱으로 띄우는 함수)에는 **의도적으로 포함하지 않았다.** 매매 엔진 버그가 알림
프로세스를 같이 죽이거나, 반대로 알림 쪽 버그로 재시작할 때 매매 루프까지 끊기는 걸 막기
위한 프로세스 격리 목적.

**서버 배포([.github/workflows/deploy.yml](../.github/workflows/deploy.yml))는 애초에 `all`
모드를 안 쓰고 PM2로 개별 프로세스를 띄우는 구조.** 기존엔 `upbit-api`/`upbit-bot`/`stock-bot`
3개뿐이었고 `coin_analysis`도 빠져 있어서(서버에서 진입 후보 소스가 갱신 안 되고 있었음),
이번에 `coin-analysis-bot`(`coin_analysis` 모드)과 `trade-bot`(`trade` 모드) 2개를 추가함.
**즉 이 PR이 머지·배포되는 순간부터 서버의 실제 `alerts.db`에 자동으로 dry-run 매매가 시작된다**
(실주문은 없지만 가상 계좌/포지션/로그가 쌓이기 시작함 — 원치 않으면 대시보드 토글로 끄거나
배포 전에 `trade-bot` 라인을 지울 것).

## 실행/일시중지 토글

`/auto-trade` 대시보드 상단 스위치로 `trade_engine_settings.enabled`를 켜고 끌 수 있다.
**주의: 이 토글은 `python main.py trade` 프로세스 자체를 켜고 끄는 스위치가 아니다.** 이미 떠
있는 프로세스가 매 사이클(주기는 아래 "매매 기준 설정"의 `loop_interval_sec`, 기본 300초)마다
이 값을 확인해서, 꺼져 있으면 판단/체결/로그 기록을 건너뛰고 다시 잠든다. 프로세스가 아예 안
떠 있으면 토글을 켜봤자 아무 일도 안 일어난다.

## 대시보드

- `/auto-trade` — 읽기 전용(주문 실행 버튼 없음, 매매는 오직 `auto_trader.py` 루프에서만 발생).
  - **매매 기준 설정** — 1종목당 매수금액/최대 동시보유/손절·익절 기준/루프 주기를 입력창에서 바로 수정(아래 참고)
  - 계좌 요약(초기자본/현금/평가금액/총자산/누적손익)
  - 보유 포지션(현재가·평가손익 포함) — 종목명 클릭 시 업비트 거래 화면(`https://upbit.com/exchange?code=CRIX.UPBIT.<ticker>`)으로 이동
  - **매매 대상 코인(진입 후보)** — `coin_screening_daily` 스냅샷 기준, 다음 사이클에 실제로 판단할 후보와 동일한 목록.
    종목명 클릭 시 업비트로 이동, 종목당 **매매 승인 체크박스** 제공(아래 참고)
  - 실행/일시중지 토글
- `/auto-trade/logs` — 매매 판단/체결 이력(BUY/SELL/HOLD/SKIP 전부) 조회 전용 페이지. 원래
  `/auto-trade`에 100건씩 인라인으로 박혀 있던 로그 표를 분리함 — 대시보드는 체크박스 하나만
  토글해도 `load()`가 통째로 재실행되는데, 그때마다 실시간성이 필요 없는 로그 100줄까지 매번
  다시 렌더링되는 게 낭비라 판단(사용자 피드백). 티커/판단(BUY 등) 필터 + 50건 단위 페이지네이션
  제공. `/auto-trade` 상단 "📜 매매 이력" 버튼 또는 하단 안내문구 링크로 이동.
- API: `/api/auto-trade/summary` (GET, 로그 미포함), `/api/auto-trade/logs` (GET, `limit`/`offset`/
  `ticker`/`decision` 쿼리파라미터), `/api/auto-trade/toggle` (POST),
  `/api/auto-trade/candidates/approve` (POST), `/api/auto-trade/settings` (POST) — 전부 기존
  전역 `require_login()`에 자동으로 걸림.
- 네비게이션 위치: 처음엔 "운영/관리" 드롭다운 안에 넣었다가, 사용자 요청으로 "대시보드"/
  "종목 메모"처럼 **최상위 메뉴**로 이동함([templates/_navbar.html](../templates/_navbar.html)).

### 매매 대상 코인 수동 승인(체크박스) — 선택 매매 모드

`trade_candidate_approval` 테이블(broker+mode+ticker당 1행)에 체크 상태를 저장. 동작 방식은
**opt-in 화이트리스트**:
- 아무 것도 체크 안 하면(기본값) → 기존과 동일하게 `coin_screening_daily` 전체 후보를 대상으로 진입 판단
- 하나 이상 체크하면 → 다음 사이클(`run_trade_cycle()`)부터 **체크한 종목만** 신규 매수 후보로 좁혀짐
  (`app/core/auto_trader.py`의 `get_approved_candidate_tickers()` ∩ 후보 목록)
- 이미 보유 중인 종목의 손절/익절 청산 판단(`evaluate_exits`)에는 영향 없음 — 체크박스는 오직
  "새로 살 종목을 제한"하는 용도
- 체크한 종목이 화면상 스크리닝 후보가 아니게 되면(예: 조건 이탈) 그 사이클엔 매수 대상이 0개가
  될 수 있음(정상 동작 — approve 목록은 "허용", 후보 조건은 별개로 계속 적용됨)

대시보드에 하나라도 체크돼 있으면 "선택 매매 모드" 배지가 표시됨.

### 매매 기준 설정 (DB 저장)

손절/익절 기준을 왜 5%/10%로 잡았냐는 질문에 "백테스트 근거는 없고 손익비 1:2 정도의 상식적인
기본값"이라고 답한 뒤, 재시작 없이 바로 튜닝할 수 있게 DB화 요청을 받아 구현함.

- `trade_strategy_settings` 테이블(싱글톤 1행)에 `max_position_krw`, `max_concurrent_positions`,
  `stop_loss_pct`, `take_profit_pct`, `loop_interval_sec` 저장. 행이 없으면(최초 실행)
  `app/config.py`의 `TRADE_*` 기본값을 그대로 씀 — 즉 Config는 "DB에 아직 아무도 안 건드렸을 때의
  기본값" 역할로 격하됨.
- `app/core/auto_trader.py`의 `_effective_strategy_config()`가 매 사이클마다 이 테이블을 다시 읽어
  `trade_strategy.py`(`evaluate_entries`/`evaluate_exits`)가 기대하는 `TRADE_STOP_LOSS_PCT` 등
  속성 이름의 `SimpleNamespace`로 감싸서 넘김 — **`trade_strategy.py` 자체는 여전히 DB에 직접
  접근하지 않는 순수 함수로 유지**(오케스트레이션 레이어에서만 DB 조회).
- 루프 주기(`loop_interval_sec`)도 DB 값으로 매 반복마다 다시 읽으므로, 대시보드에서 바꾸면
  다음 sleep부터 바로 새 주기로 동작(재시작 불필요).
- `TRADE_INITIAL_CASH_KRW`(가상 계좌 초기 자본)만 DB화 대상에서 제외 — 계좌 최초 생성 시 1회만
  쓰이는 값이라 이미 만들어진 계좌엔 나중에 바꿔도 소급 적용되지 않아 "매매 기준"과 성격이 다름.
- API 유효성 검사: 손절/익절/포지션금액은 0보다 커야 하고, 동시보유 종목수는 1 이상, 루프 주기는
  최소 10초(공개 API 남용 방지) — 위반 시 400.

## Slack 알림 — 우선 비활성화

`Config.TRADE_SLACK_ALERT = False`(기본 `True`에서 변경). 모의 체결 시 `[모의매매]` 접두어로
Slack 알림을 보내는 코드(`auto_trader.py`의 `_execute()`)는 이미 있지만, 사용자 요청으로
당장은 끄고 나중에 필요할 때 `True`로 전환하기로 함.

## 검증한 것 (로컬)

실제 `alerts.db`를 건드리지 않으려고 스크래치 복사본으로 먼저 검증:
- `tests/test_trade_dry_run.py` 1차 실행 → 실제 진입 후보 20개 중 한도(5종목)만 정상 매수,
  현금 100만→50만원 정확히 차감
- 2차 실행 → 이미 보유 종목 재매수 안 함, 한도 초과분 정상 스킵
- `get_dashboard_summary()` 현재가·평가손익 계산 확인
- Flask 라우트 등록/템플릿 렌더링(네비게이션 포함) 확인
- `trade_engine_settings` 토글 round-trip 확인

이후 사용자가 로컬에서 직접 `python main.py trade`를 실행해 실제 `alerts.db`에도 정상적으로
5종목 매수 + 20건 감사로그가 쌓인 것을 확인함(2026-08-12 11:44경, `KRW-DOGE`/`SOL`/`LINK`/
`TRX`/`LSK` 매수).

### 알아둘 점 — 코드 수정 후 대시보드에 안 보일 때

`python main.py`(기본 `all` 모드)로 띄운 API 프로세스는 `use_reloader=False`로 뜨기 때문에
([main.py](../main.py) 참고), `.py` 파일을 고쳐도 자동 재시작되지 않는다(템플릿은 매 요청마다
새로 읽으므로 HTML/JS 변경은 바로 반영됨). 토글 API처럼 Python 로직을 새로 추가한 뒤엔
서버 프로세스를 수동으로 재시작해야 반영된다.

### 알아둘 점 — PM2 배포 커밋이 한 번 유실됐었음

`.github/workflows/deploy.yml`에 `coin-analysis-bot`/`trade-bot`을 추가하고 `/coin-screening`
차트 링크를 고친 커밋을, **이미 병합되고 닫힌 PR #45 브랜치에 그대로 푸시**하는 실수가 있었다.
그 브랜치는 다시 PR을 열지 않는 한 main에 반영되지 않으므로, 서버에는 dry-run 엔진 코드
자체는 배포됐어도(`python main.py trade`가 존재) **`trade-bot`/`coin-analysis-bot`이 PM2에
등록된 적이 없어 실제로 돌고 있지 않았을 가능성이 높다.** 이후 새 브랜치(`feature/upbit-auto-
trade-trailing-dca`)로 그 커밋과 이번 트레일링/물타기 기능을 함께 다시 PR로 올림 — **PR을
머지할 때마다 실제로 origin/main에 반영됐는지(`git merge-base --is-ancestor <커밋> origin/main`)
확인하는 습관이 필요함.**

## 트레일링 손절 + 연속 확인 + 물타기(선택)

"손절 -5%가 무조건 매도라 아쉽다"는 피드백을 받고, 진입가 기준 고정 손절 대신 다음 3단계를 도입:

1. **트레일링 손절** — 기준점을 진입가가 아니라 **포지션의 보유 중 최고가(`peak_price`)**로 바꿈.
   `paper_positions.peak_price`를 매 사이클 `max(기존 peak, 현재가)`로 갱신하고, 손절 판단은
   `(peak - 현재가) / peak >= stop_loss_pct`로 계산. 상승 추세에서 손절선이 같이 따라 올라가
   수익을 지키면서, 눌림목에 바로 잘리는 걸 줄임.
2. **연속 확인(`stop_loss_confirm_cycles`)** — 트레일링 손절 조건이 몇 사이클 연속으로 유지돼야
   실제 매도할지. `paper_positions.below_stop_streak`로 추적, 조건이 깨지면(가격 회복) 0으로
   리셋. 기본값 1(=즉시, 기존 동작과 동일)이라 안 건드리면 동작이 안 바뀜.
3. **물타기(체크박스, `dca_enabled`)** — 연속 확인까지 끝나서 정말 매도해야 할 시점에, 이 포지션에
   물타기가 켜져 있고 아직 남은 횟수가 있으면(`dca_count < dca_max_count`) 곧바로 팔지 않고 **평단
   대비 `-dca_trigger_pct`(기본 -10%)까지 한 번 더 대기**. 거기 도달하면 매도 대신 "1종목당
   매수금액"만큼 추가매수(`DCA_BUY`)해서 평단을 낮추고, `peak_price`/`below_stop_streak`를 새 평단
   시점으로 리셋 — 이후엔 그 새 평단 기준으로 트레일링 손절/익절을 다시 시작하고, 다시 손절 조건에
   닿으면 이 ③번 단계가 반복된다(남은 횟수가 있는 한).
   **물타기는 포지션당 `dca_max_count`회까지만 허용**(기본 2회, 대시보드 "매매 기준 설정"에서
   조정 가능) — 무제한으로 계속 추가매수하면 하락장에서 손실이 끝없이 커질 수 있어서 넣은
   안전장치. `dca_max_count`회를 다 쓰면 그다음부터는 `dca_enabled` 여부와 무관하게 그냥 일반
   손절이 나간다. 익절(`take_profit_pct`)은 이 셋과 무관하게 항상 최우선으로 확인한다.
   (최초 구현 시엔 1회 고정(`dca_used` 플래그)이었다가, "1회만 되는데 2회까지 허용해달라"는
   요청으로 `dca_count`/`dca_max_count`로 일반화함 — 기존 배포에서 이미 1회 물탄 행은 마이그레이션
   시 `dca_count=1`로 채워져 남은 허용 횟수가 정확히 유지된다.)

`trade_strategy.py`의 `evaluate_exits()`는 여전히 DB/네트워크에 직접 접근하지 않는 순수 함수로
유지 — `peak_price`/`below_stop_streak`/`dca_enabled`/`dca_count`는 호출부가 `positions` 딕셔너리에
실어 넘겨주고, 판단 결과(다음 `peak_price`/`streak`)도 `TradeDecision`에 실어 반환하면
`auto_trader.py`가 그 값을 다시 DB(`update_position_tracking`/`mark_position_dca_used`)에 쓴다.

**대시보드**: `/auto-trade`의 "매매 기준 설정"에 손절 연속확인 사이클수·물타기 트리거(%)·물타기
최대 횟수 입력칸 추가. "보유 포지션" 표엔 최고가·상태 배지(🟡 손절 대기중 / 🔵 물타기 대기중 /
🟣 물타기 N/최대 완료)·물타기 체크박스 열 추가(최대 횟수를 다 쓰면 체크박스 비활성화). 상태 배지는
`get_dashboard_summary()`가 `evaluate_exits()`를 읽기 전용으로 한 번 더 돌려서(순수 함수라 부작용
없음) "다음 사이클에 실제로 어떤 판단이 내려질지"를 미리 계산해 보여주는 것 — 실행 로직과 완전히
같은 함수를 재사용하므로 화면과 실제 동작이 항상 일치한다.

새 API: `POST /api/auto-trade/positions/dca` (`{ticker, enabled}`), 기존
`POST /api/auto-trade/settings`에 `stop_loss_confirm_cycles`/`dca_trigger_pct`/`dca_max_count`
필드 추가.

신규 DB 컬럼(마이그레이션, 이미 배포된 테이블이라 `ALTER TABLE`로 추가):
`paper_positions.peak_price/below_stop_streak/dca_enabled/dca_used(구버전, 더 이상 안 씀)/dca_count`,
`trade_strategy_settings.stop_loss_confirm_cycles/dca_trigger_pct/dca_max_count`.

## 정밀 매수조건 (다중 시간대: 일봉/5분봉/1분봉)

"매수 기준을 더 구체적으로(일봉 20MA 위 + 5분봉 20선 지지 + 1분봉 볼밴 상단 거래량 돌파 등) 잡고
싶고, 대시보드에서 조건을 선택/강제매수 하고 싶다"는 요청으로 추가. 구현 전 범위를 4가지로 확정함:

1. **기존 후보 위에 추가 필터** — `coin_screening_daily`(4시간봉 돌파/구름/200선) 후보군은 그대로 두고,
   대시보드에서 **정밀검사 체크박스**를 켠 종목에만 이 조건들을 추가로 요구한다. 체크 안 한 종목은
   기존과 동일(추가 제약 없음) — 즉 opt-in.
2. **조건 결합은 종목 단위가 아니라 조건 단위로 AND/OR 선택** — 각 조건에 "필수(AND)"/"택일(OR)"
   역할을 지정. 최종 판정 = (필수 조건 전부 통과) AND (택일 조건이 없거나 그중 하나 이상 통과).
   켠 조건이 하나도 없으면 정밀검사는 항상 통과로 취급(추가 제약 없음).
3. **강제 매수 = 종목 지정 즉시매수 버튼** — 후보 여부/승인/정밀조건/동시보유 한도를 전부 건너뛰고
   지정 티커를 "1종목당 매수금액"만큼 지금 즉시 매수. 자동매매 루프의 정상 진입 경로와는 완전히
   별개(감사로그엔 `강제매수(수동)`로 남음).
4. **5분봉/1분봉은 실시간성이 중요해 별도 루프·별도 대상** — 전체 후보를 매 매매 사이클(기본
   300초)마다 다중 시간대로 재조회하면 API 호출이 너무 많아지므로, **정밀검사 체크한 종목만** 별도
   프로세스가 독립 주기(`condition_check_interval_sec`, 기본 60초)로 검사하고 결과를 캐시한다.
   `evaluate_entries()`는 이 캐시만 읽는다(매매 사이클 안에서 직접 캔들을 재조회하지 않음).

### 조건 3종 (기본 전부 비활성화 — 켜기 전까진 동작이 안 바뀜)

| condition_key | 의미 | 기본 파라미터 |
|---|---|---|
| `daily_above_ma` | 일봉 종가가 N일 이동평균 이상 | `ma_period=20` |
| `m5_ma_support` | 5분봉이 N선에 지지받고 반등 (저가가 이평선 근접까지 눌렸다가 종가는 이평선 위 마감) | `ma_period=20`, `touch_tolerance_pct=0.3` |
| `m1_bb_breakout_volume` | 1분봉이 볼린저밴드 상단을 거래량 동반 돌파 (종가>상단밴드, 거래량≥직전 평균×배수) | `bb_period=20`, `bb_mult=2.0`, `vol_lookback=20`, `vol_ratio_threshold=2.0` |

`app/core/entry_conditions.py`(순수 함수, DB/네트워크 직접 접근 없음 — 캔들은 콜백으로 주입)에
계산 로직이 있고, `app/core/entry_condition_checker.py`(오케스트레이션, `python main.py
condition_check`로 독립 프로세스 실행 — `trade`/`coin_analysis`와 같은 이유로 격리)가 캔들 조회
(`pyupbit.get_ohlcv`, 공개 API)와 DB 캐시 저장을 담당한다.

### DB

- `trade_condition_settings` — 조건별 설정(싱글톤 아님, condition_key당 1행): `enabled`,
  `logic_group`(AND/OR), `params`(JSON). `init_db()`가 최초 1회 3종 기본 행을 시딩(전부 `enabled=0`).
- `trade_candidate_approval.condition_watch` — "정밀검사" 체크박스(기존 `approved` 컬럼과 독립).
- `trade_condition_status` — 검사 결과 캐시(broker+mode+ticker당 1행): `passed`, `detail`(조건별
  개별 결과 JSON), `checked_at`. `evaluate_entries()`가 읽기 전용으로 참조.
- `trade_strategy_settings.condition_check_interval_sec` — 정밀검사 루프 주기(기본 60초, 최소 10초).

### auto_trader.py 통합

`trade_strategy.py`의 `evaluate_entries()`는 여전히 순수 함수 — `condition_watch_tickers`(집합)와
`condition_status_map`(캐시된 dict)을 파라미터로 받을 뿐 DB에 직접 접근하지 않는다. watch 대상인데
캐시에 결과가 없으면(검사 루프가 안 떠 있거나 아직 첫 검사 전) `SKIP: 정밀조건 검사 결과 없음`,
있는데 `passed=False`면 `SKIP: 정밀조건 미충족`으로 매수를 보류한다.

### API / 대시보드

새 API: `POST /api/auto-trade/candidates/condition-watch` (`{ticker, enabled}`),
`POST /api/auto-trade/conditions/settings` (`{conditions: [{condition_key, enabled?, logic_group?,
params?}, ...]}`, 여러 건 한 번에 부분 갱신), `POST /api/auto-trade/force-buy` (`{ticker}`).
기존 `POST /api/auto-trade/settings`에 `condition_check_interval_sec` 필드 추가.

`/auto-trade` 대시보드에 "🧪 정밀 매수조건" 카드(조건 3종 체크박스+AND/OR 선택+파라미터 입력) 추가.
"매매 대상 코인" 표에 **정밀검사** 체크박스 열과 상태 배지(⏳ 검사 대기 / ✅ 조건 충족 / ❌ 미충족)
추가. 상단에 **강제 매수**(티커 입력 + 버튼) 추가 — 조건 확인창(`confirm()`) 후 실행.

### 배포

`.github/workflows/deploy.yml`에 `condition-check-bot`(`condition_check` 모드) PM2 프로세스 추가 —
`trade-bot`과 마찬가지로 `all`/`start_all()`엔 포함하지 않고 독립 프로세스로 격리. 이 프로세스가 안
떠 있으면 정밀검사 켠 종목은 검사 결과가 영영 안 생겨 매수가 계속 보류된다(의도된 안전 기본값 —
검사 없이 통과시키지 않음).

## 다음 단계

- 실계좌 선택 잔고 조회는 [업비트 실계좌 연결 문서](auto-trade-upbit-live.md)를 참고한다. 먼저 `python main.py live_balance`로 `KRW + 선택 코인` 필터링을 검증한다.
- 실계좌 자동매매는 잔고 조회 검증, live 원장 분리, 주문 체결 재조회, 중복 주문 방지 검증 이후에 별도 단계로 진행한다.

## 이후 범위

- Slack 알림 재활성화 여부 결정
- KIS/토스증권 브로커 구현 — `BrokerClient` 인터페이스는 이미 확장 가능하게 설계됨
- 실거래(live) 전환 — `UPBIT_ACCESS_KEY`/`SECRET_KEY` 추가, 실주문 브로커 구현 필요
- 부분매매, 다중 전략, 백테스트
