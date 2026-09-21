"""백테스트 시뮬레이션 루프 — 판단은 실거래와 같은 코드로, 체결만 과거 캔들로.

매매 판단은 app/core/trade_strategy.py의 evaluate_entries()/evaluate_exits()를 그대로 부른다.
이 함수들이 시세를 get_price_fn 콜백으로 받는 순수 함수라서 가능한 일이고, 덕분에 "백테스트용으로
따로 베낀 로직"이 원본과 어긋나는 흔한 사고가 애초에 생기지 않는다. 목표 익절·RSI 매도·트레일링
손절·되돌림 익절·회복형 분할 물타기가 전부 실거래에서 도는 그 코드다.

체결 기록은 trade_order_log와 같은 모양의 dict로 남긴다 — 그래야 성과 집계도
app/core/trade_performance.py를 그대로 재사용할 수 있다(승률/손익비/MDD/청산사유별 기여가
매매 성과 화면과 같은 정의로 나온다).

미래를 당겨쓰지 않기 위한 규칙 두 가지:
  - 판단은 시각 T에 "닫힌" 봉까지만 본다(순위·시세·RSI 모두).
  - 체결가는 그 다음 봉의 시가다. T의 종가를 보고 T의 종가에 사는 건 현실에 없는 체결이다.

수수료는 현금 원장에서 바로 뺀다(업비트 0.05% 매수/매도). 모의매매 브로커는 수수료를 안 떼고
성과 화면이 나중에 추정치로 빼지만, 백테스트는 자산 곡선 자체가 결과물이라 원장에 반영하는 쪽이
맞다 — 리포트의 실현손익(net)과 자산 곡선이 같은 기준이 된다.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from app.backtest.candle_store import KST
from app.backtest.market_data import MarketData, SELECT_GAINERS
from app.core.trade_strategy import evaluate_entries, evaluate_exits, TradeDecision

# 진입 근거 라벨 — 성과 집계가 이 문자열로 신호를 묶으므로 종목/순위별로 달라지면 안 된다
# (trade_performance.normalize_entry_signal은 reason 전체를 신호 이름으로 쓴다).
ENTRY_REASONS = {
    'gainers': '당일상승률상위',
    'trade_value': '당일거래대금상위',
}

EXIT_MODE_STRATEGY = 'strategy'   # 현재 자동매매의 청산 로직 그대로
EXIT_MODE_HOLD = 'hold'           # 기준선 — 정해진 시간만 들고 있다가 무조건 청산

TS_FORMAT = '%Y-%m-%d %H:%M:%S'


@dataclass
class BacktestParams:
    """한 번의 백테스트를 규정하는 값들. CLI 인자와 1:1로 맞춰 둔다."""
    selection: str = SELECT_GAINERS
    top_n: int = 10
    exit_mode: str = EXIT_MODE_STRATEGY
    hold_hours: float = 24.0          # exit_mode='hold'일 때만 의미 있음
    initial_cash: float = 1_000_000
    entry_hour: Optional[int] = None  # KST 기준 이 시각에만 신규 진입(None이면 매 봉마다)
    min_trade_value_krw: float = 0.0  # 순위 후보 최소 당일 거래대금(유동성 필터)
    buy_fee_rate: float = 0.0005
    sell_fee_rate: float = 0.0005
    rsi_period: int = 14


@dataclass
class BacktestResult:
    params: BacktestParams
    orders: List[dict] = field(default_factory=list)       # trade_order_log와 같은 모양
    equity_curve: List[dict] = field(default_factory=list)
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    candle_count: int = 0
    market_count: int = 0


def _ts_str(ts: int) -> str:
    return datetime.fromtimestamp(ts, KST).strftime(TS_FORMAT)


def _ts_naive(ts: int) -> datetime:
    """KST 기준 naive datetime — 저장소가 시각을 전부 이 형태로 다루므로 맞춰 준다."""
    return datetime.fromtimestamp(ts, KST).replace(tzinfo=None)


def _new_position(ticker: str, qty: float, price: float, amount: float, ts: int) -> dict:
    """trade_strategy가 읽는 필드를 전부 갖춘 포지션 — paper_positions 행과 같은 모양."""
    return {
        'ticker': ticker, 'qty': qty, 'avg_buy_price': price,
        'peak_price': price, 'below_stop_streak': 0,
        'dca_count': 0, 'recovery_dca_count': 0,
        'total_invested_krw': amount, 'first_entry_price': price,
        'entry_at': _ts_str(ts), 'last_dca_at': None, 'last_partial_stop_at': None,
    }


def _rsi_from_closes(closes: List[float], period: int) -> Optional[float]:
    """확정 종가 리스트로 RSI를 계산한다.

    계산은 app/core/exit_conditions.py의 compute_rsi()를 그대로 쓴다(실매매와 같은 공식이어야
    결과가 비교 가능하다). 그 함수는 "마지막 행은 진행 중인 봉"으로 보고 끝에서 두 번째를 기준
    삼으므로, 마지막 확정 봉을 한 번 더 붙여 넘긴다 — 그 행의 종가는 계산에 쓰이지 않는다.

    pandas 임포트를 이 함수 안에서 하는 이유: RSI 매도조건을 끈 백테스트는 pandas 없이도 돌아야
    하고(개발 세션에는 설치돼 있지 않다), 그래야 목 데이터 테스트가 네트워크·무거운 의존 없이 돈다.
    """
    if len(closes) < period * 2 + 2:
        return None
    import pandas as pd
    from app.core.exit_conditions import compute_rsi
    padded = list(closes) + [closes[-1]]
    return compute_rsi(pd.DataFrame({'close': padded}), period=period)


def _hold_exits(positions: List[dict], price_fn, hold_hours: float, now: datetime) -> List[TradeDecision]:
    """기준선용 청산 — 보유 시간이 차면 무조건 전량 매도.

    현재 청산 로직이 보탬이 되는지 보려면 "아무 규칙 없이 그냥 들고 있었으면" 어땠는지와 견줘야
    한다. 종목 선정이 좋아서 번 건지, 청산이 좋아서 번 건지 이게 없으면 구분되지 않는다.
    """
    decisions = []
    for pos in positions:
        price = price_fn(pos['ticker'])
        if not price:
            decisions.append(TradeDecision(pos['ticker'], 'SKIP', reason='시세 없음'))
            continue
        qty, avg = pos['qty'], pos['avg_buy_price']
        pnl_krw = (price - avg) * qty
        pnl_pct = (price - avg) / avg * 100 if avg else 0.0
        held_hours = (now - datetime.strptime(pos['entry_at'], TS_FORMAT)).total_seconds() / 3600
        if held_hours >= hold_hours:
            decisions.append(TradeDecision(
                pos['ticker'], 'SELL', reason=f'hold_period({held_hours:.0f}시간 경과)',
                price=price, qty=qty, pnl_krw=pnl_krw, pnl_pct=pnl_pct,
            ))
        else:
            decisions.append(TradeDecision(
                pos['ticker'], 'HOLD', reason=f'보유 {held_hours:.0f}/{hold_hours:.0f}시간',
                price=price, qty=qty, pnl_krw=pnl_krw, pnl_pct=pnl_pct,
            ))
    return decisions


def run_backtest(md: MarketData, cfg, params: BacktestParams) -> BacktestResult:
    """캔들을 시간순으로 훑으며 매 봉마다 청산 → 진입을 판단하고 가상 체결한다.

    cfg는 auto_trader._effective_strategy_config()가 만드는 것과 같은 네임스페이스(TRADE_* 속성)다 —
    대시보드에 저장된 실제 설정을 그대로 넘기면 "지금 설정으로 과거를 돌리면" 이 된다.
    """
    cash = float(params.initial_cash)
    positions: Dict[str, dict] = {}
    result = BacktestResult(params=params, market_count=len(md.markets))
    timestamps = md.timestamps
    result.candle_count = len(timestamps)
    if timestamps:
        result.start_ts, result.end_ts = timestamps[0], timestamps[-1]

    rsi_enabled = bool(getattr(cfg, 'TRADE_RSI_EXIT_ENABLED', False))
    entry_reason = ENTRY_REASONS.get(params.selection, params.selection)

    for idx, ts in enumerate(timestamps):
        now = _ts_naive(ts)
        is_last = idx == len(timestamps) - 1

        def price_fn(ticker, _ts=ts):
            return md.price(ticker, _ts)

        # ---- 청산 판단 -------------------------------------------------------
        held = list(positions.values())
        if params.exit_mode == EXIT_MODE_HOLD:
            exit_decisions = _hold_exits(held, price_fn, params.hold_hours, now)
        else:
            rsi_map = {}
            if rsi_enabled:
                for pos in held:
                    closes = md.closes_until(pos['ticker'], ts, params.rsi_period * 4)
                    rsi_map[pos['ticker']] = _rsi_from_closes(closes, params.rsi_period)
            exit_decisions = evaluate_exits(held, price_fn, cfg, rsi_map=rsi_map, now=now)

        # 마지막 봉에서는 남은 포지션을 전부 정리한다 — 안 그러면 미청산 포지션의 평가손익이
        # 성과에 안 잡혀 "청산된 것만 집계한 승률"이 실제보다 좋게 보인다.
        if is_last:
            exit_decisions = [_force_close(pos, price_fn) for pos in held]
            exit_decisions = [d for d in exit_decisions if d]

        sold_this_cycle = False
        for decision in exit_decisions:
            cash, sold = _apply_exit(decision, positions, md, ts, cash, params, result)
            sold_this_cycle = sold_this_cycle or sold

        # ---- 진입 판단 -------------------------------------------------------
        # 매도가 나간 사이클에는 신규 진입을 건너뛴다 — 실거래 루프(auto_trader.run_trade_cycle)가
        # 회수한 현금을 같은 사이클에 곧바로 재투입하지 않는 것과 같은 규칙이다.
        if is_last or sold_this_cycle or not _entry_allowed(ts, params):
            _record_equity(result, ts, cash, positions, md)
            continue

        ranked = md.rank(ts, params.selection, params.top_n, params.min_trade_value_krw)
        candidates = [{'ticker': r['ticker'], 'entry_reason': entry_reason} for r in ranked]
        entry_decisions = evaluate_entries(candidates, list(positions.values()), cash, price_fn, cfg)
        for decision in entry_decisions:
            cash = _apply_entry(decision, positions, md, ts, cash, params, result)

        _record_equity(result, ts, cash, positions, md)

    return result


def _force_close(pos: dict, price_fn) -> Optional[TradeDecision]:
    """구간 마지막 봉 — 남은 포지션을 종가로 정리하는 판단(집계에서 빠지지 않게)."""
    price = price_fn(pos['ticker'])
    if not price:
        return None
    qty, avg = pos['qty'], pos['avg_buy_price']
    return TradeDecision(
        pos['ticker'], 'SELL', reason='backtest_end(구간 종료 정리)',
        price=price, qty=qty, pnl_krw=(price - avg) * qty,
        pnl_pct=(price - avg) / avg * 100 if avg else 0.0,
    )


def _entry_allowed(ts: int, params: BacktestParams) -> bool:
    if params.entry_hour is None:
        return True
    return datetime.fromtimestamp(ts, KST).hour == params.entry_hour


def _order_row(ts: int, decision_name: str, ticker: str, reason: str, price: float,
               qty: float, amount: float, cash_after: float,
               pnl_krw: float = None, pnl_pct: float = None) -> dict:
    """trade_order_log 행과 같은 모양 — trade_performance.build_performance()의 입력이 된다."""
    return {
        'ticker': ticker, 'decision': decision_name, 'reason': reason,
        'price': price, 'qty': qty, 'amount_krw': amount,
        'cash_balance_after': cash_after, 'pnl_krw': pnl_krw, 'pnl_pct': pnl_pct,
        'created_at': _ts_str(ts),
    }


def _apply_exit(decision: TradeDecision, positions: Dict[str, dict], md: MarketData,
                ts: int, cash: float, params: BacktestParams, result: BacktestResult):
    """매도/물타기/보유 판단을 원장에 반영한다. (새 현금, 이번에 팔았는지)를 돌려준다."""
    pos = positions.get(decision.ticker)
    if pos is None:
        return cash, False

    if decision.action == 'HOLD':
        # 다음 봉의 트레일링 손절 판단이 쓰는 추적값을 그대로 이어 받는다(실매매의 DB 갱신과 같은 역할)
        if decision.peak_price is not None:
            pos['peak_price'] = decision.peak_price
            pos['below_stop_streak'] = decision.streak or 0
        return cash, False

    fill = md.fill_price(decision.ticker, ts)
    if not fill:
        return cash, False

    if decision.action == 'DCA_BUY':
        amount = decision.amount_krw or 0
        fee = amount * params.buy_fee_rate
        if cash < amount + fee:
            return cash, False
        add_qty = amount / fill
        total_qty = pos['qty'] + add_qty
        pos['avg_buy_price'] = (pos['qty'] * pos['avg_buy_price'] + amount) / total_qty
        pos['qty'] = total_qty
        pos['total_invested_krw'] = (pos.get('total_invested_krw') or 0) + amount
        # 실매매의 mark_position_dca_used()/mark_recovery_dca_used()와 같은 리셋 —
        # 새 평단 시점을 기준으로 트레일링을 다시 시작한다
        pos['peak_price'] = fill
        pos['below_stop_streak'] = 0
        if decision.recovery:
            pos['recovery_dca_count'] = (pos.get('recovery_dca_count') or 0) + 1
            pos['last_dca_at'] = _ts_str(ts)
        else:
            pos['dca_count'] = (pos.get('dca_count') or 0) + 1
        cash -= amount + fee
        result.orders.append(_order_row(
            ts, 'DCA_BUY', decision.ticker, decision.reason, fill, add_qty, amount, cash,
            decision.pnl_krw, decision.pnl_pct,
        ))
        return cash, False

    if decision.action == 'SELL':
        qty = min(decision.qty or pos['qty'], pos['qty'])
        amount = qty * fill
        fee = amount * params.sell_fee_rate
        avg = pos['avg_buy_price']
        # 판단 시점 시세로 계산된 손익 대신, 실제 체결가(다음 봉 시가) 기준으로 다시 계산한다
        pnl_krw = (fill - avg) * qty
        pnl_pct = (fill - avg) / avg * 100 if avg else 0.0
        cash += amount - fee
        remaining = pos['qty'] - qty
        if remaining <= 1e-12:
            positions.pop(decision.ticker, None)
        else:
            pos['qty'] = remaining
            if decision.partial_sell:
                pos['last_partial_stop_at'] = _ts_str(ts)
        result.orders.append(_order_row(
            ts, 'SELL', decision.ticker, decision.reason, fill, qty, amount, cash, pnl_krw, pnl_pct,
        ))
        return cash, True

    return cash, False


def _apply_entry(decision: TradeDecision, positions: Dict[str, dict], md: MarketData,
                 ts: int, cash: float, params: BacktestParams, result: BacktestResult) -> float:
    if decision.action != 'BUY':
        return cash
    fill = md.fill_price(decision.ticker, ts)
    if not fill:
        return cash
    amount = decision.amount_krw or 0
    fee = amount * params.buy_fee_rate
    if cash < amount + fee or amount <= 0:
        return cash
    qty = amount / fill
    positions[decision.ticker] = _new_position(decision.ticker, qty, fill, amount, ts)
    cash -= amount + fee
    result.orders.append(_order_row(
        ts, 'BUY', decision.ticker, decision.reason, fill, qty, amount, cash,
    ))
    return cash


def _record_equity(result: BacktestResult, ts: int, cash: float,
                   positions: Dict[str, dict], md: MarketData) -> None:
    """자산 곡선 한 점 — 현금 + 보유 평가액. 최대낙폭을 "사이클 손익"이 아니라 "계좌 잔고" 기준으로
    보기 위해 따로 쌓는다(성과 화면의 MDD는 청산된 사이클 누적손익 기준이라 성격이 다르다)."""
    holdings = 0.0
    for pos in positions.values():
        price = md.price(pos['ticker'], ts) or pos['avg_buy_price']
        holdings += pos['qty'] * price
    result.equity_curve.append({
        'ts': ts, 'at': _ts_str(ts), 'cash': cash,
        'holdings': holdings, 'equity': cash + holdings,
        'open_positions': len(positions),
    })
