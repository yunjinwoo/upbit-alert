"""백테스트 결과를 사람이 읽는 표로.

집계는 새로 만들지 않고 app/core/trade_performance.py의 build_performance()를 그대로 쓴다 —
백테스트 결과가 매매 성과 화면과 같은 정의(승률/손익비/청산사유별 기여)로 읽혀야, 화면에서 보던
숫자와 곧바로 비교가 된다. 여기서 따로 계산하는 건 계좌 자산 곡선에서만 나오는 두 가지
(최대낙폭, 최종 수익률)뿐이다.

성과 화면의 MDD는 "청산된 사이클의 누적 실현손익" 기준이라 아직 안 판 포지션의 하락을 못 본다.
백테스트는 계좌 전체가 얼마나 깊게 빠졌는지가 중요하므로(그게 실제로 못 버티고 끄게 되는 지점이다)
평가액까지 포함한 자산 곡선 기준 낙폭을 따로 낸다.
"""
import unicodedata
from typing import List, Optional

from app.backtest.engine import BacktestResult
from app.backtest.market_data import SELECTION_LABELS
from app.core.trade_performance import build_performance

EXIT_MODE_LABELS = {
    'strategy': '현재 자동매매 청산 로직',
    'hold': '기준선(정해진 시간 보유 후 청산)',
}


def equity_stats(curve: List[dict], initial_cash: float) -> dict:
    """자산 곡선에서 최종 수익률과 최대낙폭(고점 대비 최대 하락폭)을 낸다."""
    if not curve:
        return {'final_equity': initial_cash, 'return_pct': 0.0, 'mdd_pct': 0.0, 'peak_equity': initial_cash}
    peak = curve[0]['equity']
    mdd = 0.0
    for point in curve:
        peak = max(peak, point['equity'])
        if peak > 0:
            mdd = max(mdd, (peak - point['equity']) / peak * 100)
    final = curve[-1]['equity']
    return {
        'final_equity': final,
        'return_pct': (final - initial_cash) / initial_cash * 100 if initial_cash else 0.0,
        'mdd_pct': mdd,
        'peak_equity': peak,
    }


def build_report(result: BacktestResult) -> dict:
    """리포트에 필요한 모든 값을 한 덩어리로. CLI 출력과 JSON 저장이 같은 값을 본다."""
    params = result.params
    performance = build_performance(result.orders, params.buy_fee_rate, params.sell_fee_rate)
    equity = equity_stats(result.equity_curve, params.initial_cash)
    max_open = max((p['open_positions'] for p in result.equity_curve), default=0)
    return {
        'params': params.__dict__,
        'period': {
            'from': result.equity_curve[0]['at'] if result.equity_curve else None,
            'to': result.equity_curve[-1]['at'] if result.equity_curve else None,
            'candle_count': result.candle_count,
            'market_count': result.market_count,
        },
        'equity': {**equity, 'max_open_positions': max_open},
        'performance': performance,
        'equity_curve': result.equity_curve,
        'orders': result.orders,
    }


def _pad(text: str, width: int, align: str = 'left') -> str:
    """터미널 폭에 맞춘 칸 맞춤.

    파이썬의 f-string 패딩은 글자 수로 세는데 한글은 터미널에서 두 칸을 먹는다 — 그대로 두면
    한글이 섞인 표의 열이 어긋나서 읽기가 나빠진다.
    """
    display = sum(2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1 for ch in text)
    fill = ' ' * max(0, width - display)
    return fill + text if align == 'right' else text + fill


def _fmt_krw(value: Optional[float]) -> str:
    return '—' if value is None else f'{value:,.0f}원'


def _fmt_pct(value: Optional[float], digits: int = 2) -> str:
    return '—' if value is None else f'{value:.{digits}f}%'


def _fmt_num(value: Optional[float], digits: int = 2) -> str:
    return '—' if value is None else f'{value:.{digits}f}'


def format_report(report: dict) -> str:
    """CLI에 찍을 텍스트."""
    params = report['params']
    period = report['period']
    equity = report['equity']
    summary = report['performance']['summary']

    lines = []
    lines.append('=' * 72)
    lines.append('업비트 백테스트 결과')
    lines.append('=' * 72)
    lines.append(f"기간        : {period['from']} ~ {period['to']} (봉 {period['candle_count']:,}개)")
    lines.append(f"대상        : KRW {period['market_count']}종목 중 "
                 f"{SELECTION_LABELS.get(params['selection'], params['selection'])} {params['top_n']}개")
    entry_when = f"매일 {params['entry_hour']}시" if params['entry_hour'] is not None else '매 봉마다'
    lines.append(f"진입 시점   : {entry_when}")
    lines.append(f"청산        : {EXIT_MODE_LABELS.get(params['exit_mode'], params['exit_mode'])}"
                 + (f" — {params['hold_hours']:.0f}시간" if params['exit_mode'] == 'hold' else ''))
    if params['min_trade_value_krw']:
        lines.append(f"유동성 필터 : 당일 거래대금 {params['min_trade_value_krw']:,.0f}원 이상")
    lines.append('')

    lines.append('[ 계좌 ]')
    lines.append(f"  초기 자본       {_fmt_krw(params['initial_cash'])}")
    lines.append(f"  최종 자산       {_fmt_krw(equity['final_equity'])}  ({_fmt_pct(equity['return_pct'])})")
    lines.append(f"  최대 낙폭(MDD)  {_fmt_pct(equity['mdd_pct'])}  ← 고점 대비 계좌가 가장 깊게 빠진 폭")
    lines.append(f"  최대 동시보유   {equity['max_open_positions']}종목")
    lines.append('')

    lines.append('[ 매매 ]')
    lines.append(f"  청산 건수       {summary['closed_count']}건 "
                 f"(이익 {summary['win_count']} / 손실 {summary['loss_count']})")
    lines.append(f"  승률            {_fmt_pct(summary['win_rate'], 1)}")
    lines.append(f"  실현손익        {_fmt_krw(summary['total_pnl_krw'])} "
                 f"(수수료 {_fmt_krw(summary['total_fee_krw'])} 차감 후 {_fmt_krw(summary['net_pnl_krw'])})")
    lines.append(f"  평균 수익률     {_fmt_pct(summary['avg_pnl_pct'])}")
    lines.append(f"  평균 이익/손실  {_fmt_krw(summary['avg_win_krw'])} / {_fmt_krw(summary['avg_loss_krw'])}"
                 f"  (손익비 {_fmt_num(summary['payoff_ratio'])})")
    lines.append(f"  Profit Factor   {_fmt_num(summary['profit_factor'])}  ← 1.0 미만이면 총손실이 총이익보다 큼")
    lines.append(f"  평균 보유시간   {_fmt_num(summary['avg_holding_hours'], 1)}시간")
    lines.append(f"  최대 연승/연패  {summary['max_win_streak']} / {summary['max_loss_streak']}")
    lines.append('')

    by_exit = report['performance']['by_exit_reason']
    if by_exit:
        lines.append('[ 청산 사유별 ]')
        lines.append('  ' + _pad('사유', 24) + _pad('건수', 6, 'right')
                     + _pad('승률', 9, 'right') + _pad('실현손익', 16, 'right'))
        for row in sorted(by_exit, key=lambda r: r['total_pnl_krw'], reverse=True):
            lines.append('  ' + _pad(row['key'], 24) + _pad(str(row['count']), 6, 'right')
                         + _pad(_fmt_pct(row['win_rate'], 1), 9, 'right')
                         + _pad(_fmt_krw(row['total_pnl_krw']), 16, 'right'))
        lines.append('')

    by_ticker = [r for r in report['performance']['by_ticker'] if r['count']]
    if by_ticker:
        ranked = sorted(by_ticker, key=lambda r: r['total_pnl_krw'], reverse=True)
        lines.append('[ 종목별 기여 상위/하위 5 ]')
        for row in ranked[:5]:
            lines.append('  + ' + _pad(row['key'], 14) + _pad(f"{row['count']}건", 6, 'right')
                         + _pad(_fmt_krw(row['total_pnl_krw']), 16, 'right'))
        if len(ranked) > 5:
            for row in ranked[-5:]:
                lines.append('  - ' + _pad(row['key'], 14) + _pad(f"{row['count']}건", 6, 'right')
                             + _pad(_fmt_krw(row['total_pnl_krw']), 16, 'right'))
        lines.append('')

    best, worst = summary.get('best_trade'), summary.get('worst_trade')
    if best:
        lines.append(f"최고 매매    {best['ticker']} {_fmt_krw(best['pnl_krw'])} "
                     f"({_fmt_pct(best.get('pnl_pct'))}, {best.get('exit_kind')})")
    if worst:
        lines.append(f"최악 매매    {worst['ticker']} {_fmt_krw(worst['pnl_krw'])} "
                     f"({_fmt_pct(worst.get('pnl_pct'))}, {worst.get('exit_kind')})")
    lines.append('=' * 72)
    return '\n'.join(lines)


def format_comparison(reports: List[dict]) -> str:
    """여러 설정을 한 표로 — 어느 조합이 나은지는 나란히 놓고 봐야 판단이 된다."""
    lines = ['', '=' * 92, '비교', '=' * 92]
    lines.append(_pad('선정', 20) + _pad('청산', 30) + _pad('수익률', 10, 'right')
                 + _pad('MDD', 9, 'right') + _pad('건수', 7, 'right')
                 + _pad('승률', 9, 'right') + _pad('PF', 8, 'right'))
    lines.append('-' * 92)
    for report in reports:
        params = report['params']
        summary = report['performance']['summary']
        selection = SELECTION_LABELS.get(params['selection'], params['selection'])
        exit_label = EXIT_MODE_LABELS.get(params['exit_mode'], params['exit_mode'])
        if params['exit_mode'] == 'hold':
            exit_label = f"기준선 {params['hold_hours']:.0f}시간 보유"
        lines.append(
            _pad(selection, 20) + _pad(exit_label, 30)
            + _pad(_fmt_pct(report['equity']['return_pct']), 10, 'right')
            + _pad(_fmt_pct(report['equity']['mdd_pct']), 9, 'right')
            + _pad(str(summary['closed_count']), 7, 'right')
            + _pad(_fmt_pct(summary['win_rate'], 1), 9, 'right')
            + _pad(_fmt_num(summary['profit_factor']), 8, 'right')
        )
    lines.append('=' * 92)
    return '\n'.join(lines)
