# 회복형 분할 물타기 (Recovery DCA) — 설계 문서

> 상태: **확정 + 구현 완료**. 아래 §5의 열린 질문은 운영자 결정으로 모두 닫혔고(2026-09-18),
> 그 결정대로 구현됐다. 기본값은 **꺼짐**(`recovery_dca_enabled=false`)이라 대시보드에서 켜기 전까지
> 기존 동작(트레일링 손절 + 항상-먼저-물타기)은 전혀 바뀌지 않는다.

## 1. 배경 — 왜 만드나

현재 [trade_strategy.py](../app/core/trade_strategy.py)의 청산 로직은 이렇다:

- 트레일링 손절(보유 중 최고가 대비 `stop_loss_pct` 하락) 조건이 `stop_loss_confirm_cycles`회
  연속 성립하면,
- 아직 물타기 횟수(`dca_max_count`)가 남았으면 평단 대비 `-dca_trigger_pct`(기본 -10%)까지 한 번
  더 기다렸다가 **매수금액 전액**으로 1회 물타고 트레일링 기준점을 리셋,
- 물타기 횟수를 다 쓰면 **전량 손절**.

운영자(사용자) 피드백:

> "특정 손실에서 매도하는 것보다, 시간이 조금 지나서 소액으로 물타기를 적당한 부분에서
> 하고 소액 손절을 반복하는 게 나랑 맞는 것 같다."
>
> 구체화: **"마이너스 20%가 됐을 때 일부를 넣어서(소액 물타기), 5% 정도 수익이 나면 매도한다.
> 이걸 반복하고 싶다."**

즉 "한 방에 크게 손절" 대신 **깊은 하락에서 소액 분할 매수 → 평단 부근 소폭 반등에서 청산 →
반복**하는, 손실 포지션을 "돌려막기"하며 회복시키는 방식.

### 이 방식의 성격 (솔직한 평가)

**장점**
- 운영자가 실제로 지킬 수 있다. 심리적으로 못 버티는 "큰 손절"을 자동화가 대신 눌러주지 못하면,
  결국 사람이 개입해서 전략을 망가뜨린다. 지킬 수 있는 전략이 이론상 최적 전략보다 낫다.
- 횡보/변동성 장에서 평단을 유리하게 만들고, 되돌림(mean-reversion) 기회를 준다.
- 손절 슬리피지를 여러 번 무는 대신, 반등에서 익절로 빠져나온다.

**위험 (반드시 하드캡으로 막아야 함)**
1. **추세 하락장에서 계속 사들이면 포지션당 투입 자본이 의도한 상한을 넘는다.** 물타기는
   손실 구간에 돈을 더 넣는 행위라, 코인이 계속 빠지면 손실 원금 자체가 커진다.
2. **끝이 없으면 안 된다.** "5% 반등"이 영영 안 오는 종목(우하향 지속, 상장폐지성 급락)에서는
   자본이 묶이거나 전액 손실로 이어진다.
3. 여러 종목이 동시에 이 상태면 현금이 고갈돼 신규 진입/다른 물타기를 못 한다.

→ **결론: "소액 물타기 + 소폭 익절 반복"은 하되, 그 위에 "여기까지 오면 무조건 정리한다"는
천장(하드캡)을 씌운 형태로 설계한다.**

---

## 2. 전략 정의 (제안)

새 청산 모드 **`recovery_dca`**. 전략 설정에서 on/off. 켜지면 기존 트레일링 손절/물타기 로직을
이 포지션에 대해 대체한다(익절 조건은 아래 참고).

### 2.1 상태 (포지션당)

`paper_positions` 테이블(모의·실거래 공용, `(broker, mode, ticker)` 단위)에 컬럼 추가:

| 컬럼 | 용도 |
|---|---|
| `first_entry_price` | 최초 진입가. 리포트/백필 기준용. 첫 매수 시 기록, 이후 불변 |
| `total_invested_krw` | 이 포지션에 넣은 매수 누적액(최초 매수 + 모든 물타기 + 강제매수). 투입 상한 판정. **부분 매도로 일부 회수해도 줄지 않는다** — 줄이면 상한이 되살아나 하락장에서 계속 사들이는 걸 못 막는다 |
| `recovery_dca_count` | 이 포지션에서 실행한 회복형 물타기 횟수. 기존 `dca_count`와 **분리**해서 센다(트리거·금액이 다른 별개 동작이라, 모드를 껐다 켜도 서로의 남은 횟수를 잡아먹지 않게) |
| `last_dca_at` | 마지막 물타기 시각. 쿨다운·시간 하드스톱 기준 |
| `last_partial_stop_at` | 마지막 소액 손절 시각. 소액 손절 반복 간격 판정 |
| `recovery_partial_stop_count` | 이 포지션에서 실행한 소액 손절 횟수(표시/로그용) |

`avg_buy_price` / `qty`는 기존대로 브로커가 관리(실거래는 실제 잔고, 모의는 가상 원장).

### 2.2 파라미터 (`trade_strategy_settings` + 대시보드)

전부 대시보드(매매 기준 설정 → "회복형 분할 물타기")에서 수정 가능하고, 저장하면 다음 사이클부터
적용된다. 기본값은 `app/config.py`의 `TRADE_RECOVERY_*` 상수.

| 파라미터 | 기본값 | 의미 |
|---|---|---|
| `recovery_dca_enabled` | `false` | 이 모드 on/off. **켜면 이 포지션들에 트레일링 손절이 동작하지 않는다** |
| `recovery_dca_trigger_pct` | `20.0` | **평단 대비** 이 % 이상 하락하면 물타기 후보 |
| `recovery_dca_amount_krw` | `50,000` | 1회 물타기 금액(소액, 절대금액). 매수금액(10만원)의 절반 |
| `recovery_dca_cooldown_min` | `60` | 직전 물타기(없으면 최초 진입)로부터 최소 경과 시간(분). "시간이 조금 지나서" |
| `recovery_take_profit_pct` | `5.0` | **평단 대비** 이 % 이상이면 전량 매도(반등 익절) |
| `recovery_dca_max_count` | `3` | 포지션당 회복형 물타기 최대 횟수 |
| `recovery_max_invested_krw` | `250,000` | 포지션당 총 투입액 상한. 다음 물타기를 더하면 넘는 시점부터 금지 |
| `recovery_time_stop_days` | `7` | **물타기 상한 소진 후** 이 일수가 지나도 손실이면 전량 정리(`0`=비활성) |
| `recovery_partial_stop_pct` | `30.0` | 상한 소진 후, 평단 대비 이 % 이상 하락하면 보유량 일부 매도(`0`=비활성) |
| `recovery_partial_stop_ratio` | `20.0` | 소액 손절 1회에 덜어낼 보유 수량 비율(%) |
| `recovery_partial_stop_cooldown_min` | `360` | 소액 손절 반복 최소 간격(분) |

`TRADE_MIN_ORDER_KRW`(5,000원, 업비트 최소 주문금액)는 거래소 제약이라 대시보드에 노출하지 않고
`app/config.py`에서만 읽는다 — 소액 손절 금액이 이 밑이면 최소 주문금액까지 올려서 팔고, 그래도
남는 쪽이 최소 주문금액 미만이면 더 쪼갤 수 없으니 전량 매도한다(팔 수 없는 잔량이 영구히 남는 걸 방지).

> ⚠️ `recovery_max_invested_krw`는 "1종목당 매수금액 + 1회 물타기 금액"보다 크게 둬야 한다.
> 작으면 진입 직후부터 물타기 여력이 없는(=상한 소진) 상태가 되고, 시간 하드스톱 타이머도
> 진입 시점부터 돌기 시작한다.

### 2.3 판정 순서 (`evaluate_exits` → `_evaluate_exit_recovery`, 이 모드일 때)

매 사이클, 보유 포지션마다. 먼저 걸린 게 이긴다.

```
pnl_pct = (price - avg_buy_price) / avg_buy_price * 100

1) 익절
   pnl_pct >= take_profit_pct(10%)          → SELL 전량 (reason: take_profit)
   pnl_pct >= recovery_take_profit_pct(5%)  → SELL 전량 (reason: recovery_take_profit)
   (기존 익절 기준이 더 높아서 먼저 확인 — 더 크게 먹을 수 있으면 그쪽으로 나간다)

2) RSI 과매수 매도 (rsi_exit_enabled 켜져 있을 때만)
   pnl_pct >= 0 이고 RSI >= rsi_exit_overbought  → SELL 전량 (reason: rsi_exit)
   손실 구간에서는 일부러 동작하지 않는다 — RSI로 팔면 결국 "큰 손절"이라 이 모드의 전제가 깨진다.

   ── 여기서 물타기 여력을 계산한다 ──
   count_left  = recovery_dca_count < recovery_dca_max_count
   budget_left = total_invested_krw + recovery_dca_amount_krw <= recovery_max_invested_krw
   capped      = not (count_left and budget_left)
   기준 시각   = last_dca_at 또는 (아직 안 물탔으면) entry_at
               → 상한이 "횟수 소진"으로 찼으면 마지막 물타기 시각이 곧 상한 도달 시각이다

3) 시간 하드스톱 (capped, recovery_time_stop_days > 0, pnl_pct < 0)
   now - 기준시각 >= recovery_time_stop_days  → SELL 전량 (reason: recovery_time_stop)
   손실이 아닐 때(0% ~ 익절선 사이)는 팔지 않고 반등을 더 기다린다.

4) 물타기 (not capped)
   pnl_pct > -recovery_dca_trigger_pct      → HOLD (status: recovery_dca_pending)
   쿨다운 미경과                             → HOLD (status: recovery_dca_pending)
   그 외                                    → DCA_BUY recovery_dca_amount_krw
   실행 후: recovery_dca_count += 1, total_invested_krw += 체결액, last_dca_at = now
           → 평단이 내려가므로 다음 -20% 기준·+5% 기준 모두 새 평단으로 자동 이동

5) 소액 손절 (capped 일 때만, recovery_partial_stop_pct > 0)
   pnl_pct <= -recovery_partial_stop_pct 이고 반복 간격 경과
       → SELL 보유량의 recovery_partial_stop_ratio% (reason: recovery_partial_stop)
   간격 기준은 last_partial_stop_at, 없으면 마지막 물타기 시각 — 물탄 직후에 곧바로 되팔지 않게.
   남길 조각이 최소 주문금액 미만이면 전량 매도(reason: recovery_partial_stop_all).
   **물타기가 가능한 구간에서는 동작하지 않는다** — 같은 구간에서 사고 파는 게 겹치지 않도록
   "물타기를 더 못 하게 된 뒤"를 전제 조건으로 뒀다.

6) 그 외 HOLD
   capped면 status='recovery_capped'(반등 대기), 아니면 'recovery_dca_pending'
```

**트레일링 손절은 이 모드에서 동작하지 않는다** — `stop_loss_pct`/`stop_loss_confirm_cycles`를
보지 않는다. 단 `peak_price`/`below_stop_streak`는 HOLD마다 계속 갱신해둔다: 이 모드를 나중에 끄면
트레일링 손절이 곧바로 그 값을 참조하는데, 멈춰 있던 옛 최고가가 남아 있으면 모드를 끈 직후에 바로
손절 연속확인이 시작되는 사고가 난다.

### 2.4 예시 흐름

매수금액 10만원, 파라미터 기본값 가정.

| 시점 | 사건 | 평단 | 보유액 | 총투입 | pnl% |
|---|---|---|---|---|---|
| T0 | 최초 매수 10만원 | 1,000 | 10만 | 10만 | 0% |
| T1 | 가격 800 (-20%), 쿨다운 경과 → 물타기 5만원 | 933 | 15만 | 15만 | -14% |
| T2 | 가격 980 → pnl +5% 도달 → **전량 매도** | — | 0 | — | +5% 실현 |
| T2' | (안 팔렸고 계속 하락) 가격 747 (평단 933 대비 -20%) → 2차 물타기 5만원 | 872 | 20만 | 20만 | ... |
| T3 | 총투입 20만 → 상한(25만) 근접, 3차까지만 가능 |
| T4 | 3차 후 총투입 25만 도달 → 이후엔 물타기 없음 (recovery_capped) |
| T5 | 상한 소진 상태에서 평단 대비 -30% 이하로 더 빠지면 6시간마다 보유량 20%씩 소액 손절 |
| T6 | 상한 소진 후 7일이 지나도 여전히 손실이면 전량 정리 (recovery_time_stop) |

---

## 3. 기존 로직과의 관계

- **진입(`evaluate_entries`)**: 변화 없음. 신규 매수는 지금처럼 스크리닝 후보 + 승인 화이트리스트로.
  회복형 물타기로 전량 청산된 종목은 다음 사이클에 일반 진입 로직이 다시 후보로 볼 수 있다.
- **강제매수/강제매도(`force_buy`/`force_sell`)**: 변화 없음. `force_buy`로 들어간 돈도
  `total_invested_krw`에 더한다(`record_position_entry_cost`) — 안 더하면 손으로 평단을 낮춘 만큼이
  상한 계산에서 사라져 이미 상한을 넘겼는데도 자동 물타기가 계속 허용된다. 회복형 물타기 "횟수"는
  늘리지 않는다(수동 매수는 전략이 센 물타기가 아니므로).
- **`_reconcile_live_positions`**: 실거래에서 실제 잔고 → 추적행 동기화. `first_entry_price`/
  `total_invested_krw`가 **비어 있는 행만** `backfill_position_cost_basis()`로 채운다
  (`first_entry_price = avg_buy_price`, `total_invested_krw = qty × avg_buy_price` — 부분 매도가 아직
  없었다면 이 근사는 실제 누적 투입액과 정확히 같다). 대상은 (a) 이 기능 전에 사서 보유 중인 종목,
  (b) 실거래 최초 매수 직후(추적 행이 아직 없어 `record_position_entry_cost`가 실패한 경우).
  **이미 값이 있는 행은 절대 덮어쓰지 않는다** — 매 사이클 도는 경로라서 덮어쓰면 물타기로 쌓아온
  누적액이 매번 평단×수량으로 되돌아가 상한이 무력화된다.
- **Slack 알림**: 같은 action이라도 성격이 다르므로 라벨로 구분한다 — "회복형 물타기 매수",
  "소액 손절 매도(일부)", "회복형 매도". Slack만 보고도 큰 손절이 아니라 일부만 덜어냈다는 걸
  알 수 있어야 함.
- **대시보드 미리보기(`get_dashboard_summary` / `get_live_dashboard_summary`)**: `evaluate_exits`를
  읽기 전용으로 한 번 더 돌리는 기존 구조 그대로 → `next_action`에 회복형 판정이 자동 반영됨.
  실거래 쪽은 미리보기용 포지션 dict를 직접 조립하므로(`_held_extra_fields`) 회복형 상태값
  (횟수/누적 투입액/마지막 실행 시각)도 함께 넘겨야 한다 — 빠지면 "한 번도 안 물탄 포지션"으로
  오해해 실제 사이클과 다른 판단을 화면에 보여준다.
  뱃지는 모드가 켜져 있을 때 회복형 것으로 바뀐다(🔵 회복형 물타기 대기 / 🟠 상한 소진 — 반등 대기).
  종목별 물타기 체크박스는 이 모드에서 판단에 쓰이지 않으므로 체크박스 대신 횟수만 표시한다.

---

## 4. 구현 범위 (완료)

1. `app/config.py` — `TRADE_RECOVERY_*` 기본값 상수 11개 + `TRADE_MIN_ORDER_KRW`.
2. `app/utils/db_manager.py` —
   - `trade_strategy_settings`에 `recovery_*` 컬럼 11개 + `ALTER TABLE` 마이그레이션.
   - `paper_positions`에 `first_entry_price`, `total_invested_krw`, `recovery_dca_count`,
     `last_dca_at`, `last_partial_stop_at`, `recovery_partial_stop_count` + 마이그레이션.
   - `get_trade_strategy_settings` / `set_trade_strategy_settings`에 신규 필드
     (`_RECOVERY_SETTING_DEFAULTS()`로 키 목록을 한 곳에 모아둠 — 컬럼이 아직 없는 배포 DB에서도
     같은 키 집합을 항상 돌려준다).
   - 상태 갱신 헬퍼: `record_position_entry_cost`, `backfill_position_cost_basis`,
     `mark_recovery_dca_used`, `mark_recovery_partial_stop`.
3. `app/core/trade_strategy.py` — `_evaluate_exit_recovery(pos, price, cfg, rsi_now, now)`로 분리한
   순수 함수 + `evaluate_exits`의 모드 분기. `TradeDecision`에 `recovery` / `partial_sell` 플래그 추가
   (체결 후 어떤 상태를 갱신할지 오케스트레이션 레이어가 이 플래그로 판단).
4. `app/core/auto_trader.py` — `_effective_strategy_config`에 신규 파라미터, `_execute`에 누적 투입액
   기록 · 회복형/기존 물타기 카운터 분기 · 부분 매도 쿨다운 기록 · Slack 라벨,
   `_reconcile_live_positions`에 백필, `force_buy`에 누적 투입액 반영,
   `_held_extra_fields`에 회복형 상태값.
5. `app/api/server.py` + `templates/auto_trade.html` — 설정 API 검증(0을 "비활성"으로 쓰는 세
   파라미터만 0 허용) + 대시보드 설정 폼 입력 11개 + 회복형 상태 뱃지 + 안내 문구.
6. 테스트 — `tests/test_recovery_dca.py` (DB/네트워크 없이 판단만 검증: 익절·트리거·쿨다운·횟수 상한·
   투입액 상한·소액 손절·최소 주문금액 처리·시간 하드스톱·RSI 예외·모드 스위치 경계값 40건).
   실행: `python tests/test_recovery_dca.py`
7. 문서 — 이 파일 + [auto-trade-upbit-live.md](auto-trade-upbit-live.md)에 모드 언급.

---

## 5. 확정된 결정 (2026-09-18, 운영자)

- **Q1. 기준 가격 → 현재 평단 대비.** 물타면 기준이 같이 내려가서 "다음 물타기는 새 평단에서 또
  -20%"가 되므로 자연스럽게 간격이 벌어지고 무분별한 연속 매수를 막는다. `+5%`도 평단 대비여야
  "물타서 평단 낮추고 → 그 평단 조금 위에서 판다"는 의도와 맞다.

- **Q2. 익절 시 → 전량 매도.** "5% 나면 매도" 문장 그대로. 일부만 팔면 잔량이 남아 상태가 복잡해지고,
  청산 후 재진입은 일반 진입 로직에 맡긴다.

- **Q3. "5% 반등"이 영영 안 올 때 → 시간 하드스톱(캡 도달 후 N일).** 물타기 상한(횟수·투입액)을 다 쓴
  시점부터 `recovery_time_stop_days`(기본 7일)가 지나도 손실이면 전량 정리한다. 가격 기준 하드스톱
  (최초 진입가 대비 -X%)은 채택하지 않았다 — "큰 손절 싫다"는 취지와 정면으로 충돌해서. 최대 손실
  원금은 `recovery_max_invested_krw`가 제한한다.
  - 타이머 기준 시각은 `last_dca_at`(없으면 `entry_at`)이다. 상한이 횟수 소진으로 찼으면 마지막
    물타기 시각이 곧 상한 도달 시각이고, 투입액이 모자라 한 번도 못 물탄 경우엔 진입 시점부터가
    이미 상한 도달 상태다.

- **Q4. "소액 손절 반복"도 살린다 — 단 물타기를 더 못 하게 된 뒤에만.** 평단 대비 -30% 이하에서
  보유량의 20%를 6시간 간격으로 덜어낸다. 물탈 수 있으면 물타고, 더 못 물면 그때부터 조금씩
  덜어내는 순서 — 물타기와 부분 매도가 같은 구간에서 서로 반대로 움직이는 걸 막기 위한 전제 조건이다.

- **Q5. 적용 대상 → 코드는 모의·실거래 공용(모드 플래그 하나), 기본값 꺼짐.** 실거래 패널에서 켜기
  전 모의로 며칠 관찰 권장.

### 부수적으로 정한 것

- **RSI 과매수 매도는 이 모드에서 수익 구간(`pnl_pct >= 0`)에서만 동작한다.** 기존 모드에서는 손익과
  무관하게 즉시 매도지만, 손실 구간에서 RSI로 팔면 결국 큰 손절이 되어 이 모드의 전제가 깨진다.
  (RSI 매도 자체는 기본 꺼짐이라 켜둔 경우에만 해당)
- **회복형 물타기 횟수는 기존 `dca_count`와 분리해서 센다.** 트리거(-20% vs 트레일링 손절 직후)와
  금액(소액 vs 매수금액 전액)이 다른 별개 동작이라, 겸용하면 모드를 껐다 켤 때 서로의 남은 횟수를
  잡아먹는다.
- **0 ~ +5% 구간과 손실 구간에는 트레일링 손절이 없다.** 이 모드를 켠 이상 의도된 노출이다 —
  +4.9%까지 올랐다가 다시 빠져도 팔지 않고, 새로 물타거나 반등을 기다린다.

---

## 6. 운영 메모

- 켜는 순서: 대시보드 "매매 기준 설정" → **회복형 분할 물타기** 그룹의 `사용` 체크 → 저장.
  실행 중인 `python main.py live_trade` 프로세스가 재시작 없이 다음 사이클부터 적용한다.
- 끄면 즉시 기존 로직(트레일링 손절 + 항상-먼저-물타기)으로 돌아간다. 회복형으로 쌓은
  `recovery_dca_count`/`total_invested_krw`는 남아 있으므로 다시 켜도 상한이 이어진다.
- 매매 이력(`/auto-trade/logs`)에서 사유 문자열로 구분된다: `recovery_dca`,
  `recovery_take_profit`, `recovery_partial_stop`, `recovery_partial_stop_all`,
  `recovery_time_stop`, 대기 상태는 `recovery_dca_pending` / `recovery_dca_cooldown` /
  `recovery_capped` / `recovery_partial_cooldown`.
