"""자동매매 진입/청산 판단 로직 — 순수 함수만 둔다 (DB/네트워크 직접 접근 없음, 테스트 용이성 목적).

시세 조회는 get_price_fn(ticker) -> float|None 콜백으로 주입받는다(브로커 구현체에 의존하지 않기 위함).
1단계는 업비트 모의매매 전용이라 규칙이 단순하다:
  - 진입: coin_screening_daily에서 걸러진 후보 중 미보유 종목을 고정 금액으로 매수
  - 청산: 보유 포지션의 손익률이 손절/익절 기준에 닿으면 전량 매도
"""
from dataclasses import dataclass
from typing import Callable, List, Optional


@dataclass
class TradeDecision:
    """매매 판단 1건 (BUY/SELL/HOLD/SKIP 전부 감사로그로 남기기 위해 판단 자체를 값으로 표현)."""
    ticker: str
    action: str  # 'BUY' / 'SELL' / 'HOLD' / 'SKIP'
    reason: str
    price: Optional[float] = None
    qty: Optional[float] = None
    amount_krw: Optional[float] = None
    pnl_krw: Optional[float] = None
    pnl_pct: Optional[float] = None


def evaluate_exits(positions: List[dict], get_price_fn: Callable[[str], Optional[float]], cfg) -> List[TradeDecision]:
    """보유 포지션마다 손절/익절 여부를 판단한다."""
    decisions = []
    for pos in positions:
        ticker = pos['ticker']
        qty = pos['qty']
        avg_price = pos['avg_buy_price']

        price = get_price_fn(ticker)
        if not price:
            decisions.append(TradeDecision(ticker, 'SKIP', reason='시세 조회 실패'))
            continue

        pnl_krw = (price - avg_price) * qty
        pnl_pct = (price - avg_price) / avg_price * 100 if avg_price else 0.0

        if pnl_pct <= -cfg.TRADE_STOP_LOSS_PCT:
            decisions.append(TradeDecision(
                ticker, 'SELL', reason=f'stop_loss({pnl_pct:.2f}%)',
                price=price, qty=qty, pnl_krw=pnl_krw, pnl_pct=pnl_pct,
            ))
        elif pnl_pct >= cfg.TRADE_TAKE_PROFIT_PCT:
            decisions.append(TradeDecision(
                ticker, 'SELL', reason=f'take_profit({pnl_pct:.2f}%)',
                price=price, qty=qty, pnl_krw=pnl_krw, pnl_pct=pnl_pct,
            ))
        else:
            decisions.append(TradeDecision(
                ticker, 'HOLD', reason=f'pnl {pnl_pct:.2f}%',
                price=price, qty=qty, pnl_krw=pnl_krw, pnl_pct=pnl_pct,
            ))
    return decisions


def evaluate_entries(candidates: List[dict], positions: List[dict], cash_balance: float,
                      get_price_fn: Callable[[str], Optional[float]], cfg) -> List[TradeDecision]:
    """진입 후보(coin_screening_daily 필터 결과) 중 신규 매수 대상을 판단한다.
    한 사이클 안에서 여러 종목을 연속 매수할 수 있으므로, 판단 도중 보유 종목 수/가상 현금을
    누적 반영해가며 계산한다(실제 체결은 auto_trader.py가 순차 실행)."""
    decisions = []
    held_tickers = {p['ticker'] for p in positions}
    open_count = len(positions)

    for cand in candidates:
        ticker = cand['ticker']

        if ticker in held_tickers:
            decisions.append(TradeDecision(ticker, 'SKIP', reason='이미 보유 중'))
            continue
        if open_count >= cfg.TRADE_MAX_CONCURRENT_POSITIONS:
            decisions.append(TradeDecision(ticker, 'SKIP', reason='최대 동시보유 종목 수 도달'))
            continue
        if cash_balance < cfg.TRADE_MAX_POSITION_KRW:
            decisions.append(TradeDecision(ticker, 'SKIP', reason='가상 현금 부족'))
            continue

        price = get_price_fn(ticker)
        if not price:
            decisions.append(TradeDecision(ticker, 'SKIP', reason='시세 조회 실패'))
            continue

        reason = 'breakout_4h' if cand.get('breakout_4h') else 'near_ma200+above_cloud'
        decisions.append(TradeDecision(
            ticker, 'BUY', reason=reason, price=price, amount_krw=cfg.TRADE_MAX_POSITION_KRW,
        ))

        # 다음 후보 판단에 이번 매수가 반영되도록 누적 갱신 (실제 체결 전 근사치)
        cash_balance -= cfg.TRADE_MAX_POSITION_KRW
        open_count += 1
        held_tickers.add(ticker)

    return decisions
