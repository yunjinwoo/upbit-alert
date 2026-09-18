"""매매 성과 집계(app/core/trade_performance.py) 단위 테스트 — DB 없이 가짜 체결 행으로 검증한다.

실행: python tests/test_trade_performance.py
"""
import os
import sys

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.trade_performance import (
    build_trade_cycles,
    build_performance,
    normalize_entry_signal,
    normalize_exit_reason,
    is_precision_entry,
    summarize,
    apply_fee_estimate,
    filter_by_exit_date,
    UNKNOWN_SIGNAL,
)


def row(created_at, ticker, decision, reason=None, price=None, qty=None,
        amount_krw=None, pnl_krw=None, pnl_pct=None):
    return {
        'created_at': created_at, 'ticker': ticker, 'decision': decision, 'reason': reason,
        'price': price, 'qty': qty, 'amount_krw': amount_krw, 'pnl_krw': pnl_krw, 'pnl_pct': pnl_pct,
    }


def check(label, actual, expected):
    assert actual == expected, f"{label}: {actual!r} != {expected!r}"
    print(f"  ✅ {label} = {actual!r}")


print("--- [테스트] reason 정규화 ---")
check('breakout_4h', normalize_entry_signal('breakout_4h'), 'breakout_4h')
check('정밀조건 접미사는 같은 신호로 묶임',
      normalize_entry_signal('near_ma200+above_cloud+정밀조건충족'), 'near_ma200+above_cloud')
check('정밀조건 여부는 따로 표시', is_precision_entry('breakout_4h+정밀조건충족'), True)
check('수동 매수', normalize_entry_signal('강제매수(수동)'), '강제매수(수동)')
check('reason 없음', normalize_entry_signal(None), UNKNOWN_SIGNAL)
check('익절', normalize_exit_reason('take_profit(12.34%)'), 'take_profit')
check('손절', normalize_exit_reason('stop_loss(최고가대비 -5.00%)'), 'stop_loss')
check('RSI 매도', normalize_exit_reason('rsi_exit(RSI 82.1>=80.0)'), 'rsi_exit')

print("\n--- [테스트] 매매 사이클 재구성 ---")
rows = [
    # ① 정상 사이클: 매수 → 익절 (+5,000원)
    row('2026-09-01 09:00:00', 'KRW-BTC', 'BUY', 'breakout_4h', price=100.0, qty=1000, amount_krw=100000),
    row('2026-09-01 15:00:00', 'KRW-BTC', 'SELL', 'take_profit(5.00%)', price=105.0, qty=1000,
        amount_krw=105000, pnl_krw=5000, pnl_pct=5.0),
    # ② 물타기 있는 사이클: 매수 → 물타기 → 손절 (-8,000원)
    row('2026-09-02 09:00:00', 'KRW-ETH', 'BUY', 'near_ma200+above_cloud+정밀조건충족',
        price=200.0, qty=500, amount_krw=100000),
    row('2026-09-02 12:00:00', 'KRW-ETH', 'DCA_BUY', 'dca_buy(1/2회, 평단대비 -10.00%)',
        price=180.0, qty=555, amount_krw=100000),
    row('2026-09-03 09:00:00', 'KRW-ETH', 'SELL', 'stop_loss(최고가대비 -5.00%)', price=185.0, qty=1055,
        amount_krw=195175, pnl_krw=-8000, pnl_pct=-4.0),
    # ③ 진입 기록이 없는 매도 — 신호를 모르므로 미확인 버킷
    row('2026-09-03 10:00:00', 'KRW-SOL', 'SELL', 'take_profit(3.00%)', price=50.0, qty=100,
        amount_krw=5000, pnl_krw=150, pnl_pct=3.0),
    # ④ 아직 청산 안 된 포지션 — 실현손익 집계에서 빠져야 함
    row('2026-09-04 09:00:00', 'KRW-XRP', 'BUY', 'breakout_1d', price=10.0, qty=10000, amount_krw=100000),
]
cycles = build_trade_cycles(rows)

check('사이클 수', len(cycles), 4)
btc, eth, sol, xrp = cycles
check('BTC 진입 신호', btc['entry_signal'], 'breakout_4h')
check('BTC 보유시간(시간)', btc['holding_hours'], 6.0)
check('BTC 실현손익', btc['pnl_krw'], 5000)
check('ETH 진입 신호(정밀조건 접미사 제거)', eth['entry_signal'], 'near_ma200+above_cloud')
check('ETH 정밀조건 진입', eth['is_precision'], True)
check('ETH 물타기 횟수', eth['add_count'], 1)
check('ETH 총 매수금액(물타기 합산)', eth['buy_amount_krw'], 200000.0)
check('SOL 진입 신호(짝 없는 매도)', sol['entry_signal'], UNKNOWN_SIGNAL)
check('SOL 보유시간(진입 기록 없음)', sol['holding_hours'], None)
check('XRP 미청산', xrp['closed'], False)

print("\n--- [테스트] HOLD 행의 평가손익은 절대 섞이지 않는다 ---")
# get_trade_fill_rows가 SQL에서 이미 HOLD/SKIP을 거르지만, 혹시 섞여 들어와도 사이클을 만들지 않아야 한다.
with_hold = rows + [row('2026-09-04 10:00:00', 'KRW-XRP', 'HOLD', 'pnl 30.00% (최고가대비 -1.00%)',
                        pnl_krw=999999, pnl_pct=30.0)]
check('HOLD가 섞여도 사이클 수 동일', len(build_trade_cycles(with_hold)), 4)
check('HOLD 손익은 누적에 안 들어감', summarize(build_trade_cycles(with_hold))['total_pnl_krw'], -2850.0)

print("\n--- [테스트] 요약 지표 ---")
s = summarize(cycles)
check('청산 건수', s['closed_count'], 3)
check('보유 중 건수', s['open_count'], 1)
check('승/패', (s['win_count'], s['loss_count']), (2, 1))
check('승률(%)', round(s['win_rate'], 2), 66.67)
check('누적 실현손익', s['total_pnl_krw'], -2850.0)   # 5000 - 8000 + 150
check('최대 연승', s['max_win_streak'], 1)
check('최대 연패', s['max_loss_streak'], 1)
# 누적 곡선: 5000 → -3000 → -2850. 고점 5000에서 -3000까지 8000원 하락
check('최대 낙폭(MDD)', s['max_drawdown_krw'], 8000.0)
check('Profit Factor', round(s['profit_factor'], 4), round(5150 / 8000, 4))
check('손익비', round(s['payoff_ratio'], 4), round(2575 / 8000, 4))

print("\n--- [테스트] 수수료 추정 ---")
fee_cycles = build_trade_cycles(rows)
apply_fee_estimate(fee_cycles, 0.0005, 0.0005)
# BTC: 매수 100,000 + 매도 105,000 = 205,000 × 0.05% = 102.5원
check('BTC 수수료 추정', round(fee_cycles[0]['fee_krw'], 2), 102.5)
check('BTC 순손익', round(fee_cycles[0]['net_pnl_krw'], 2), 4897.5)
check('미청산 사이클은 순손익 없음', fee_cycles[3]['net_pnl_krw'], None)

print("\n--- [테스트] 기간 필터(청산일 기준) ---")
check('9/3 하루만', len(filter_by_exit_date(cycles, '2026-09-03', '2026-09-03')), 2)
check('9/1까지', len(filter_by_exit_date(cycles, None, '2026-09-01')), 1)

print("\n--- [테스트] build_performance 통합 ---")
perf = build_performance(rows, 0.0005, 0.0005, date_from='2026-09-01', date_to='2026-09-02')
check('기간 안 청산 건수', perf['summary']['closed_count'], 1)
check('미청산은 기간과 무관하게 남음', perf['summary']['open_count'], 1)
check('신호별 그룹 수', len(perf['by_signal']), 1)
check('신호별 키', perf['by_signal'][0]['key'], 'breakout_4h')

perf_all = build_performance(rows, 0.0005, 0.0005)
check('전체 기간 청산 건수', perf_all['summary']['closed_count'], 3)
check('일별 시리즈 날짜 수', len(perf_all['daily']), 2)   # 9/1, 9/3
check('일별 누적 손익 마지막 값', perf_all['daily'][-1]['cum_pnl_krw'], -2850.0)
check('물타기 구분 그룹', sorted(r['key'] for r in perf_all['by_dca']), ['물타기 없음', '물타기 있음'])
check('사이클 목록은 최신 청산순', [c['ticker'] for c in perf_all['cycles']],
      ['KRW-SOL', 'KRW-ETH', 'KRW-BTC'])

print("\n✅ 매매 성과 집계 테스트 전부 통과")
