"""수렴 자동 매수(docs/auto-trade-convergence.md) 검증.

    python tests/test_convergence_buy.py

확인하는 것:
  1. 계산 — 5분봉 현재가·구름·MA80·MA120 이격(%)과 위/아래, 일봉 구름·기준선 위 여부
  2. 판정 — 거래대금 미달/스테이블코인/보유·최근 매매/이격 초과/아래/일봉 추세 미충족은 제외, 이격 작은 순으로 한도만큼
  3. DB — 기본값(켜짐, 하루 1종목, 400억, 1.5%), 부분 저장, 오늘 수렴매수 체결·최근 매매 종목 조회
  4. 매매 사이클 — 하루 한도만큼만 사고, 산 코인은 바로 추적 행이 생겨 일반 청산 대상이 되며, 한도가 차면 더 안 훑는다
"""
import os
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import convergence_buy as cb

SETTINGS = {'min_trade_value_24h': 40_000_000_000, 'max_gap_pct': 1.5, 'require_above': True,
            'require_daily_trend': True}


def _df(closes, spread=0.001):
    closes = pd.Series(closes, dtype=float)
    return pd.DataFrame({'open': closes, 'high': closes * (1 + spread), 'low': closes * (1 - spread), 'close': closes})


def _m5_converged(end=100.5):
    """200봉 내내 100 근처에서 옆으로 기다가 마지막 확정 캔들이 살짝 위(end) — 이격 작고 구름·120선 위."""
    return _df([100.0] * 197 + [end, end, end])


def _daily_up():
    return _df([50 + i * 0.6 for i in range(90)])


def _daily_down():
    return _df([100 - i * 0.6 for i in range(90)])


def test_measure():
    m = cb.measure_convergence(_m5_converged())
    assert m and m['above'] and 0 < m['gap_pct'] < 1.5, m
    below = cb.measure_convergence(_m5_converged(end=99.5))
    assert below and not below['above'], below
    wide = cb.measure_convergence(_df([80.0] * 100 + [100.0] * 100))
    assert wide and wide['gap_pct'] > 1.5, wide
    assert cb.measure_convergence(_df([100.0] * 50)) is None, '캔들 부족이면 None'

    assert cb.measure_daily_trend(_daily_up())['ok'] is True
    assert cb.measure_daily_trend(_daily_down())['ok'] is False
    print('✓ 계산: 5분봉 이격·위/아래, 일봉 구름·기준선 위 여부')


def _ticker(market, eok):
    return {'market': market, 'acc_trade_price_24h': eok * 1e8}


def test_scan_and_pick():
    candles = {
        ('KRW-AAA', 'minute5'): _m5_converged(100.3), ('KRW-AAA', 'day'): _daily_up(),   # 통과(이격 작음)
        ('KRW-BBB', 'minute5'): _m5_converged(101.0), ('KRW-BBB', 'day'): _daily_up(),   # 통과(이격 조금 큼)
        ('KRW-CCC', 'minute5'): _m5_converged(99.5), ('KRW-CCC', 'day'): _daily_up(),    # 아래
        ('KRW-DDD', 'minute5'): _m5_converged(100.3), ('KRW-DDD', 'day'): _daily_down(), # 일봉 추세 아래
        ('KRW-EEE', 'minute5'): _df([80.0] * 100 + [100.0] * 100),                        # 이격 큼
    }
    calls = []

    def get_candles(ticker, interval, count):
        calls.append((ticker, interval))
        return candles.get((ticker, interval))

    tickers = [_ticker('KRW-AAA', 500), _ticker('KRW-BBB', 900), _ticker('KRW-CCC', 450), _ticker('KRW-DDD', 600),
               _ticker('KRW-EEE', 700), _ticker('KRW-FFF', 300), _ticker('KRW-USDT', 5000), _ticker('KRW-HELD', 800),
               _ticker('BTC-XRP', 900)]
    rows = cb.scan_market(SETTINGS, {'KRW-HELD': '보유 중'}, fetch_tickers=lambda: tickers, get_candles=get_candles)
    by = {r['ticker']: r for r in rows}
    assert 'KRW-FFF' not in by and 'BTC-XRP' not in by, '거래대금 미달·KRW 외 마켓은 목록에서 뺀다'
    assert by['KRW-AAA']['reason'] is None and by['KRW-BBB']['reason'] is None, rows
    assert '아래' in by['KRW-CCC']['reason'] and '일봉' in by['KRW-DDD']['reason'], rows
    assert '이격' in by['KRW-EEE']['reason'], rows
    assert by['KRW-USDT']['reason'] == '스테이블코인' and by['KRW-HELD']['reason'] == '보유 중', rows
    assert ('KRW-HELD', 'minute5') not in calls and ('KRW-USDT', 'minute5') not in calls, '제외 코인은 캔들을 안 받는다'
    assert ('KRW-CCC', 'day') not in calls and ('KRW-EEE', 'day') not in calls, '5분봉 탈락이면 일봉을 안 받는다'
    assert [r['ticker'] for r in rows[:2]] == ['KRW-AAA', 'KRW-BBB'], '통과가 먼저, 이격 작은 순'

    buys = cb.pick_buys(rows, remaining=1, amount_krw=50_000, cash_balance=1_000_000)
    assert [d.ticker for d in buys] == ['KRW-AAA'] and buys[0].reason.startswith('수렴매수'), buys
    assert len(cb.pick_buys(rows, remaining=2, amount_krw=50_000, cash_balance=1_000_000)) == 2
    assert len(cb.pick_buys(rows, remaining=2, amount_krw=50_000, cash_balance=60_000)) == 1, '현금만큼만'

    loose = {**SETTINGS, 'require_daily_trend': False}
    rows2 = cb.scan_market(loose, {}, fetch_tickers=lambda: tickers, get_candles=get_candles)
    assert {r['ticker'] for r in rows2 if r['reason'] is None} == {'KRW-AAA', 'KRW-BBB', 'KRW-DDD', 'KRW-HELD'} - {'KRW-HELD'}, rows2
    print('✓ 판정: 거래대금·스테이블·보유·이격·아래·일봉 추세로 거르고, 이격 작은 순으로 한도·현금만큼 BUY')


def test_db():
    from app.utils import db_manager
    with tempfile.TemporaryDirectory() as tmp:
        db_manager.DB_PATH = os.path.join(tmp, 'test.db')
        db_manager.init_db()
        s = db_manager.get_convergence_settings()
        assert s['enabled'] is True and s['daily_limit'] == 1 and s['amount_krw'] is None, s
        assert s['min_trade_value_24h'] == 40_000_000_000 and s['max_gap_pct'] == 1.5 and s['require_daily_trend'], s

        db_manager.save_convergence_scan([{'ticker': 'KRW-AAA', 'reason': None}])
        s = db_manager.set_convergence_settings(daily_limit=2, amount_krw=30_000)
        assert s['daily_limit'] == 2 and s['amount_krw'] == 30_000 and s['enabled'] is True, s
        assert s['last_scan'] == [{'ticker': 'KRW-AAA', 'reason': None}], '설정 저장이 검사 결과를 지우지 않는다'
        s = db_manager.set_convergence_settings(amount_krw=None, require_above=False)
        assert s['amount_krw'] is None and s['require_above'] is False and s['daily_limit'] == 2, s

        log = db_manager.save_trade_order_log
        log('upbit', 'live', 'KRW-AAA', 'BUY', reason='수렴매수(이격 0.30%)')
        log('upbit', 'live', 'KRW-BBB', 'SKIP', reason='수렴매수(이격 0.5%) (실패: 잔고 부족)')
        log('upbit', 'live', 'KRW-CCC', 'SELL', reason='손절')
        log('upbit', 'live', 'KRW-DDD', 'HOLD', reason='보유')
        assert db_manager.get_convergence_buys_since('upbit', 'live', '2000-01-01 00:00:00') == ['KRW-AAA']
        assert db_manager.get_recently_traded_tickers('upbit', 'live', '2000-01-01 00:00:00') == {'KRW-AAA', 'KRW-CCC'}
        print('✓ DB: 기본값·부분 저장, 오늘 수렴매수 체결과 최근 매매 종목 조회')


class FakeLiveBroker:
    broker_name, mode = 'upbit', 'live'

    def __init__(self, prices):
        self.holdings = {}
        self.prices = prices
        self.cash = 1_000_000
        self.orders = []

    def get_positions(self):
        from app.core.brokers.base import Position
        return [Position(t, q, a) for t, (q, a) in self.holdings.items()]

    def get_current_price(self, ticker):
        return self.prices.get(ticker)

    def get_cash_balance(self):
        return self.cash

    def buy_market(self, ticker, amount_krw, reason=''):
        from app.core.brokers.base import OrderResult
        price = self.prices[ticker]
        self.holdings[ticker] = (amount_krw / price, price)
        self.cash -= amount_krw
        self.orders.append(('BUY', ticker, reason))
        return OrderResult(True, ticker, 'BUY', price=price, qty=amount_krw / price, amount_krw=amount_krw)

    def sell_market(self, ticker, qty, reason=''):
        from app.core.brokers.base import OrderResult
        self.holdings.pop(ticker, None)
        self.orders.append(('SELL', ticker, reason))
        return OrderResult(True, ticker, 'SELL', price=self.prices[ticker], qty=qty)


def test_trade_cycle():
    from app.utils import db_manager
    from app.core import auto_trader
    auto_trader.send_slack_msg = lambda *a, **k: None
    tickers = [_ticker('KRW-AAA', 500), _ticker('KRW-BBB', 900)]
    candles = {('KRW-AAA', 'minute5'): _m5_converged(100.3), ('KRW-BBB', 'minute5'): _m5_converged(101.0),
               ('KRW-AAA', 'day'): _daily_up(), ('KRW-BBB', 'day'): _daily_up()}
    scans = []
    original_scan = cb.scan_market

    def fake_scan(settings, excluded):
        scans.append(dict(excluded))
        return original_scan(settings, excluded, fetch_tickers=lambda: tickers,
                             get_candles=lambda t, i, c: candles.get((t, i)))

    auto_trader.scan_convergence_market = fake_scan
    with tempfile.TemporaryDirectory() as tmp:
        db_manager.DB_PATH = os.path.join(tmp, 'test.db')
        db_manager.init_db()
        db_manager.set_convergence_settings(amount_krw=50_000)
        broker = FakeLiveBroker({'KRW-AAA': 100.3, 'KRW-BBB': 101.0})

        auto_trader.run_trade_cycle(broker=broker)
        assert [o[:2] for o in broker.orders] == [('BUY', 'KRW-AAA')], broker.orders
        tracked = db_manager.get_paper_position('upbit', 'live', 'KRW-AAA')
        assert tracked is not None, '산 즉시 추적 행이 생겨야 손절/익절이 걸린다'
        assert db_manager.get_convergence_settings()['last_scan'], '검사 결과가 화면용으로 저장된다'

        auto_trader.run_trade_cycle(broker=broker)
        assert len(broker.orders) == 1 and len(scans) == 1, f'하루 1종목 한도가 차면 더 안 훑는다: {broker.orders}'

        # 한도를 늘리면 방금 산 AAA는 "보유 중"으로 빠지고 BBB를 산다
        db_manager.set_convergence_settings(daily_limit=2)
        auto_trader.run_trade_cycle(broker=broker)
        assert [o[:2] for o in broker.orders] == [('BUY', 'KRW-AAA'), ('BUY', 'KRW-BBB')], broker.orders
        assert scans[-1].get('KRW-AAA') in ('보유 중', '최근 7일 안에 매매함'), scans[-1]

        # 산 코인은 일반 청산 규칙을 탄다 — 크게 떨어지면 이 설정에선 물타기 → 손절
        broker.prices['KRW-AAA'] = 50.0
        db_manager.set_convergence_settings(enabled=False)
        auto_trader.run_trade_cycle(broker=broker)
        later = [o for o in broker.orders[2:] if o[1] == 'KRW-AAA']
        assert later and not later[0][2].startswith('수렴매수'), broker.orders
    auto_trader.scan_convergence_market = original_scan
    print(f'✓ 매매 사이클: 하루 한도만큼만 사고, 산 코인은 바로 추적돼 손절 대상이 되며, 한도가 차면 안 훑는다({later[0][2]})')


if __name__ == '__main__':
    test_measure()
    test_scan_and_pick()
    test_db()
    test_trade_cycle()
    print('\n전부 통과')
