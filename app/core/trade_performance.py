"""매매 성과 집계 — trade_order_log를 "매매 사이클"로 재구성하고 승률/손익/신호별 기여도를 계산한다.

**읽기 전용 분석 모듈이다.** 매매 판단/체결 로직(auto_trader.py, trade_strategy.py, exit_conditions.py,
브로커)은 이 파일을 임포트하지 않으며, 이 파일도 DB나 네트워크에 직접 접근하지 않는다 —
행(dict) 리스트를 받아 계산만 하는 순수 함수 모음이라 테스트에 DB가 필요 없다
(tests/test_trade_performance.py 참고).

## 왜 "사이클 재구성"이 필요한가
trade_order_log는 한 행이 곧 판단 1건이라, 진입 신호는 BUY 행의 reason에만 있고 실현손익은 SELL 행의
pnl_krw에만 있다. "breakout_4h로 들어간 매매의 승률"을 내려면 같은 종목의 BUY → (DCA_BUY…) → SELL을
하나로 이어붙여야 한다. 그 묶음을 여기선 '사이클(cycle)'이라고 부른다.

## 집계에서 꼭 지켜야 하는 것
- **HOLD 행의 pnl_krw는 평가손익(미실현)이다.** 실현손익은 decision='SELL' 행만 쓴다
  (app/core/trade_strategy.py의 evaluate_exits()가 HOLD에도 pnl을 채워 넣는다).
- 주문 실패는 decision이 'SKIP'으로 기록되므로(auto_trader.py의 _execute) 애초에 체결 행에 안 들어온다.
- 짝이 되는 BUY가 없는 SELL(기록을 남기기 전부터 들고 있던 포지션)은 진입 신호를 알 수 없으므로
  UNKNOWN_SIGNAL 버킷으로 따로 센다 — 임의의 신호에 손익을 떠넘기지 않는다.
"""
from datetime import datetime
from typing import Callable, List, Optional

# 짝이 되는 BUY를 못 찾은 사이클의 진입 신호 라벨
UNKNOWN_SIGNAL = '미확인'
# 대시보드 수동 버튼으로 낸 주문(trade_strategy.py가 아니라 auto_trader.py가 직접 reason을 적는다)
MANUAL_BUY_SIGNAL = '강제매수(수동)'
MANUAL_SELL_REASON = '강제매도(수동)'
# reason 접미사 — 정밀 매수조건(entry_conditions.py) 검사를 통과해서 들어간 진입에만 붙는다
PRECISION_SUFFIX = '+정밀조건충족'

# 체결로 간주하는 decision (HOLD/SKIP은 주문이 없었던 판단이라 제외)
FILL_DECISIONS = ('BUY', 'DCA_BUY', 'SELL')

_TS_FORMAT = '%Y-%m-%d %H:%M:%S'


def _parse_ts(value) -> Optional[datetime]:
    """created_at('YYYY-MM-DD HH:MM:SS') 파싱 — 형식이 깨진 값은 조용히 None(집계에서 제외)."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], _TS_FORMAT)
    except (ValueError, TypeError):
        return None


def _amount_of(row: dict) -> float:
    """체결 금액. 실거래는 체결 확인이 지연되면 amount_krw가 비어 있을 수 있어(브로커의 _wait_for_fill
    타임아웃) price×qty로 보정하고, 둘 다 없으면 0으로 둔다 — 수수료 추정이 과대계상되지 않게."""
    amount = row.get('amount_krw')
    if amount:
        return float(amount)
    price, qty = row.get('price'), row.get('qty')
    if price and qty:
        return float(price) * float(qty)
    return 0.0


def normalize_entry_signal(reason: Optional[str]) -> str:
    """BUY reason을 신호 이름으로 정규화한다.

    trade_strategy.py의 evaluate_entries()는 'breakout_4h', 'near_ma200+above_cloud' 같은 기본 신호에
    정밀조건 검사 대상이면 '+정밀조건충족'을 덧붙인다. 접미사까지 다른 신호로 세면 표본이 쪼개져
    승률이 의미를 잃으므로, 같은 신호로 묶고 "정밀조건" 건수는 별도 컬럼으로 따로 센다
    (is_precision_entry 참고)."""
    if not reason:
        return UNKNOWN_SIGNAL
    text = str(reason).strip()
    if text.startswith('강제매수'):
        return MANUAL_BUY_SIGNAL
    base = text.split(PRECISION_SUFFIX)[0].strip()
    return base or UNKNOWN_SIGNAL


def is_precision_entry(reason: Optional[str]) -> bool:
    """이 진입이 정밀 매수조건까지 통과하고 들어간 건인지."""
    return PRECISION_SUFFIX in (reason or '')


def normalize_exit_reason(reason: Optional[str]) -> str:
    """SELL reason을 청산 사유로 정규화한다. reason에는 수치가 같이 들어있어서
    ('take_profit(12.34%)') 앞부분만 떼어 묶는다."""
    text = (reason or '').strip()
    if text.startswith('take_profit'):
        return 'take_profit'
    if text.startswith('stop_loss'):
        return 'stop_loss'
    if text.startswith('rsi_exit'):
        return 'rsi_exit'
    if text.startswith('강제매도'):
        return MANUAL_SELL_REASON
    return '기타'


def build_trade_cycles(rows: List[dict]) -> List[dict]:
    """체결 행(BUY/DCA_BUY/SELL)을 종목별로 훑어 매매 사이클 리스트를 만든다. 입력은 시간순(id 오름차순)
    정렬돼 있다고 가정한다(db_manager.get_trade_fill_rows가 그렇게 준다).

    - BUY: 열린 사이클이 없으면 새 사이클을 연다. 이미 열려 있으면 추가매수로 합산한다
      (evaluate_entries가 보유 중인 종목은 SKIP하므로 정상 동작에선 안 생기지만, 수동 강제매수로는
      생길 수 있어 사이클을 쪼개지 않고 한 포지션으로 본다).
    - DCA_BUY: 새 사이클을 열지 않고 열린 사이클의 물타기 횟수/금액에 더한다.
    - SELL: 열린 사이클을 닫는다. 현재 매매 로직은 전량매도라 분할청산은 고려하지 않는다.
      열린 사이클이 없으면 진입 신호 UNKNOWN_SIGNAL인 "청산만 있는" 사이클로 남긴다.

    반환된 사이클의 closed=False는 아직 청산되지 않은(보유 중) 포지션이다 — 실현손익 집계에선 빠지고
    화면에는 건수만 보여준다."""
    open_cycles = {}   # ticker -> 열려 있는 사이클
    cycles = []

    for row in rows:
        ticker = row.get('ticker')
        decision = row.get('decision')
        amount = _amount_of(row)

        if decision == 'BUY' and ticker not in open_cycles:
            cycle = {
                'ticker': ticker,
                'entry_at': row.get('created_at'),
                'entry_price': row.get('price'),
                'entry_reason': row.get('reason'),
                'entry_signal': normalize_entry_signal(row.get('reason')),
                'is_precision': is_precision_entry(row.get('reason')),
                'buy_amount_krw': amount,
                'add_count': 0,
                'exit_at': None, 'exit_price': None, 'exit_qty': None,
                'exit_amount_krw': 0.0, 'exit_reason': None, 'exit_kind': None,
                'pnl_krw': None, 'pnl_pct': None, 'holding_hours': None,
                'closed': False,
            }
            open_cycles[ticker] = cycle
            cycles.append(cycle)
            continue

        if decision in ('BUY', 'DCA_BUY'):
            cycle = open_cycles.get(ticker)
            if cycle is None:
                # 진입 기록 없이 물타기만 남은 경우 — 신호를 모르는 사이클로 연다
                cycle = {
                    'ticker': ticker,
                    'entry_at': row.get('created_at'),
                    'entry_price': row.get('price'),
                    'entry_reason': row.get('reason'),
                    'entry_signal': UNKNOWN_SIGNAL,
                    'is_precision': False,
                    'buy_amount_krw': 0.0,
                    'add_count': 0,
                    'exit_at': None, 'exit_price': None, 'exit_qty': None,
                    'exit_amount_krw': 0.0, 'exit_reason': None, 'exit_kind': None,
                    'pnl_krw': None, 'pnl_pct': None, 'holding_hours': None,
                    'closed': False,
                }
                open_cycles[ticker] = cycle
                cycles.append(cycle)
            cycle['buy_amount_krw'] += amount
            cycle['add_count'] += 1
            continue

        if decision == 'SELL':
            cycle = open_cycles.pop(ticker, None)
            if cycle is None:
                cycle = {
                    'ticker': ticker,
                    'entry_at': None, 'entry_price': None, 'entry_reason': None,
                    'entry_signal': UNKNOWN_SIGNAL, 'is_precision': False,
                    'buy_amount_krw': 0.0, 'add_count': 0,
                }
                cycles.append(cycle)
            cycle['exit_at'] = row.get('created_at')
            cycle['exit_price'] = row.get('price')
            cycle['exit_qty'] = row.get('qty')
            cycle['exit_amount_krw'] = amount
            cycle['exit_reason'] = row.get('reason')
            cycle['exit_kind'] = normalize_exit_reason(row.get('reason'))
            cycle['pnl_krw'] = row.get('pnl_krw')
            cycle['pnl_pct'] = row.get('pnl_pct')
            cycle['closed'] = True

            entry_ts, exit_ts = _parse_ts(cycle.get('entry_at')), _parse_ts(cycle['exit_at'])
            cycle['holding_hours'] = (
                (exit_ts - entry_ts).total_seconds() / 3600 if entry_ts and exit_ts else None
            )

    return cycles


def apply_fee_estimate(cycles: List[dict], buy_fee_rate: float, sell_fee_rate: float) -> None:
    """사이클마다 수수료 추정치(fee_krw)와 수수료 차감 후 실현손익(net_pnl_krw)을 채운다(제자리 수정).

    trade_order_log의 pnl_krw는 체결가 단순 차액이라 수수료·세금이 빠져 있다. 원본에 실제 부과액이
    없으므로 요율 가정으로 추정만 한다 — 화면에 요율을 같이 표시해서 추정치임을 밝힌다.
    sell_fee_rate에는 매도 시에만 붙는 세금(국내주식 증권거래세 등)을 포함해서 넘긴다."""
    for cycle in cycles:
        buy_amount = cycle.get('buy_amount_krw') or 0.0
        sell_amount = cycle.get('exit_amount_krw') or 0.0
        cycle['fee_krw'] = buy_amount * buy_fee_rate + sell_amount * sell_fee_rate
        pnl = cycle.get('pnl_krw')
        cycle['net_pnl_krw'] = None if pnl is None else float(pnl) - cycle['fee_krw']


def closed_cycles(cycles: List[dict]) -> List[dict]:
    """실현손익 집계 대상 — 청산됐고 손익이 기록된 사이클만, 청산 시각 오름차순."""
    done = [c for c in cycles if c.get('closed') and c.get('pnl_krw') is not None]
    return sorted(done, key=lambda c: (c.get('exit_at') or ''))


def filter_by_exit_date(cycles: List[dict], date_from: str = None, date_to: str = None) -> List[dict]:
    """청산일(exit_at의 날짜) 기준으로 기간 필터. 사이클 재구성은 기간 밖 BUY까지 봐야 하므로
    재구성이 끝난 뒤에 이 함수로 거른다. date_from/date_to는 'YYYY-MM-DD'(양끝 포함)."""
    result = []
    for cycle in cycles:
        exit_date = (cycle.get('exit_at') or '')[:10]
        if not exit_date:
            continue
        if date_from and exit_date < date_from:
            continue
        if date_to and exit_date > date_to:
            continue
        result.append(cycle)
    return result


def _max_drawdown(cum_values: List[float]) -> float:
    """누적 실현손익 곡선의 최대 낙폭(고점 대비 최대 하락폭, 양수 원화). 승률만 보면 놓치는
    "연속 손실로 자본이 얼마나 깎였나"를 보여주는 값."""
    peak = 0.0
    mdd = 0.0
    for value in cum_values:
        peak = max(peak, value)
        mdd = max(mdd, peak - value)
    return mdd


def _streaks(wins: List[bool]) -> tuple:
    """(최대 연승, 최대 연패)."""
    best_win = best_loss = cur_win = cur_loss = 0
    for is_win in wins:
        if is_win:
            cur_win, cur_loss = cur_win + 1, 0
        else:
            cur_loss, cur_win = cur_loss + 1, 0
        best_win, best_loss = max(best_win, cur_win), max(best_loss, cur_loss)
    return best_win, best_loss


def _avg(values: List[float]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


def summarize(cycles: List[dict]) -> dict:
    """요약 카드용 지표. cycles는 이미 기간 필터가 끝난 전체 사이클(미청산 포함)을 넘긴다."""
    done = closed_cycles(cycles)
    pnls = [float(c['pnl_krw']) for c in done]
    wins = [p > 0 for p in pnls]
    win_pnls = [p for p in pnls if p > 0]
    loss_pnls = [p for p in pnls if p <= 0]

    cum, running = [], 0.0
    for pnl in pnls:
        running += pnl
        cum.append(running)

    avg_win = _avg(win_pnls)
    avg_loss = _avg([abs(p) for p in loss_pnls])
    gross_profit = sum(win_pnls)
    gross_loss = abs(sum(loss_pnls))
    max_win_streak, max_loss_streak = _streaks(wins)

    best = max(done, key=lambda c: float(c['pnl_krw'])) if done else None
    worst = min(done, key=lambda c: float(c['pnl_krw'])) if done else None

    return {
        'closed_count': len(done),
        'open_count': sum(1 for c in cycles if not c.get('closed')),
        'win_count': sum(wins),
        'loss_count': len(wins) - sum(wins),
        # 승률은 표본이 없으면 0%가 아니라 "없음"이어야 한다 — 화면에서 —로 표시
        'win_rate': (sum(wins) / len(wins) * 100) if wins else None,
        'total_pnl_krw': sum(pnls) if pnls else 0.0,
        'total_fee_krw': sum(c.get('fee_krw') or 0.0 for c in done),
        'net_pnl_krw': sum((c.get('net_pnl_krw') if c.get('net_pnl_krw') is not None else float(c['pnl_krw'])) for c in done) if done else 0.0,
        'avg_pnl_pct': _avg([c.get('pnl_pct') for c in done]),
        'avg_win_krw': avg_win,
        'avg_loss_krw': avg_loss,
        # 손익비 = 평균이익/평균손실, Profit Factor = 총이익/총손실. 손실이 0이면 나눌 수 없어 None
        'payoff_ratio': (avg_win / avg_loss) if avg_win and avg_loss else None,
        'profit_factor': (gross_profit / gross_loss) if gross_loss else None,
        'max_drawdown_krw': _max_drawdown(cum),
        'max_win_streak': max_win_streak,
        'max_loss_streak': max_loss_streak,
        'avg_holding_hours': _avg([c.get('holding_hours') for c in done]),
        'best_trade': best,
        'worst_trade': worst,
    }


def group_performance(cycles: List[dict], key_fn: Callable[[dict], str]) -> List[dict]:
    """key_fn이 돌려주는 키로 묶어 그룹별 성과를 낸다(누적 실현손익 내림차순).
    진입 신호별/청산 사유별/종목별/물타기 여부별 표가 전부 이 함수 하나를 쓴다."""
    buckets = {}
    for cycle in closed_cycles(cycles):
        buckets.setdefault(key_fn(cycle), []).append(cycle)

    rows = []
    for key, items in buckets.items():
        pnls = [float(c['pnl_krw']) for c in items]
        win_count = sum(1 for p in pnls if p > 0)
        rows.append({
            'key': key,
            'count': len(items),
            'win_count': win_count,
            'loss_count': len(items) - win_count,
            'win_rate': win_count / len(items) * 100 if items else None,
            'total_pnl_krw': sum(pnls),
            'net_pnl_krw': sum((c.get('net_pnl_krw') if c.get('net_pnl_krw') is not None else float(c['pnl_krw'])) for c in items),
            'avg_pnl_pct': _avg([c.get('pnl_pct') for c in items]),
            'avg_holding_hours': _avg([c.get('holding_hours') for c in items]),
            'precision_count': sum(1 for c in items if c.get('is_precision')),
            'dca_count': sum(1 for c in items if (c.get('add_count') or 0) > 0),
        })
    return sorted(rows, key=lambda r: r['total_pnl_krw'], reverse=True)


def daily_series(cycles: List[dict]) -> List[dict]:
    """청산일별 실현손익과 누적 손익(차트용, 날짜 오름차순). 매매가 없는 날은 행이 없다."""
    by_date = {}
    for cycle in closed_cycles(cycles):
        date = (cycle.get('exit_at') or '')[:10]
        bucket = by_date.setdefault(date, {'date': date, 'count': 0, 'pnl_krw': 0.0, 'net_pnl_krw': 0.0})
        pnl = float(cycle['pnl_krw'])
        bucket['count'] += 1
        bucket['pnl_krw'] += pnl
        bucket['net_pnl_krw'] += cycle.get('net_pnl_krw') if cycle.get('net_pnl_krw') is not None else pnl

    rows = [by_date[d] for d in sorted(by_date)]
    cum = cum_net = 0.0
    for row in rows:
        cum += row['pnl_krw']
        cum_net += row['net_pnl_krw']
        row['cum_pnl_krw'] = cum
        row['cum_net_pnl_krw'] = cum_net
    return rows


def build_performance(rows: List[dict], buy_fee_rate: float, sell_fee_rate: float,
                       date_from: str = None, date_to: str = None) -> dict:
    """체결 행 → 성과 화면이 필요한 모든 집계를 한 번에. API 라우트는 이 함수만 부르면 된다."""
    all_cycles = build_trade_cycles(rows)
    apply_fee_estimate(all_cycles, buy_fee_rate, sell_fee_rate)

    # 청산된 사이클은 청산일로 거르고, 미청산(보유 중)은 기간과 무관하게 "지금 열려 있는" 것이므로 그대로 둔다
    scoped = filter_by_exit_date(all_cycles, date_from, date_to) + [c for c in all_cycles if not c.get('closed')]

    return {
        'summary': summarize(scoped),
        'by_signal': group_performance(scoped, lambda c: c.get('entry_signal') or UNKNOWN_SIGNAL),
        'by_exit_reason': group_performance(scoped, lambda c: c.get('exit_kind') or '기타'),
        'by_ticker': group_performance(scoped, lambda c: c.get('ticker') or '-'),
        'by_dca': group_performance(scoped, lambda c: '물타기 있음' if (c.get('add_count') or 0) > 0 else '물타기 없음'),
        'daily': daily_series(scoped),
        'cycles': sorted(closed_cycles(scoped), key=lambda c: (c.get('exit_at') or ''), reverse=True),
        'open_cycles': [c for c in scoped if not c.get('closed')],
        'fee_rates': {'buy': buy_fee_rate, 'sell': sell_fee_rate},
    }
