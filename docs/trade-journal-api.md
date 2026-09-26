# 매매일지 연동 API (`/api/journal/fills`)

같은 서버에서 도는 매매일지(stock-history, `/stock/`)가 코인 거래마다 **왜 샀고 왜 팔았나**를 붙여 보여줄 수
있게, 종목 하나의 체결 기록을 돌려준다. 업비트 거래내역에는 "무엇을 얼마에 몇 개"만 있어서, 앱이
`trade_order_log`에 남긴 판단 근거를 여기서 꺼내 간다.

## 요청

```
GET http://127.0.0.1:5000/api/journal/fills?symbol=EGLD&mode=live&from=2026-09-20&to=2026-09-24
```

| 파라미터 | 설명 |
|---|---|
| `symbol` | 필수. `EGLD` 또는 `KRW-EGLD` |
| `mode` | `live`(기본) / `paper` |
| `from`, `to` | `YYYY-MM-DD`, 체결일 기준, 양끝 포함. 생략하면 전체 |

**인증**: 세션 로그인 대신 "같은 서버에서 5000 포트로 직접 부른 요청"만 받는다. nginx(`/upbit/`)를 거친
요청은 403이다. `.env`에 `JOURNAL_API_TOKEN`을 넣으면 `X-Journal-Token` 헤더도 같아야 한다.

## 응답

```json
{"status": "success", "symbol": "EGLD", "mode": "live", "fills": [
  {"id": 101, "at": "2026-09-20 10:00:00", "side": "매수", "decision": "BUY",
   "price": 7045, "qty": 4.25, "amount_krw": 30000,
   "reason": "breakout_4h+정밀조건충족", "reason_label": "4시간봉 돌파 · 정밀조건",
   "regime": {"key": "neutral", "label": "애매"}},
  {"id": 188, "at": "2026-09-24 09:06:00", "side": "매도", "decision": "SELL",
   "price": 7505, "qty": 9.45, "amount_krw": 70945,
   "reason": "tight_trail_exit(고점 22.00% → 현재 17.40%, …)", "reason_label": "고점 대비 하락 매도",
   "regime": {"key": "good", "label": "좋음"},
   "pnl_krw": 9000, "pnl_pct": 17.4, "peak_pnl_pct": 22.0, "trough_pnl_pct": -4.5,
   "entry_regime": {"key": "neutral", "label": "애매"}}
]}
```

- `decision`: `BUY`(새 진입) / `DCA_BUY`(물타기) / `SELL`. HOLD/SKIP은 빠진다.
- `regime`: 그 시각의 확정 시장 판단. 판단 봇(market-regime-bot)을 켜기 전 체결은 `null`.
- 매도 행에만: `pnl_krw`/`pnl_pct`(앱 계산, **수수료 미반영**), `peak_pnl_pct`/`trough_pnl_pct`(이 포지션을 연
  매수부터 이 매도까지 매 사이클 평가손익 중 최고/최저), `entry_regime`(포지션을 열 때의 시장 판단).
  앱 기록 이전부터 들고 있던 포지션의 매도는 최고/최저·진입 국면이 `null`이다.
- 일지 쪽 행과의 짝 맞추기(시각·수량)는 일지가 한다. 업비트 체결 시각과 앱 기록 시각은 수십 초~1분 정도
  차이 날 수 있다.

코드: `app/core/trade_journal.py`, 테스트: `python tests/test_trade_journal.py`

# 보유 현황 API (`/api/journal/holdings`)

매매일지 코인 화면 위쪽 "🤖 자동매매 보유 현황" 패널용. 지금 봇이 들고 있는 코인마다 손익과 "왜 샀고 어떤
규칙으로 팔 건지"를 돌려준다. 실계좌를 **조회만** 한다(주문 없음). 인증은 위 `/api/journal/fills`와 같다.

```
GET http://127.0.0.1:5000/api/journal/holdings
```

범위는 대시보드 🔴 실거래 표와 같다 — 실거래 승인했거나 봇이 추적 중인 종목만. 봇과 무관하게 원래 갖고 있던
코인은 빠진다. 평가금액 큰 순.

```json
{"status": "success", "cash_krw": 123456,
 "current_preset": {"key": "neutral", "label": "애매", "emoji": "🟡"},
 "holdings": [
  {"ticker": "KRW-EGLD", "symbol": "EGLD", "qty": 6, "avg_buy_price": 6666.67, "current_price": 7000,
   "cost_krw": 40000, "eval_krw": 42000, "pnl_krw": 2000, "pnl_pct": 5.0,
   "entry_at": "2026-09-22 09:00:00", "entry_reason": "near_ma200+above_cloud+정밀조건충족",
   "entry_reason_label": "200선 근접+구름 위 · 정밀조건", "entry_regime": {"key": "good", "label": "좋음"},
   "dca_count": 1, "peak_pnl_pct": 5.5, "trough_pnl_pct": -2.0,
   "exit_rule": {"preset": {"key": "good", "label": "좋음", "emoji": "🟢"},
                 "text": "손절 -8%, +5% 넘으면 고점 대비 -10%까지 보유, 익절 +30%", "locked": true}}
 ]}
```

- `pnl_*`: 앱 계산, **수수료 미반영**. 시세 조회에 실패하면 `current_price`/`eval_krw`/`pnl_*`가 `null`.
- `entry_*`: 지금 포지션을 연 첫 매수. 앱 기록 이전부터 들고 있던 종목은 사유가 `null`(진입 시각은 추적 행 값).
- `peak_pnl_pct`/`trough_pnl_pct`: 진입 이후 매 사이클 평가손익 중 최고/최저.
- `exit_rule`: 이 종목에 실제로 적용되는 청산 규칙. `locked: true`면 전략 묶음을 바꾸기 전에 산 종목이라 산 시점
  규칙을 계속 쓴다. `preset`이 `null`이면 어느 묶음과도 다른 직접 설정.
- `current_preset`: 지금 전체 설정이 어느 묶음인지(새로 사는 종목에 적용).

코드: `app/core/trade_journal.py`(`build_journal_holdings`, `load_live_holdings`)
