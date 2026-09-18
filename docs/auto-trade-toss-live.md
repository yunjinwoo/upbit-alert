# 토스증권 실거래 자동매매 — 화면(DB)에서 전부 제어

- 상태: ✅ 운영 중 (2026-08-22 추가 PR #54 → 2026-08-24 리뷰 지적 실거래 위험 버그 수정)
- 이전 단계: [auto-trade-toss-paper.md](auto-trade-toss-paper.md) (2단계 — 토스증권 모의매매)
- 대응하는 업비트 문서: [auto-trade-upbit-live.md](auto-trade-upbit-live.md)

## 개요

모의매매(`TossBroker`)와 완전히 독립된 실거래 경로. `TossLiveBroker`
(app/core/brokers/toss_live_broker.py)가 진짜 토스증권 계좌로 시장가 매수/매도를 낸다.
매매 대상 종목 선택과 실행 on/off는 `.env`가 아니라 대시보드(`/toss-trade`)의 "🔴 실거래"
패널에서 전부 제어한다 — DB에 저장되므로 서버 재시작 후에도 유지된다.

업비트 실거래와 같은 구조를 의도적으로 그대로 따랐다. 브로커 비의존 순수 함수
(`app/core/trade_strategy.py`, `app/core/entry_conditions.py`)와 사이클 오케스트레이션
(`run_trade_cycle`)은 공유하고, 거래소별 차이만 브로커 구현체에 가둔다.

## 안전장치

업비트 실거래와 동일한 2단계 + 이중 확인 구조다.

1. **실거래 승인 화이트리스트(opt-in 필수)** — `trade_candidate_approval`(broker='toss',
   mode='live')에 체크된 종목이 하나도 없으면, 실행 스위치가 켜져 있어도 아무것도 매수하지
   않는다(모의매매는 반대로 "아무것도 안 켜면 전체 후보 대상").
2. **매매 대상(관심등록) 이중 확인** — 승인(2단계)이 켜져 있어도 `watchlist`(1단계)에 없으면
   신규 진입 대상에서 제외한다(`run_trade_cycle`의 방어적 재확인).
3. **실거래 실행 스위치(기본 꺼짐)** — `trade_engine_settings`(broker='toss', mode='live').
   `TossLiveBroker._blocked_reason()`이 매 주문 직전에 이 값을 다시 읽으므로, 스위치가 꺼져
   있으면 `toss-live-trade-bot` 프로세스가 떠 있어도 매수/매도 둘 다 API 호출 전에 차단된다.
4. **최소 1주 검증** — 국내 주식은 소수점 매수가 안 된다. "1종목당 매수금액 ÷ 현재가"로 정수 주
   수를 구하고 0주면 주문을 아예 내지 않는다(`MIN_ORDER_SHARES`).
5. **보유 범위 격리** — 계좌에 이 봇과 무관한 보유 종목이 있어도, 승인했거나 이미 이 봇이 사서
   추적 중인 종목만 손절/익절 대상이 된다(`_reconcile_live_positions`).
6. **원화(KRW) 종목만** — `get_holdings()`에서 `currency == 'KRW'`만 다룬다. 계좌가 해외주식을
   같이 보유해도(예: 미국 ETF) 이 봇의 관리 대상도, 대시보드 표시 대상도 아니다.
7. **동시성 락** — "지금 즉시 실행"/"강제매수"를 연타해도 같은 종목을 이중 매수하지 않도록
   사이클 락을 잡고, 중복 요청엔 409로 응답한다.

## 업비트와 다른 점 (이 브로커에서만 신경 쓸 부분)

| 항목 | 업비트 | 토스증권 |
|---|---|---|
| 인증 | access/secret 키 서명 | OAuth2 client_credentials (`app/core/toss_client.py`) |
| 계좌 컨텍스트 | 불필요 | `X-Tossinvest-Account` 헤더에 `accountSeq` — `GET /api/v1/accounts`로 최초 1회 조회 후 캐시 |
| 주문 단위 | 소수점 가능 | 정수 주(株)만 — 1주도 못 사면 주문 없음 |
| 체결 | 시장가 = 사실상 즉시 전량 체결 | 호가창 물량에 따라 부분체결/미체결 가능 → `GET /api/v1/orders/{orderId}` 폴링 |
| 현금 | 잔고 API의 KRW | `buying-power`(cashBuyingPower) — 별도 예수금 엔드포인트가 없음 |
| 고액 주문 | 해당 없음 | 1억원 이상이면 `confirmHighValueOrder=true` 필수(`HIGH_VALUE_ORDER_KRW`) — 매수·매도 양쪽 |

### 체결 확인(폴링)이 필요한 이유

`_wait_for_fill()`은 주문 상태가 종료 상태(FILLED/CANCELED/REJECTED 등)가 될 때까지 최대 8초
폴링한다. 시간 내에 못 끝나면 **실패로 취급하지 않는다** — 주문 자체는 이미 나갔기 때문에,
체결이 확인된 분량만 반영하고 나머지는 다음 사이클의 실제 잔고 재조회(reconcile)로 정정된다.

미체결 상태에서는 감사로그에 "요청한 수량"을 확정 체결처럼 남기지 않는다(`amount_krw`만 기록).
업비트 브로커와 동일한 관례이며, PR #54 리뷰에서 수정된 항목이다.

## 화면 사용법 (`/toss-trade`)

1. "🎯 매매 대상" 표에서 관심 등록(1단계)을 켠다.
2. "🔴 실거래" 표에서 같은 종목의 **실거래 승인**(2단계)을 켠다 — 모의매매 승인 체크박스와는
   완전히 별도로 저장된다.
3. **실거래 실행** 스위치를 켠다(확인 팝업이 뜬다).
4. 운영 서버에는 `toss-live-trade-bot`(PM2, `python main.py toss_live_trade`)이 배포마다 자동으로
   뜬다(.github/workflows/deploy.yml).
5. "⚡ 실거래 강제매수"로 스위치를 안 켜고 1건만 테스트할 수는 없다 — 실행 스위치는 모든
   실주문의 공통 관문이라 꺼져 있으면 강제매수도 차단된다.
6. "▶ 지금 즉시 실행"으로 다음 사이클을 안 기다리고 판단(청산→진입) 1회를 즉시 돌릴 수 있다.
7. 매매 기준(포지션당 매수금액/최대 동시보유/손절·익절 %/루프 주기)은 모의매매와 그대로
   공유한다 — 실거래 전용 기준은 따로 없다.
8. 매매 이력은 `/toss-trade/logs` 페이지에서 "구분" 드롭다운을 🔴 실거래로 바꿔 조회한다
   (모의매매와 DB상 완전히 분리 — mode='live').

## 프로세스 실행

```powershell
python main.py toss_live_trade
```

- `main.py`의 `all`(기본) 모드에도, 업비트 실거래(`live_trade`)에도 포함되지 않는 별도 프로세스다.
- `TOSS_CLIENT_ID`/`TOSS_CLIENT_SECRET`이 없거나 계좌 조회에 실패하면 시작 시 에러 로그를 남기고
  즉시 종료한다.
- 루프 주기는 모의매매와 같은 `trade_strategy_settings.loop_interval_sec`을 공유한다.

## `.env`

```env
TOSS_CLIENT_ID=발급받은_client_id
TOSS_CLIENT_SECRET=발급받은_client_secret
```

WTS 콘솔(developers.tossinvest.com)에서 발급하며, **실행 서버의 아웃바운드 IP를 허용 목록에
등록**해둬야 한다(미등록 시 403). 매매 대상/실행 여부는 전부 화면(DB)에서 관리하므로 `.env`에
넣을 값은 이 둘뿐이다.

## 알려진 한계 / 계속 지켜볼 것

- **계좌가 여러 개면 첫 번째를 쓴다** — 위탁/연금 계좌를 같이 쓰는 경우 의도한 계좌가 아닐 수
  있다. 지금은 단일 계좌 사용을 가정한 구현이다.
- **부분체결 정정은 다음 사이클에 일어난다** — 체결 확인이 8초를 넘긴 주문은 그 사이클 동안
  추적 수량이 실제와 다를 수 있다.
- **수수료/세금은 어디에도 반영돼 있지 않다** — 손익 표시는 전부 체결가 기준이다.
- 장 운영 시간 가드는 브로커가 아니라 시세 조회 실패로 자연히 걸린다 — 장외 시간엔 판단이
  SKIP으로만 기록된다.

## 공식 API 참고

- [토스증권 Open API 문서](https://developers.tossinvest.com/docs)
