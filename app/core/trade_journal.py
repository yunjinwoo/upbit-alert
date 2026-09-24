"""매매일지(stock-history) 연동 — 종목 하나의 체결 기록에 "왜 샀고 왜 팔았나"를 붙여 돌려준다.

매매일지는 업비트 거래내역(무엇을 얼마에 몇 개)만 가지고 있다. 이 모듈은 앱이 trade_order_log에 남긴
체결마다 아래를 붙인다.
- 사유: 진입 신호(breakout_4h 등) / 청산 사유(tight_stop_loss 등) — 원문(reason)과 짧은 한글 이름(reason_label)
- 시장: 그 시각의 확정 국면(좋음/애매/나쁨) — market_regime_history로 찾는다. 판단 봇을 켜기 전 체결은 None.
- 보유 중 최고/최저 수익률: 매도 행에만. 이 포지션을 연 매수부터 이 매도까지 HOLD 행의 평가손익(pnl_pct)
  중 최대/최소. 앱이 매 사이클 HOLD를 남기므로 "+6%까지 갔다가 +1%에 팔았다"를 볼 수 있다.

일지 쪽이 시각·수량으로 자기 행과 짝을 맞추므로, 여기서는 체결을 있는 그대로 전부 돌려준다(기간 필터만).
"""
from bisect import bisect_right
from typing import Optional

from app.utils.db_manager import get_market_regime_changes, get_trade_log_for_ticker
from app.core.market_regime import REGIMES

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
