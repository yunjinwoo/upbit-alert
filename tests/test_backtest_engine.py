"""백테스트 검증 — 목 캔들로 순위 재현·미래 참조 방지·청산 판단을 확인한다.

업비트 API는 개발 세션에서 막혀 있으므로(app/backtest/collector.py 참고) 여기서는 캔들을 직접
만들어 캐시에 넣고 돌린다. 네트워크도 pandas도 필요 없다 — 이 저장소 관례대로 pytest가 아니라
assert + print 스크립트다.

    python tests/test_backtest_engine.py

확인하는 것:
  1. 순위가 "그 시각까지의" 데이터로만 계산되는가 (전일 종가 대비 상승률, KST 00시부터의 누적 거래대금)
  2. 거래대금 누적이 KST 자정에 0으로 리셋되는가 (업비트의 "당일" 정의)
  3. 체결가가 판단 봉의 종가가 아니라 다음 봉의 시가인가 (미래를 당겨쓰지 않음)
  4. 기존 청산 로직(익절/트레일링 손절)이 백테스트에서 그대로 동작하는가
  4-1. 짧은 손절 · 긴 수익 모드가 백테스트에서도 그대로 동작하는가(물타기 없이 짧게 끊는지)
  5. 기준선(정해진 시간 보유)이 그 시간에 맞춰 청산하는가
  6. 성과 리포트가 기존 집계 모듈로 만들어지는가
"""
import os
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.backtest import candle_store as store
from app.backtest.candle_store import KST
from app.backtest.engine import BacktestParams, run_backtest, EXIT_MODE_HOLD, EXIT_MODE_STRATEGY
from app.backtest.market_data import MarketData, SELECT_GAINERS, SELECT_TRADE_VALUE
from app.backtest.report import build_report
from app.backtest.strategy_config import strategy_config

DAY_SEC = 86400
HOUR_SEC = 3600
BASE_TS = int(datetime(2026, 1, 5, tzinfo=KST).timestamp())   # 2026-01-05 00:00 KST


# 다음 봉의 시가를 직전 봉 종가와 일부러 어긋나게 둔다 — 두 값이 같으면 "체결가가 다음 봉 시가"인지
# "판단 봉 종가"인지 구분이 안 돼서 미래 참조 방지 테스트가 무의미해진다.
OPEN_GAP = 1.002


def _make_day(market, day_ts, closes, hour_value, prev_close, db_path):
    """하루치 1시간봉 24개와 그날의 일봉 1개를 캐시에 넣는다."""
    hourly = []
    for h, close in enumerate(closes):
        open_price = (closes[h - 1] if h else prev_close) * OPEN_GAP
        hourly.append({
            'ts': day_ts + h * HOUR_SEC, 'open': open_price,
            'high': max(open_price, close), 'low': min(open_price, close),
            'close': close, 'volume': 1.0, 'value': hour_value, 'prev_close': None,
        })
    daily = {
        'ts': day_ts, 'open': hourly[0]['open'],
        'high': max(c['high'] for c in hourly), 'low': min(c['low'] for c in hourly),
        'close': closes[-1], 'volume': 24.0, 'value': hour_value * 24, 'prev_close': prev_close,
    }
    store.upsert_candles(market, 'minutes60', hourly, db_path)
    store.upsert_candles(market, 'days', [daily], db_path)


def _ramp(start, end, steps=24):
    """start에서 end까지 일정하게 오르내리는 종가 24개."""
    return [start + (end - start) * (i + 1) / steps for i in range(steps)]


def build_fixture(db_path):
    """3종목 × 3일치 목 데이터.

      AAA — 1일차 보합, 2일차 +20% 급등, 3일차 -10% 되돌림  (상승률 상위 후보)
      BBB — 내내 보합이지만 거래대금이 압도적                (거래대금 상위 후보)
      CCC — 내내 하락                                        (손절 쪽 확인용)
    """
    store.init_db(db_path)
    day1, day2, day3 = BASE_TS, BASE_TS + DAY_SEC, BASE_TS + 2 * DAY_SEC

    _make_day('KRW-AAA', day1, [1000.0] * 24, 1_000_000, 1000.0, db_path)
    _make_day('KRW-AAA', day2, _ramp(1000.0, 1200.0), 2_000_000, 1000.0, db_path)
    _make_day('KRW-AAA', day3, _ramp(1200.0, 1080.0), 1_500_000, 1200.0, db_path)

    for day, prev, closes in ((day1, 2000.0, [2000.0] * 24), (day2, 2000.0, [2000.0] * 24),
                              (day3, 2000.0, [2000.0] * 24)):
        _make_day('KRW-BBB', day, closes, 50_000_000, prev, db_path)

    _make_day('KRW-CCC', day1, _ramp(500.0, 480.0), 300_000, 500.0, db_path)
    _make_day('KRW-CCC', day2, _ramp(480.0, 400.0), 300_000, 480.0, db_path)
    _make_day('KRW-CCC', day3, _ramp(400.0, 360.0), 300_000, 400.0, db_path)
    return day1, day2, day3


def test_ranking_is_point_in_time(md, day2):
    """2일차 5시 시점 — 그때까지 오른 만큼만 상승률에 반영돼야 한다(마감 기준 +20%가 아니라)."""
    ts = day2 + 5 * HOUR_SEC
    snapshot = {r['ticker']: r for r in md.snapshot(ts)}
    aaa = snapshot['KRW-AAA']
    expected = (1000.0 + 200.0 * 6 / 24 - 1000.0) / 1000.0 * 100   # 6번째 봉까지 진행
    assert abs(aaa['change_rate'] - expected) < 1e-6, aaa
    assert aaa['change_rate'] < 20.0, '마감 기준 상승률을 당겨쓰면 안 된다'

    gainers = md.rank(ts, SELECT_GAINERS, limit=3)
    assert gainers[0]['ticker'] == 'KRW-AAA', gainers
    assert gainers[-1]['ticker'] == 'KRW-CCC', gainers

    by_value = md.rank(ts, SELECT_TRADE_VALUE, limit=3)
    assert by_value[0]['ticker'] == 'KRW-BBB', by_value
    print('✓ 순위가 그 시각까지의 데이터로만 계산된다')


def test_trade_value_resets_at_kst_midnight(md, day2):
    """업비트의 "당일 거래대금"은 KST 00시부터다 — 자정을 넘으면 0에서 다시 쌓여야 한다."""
    last_hour = {r['ticker']: r for r in md.snapshot(day2 - HOUR_SEC)}['KRW-AAA']
    first_hour = {r['ticker']: r for r in md.snapshot(day2)}['KRW-AAA']
    assert last_hour['trade_value'] == 1_000_000 * 24, last_hour
    assert first_hour['trade_value'] == 2_000_000, first_hour
    print('✓ 당일 거래대금이 KST 자정에 리셋된다')


def test_fill_price_is_next_candle_open(md, day2):
    """판단은 종가로, 체결은 다음 봉 시가로 — 같은 봉 종가에 체결하면 미래를 당겨쓴 것이 된다."""
    ts = day2 + 3 * HOUR_SEC
    decision_price = md.price('KRW-AAA', ts)
    fill = md.fill_price('KRW-AAA', ts)
    next_open = decision_price * OPEN_GAP   # 목 데이터의 다음 봉 시가
    assert abs(fill - next_open) < 1e-9, (fill, next_open)
    assert fill != decision_price, '판단 봉 종가로 체결하면 미래를 당겨쓴 것이 된다'
    last_ts = md.timestamps[-1]
    assert md.fill_price('KRW-AAA', last_ts) == md.price('KRW-AAA', last_ts), '마지막 봉은 종가로 정리'
    print('✓ 체결가가 다음 봉 시가다(판단 봉 종가가 아니다)')


def test_take_profit_fires(md):
    """AAA가 2일차에 +20% 오르므로 익절(+10%)이 반드시 한 번은 걸려야 한다."""
    cfg = strategy_config(overrides={
        'TRADE_TAKE_PROFIT_PCT': 10.0, 'TRADE_STOP_LOSS_PCT': 90.0,
        'TRADE_DCA_MAX_COUNT': 0, 'TRADE_MAX_POSITION_KRW': 100_000,
        'TRADE_MAX_CONCURRENT_POSITIONS': 5,
    })
    result = run_backtest(md, cfg, BacktestParams(selection=SELECT_GAINERS, top_n=3, initial_cash=1_000_000))
    sells = [o for o in result.orders if o['decision'] == 'SELL']
    take_profits = [o for o in sells if o['reason'].startswith('take_profit') and o['ticker'] == 'KRW-AAA']
    assert take_profits, [o['reason'] for o in sells]
    assert take_profits[0]['pnl_krw'] > 0, take_profits[0]
    buys = [o for o in result.orders if o['decision'] == 'BUY']
    assert all(o['reason'] == '당일상승률상위' for o in buys), {o['reason'] for o in buys}
    print(f"✓ 익절이 걸린다 ({len(take_profits)}건, 첫 건 +{take_profits[0]['pnl_pct']:.1f}%)")


def test_stop_loss_fires(md):
    """CCC는 내내 내려가므로 트레일링 손절이 걸려야 한다(물타기 없이 돌렸을 때)."""
    cfg = strategy_config(overrides={
        'TRADE_TAKE_PROFIT_PCT': 50.0, 'TRADE_STOP_LOSS_PCT': 3.0,
        'TRADE_DCA_MAX_COUNT': 0, 'TRADE_STOP_LOSS_CONFIRM_CYCLES': 1,
        'TRADE_MAX_POSITION_KRW': 100_000, 'TRADE_MAX_CONCURRENT_POSITIONS': 5,
    })
    result = run_backtest(md, cfg, BacktestParams(selection=SELECT_TRADE_VALUE, top_n=3, initial_cash=1_000_000))
    stops = [o for o in result.orders
             if o['decision'] == 'SELL' and o['reason'].startswith('stop_loss')]
    assert stops, [o['reason'] for o in result.orders if o['decision'] == 'SELL']
    assert any(o['ticker'] == 'KRW-CCC' for o in stops), stops
    print(f"✓ 트레일링 손절이 걸린다 ({len(stops)}건)")


def test_tight_stop_mode_fires(md):
    """짧은 손절 · 긴 수익 모드(docs/auto-trade-tight-stop.md)가 백테스트에서도 그대로 동작하는가 —
    내내 내려가는 CCC는 짧은 손절로 끊기고, 이 모드에서는 물타기가 한 건도 나가지 않아야 한다."""
    cfg = strategy_config(overrides={
        'TRADE_TAKE_PROFIT_PCT': 50.0, 'TRADE_DCA_MAX_COUNT': 2,
        'TRADE_TIGHT_STOP_ENABLED': True, 'TRADE_TIGHT_STOP_INITIAL_PCT': 2.0,
        'TRADE_TIGHT_STOP_ARM_PCT': 5.0, 'TRADE_TIGHT_STOP_TRAIL_PCT': 8.0,
        'TRADE_MAX_POSITION_KRW': 100_000, 'TRADE_MAX_CONCURRENT_POSITIONS': 5,
    })
    result = run_backtest(md, cfg, BacktestParams(selection=SELECT_TRADE_VALUE, top_n=3, initial_cash=1_000_000))
    stops = [o for o in result.orders
             if o['decision'] == 'SELL' and o['reason'].startswith('tight_stop_loss')]
    assert stops, [o['reason'] for o in result.orders if o['decision'] == 'SELL']
    assert any(o['ticker'] == 'KRW-CCC' for o in stops), stops
    # 물타기 최대 횟수를 2회로 줘도 이 모드에서는 추가매수가 나가지 않는다
    assert not [o for o in result.orders if o['decision'] == 'DCA_BUY'], result.orders
    # 짧은 손절이라 손실 폭이 기준(-2%) 근처에서 끊겨야 한다(체결가는 다음 봉 시가라 약간의 여유를 둠)
    assert all(o['pnl_pct'] > -8 for o in stops), stops
    print(f"✓ 짧은 손절이 걸리고 물타기가 없다 ({len(stops)}건, 최악 {min(o['pnl_pct'] for o in stops):.1f}%)")


def test_hold_baseline(md):
    """기준선 — 보유 시간이 차면 손익과 무관하게 청산한다."""
    cfg = strategy_config(overrides={'TRADE_MAX_POSITION_KRW': 100_000, 'TRADE_MAX_CONCURRENT_POSITIONS': 5})
    params = BacktestParams(selection=SELECT_GAINERS, top_n=3, exit_mode=EXIT_MODE_HOLD,
                            hold_hours=3, initial_cash=1_000_000)
    result = run_backtest(md, cfg, params)
    holds = [o for o in result.orders if o['decision'] == 'SELL' and o['reason'].startswith('hold_period')]
    assert holds, [o['reason'] for o in result.orders if o['decision'] == 'SELL']

    buy_at = {}
    for order in result.orders:
        if order['decision'] == 'BUY':
            buy_at[order['ticker']] = order['created_at']
        elif order['decision'] == 'SELL' and order['ticker'] in buy_at:
            fmt = '%Y-%m-%d %H:%M:%S'
            held = (datetime.strptime(order['created_at'], fmt)
                    - datetime.strptime(buy_at.pop(order['ticker']), fmt)).total_seconds() / 3600
            assert held >= 3, (order, held)
    print(f"✓ 기준선이 정해진 시간에 청산한다 ({len(holds)}건)")


def test_report_builds(md):
    """성과 집계가 기존 모듈(trade_performance)로 만들어지고 자산 곡선과 앞뒤가 맞는지."""
    cfg = strategy_config(overrides={
        'TRADE_TAKE_PROFIT_PCT': 10.0, 'TRADE_STOP_LOSS_PCT': 5.0,
        'TRADE_MAX_POSITION_KRW': 100_000, 'TRADE_MAX_CONCURRENT_POSITIONS': 3,
    })
    result = run_backtest(md, cfg, BacktestParams(selection=SELECT_GAINERS, top_n=3, initial_cash=1_000_000))
    report = build_report(result)
    summary = report['performance']['summary']

    assert summary['closed_count'] > 0, summary
    assert summary['open_count'] == 0, '구간 끝에서 모든 포지션이 정리돼야 한다'
    assert report['equity']['mdd_pct'] >= 0
    # 전부 청산됐으므로 최종 자산은 현금과 같아야 한다
    last = result.equity_curve[-1]
    assert abs(last['equity'] - last['cash']) < 1e-6, last
    # 자산 곡선의 증감이 실현손익(수수료 차감 후)과 어긋나지 않는지
    diff = last['equity'] - report['params']['initial_cash']
    assert abs(diff - summary['net_pnl_krw']) < max(1.0, abs(diff) * 0.05), (diff, summary['net_pnl_krw'])
    print(f"✓ 리포트가 만들어진다 (청산 {summary['closed_count']}건, "
          f"승률 {summary['win_rate']:.0f}%, 수익률 {report['equity']['return_pct']:.2f}%)")


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, 'backtest_candles.db')
        day1, day2, day3 = build_fixture(db_path)
        md = MarketData('minutes60', day1, day3 + DAY_SEC, db_path=db_path)
        print(f"목 데이터 — {len(md.markets)}종목, 봉 {len(md.timestamps)}개\n")

        test_ranking_is_point_in_time(md, day2)
        test_trade_value_resets_at_kst_midnight(md, day2)
        test_fill_price_is_next_candle_open(md, day2)
        test_take_profit_fires(md)
        test_stop_loss_fires(md)
        test_tight_stop_mode_fires(md)
        test_hold_baseline(md)
        test_report_builds(md)
        print('\n전부 통과')
