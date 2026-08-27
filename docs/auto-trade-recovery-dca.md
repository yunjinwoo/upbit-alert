# 회복형 분할 물타기 (Recovery DCA) — 설계 문서

> 상태: **설계 초안** (구현 전). 이 문서로 방향/파라미터를 확정한 뒤 구현한다.

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
| `first_entry_price` | 최초 진입가. 하드스톱 판정·리포트용. 첫 매수 시 기록, 이후 불변 |
| `total_invested_krw` | 이 포지션에 넣은 매수 누적액(최초 매수 + 모든 물타기). 투입 상한 판정 |
| `recovery_dca_count` | 이 포지션에서 실행한 회복형 물타기 횟수 (기존 `dca_count`와 분리할지 겸용할지는 §5 Q4) |
| `last_dca_at` | 마지막 물타기 시각. 쿨다운 판정 |

`avg_buy_price` / `qty`는 기존대로 브로커가 관리(실거래는 실제 잔고, 모의는 가상 원장).

### 2.2 파라미터 (`trade_strategy_settings` + 대시보드)

| 파라미터 | 기본값(제안) | 의미 |
|---|---|---|
| `recovery_dca_enabled` | `false` | 이 모드 on/off |
| `recovery_dca_trigger_pct` | `20.0` | **평단 대비** 이 % 이상 하락하면 물타기 후보 |
| `recovery_dca_amount_krw` | 매수금액의 50% | 1회 물타기 금액(소액). 절대금액 or `max_position_krw` 대비 비율 |
| `recovery_dca_cooldown_min` | `60` | 직전 물타기(또는 최초 진입)로부터 최소 경과 시간(분). "시간이 조금 지나서" |
| `recovery_take_profit_pct` | `5.0` | **평단 대비** 이 % 이상이면 전량 매도(반등 익절) |
| `recovery_dca_max_count` | `3` | 포지션당 회복형 물타기 최대 횟수 |
| `recovery_max_invested_krw` | 매수금액 × 2.5 | 포지션당 총 투입액 상한. 초과 물타기 금지 |
| `recovery_hard_stop_pct` | `0` (비활성) | >0이면, **최초 진입가 대비** 이 % 하락 시 조건 무시하고 전량 청산 |

### 2.3 판정 순서 (`evaluate_exits`, 이 모드일 때)

매 사이클, 보유 포지션마다:

```
pnl_pct = (price - avg_buy_price) / avg_buy_price * 100

1) 익절:  pnl_pct >= recovery_take_profit_pct   → SELL 전량   (reason: recovery_take_profit)
   (기존 take_profit_pct(10%)도 그대로 병행 — 더 크게 먹을 수 있으면 그쪽으로 먼저 매도)

2) 하드스톱(활성 시):
   (price - first_entry_price) / first_entry_price * 100 <= -recovery_hard_stop_pct
        → SELL 전량   (reason: recovery_hard_stop)

3) 물타기:  아래 전부 만족 시 DCA_BUY (recovery_dca_amount_krw)
      - pnl_pct <= -recovery_dca_trigger_pct
      - recovery_dca_count < recovery_dca_max_count
      - total_invested_krw + 다음_매수액 <= recovery_max_invested_krw
      - now - last_dca_at >= recovery_dca_cooldown_min 분
      - (현금 충분 — 브로커가 최종 검증)
   실행 후: recovery_dca_count += 1, total_invested_krw += 체결액, last_dca_at = now
            → 평단이 내려가므로 다음 -20% 기준·+5% 기준 모두 새 평단으로 자동 이동

4) 그 외:  HOLD
      - 물타기 상한(횟수 or 투입액)에 도달했고 아직 익절가도 하드스톱가도 아니면
        → 계속 HOLD (반등을 기다림). status='recovery_capped'로 대시보드에 표시
```

**트레일링 손절은 이 모드에서 동작하지 않는다** — `peak_price` 추적/`below_stop_streak`는 사용
안 함(컬럼은 유지).

### 2.4 예시 흐름

매수금액 10만원, 파라미터 기본값 가정.

| 시점 | 사건 | 평단 | 보유액 | 총투입 | pnl% |
|---|---|---|---|---|---|
| T0 | 최초 매수 10만원 | 1,000 | 10만 | 10만 | 0% |
| T1 | 가격 800 (-20%), 쿨다운 경과 → 물타기 5만원 | 933 | 15만 | 15만 | -14% |
| T2 | 가격 980 → pnl +5% 도달 → **전량 매도** | — | 0 | — | +5% 실현 |
| T2' | (안 팔렸고 계속 하락) 가격 747 (평단 933 대비 -20%) → 2차 물타기 5만원 | 872 | 20만 | 20만 | ... |
| T3 | 총투입 20만 → 상한(25만) 근접, 3차까지만 가능 |
| T4 | 3차 후 총투입 25만 도달 → 이후엔 HOLD만 (recovery_capped) |
| T5 | 하드스톱 활성(예: -15%)이었다면 진입가 1,000 대비 850 이하에서 전량 청산 |

---

## 3. 기존 로직과의 관계

- **진입(`evaluate_entries`)**: 변화 없음. 신규 매수는 지금처럼 스크리닝 후보 + 승인 화이트리스트로.
  회복형 물타기로 전량 청산된 종목은 다음 사이클에 일반 진입 로직이 다시 후보로 볼 수 있다.
- **강제매수/강제매도(`force_buy`/`force_sell`)**: 변화 없음. 단 `force_buy`로 평단을 낮추면
  `total_invested_krw`도 갱신해야 상한 계산이 맞음 → 구현 시 반영.
- **`_reconcile_live_positions`**: 실거래에서 실제 잔고 → 추적행 동기화. `first_entry_price`/
  `total_invested_krw`가 없는(이전에 산) 종목은 첫 동기화 때 `first_entry_price = avg_buy_price`,
  `total_invested_krw = qty * avg_buy_price`로 채운다(근사).
- **Slack 알림**: `recovery_take_profit` / `recovery_hard_stop` / 회복형 물타기에 맞는 라벨 추가.
- **대시보드 미리보기(`get_dashboard_summary` / `get_live_dashboard_summary`)**: `evaluate_exits`를
  읽기 전용으로 한 번 더 돌리는 기존 구조 그대로 → `next_action`에 회복형 판정이 자동 반영됨.
  `recovery_capped` 상태 뱃지만 프론트에 추가.

---

## 4. 구현 범위 (확정 후)

1. `app/config.py` — `TRADE_RECOVERY_*` 기본값 상수.
2. `app/utils/db_manager.py` —
   - `trade_strategy_settings`에 컬럼 8개 추가 + `ALTER TABLE` 마이그레이션.
   - `paper_positions`에 `first_entry_price`, `total_invested_krw`, `recovery_dca_count`,
     `last_dca_at` 추가 + 마이그레이션.
   - `get_trade_strategy_settings` / `update_trade_strategy_settings`에 신규 필드.
   - 물타기 실행 반영 헬퍼(`mark_recovery_dca_used` 등).
3. `app/core/trade_strategy.py` — `evaluate_exits`에 모드 분기. 가급적 별도 함수
   `_evaluate_exit_recovery(pos, price, cfg)`로 빼서 순수 함수 유지.
4. `app/core/auto_trader.py` — `_effective_strategy_config`에 신규 파라미터, `_execute`에
   회복형 물타기 후 상태 갱신 + Slack 라벨.
5. `app/api/server.py` + 템플릿 — 대시보드 전략 설정 폼에 슬라이더/입력 8개, `recovery_capped`
   뱃지.
6. 테스트 — `evaluate_exits` 회복형 분기 단위 테스트(트리거/쿨다운/상한/하드스톱/익절 경계값).
7. 문서 — 이 파일 확정본 + [auto-trade-upbit-live.md](auto-trade-upbit-live.md)에 모드 언급.

---

## 5. 확정이 필요한 열린 질문

- **Q1. 기준 가격.** `-20%` 트리거와 `+5%` 익절을 **현재 평단**(물탈 때마다 이동) 대비로 볼지,
  아니면 **최초 진입가 고정** 대비로 볼지.
  - 제안: **현재 평단 대비**. 물타면 기준이 같이 내려가서 "다음 물타기는 새 평단에서 또 -20%"가
    되므로 자연스럽게 간격이 벌어지고 무분별한 연속 매수를 막는다. `+5%`도 평단 대비여야
    "물타서 평단 낮추고 → 그 평단 조금 위에서 판다"는 의도와 맞다.

- **Q2. 익절 시 전량 매도 vs 일부 매도.**
  - 제안: **전량 매도**. "5% 나면 매도" 문장 그대로. 일부만 팔면 잔량(bag)이 남아 상태가
    복잡해지고, 청산 후 재진입은 일반 진입 로직에 맡기면 된다.
  - 대안(원하면): 마지막에 넣은 물타기 tranche 수량만 매도(원금 회수형). 상태 추적 복잡도 ↑.

- **Q3. "5% 반등"이 영영 안 올 때.** 물타기 상한(횟수·투입액)에 도달한 뒤:
  - (a) **무기한 HOLD** — 자본이 묶이지만 실현 손실은 없음. `recovery_hard_stop_pct=0` 기본.
  - (b) **하드스톱** — `recovery_hard_stop_pct`를 켜서 최초 진입가 대비 -X%에서 전량 정리.
    "큰 손절 싫다"는 취지와 충돌하지만, 유일한 자본 보호 장치.
  - (c) **캡 도달 후 시간 하드스톱** — 상한 도달 후 N일 지나도 익절 못 하면 정리.
  - 제안: 기본은 (a) + `recovery_max_invested_krw`로 최대 손실 원금만 제한. 하드스톱은
    선택적으로 켤 수 있게 파라미터만 만들어 둠. **운영자 결정 필요.**

- **Q4. 첫 메시지의 "소액 손절 반복"은 살릴지.** 구체화된 설명("+5% 익절 반복")에는 손절이
  없다. 둘 중 하나:
  - (a) 손절 없음 — 반등에서만 나온다(위 설계). 하드스톱이 유일한 손절.
  - (b) 회복형 물타기와 병행해, 트레일링/일정 손실에서 **보유량 일부만**(예: 20%) 잘라내는
    "소액 손절"도 유지. 물타기로 산 걸 조금씩 덜어내며 위험을 줄이는 형태.
  - **운영자 결정 필요.**

- **Q5. 적용 대상.** 모의매매에서 먼저 검증 후 실거래에 붙일지, 실거래에 바로 파라미터로 열지.
  - 제안: 코드는 공용(모드 플래그 하나), 대시보드 기본값 `recovery_dca_enabled=false`.
    실거래 패널에서 켜기 전 모의로 며칠 관찰 권장.
