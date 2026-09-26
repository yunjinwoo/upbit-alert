"""매매일지(stock-history) 연동 — 종목 하나의 체결 기록에 "왜 샀고 왜 팔았나"를 붙여 돌려준다.

매매일지는 업비트 거래내역(무엇을 얼마에 몇 개)만 가지고 있다. 이 모듈은 앱이 trade_order_log에 남긴
체결마다 아래를 붙인다.
- 사유: 진입 신호(breakout_4h 등) / 청산 사유(tight_stop_loss 등) — 원문(reason)과 짧은 한글 이름(reason_label)
- 시장: 그 시각의 확정 국면(좋음/애매/나쁨) — market_regime_history로 찾는다. 판단 봇을 켜기 전 체결은 None.
- 보유 중 최고/최저 수익률: 매도 행에만. 이 포지션을 연 매수부터 이 매도까지 HOLD 행의 평가손익(pnl_pct)
  중 최대/최소. 앱이 매 사이클 HOLD를 남기므로 "+6%까지 갔다가 +1%에 팔았다"를 볼 수 있다.

일지 쪽이 시각·수량으로 자기 행과 짝을 맞추므로, 여기서는 체결을 있는 그대로 전부 돌려준다(기간 필터만).

build_journal_holdings는 지금 봇이 들고 있는 코인 현황(수량·평단·현재가·손익)에 같은 방식으로
진입 사유·진입 시장·보유 중 최고/최저 수익률·적용 중인 청산 규칙을 붙인다(/api/journal/holdings).
"""
import json
from bisect import bisect_right
from typing import Callable, Optional

from app.utils.db_manager import get_market_regime_changes, get_trade_log_for_ticker
from app.core.market_regime import REGIMES
from app.core.strategy_presets import PRESET_CFG_ATTRS, STRATEGY_PRESETS, match_preset

# 보유 수량이 이 값 이하로 남으면 전량 청산으로 본다(부동소수 오차)
QTY_EPS = 1e-9

# reason 앞부분(괄호 앞) → 짧은 한글 이름. 모르는 사유는 원문 그대로 보여준다.
_REASON_LABELS = {
    'breakout_4h': '4시간봉 돌파',
    'breakout_1d': '일봉 돌파',
    'near_ma200+above_cloud': '200선 근접+구름 위',
    'near_ma200+above_cloud_1d': '200선 근접+일봉 구름 위',
    '강제매수': '수동 매수',
    '강제매도': '수동 매도',
    'dca_buy': '물타기',
    'recovery_dca': '회복 물타기',
    'take_profit': '익절',
    'recovery_take_profit': '회복 익절',
    'trailing_take_profit': '되돌림 익절',
    'rsi_exit': 'RSI 과열 매도',
    'stop_loss': '손절(고점 대비)',
    'tight_stop_loss': '손절',
    'tight_trail_exit': '고점 대비 하락 매도',
    'tight_breakeven_exit': '본전 매도',
}
_PRECISION_SUFFIX = '+정밀조건충족'


def reason_label(reason: Optional[str]) -> Optional[str]:
    if not reason:
        return None
    text = reason
    precision = False
    if _PRECISION_SUFFIX in text:
        precision = True
        text = text.replace(_PRECISION_SUFFIX, '')
    key = text.split('(', 1)[0].strip()
    label = _REASON_LABELS.get(key, key)
    return f'{label} · 정밀조건' if precision else label


class _RegimeLookup:
    """시각 → 그 시각의 확정 국면 키. 이력이 없거나 첫 변경 이전이면 첫 변경의 from_regime(없으면 None)."""

    def __init__(self, changes: list):
        self._times = [c['changed_at'] for c in changes]
        self._to = [c['to_regime'] for c in changes]
        self._first_from = changes[0].get('from_regime') if changes else None

    def at(self, ts: Optional[str]) -> Optional[str]:
        if not ts or not self._times:
            return None
        i = bisect_right(self._times, ts)
        return self._to[i - 1] if i > 0 else self._first_from


def _regime_payload(key: Optional[str]) -> Optional[dict]:
    if not key or key not in REGIMES:
        return None
    return {'key': key, 'label': REGIMES[key]['label']}


def build_journal_fills(symbol: str, broker: str = 'upbit', mode: str = 'live',
                        date_from: Optional[str] = None, date_to: Optional[str] = None,
                        rows: Optional[list] = None, regime_changes: Optional[list] = None) -> list:
    """symbol('EGLD' 또는 'KRW-EGLD')의 체결 목록. date_from/date_to는 'YYYY-MM-DD'(체결일 기준, 양끝 포함).
    rows/regime_changes는 테스트용 주입 — 생략하면 DB에서 읽는다."""
    ticker = symbol if '-' in symbol else f'KRW-{symbol.upper()}'
    if rows is None:
        rows = get_trade_log_for_ticker(broker, mode, ticker)
    regimes = _RegimeLookup(regime_changes if regime_changes is not None else get_market_regime_changes())

    fills = []
    open_qty = 0.0
    qty_known = True
    position_open = False
    peak = trough = None
    entry_regime = None

    def track(pnl):
        nonlocal peak, trough
        if pnl is None:
            return
        peak = pnl if peak is None else max(peak, pnl)
        trough = pnl if trough is None else min(trough, pnl)

    for row in rows:
        decision = row['decision']
        at = row.get('created_at')

        if decision == 'HOLD':
            if position_open:
                track(row.get('pnl_pct'))
            continue

        fill = {
            'id': row['id'],
            'at': at,
            'side': '매도' if decision == 'SELL' else '매수',
            'decision': decision,
            'price': row.get('price'),
            'qty': row.get('qty'),
            'amount_krw': row.get('amount_krw'),
            'reason': row.get('reason'),
            'reason_label': reason_label(row.get('reason')),
            'regime': _regime_payload(regimes.at(at)),
        }

        if decision in ('BUY', 'DCA_BUY'):
            if not position_open:
                position_open = True
                open_qty, qty_known = 0.0, True
                peak = trough = None
                entry_regime = regimes.at(at)
            if row.get('qty') is None:
                qty_known = False
            else:
                open_qty += float(row['qty'])
        else:  # SELL
            if position_open:
                track(row.get('pnl_pct'))
            else:
                peak = trough = None  # 앱 기록 이전부터 들고 있던 포지션 — 보유 중 기록이 없으니 비워둔다
            fill.update({
                'pnl_krw': row.get('pnl_krw'),
                'pnl_pct': row.get('pnl_pct'),
                'peak_pnl_pct': peak,
                'trough_pnl_pct': trough,
                'entry_regime': _regime_payload(entry_regime) if position_open else None,
            })
            if row.get('qty') is None or not qty_known:
                position_open = False
            else:
                open_qty -= float(row['qty'])
                if open_qty <= QTY_EPS:
                    position_open = False

        fills.append(fill)

    def in_range(f):
        day = (f['at'] or '')[:10]
        if date_from and day < date_from:
            return False
        if date_to and day > date_to:
            return False
        return True

    return [f for f in fills if in_range(f)]


def _open_position_info(rows: list) -> dict:
    """체결 로그에서 지금 열려 있는 포지션의 진입 행·물타기 횟수·보유 중 최고/최저 수익률.
    build_journal_fills와 같은 방식으로 포지션을 따라간다. 앱 기록에 진입이 없으면(앱 이전부터 보유) 빈 값."""
    entry = None
    dca = 0
    open_qty, qty_known = 0.0, True
    peak = trough = None
    for row in rows:
        decision = row['decision']
        pnl = row.get('pnl_pct')
        if decision == 'HOLD':
            if entry is not None and pnl is not None:
                peak = pnl if peak is None else max(peak, pnl)
                trough = pnl if trough is None else min(trough, pnl)
        elif decision in ('BUY', 'DCA_BUY'):
            if entry is None:
                entry, dca, open_qty, qty_known, peak, trough = row, 0, 0.0, True, None, None
            elif decision == 'DCA_BUY':
                dca += 1
            if row.get('qty') is None:
                qty_known = False
            else:
                open_qty += float(row['qty'])
        elif decision == 'SELL' and entry is not None:
            if row.get('qty') is None or not qty_known:
                entry = None
            else:
                open_qty -= float(row['qty'])
                if open_qty <= QTY_EPS:
                    entry = None
    if entry is None:
        return {'entry': None, 'dca_count': 0, 'peak': None, 'trough': None}
    return {'entry': entry, 'dca_count': dca, 'peak': peak, 'trough': trough}


_CFG_TO_SETTING = {attr: key for key, attr in PRESET_CFG_ATTRS.items()}


def _fmt_pct(v) -> str:
    return f'{float(v):g}'


def exit_rule_payload(settings: dict, locked_rule=None) -> dict:
    """이 종목에 실제로 적용되는 청산 규칙. locked_rule(paper_positions.exit_rule, 전략 묶음을 바꾸기 전에 산
    종목에 고정된 값)이 있으면 그 값을 현재 설정 위에 덮어쓴다 — trade_strategy.position_cfg와 같은 규칙."""
    rule = locked_rule
    if isinstance(rule, str):
        try:
            rule = json.loads(rule)
        except ValueError:
            rule = None
    locked = isinstance(rule, dict) and bool(rule)
    eff = dict(settings)
    if locked:
        for attr, v in rule.items():
            if attr in _CFG_TO_SETTING:
                eff[_CFG_TO_SETTING[attr]] = v
    preset = match_preset(eff)
    tp = eff.get('take_profit_pct')
    if eff.get('tight_stop_enabled'):
        text = (f"손절 -{_fmt_pct(eff['tight_stop_initial_pct'])}%, "
                f"+{_fmt_pct(eff['tight_stop_arm_pct'])}% 넘으면 고점 대비 -{_fmt_pct(eff['tight_stop_trail_pct'])}%까지 보유")
    else:
        text = f"손절 고점 대비 -{_fmt_pct(eff.get('stop_loss_pct') or 0)}%"
        if eff.get('trailing_tp_enabled'):
            text += ', 되돌림 익절'
    if tp:
        text += f', 익절 +{_fmt_pct(tp)}%'
    p = STRATEGY_PRESETS.get(preset)
    return {
        'preset': {'key': preset, 'label': p['label'], 'emoji': p['emoji']} if p else None,
        'text': text,
        'locked': locked,  # True면 "산 시점 규칙 유지"(묶음을 바꿔도 이 종목은 그대로)
    }


def build_journal_holdings(positions: list, tracking_rows: dict, get_price: Callable[[str], Optional[float]],
                           settings: dict, trade_rows_fn: Callable[[str], list],
                           regime_changes: Optional[list] = None) -> list:
    """봇이 들고 있는 코인 현황. positions: [{'ticker', 'qty', 'avg_buy_price'}](실계좌 중 봇 관리 대상),
    tracking_rows: ticker → paper_positions 행(mode='live'), trade_rows_fn: ticker → get_trade_log_for_ticker 결과.
    평가손익은 수수료 미반영(앱 화면과 같은 계산)."""
    regimes = _RegimeLookup(regime_changes if regime_changes is not None else get_market_regime_changes())
    out = []
    for pos in positions:
        ticker = pos['ticker']
        qty = float(pos['qty'])
        avg = float(pos['avg_buy_price'] or 0)
        price = get_price(ticker)
        cost = avg * qty
        eval_amount = price * qty if price else None
        tracked = tracking_rows.get(ticker) or {}
        info = _open_position_info(trade_rows_fn(ticker))
        entry = info['entry']
        entry_at = (entry or {}).get('created_at') or tracked.get('entry_at')
        out.append({
            'ticker': ticker,
            'symbol': ticker.split('-', 1)[-1],
            'qty': qty,
            'avg_buy_price': avg,
            'current_price': price,
            'cost_krw': cost,
            'eval_krw': eval_amount,
            'pnl_krw': eval_amount - cost if eval_amount is not None else None,
            'pnl_pct': (price - avg) / avg * 100 if price and avg else None,
            'entry_at': entry_at,
            'entry_reason': entry.get('reason') if entry else None,
            'entry_reason_label': reason_label(entry.get('reason')) if entry else None,
            'entry_regime': _regime_payload(regimes.at(entry_at)),
            'dca_count': info['dca_count'],
            'peak_pnl_pct': info['peak'],
            'trough_pnl_pct': info['trough'],
            'exit_rule': exit_rule_payload(settings, tracked.get('exit_rule')),
        })
    out.sort(key=lambda h: h['eval_krw'] or h['cost_krw'], reverse=True)
    return out


def load_live_holdings() -> dict:
    """서버에서 실계좌를 조회해 build_journal_holdings를 채운다. 범위는 대시보드 실거래 표와 같다 —
    실거래 승인했거나 봇이 추적 중인 종목만(봇과 무관하게 원래 갖고 있던 코인은 뺀다)."""
    from app.core.brokers.upbit_live_broker import UpbitLiveBroker
    from app.utils.db_manager import (
        get_approved_candidate_tickers, get_paper_positions, get_trade_strategy_settings,
    )
    broker = UpbitLiveBroker()
    tracking_rows = {r['ticker']: r for r in get_paper_positions(broker.broker_name, broker.mode)}
    in_scope = get_approved_candidate_tickers(broker.broker_name, broker.mode) | set(tracking_rows)
    positions = [{'ticker': p.ticker, 'qty': p.qty, 'avg_buy_price': p.avg_buy_price}
                 for p in broker.get_positions() if p.qty > 0 and p.ticker in in_scope]
    settings = get_trade_strategy_settings(broker.broker_name)
    holdings = build_journal_holdings(
        positions, tracking_rows, broker.get_current_price, settings,
        lambda t: get_trade_log_for_ticker(broker.broker_name, broker.mode, t),
    )
    return {
        'holdings': holdings,
        'current_preset': exit_rule_payload(settings)['preset'],
        'cash_krw': broker.get_cash_balance(),
    }
