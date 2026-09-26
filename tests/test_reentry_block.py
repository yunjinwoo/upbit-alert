"""매도 후 재매수 대기(reentry_block_hours) 검증.

판 종목이 스크리닝 후보·실거래 승인 상태로 남아 있으면 다음 사이클(5분 뒤)에 곧바로 다시 사서
또 손절하는 반복이 생긴다. 이 옵션은 판 종목을 정해진 시간 동안 신규 매수에서 뺀다(0=꺼짐).

    python tests/test_reentry_block.py

확인하는 것:
  1. 꺼져 있으면(0) 방금 판 종목도 예전처럼 산다 — 기본 동작이 바뀌지 않는다
  2. 켜져 있으면 대기 시간 안에 판 종목은 SKIP, 지나면 다시 BUY
  3. 백테스트에서 계속 떨어지는 종목을 손절→재매수 반복하던 게 대기 시간 동안 멈춘다
  4. DB — 마지막 매도 시각 조회가 체결된 SELL만 보고, 설정이 저장·복원된다
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.trade_strategy import evaluate_entries


def _cfg(block_hours):
    return SimpleNamespace(
        TRADE_MAX_POSITION_KRW=100_000, TRADE_MAX_CONCURRENT_POSITIONS=5,
        TRADE_REENTRY_BLOCK_HOURS=block_hours,
    )


def _entries(block_hours, sold_hours_ago):
    now = datetime(2026, 9, 26, 12, 0, 0)
    sold_at = (now - timedelta(hours=sold_hours_ago)).strftime('%Y-%m-%d %H:%M:%S')
    decisions = evaluate_entries(
        [{'ticker': 'KRW-AAA'}, {'ticker': 'KRW-BBB'}], [], 1_000_000, lambda t: 1000.0, _cfg(block_hours),
        last_sell_at={'KRW-AAA': sold_at}, now=now,
    )
    return {d.ticker: d for d in decisions}


def test_off_keeps_old_behavior():
    d = _entries(block_hours=0, sold_hours_ago=0.1)
    assert d['KRW-AAA'].action == 'BUY', d['KRW-AAA']
    # cfg에 속성 자체가 없어도(토스 등 아직 안 넘기는 호출부) 예전과 같아야 한다
    cfg = SimpleNamespace(TRADE_MAX_POSITION_KRW=100_000, TRADE_MAX_CONCURRENT_POSITIONS=5)
    old = evaluate_entries([{'ticker': 'KRW-AAA'}], [], 1_000_000, lambda t: 1000.0, cfg)
    assert old[0].action == 'BUY', old
    print('✓ 꺼져 있으면(0) 방금 판 종목도 예전처럼 산다')


def test_on_blocks_within_window():
    d = _entries(block_hours=6, sold_hours_ago=2)
    assert d['KRW-AAA'].action == 'SKIP', d['KRW-AAA']
    assert '재매수 대기' in d['KRW-AAA'].reason and '2.0/6시간' in d['KRW-AAA'].reason, d['KRW-AAA'].reason
    assert d['KRW-BBB'].action == 'BUY', '안 판 종목은 영향 없음'

    d = _entries(block_hours=6, sold_hours_ago=7)
    assert d['KRW-AAA'].action == 'BUY', '대기 시간이 지나면 다시 산다'
    print(f"✓ 대기 시간 안에 판 종목은 SKIP ({_entries(6, 2)['KRW-AAA'].reason}), 지나면 BUY")


def test_backtest_stops_rebuy_loop():
    from test_backtest_engine import build_fixture, DAY_SEC
    from app.backtest.engine import BacktestParams, run_backtest
    from app.backtest.market_data import MarketData, SELECT_TRADE_VALUE
    from app.backtest.strategy_config import strategy_config

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, 'backtest_candles.db')
        day1, day2, day3 = build_fixture(db_path)
        md = MarketData('minutes60', day1, day3 + DAY_SEC, db_path=db_path)

        def ccc_buys(block_hours):
            # CCC는 3일 내내 하락 — 손절 2%면 사고 → 손절 → 다음 봉에 또 사는 걸 반복한다
            cfg = strategy_config(overrides={
                'TRADE_TAKE_PROFIT_PCT': 50.0, 'TRADE_STOP_LOSS_PCT': 2.0,
                'TRADE_DCA_MAX_COUNT': 0, 'TRADE_MAX_POSITION_KRW': 100_000,
                'TRADE_MAX_CONCURRENT_POSITIONS': 5, 'TRADE_REENTRY_BLOCK_HOURS': block_hours,
            })
            result = run_backtest(md, cfg, BacktestParams(selection=SELECT_TRADE_VALUE, top_n=3,
                                                          initial_cash=1_000_000))
            return [o for o in result.orders if o['ticker'] == 'KRW-CCC' and o['decision'] == 'BUY']

        loop = ccc_buys(0)
        blocked = ccc_buys(24)
        assert len(loop) >= 3, f'꺼져 있으면 재매수 반복이 나와야 테스트가 의미 있다: {len(loop)}'
        assert len(blocked) < len(loop), (len(blocked), len(loop))
        # 켜져 있을 때 연속된 매수 사이 간격은 24시간 이상이어야 한다(그 사이에 매도가 있었으므로)
        times = [datetime.strptime(o['created_at'], '%Y-%m-%d %H:%M:%S') for o in blocked]
        for a, b in zip(times, times[1:]):
            assert (b - a).total_seconds() / 3600 >= 24, (a, b)
        print(f"✓ 백테스트: 하락 종목 재매수 {len(loop)}회 → {len(blocked)}회 (24시간 대기)")


def test_db_last_sell_times_and_settings():
    from app.utils import db_manager
    with tempfile.TemporaryDirectory() as tmp:
        db_manager.DB_PATH = os.path.join(tmp, 'test.db')
        db_manager.init_db()

        assert db_manager.get_trade_strategy_settings()['reentry_block_hours'] == 0, '기본은 꺼짐'
        saved = db_manager.set_trade_strategy_settings(reentry_block_hours=12)
        assert saved['reentry_block_hours'] == 12
        again = db_manager.get_trade_strategy_settings()
        assert again['reentry_block_hours'] == 12, again
        assert again['stop_loss_pct'] == saved['stop_loss_pct'], '다른 값은 그대로'
        # 다른 필드만 저장해도 대기 시간이 유지돼야 한다(부분 갱신)
        db_manager.set_trade_strategy_settings(take_profit_pct=7.0)
        assert db_manager.get_trade_strategy_settings()['reentry_block_hours'] == 12

        log = db_manager.save_trade_order_log
        log('upbit', 'live', 'KRW-AAA', 'SELL', reason='stop_loss')
        log('upbit', 'live', 'KRW-BBB', 'SKIP', reason='stop_loss (실패: 잔고 부족)')   # 실패한 매도
        log('upbit', 'live', 'KRW-CCC', 'BUY')
        log('upbit', 'paper', 'KRW-DDD', 'SELL')                                         # 다른 모드
        since = (datetime.now() - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
        got = db_manager.get_last_sell_times('upbit', 'live', since)
        assert set(got) == {'KRW-AAA'}, got
        future = (datetime.now() + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
        assert db_manager.get_last_sell_times('upbit', 'live', future) == {}, '대기 시간 밖 매도는 안 잡힌다'
        print('✓ DB: 체결된 SELL만 마지막 매도 시각으로 잡히고, 설정이 저장·복원된다')


if __name__ == '__main__':
    test_off_keeps_old_behavior()
    test_on_blocks_within_window()
    test_backtest_stops_rebuy_loop()
    test_db_last_sell_times_and_settings()
    print('\n전부 통과')
