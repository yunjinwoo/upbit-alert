# 하락위험 코인 관심목록 (Downside Watch) — 설계 문서

> 상태: **Phase 1 확정** (Q1~Q3 결정 완료, 구현 착수). Phase 2(§6 Q4)는 별도 결정.

## 1. 배경 — 왜 만드나

현재 `/auto-trade` 실거래 패널의 흐름은 **매수 한 방향**뿐이다:

```
🎯 매매 대상 코인  ──(관심 등록 체크)──▶  🔴 실거래  ──(실거래 승인 체크)──▶  봇이 매수
   (스크리닝 후보 ~80개)                    (관심 등록된 것만)
```

진입 신호는 3가지로 늘었지만([upbit_market_analysis.py](../app/core/upbit_market_analysis.py)의
`breakout_4h` / `near_ma200+above_cloud` / `momentum_confluence`), **진입 신호에 대응하는
하락/청산 신호를 화면에서 보는 수단이 없다.**

운영자(사용자) 피드백:

> "매매 대상처럼 '하락위험 코인'이란 영역으로 코인을 확인하고, 체크박스로 관심 등록해서
> 거래 쪽으로 옮길 수 있게 하고 싶다."
>
> 스크리닝 범위: **전체 KRW 마켓**
> 체크 후 동작: **일단은 목록만 — 확인하고 관심 등록해서 거래 쪽으로 옮기는 것까지.**
> (관심 등록된 종목을 봇이 어떻게 처리할지는 별도 결정 = §6 Phase 2)

즉 이번 범위는 **매수 파이프라인의 거울상인 "관찰/스테이징" 목록**을 만드는 것. 자동 매도는
포함하지 않는다.

## 2. 신호 세트 — "하락위험"의 정의

**유일한 소스: `db_manager.DOWNSIDE_SIGNALS`** (key/라벨/`gates` 플래그). WHERE 절, 대시보드
요약(signals/signal_count), 템플릿의 필터 체크박스·뱃지 라벨이 전부 이 목록 하나를 참조한다
— 신호 추가/이름변경은 여기 한 곳만 고치면 된다(`calc_indicators()`에 계산 로직 + 마이그레이션
컬럼은 별도).

전부 기존 `calc_indicators()`가 쓰는 **업비트 4시간봉 같은 캔들**에서 계산한다 → 네트워크 호출
추가 없음. 진입 3신호의 반대편:

| 신호 | 판정 (마지막 확정 캔들 = -2) | 대응하는 진입 신호 |
|---|---|---|
| `below_ma200` | 종가가 200이평선 대비 `-COIN_MA200_NEAR_PCT`(-3%) 아래 — "근접"이 아니라 명확히 이탈 | `near_ma200` |
| `below_cloud` | 종가가 일목균형표 구름 **하단**(min(선행A, 선행B)) 아래 | `above_cloud` |
| `ema_dead_cross` | EMA5가 EMA20을 이번 캔들에 하향 돌파, **또는** EMA20 < EMA60 역배열 | `momentum_confluence`의 골든크로스 |
| `macd_neg` | MACD(12,26,9) 히스토그램 음수 | `momentum_confluence`의 히스토그램 양수 |
| `rsi_overbought` | RSI(14) > 70 | `momentum_confluence`의 RSI<70 |

### 후보 필터 (mirror of `get_coin_screening_candidates`)

```sql
-- get_coin_downside_candidates()
SELECT * FROM coin_screening_daily
WHERE below_ma200 = 1 OR below_cloud = 1 OR ema_dead_cross = 1 OR macd_neg = 1
ORDER BY trade_value DESC
LIMIT 120   -- DOWNSIDE_CANDIDATE_LIMIT — 하락장엔 후보가 200+개까지 나와 응답/DOM이 비대해짐
```

**확정 (2026-09-03):**
- 서버 필터는 위 **4개 하락 신호 중 1개 이상**이면 목록에 등재 — 느슨하게 다 내보낸다.
  단 하락장 폭주 방지로 **거래대금 상위 120개(`DOWNSIDE_CANDIDATE_LIMIT`)**로 자른다.
- `rsi_overbought`는 서버 필터 제외(단독 등재 안 함) — 상승장에서도 떠서 "하락위험" 목록엔
  노이즈. 계산·저장은 하고 **뱃지 + 프론트 필터 체크박스**로만 쓴다.
- **강도 게이팅 없음.** 대신 프론트에서 신호별 체크박스로 좁혀 본다(아래).

## 3. 데이터 모델

### 3.1 `coin_screening_daily` 컬럼 추가 (마이그레이션)

기존: `ma200, ma200_dist_pct, near_ma200, above_cloud, breakout_4h, breakout_vol_ratio,
breakout_candle_rate, momentum_confluence`

추가:

| 컬럼 | 타입 | 용도 |
|---|---|---|
| `below_ma200` | INTEGER 0/1 | 신호 |
| `below_cloud` | INTEGER 0/1 | 신호 |
| `ema_dead_cross` | INTEGER 0/1 | 신호 |
| `macd_neg` | INTEGER 0/1 | 신호 |
| `rsi_overbought` | INTEGER 0/1 | 뱃지 |
| `rsi` | REAL | 화면 표시(현재 저장 안 함) |
| `macd_hist` | REAL | 화면 표시 |

`ALTER TABLE coin_screening_daily ADD COLUMN ...` 7건. 기존 배포 DB는 컬럼만 얹으면 되고, 다음
스크리닝 사이클(30분)에 값이 채워진다.

### 3.2 `trade_candidate_approval` 컬럼 추가

현재 `(broker, mode, ticker)` 단위로 `approved / condition_watch / watchlist` 를 들고 있다.
여기에 하나 더:

| 컬럼 | 용도 |
|---|---|
| `downside_watchlist` | "하락위험 관심 등록" 체크박스. 기존 `watchlist`(매수 관심)와 완전 독립 |

`downside_approved`는 Phase 2에서 필요해지면 그때 추가(지금은 안 만든다).

컬럼은 마이그레이션 목록 + `CREATE TABLE trade_candidate_approval` 본문 양쪽에 넣는다
(형제 컬럼 `watchlist` / `condition_watch`와 동일 패턴).

## 4. 백엔드

### 4.1 `upbit_market_analysis.py`

- `calc_indicators()`에 위 5개 신호 + `rsi` / `macd_hist` 원시값 계산 추가. 이미 `_ema` /
  `_rsi` / `_macd_histogram` / 구름 계산이 다 있으므로 **같은 시리즈 재사용**, 캔들 재조회 없음.
- `run_coin_screening()` → `save_coin_screening()`가 새 필드까지 upsert.

### 4.2 `db_manager.py`

- `save_coin_screening` INSERT/UPDATE 문에 새 컬럼.
- `get_coin_downside_candidates()` 신규 (위 SQL).
- `get_downside_watchlist_tickers(broker, mode)` / `set_candidate_downside_watchlist(broker,
  mode, ticker, on)` — 기존 `get_watchlist_tickers` / `set_candidate_watchlist`와 동일 패턴.
- 마이그레이션 목록에 `ALTER TABLE` 추가.

### 4.3 `auto_trader.py` — `get_live_dashboard_summary()`

반환 dict에 추가:

| 키 | 내용 |
|---|---|
| `downside_candidates` | `get_coin_downside_candidates()` 결과 + `downside_watchlist` 상태 + 어떤 신호가 켜졌는지, 보유 중이면 평가손익 |
| (관심 등록분은 위 목록 안에서 `downside_watchlist=true`로 구분 — 별도 표는 프론트에서) |

**매매 판단/주문은 절대 없음** — 기존 요약 함수와 동일하게 읽기 전용.
`_reconcile_live_positions` / `evaluate_exits` / 루프는 **건드리지 않는다**(Phase 1 범위 밖).

### 4.4 `server.py` 라우트 (mirror of `/api/auto-trade/live/candidates/watchlist`)

- `POST /api/auto-trade/live/downside/watchlist` — body `{ticker, watchlisted}` →
  `set_candidate_downside_watchlist('upbit', 'live', ticker, watchlisted)`
- 목록 자체는 기존 `GET /api/auto-trade/live/summary`에 실려 나감(위 4.3).

## 5. 프론트엔드 (`templates/auto_trade.html`)

- "🎯 매매 대상 코인" 패널 **바로 아래**에 "⚠️ 하락위험 코인" 패널 신규 — 같은 테이블 구조:
  종목 / 현재가 / 24h 변동 / **켜진 신호 뱃지**(`200선이탈` `구름아래` `데드크로스` `MACD-` `RSI과열`)
  / 거래대금 / **[관심 등록] 체크박스**. 보유 중인 종목 행엔 `보유중 +N%` 뱃지.
- **패널 상단 신호 필터 체크박스** — `200선이탈` `구름아래` `데드크로스` `MACD-` `RSI과열` 5개.
  체크한 신호를 **모두** 가진 코인만 표에 보인다(AND, 클라이언트 사이드 필터 — 서버 재조회 없음).
  아무것도 체크 안 하면 서버 필터 통과분(하락 신호 ≥1) 전부 표시. 기본 정렬은 **켜진 신호 개수
  많은 순**, 동수면 거래대금 순.
- 관심 등록된 종목은 "🔴 실거래" 영역 안에 "하락위험 관심" 소목록으로 모아 표시(보유 중이면
  수량/평단/평가손익도). — 매수 관심(`watchlist`) 목록과 시각적으로 구분.
- Phase 1에서는 여기까지. "실거래 매도 승인" 같은 버튼은 만들지 않는다.

## 6. 확정 사항 / 열린 질문

**확정 (2026-09-03):**
- **Q1. 목록 등재 기준** → 하락 신호 **1개 이상**이면 다 등재. 강도 게이팅 없음. 프론트에서
  신호별 체크박스(AND)로 좁혀 본다. 기본 정렬 = 켜진 신호 개수 많은 순.
- **Q2. `rsi_overbought`** → 서버 필터 제외. 뱃지 + 프론트 필터 체크박스로만.
- **Q3. 스코프** → 순수 스크리닝 결과만. 보유 중인 행엔 `보유중 +N%` 뱃지. (보유 중인데
  신호 없는 코인은 이 목록에 안 나옴 — Phase 2 매도 모니터링 영역.)

**열린 질문:**

- **Q4. Phase 2 (별도 결정, 이번 구현 안 함).** 관심 등록된 하락위험 종목을 봇이 어떻게
  처리할지:
  - (a) **알림만** — 신호가 새로 켜지면 Slack 1회 푸시.
  - (b) **보유 중이면 손절/트레일링 매도 실제 활성화** — 지금 손절 90%라 안 걸리는데, 관심
    등록(+승인)한 종목만 실제 손절가로 청산되게.
  - (c) **매수 제외** — 하락위험 관심 종목은 매수 관심(`watchlist`)에 있어도 신규 진입 대상에서 뺌.
  - Phase 1 완료 후 사용 패턴 보고 결정.

## 7. 구현 상태 (Phase 1 — 2026-09-03 구현 완료)

1. ✅ `app/core/upbit_market_analysis.py` — `calc_indicators()`에 `below_ma200` / `below_cloud`
   / `ema_dead_cross` / `macd_neg` / `rsi_overbought` + 원시값 `rsi` / `macd_hist`.
   EMA/RSI/MACD 계산을 200선 블록 밖으로 빼서 신규 상장 종목(캔들<200)도 신호가 잡히게 함.
2. ✅ `app/utils/db_manager.py` — `coin_screening_daily` 컬럼 7 + 마이그레이션,
   `save_coin_screening` upsert 문 확장, `get_coin_downside_candidates()`,
   `trade_candidate_approval.downside_watchlist` 컬럼 + 마이그레이션,
   `get_downside_watchlist_tickers` / `set_candidate_downside_watchlist`.
3. ✅ `app/core/auto_trader.py` — `get_live_dashboard_summary()` 반환에 `downside_candidates`
   (신호 리스트 / `signal_count`(rsi 제외) / `downside_watchlist` / 보유 시 `pnl_pct`),
   `signal_count` 내림차순 정렬. **`evaluate_exits` / `_reconcile_live_positions` / 루프 무변경.**
4. ✅ `app/api/server.py` — `POST /api/auto-trade/live/downside/watchlist`.
5. ✅ `templates/auto_trade.html` — "⚠️ 하락위험 코인" 패널(매매 대상 코인 패널 바로 아래),
   신호 필터 체크박스 5개(AND, 클라이언트), 관심 등록 체크박스, 보유 종목 `보유중` 뱃지.
6. 검증 — `calc_indicators` 합성 상승/하락/짧은이력 스모크(신호 방향성 확인), DB 마이그레이션
   idempotent + 신규 함수 동작 확인. **실 스크리닝 재수집(전체 마켓) 후 화면 육안 확인은 운영
   환경에서 필요** — `coin_screening_daily`의 새 컬럼은 다음 `coin_analysis` 사이클에 채워짐.
7. 이 문서. [auto-trade-upbit-live.md](auto-trade-upbit-live.md) 갱신은 Phase 2 확정 시 함께.

**배포 후 필요**: 운영 서버에서 `python main.py coin_analysis` 1회(또는 30분 대기)로 새 신호
컬럼을 채워야 패널에 종목이 나온다. 마이그레이션은 앱 기동 시 `init_db()`가 자동 적용.

토스증권 쪽(`toss_market_analysis.py` / `toss_trade.html`)은 이번엔 손대지 않는다 — 업비트에서
먼저 쓰고 나서 동일 패턴으로 이식.
