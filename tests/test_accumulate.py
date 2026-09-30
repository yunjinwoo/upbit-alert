"""모아가기(docs/auto-trade-accumulate.md) 검증.

등록한 코인(BTC/ETH 등)은 정밀 매수조건과 무관하게 정해둔 금액만큼 사고(코인별 매수 간격에 1번),
자동 매도(손절/익절/RSI/물타기)는 하지 않는다.

    python tests/test_accumulate.py

확인하는 것:
  1. 판단 — 간격이 지났으면 BUY, 간격 대기/현금 부족이면 SKIP(정밀조건은 보지 않는다)
  2. DB — 설정이 저장·복원되고('btc' → 'KRW-BTC'), 모아가기로 체결된 BUY만 마지막 매수 시각으로 잡힌다
  3. 매매 사이클 — 손절 구간인 모아가기 코인을 팔지 않고, 보유 중이어도 또 사며, 끄면 일반 청산 규칙을 탄다
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.trade_strategy import evaluate_accumulation

NOW = datetime(2026, 9, 26, 12, 0, 0)


def _ts(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).strftime('%Y-%m-%d %H:%M:%S')


def _decide(last_buy_hours_ago=None, cash=1_000_000):
    last_buy = {'KRW-BTC': _ts(last_buy_hours_ago)} if last_buy_hours_ago is not None else {}
    decisions = evaluate_accumulation(
        ['KRW-BTC'], cash, 50_000, 24,
        last_buy_at=last_buy, get_price_fn=lambda t: 100_000_000.0, now=NOW,
    )
    return decisions[0]


def test_evaluate_accumulation():
    d = _decide()
    assert d.action == 'BUY' and d.amount_krw == 50_000 and d.reason.startswith('모아가기'), d

    cases = {
        '매수 간격 대기': _decide(last_buy_hours_ago=5),
        '현금 부족': _decide(cash=10_000),
    }
    for keyword, d in cases.items():
        assert d.action == 'SKIP' and keyword in d.reason, (keyword, d)
    assert _decide(last_buy_hours_ago=25).action == 'BUY', '간격이 지나면 다시 산다'

    # 한 사이클에 여러 코인을 사면 현금을 누적 차감한다
    both = evaluate_accumulation(
        ['KRW-BTC', 'KRW-ETH'], 60_000, 50_000, 24,
        last_buy_at={}, get_price_fn=lambda t: 1000.0, now=NOW,
    )
    assert [d.action for d in both] == ['BUY', 'SKIP'], both

    # 수량으로 사는 코인은 수량×현재가(원 올림)를 주문 금액으로 쓴다
    by_qty = evaluate_accumulation(
        ['KRW-BTC', 'KRW-ETH', 'KRW-XRP'], 1_000_000, 50_000, 24, last_buy_at={},
        get_price_fn=lambda t: {'KRW-BTC': 150_000_000.0, 'KRW-ETH': 5_000_000.0, 'KRW-XRP': 1000.0}[t],
        now=NOW, quantities={'KRW-BTC': 0.00005, 'KRW-ETH': 0.002, 'KRW-XRP': 1},
    )
    assert by_qty[0].action == 'BUY' and by_qty[0].amount_krw == 7_500 and by_qty[0].qty == 0.00005, by_qty[0]
    assert by_qty[1].action == 'BUY' and by_qty[1].amount_krw == 10_000, by_qty[1]
    # 5,000원이 안 되면 입력 수량의 마지막 자릿수 단위로 올려 5,000원 이상에서 산다(1 → 1단위 → 5개)
    assert by_qty[2].action == 'BUY' and by_qty[2].qty == 5 and by_qty[2].amount_krw == 5_000, by_qty[2]
    assert '최소 주문금액 맞춤' in by_qty[2].reason and by_qty[2].reason.startswith('모아가기'), by_qty[2]
    assert '맞춤' not in by_qty[0].reason

    from app.core.trade_strategy import accumulate_order_plan
    plan = accumulate_order_plan(80_000_000.0, 10_000, 0.00005)   # 4,000원 → 0.00001씩 올려 0.00007(5,600원)
    assert plan == {'qty': 0.00007, 'amount_krw': 5_600, 'bumped_from': 0.00005}, plan
    plan = accumulate_order_plan(2_000_000.0, 10_000, 0.002)      # 4,000원 → 0.001씩 올려 0.003(6,000원)
    assert plan == {'qty': 0.003, 'amount_krw': 6_000, 'bumped_from': 0.002}, plan
    plan = accumulate_order_plan(1_000_000.0, 10_000, 0.002)      # 딱 5,000원이면 그걸로
    assert plan == {'qty': 0.005, 'amount_krw': 5_000, 'bumped_from': 0.002}, plan
    plan = accumulate_order_plan(150_000_000.0, 10_000, 0.00005)  # 이미 넘으면 그대로
    assert plan == {'qty': 0.00005, 'amount_krw': 7_500, 'bumped_from': None}, plan
    plan = accumulate_order_plan(100_000_000.0, 10_000)           # 금액 코인
    assert plan['amount_krw'] == 10_000 and plan['bumped_from'] is None, plan
    poor = evaluate_accumulation(['KRW-BTC'], 5_000, 50_000, 24, last_buy_at={}, get_price_fn=lambda t: 150_000_000.0,
                                 now=NOW, quantities={'KRW-BTC': 0.00005})
    assert poor[0].action == 'SKIP' and '현금 부족' in poor[0].reason, poor
    print('✓ 판단: 정밀조건 없이 간격마다 BUY, 간격 대기/현금 부족이면 SKIP, 수량 지정 코인은 수량×현재가로 BUY(5,000원 미만이면 수량 올림)')


def test_db_settings_and_last_buy():
    from app.utils import db_manager
    with tempfile.TemporaryDirectory() as tmp:
        db_manager.DB_PATH = os.path.join(tmp, 'test.db')
        db_manager.init_db()

        s = db_manager.get_accumulate_settings()
        assert s['enabled'] is False and s['tickers'] == [], '기본은 꺼짐'
        assert db_manager.get_active_accumulate_tickers() == set()

        saved = db_manager.set_accumulate_settings(tickers='btc, KRW-ETH, BTC', amount_krw=30_000)
        assert saved['tickers'] == ['KRW-BTC', 'KRW-ETH'], saved
        assert db_manager.get_active_accumulate_tickers() == set(), '꺼져 있으면 일반 매매에서 안 뺀다'
        db_manager.set_accumulate_settings(enabled=True)
        again = db_manager.get_accumulate_settings()
        assert again['tickers'] == ['KRW-BTC', 'KRW-ETH'] and again['amount_krw'] == 30_000, '부분 갱신'
        assert db_manager.get_active_accumulate_tickers() == {'KRW-BTC', 'KRW-ETH'}
        assert again['quantities'] == {}

        # 'BTC:0.00005'처럼 수량을 붙이면 그 코인만 수량으로, 다른 필드만 바꿔도 수량은 유지
        db_manager.set_accumulate_settings(tickers='btc:0.00005, ETH')
        db_manager.set_accumulate_settings(amount_krw=20_000)
        q = db_manager.get_accumulate_settings()
        assert q['tickers'] == ['KRW-BTC', 'KRW-ETH'] and q['quantities'] == {'KRW-BTC': 0.00005}, q
        for bad in ('BTC:abc', 'BTC:0'):
            try:
                db_manager.set_accumulate_settings(tickers=bad)
                raise AssertionError(f'{bad}는 거부해야 한다')
            except ValueError:
                pass
        db_manager.set_accumulate_settings(tickers='BTC, ETH')

        log = db_manager.save_trade_order_log
        log('upbit', 'live', 'KRW-BTC', 'BUY', reason='모아가기+정밀조건충족')
        log('upbit', 'live', 'KRW-ETH', 'SKIP', reason='모아가기: 정밀조건 미충족')
        log('upbit', 'live', 'KRW-XRP', 'BUY', reason='breakout_4h')                   # 일반 매수
        log('upbit', 'paper', 'KRW-ETH', 'BUY', reason='모아가기+정밀조건충족')         # 다른 모드
        got = db_manager.get_last_accumulate_buy_times('upbit', 'live')
        assert set(got) == {'KRW-BTC'}, got
        print('✓ DB: 설정 저장·복원(티커 정리), 모아가기로 체결된 BUY만 마지막 매수 시각으로 잡힌다')


class FakeLiveBroker:
    broker_name, mode = 'upbit', 'live'

    def __init__(self, holdings, prices):
        self.holdings = dict(holdings)  # {ticker: (qty, avg)}
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
        qty = amount_krw / price
        q, a = self.holdings.get(ticker, (0, 0))
        self.holdings[ticker] = (q + qty, (q * a + amount_krw) / (q + qty))
        self.cash -= amount_krw
        self.orders.append(('BUY', ticker, reason))
        return OrderResult(True, ticker, 'BUY', price=price, qty=qty, amount_krw=amount_krw)

    def sell_market(self, ticker, qty, reason=''):
        from app.core.brokers.base import OrderResult
        self.holdings.pop(ticker, None)
        self.orders.append(('SELL', ticker, reason))
        return OrderResult(True, ticker, 'SELL', price=self.prices[ticker], qty=qty)


def test_trade_cycle_does_not_sell_and_keeps_buying():
    from app.utils import db_manager
    from app.core import auto_trader
    auto_trader.send_slack_msg = lambda *a, **k: None
    with tempfile.TemporaryDirectory() as tmp:
        db_manager.DB_PATH = os.path.join(tmp, 'test.db')
        db_manager.init_db()
        # 정밀조건을 켜두고 BTC는 미충족으로 캐시 — 모아가기는 그래도 사야 한다
        key = db_manager.get_trade_condition_settings('upbit')[0]['condition_key']
        db_manager.set_trade_condition_setting(key, enabled=True)
        db_manager.save_condition_status('upbit', 'live', 'KRW-BTC', False, {})
        db_manager.set_accumulate_settings(enabled=True, tickers='BTC', amount_krw=50_000, interval_hours=24)

        # BTC를 이미 들고 있고 -30% 손실(평소라면 손절 대상). 승인·추적 중이어도 팔면 안 된다.
        db_manager.set_candidate_approval('upbit', 'live', 'KRW-BTC', True)
        db_manager.upsert_paper_position('upbit', 'live', 'KRW-BTC', 0.001, 100_000_000)
        broker = FakeLiveBroker({'KRW-BTC': (0.001, 100_000_000)}, {'KRW-BTC': 70_000_000})

        auto_trader.run_trade_cycle(broker=broker)
        assert ('SELL', 'KRW-BTC', ) not in [o[:2] for o in broker.orders], broker.orders
        assert broker.orders == [('BUY', 'KRW-BTC', '모아가기(정기 매수)')], broker.orders

        auto_trader.run_trade_cycle(broker=broker)
        assert len(broker.orders) == 1, f'간격(24시간) 안에는 한 번만 산다: {broker.orders}'

        # 모아가기를 끄면 원래대로 일반 청산 규칙(이 설정에선 물타기 → 손절)을 탄다
        db_manager.set_accumulate_settings(enabled=False)
        auto_trader.run_trade_cycle(broker=broker)
        later = broker.orders[1:]
        assert later and not later[0][2].startswith('모아가기'), broker.orders
        print(f'✓ 매매 사이클: 손절 구간 모아가기 코인을 안 팔고 보유 중에도 사며, 간격 안엔 1번만, '
              f'끄면 일반 청산 규칙을 탄다({later[0][2]})')


def test_upbit_live_buy_qty_uses_limit_order():
    """수량 지정 코인은 시장가(원화 금액)가 아니라 매도 1호가 지정가로 수량 그대로 주문한다 —
    시장가는 체결 순간 가격이 움직이면 0.002 대신 0.001999개가 된다."""
    from app.core.brokers import upbit_live_broker as m
    orig = (m.get_trade_engine_settings, m.pyupbit.get_orderbook, m.time.sleep)
    m.get_trade_engine_settings = lambda *a: {'enabled': True}
    ask = {'price': 3_626_000.0}
    m.pyupbit.get_orderbook = lambda t: {'orderbook_units': [{'ask_price': ask['price']}]}
    m.time.sleep = lambda x: None

    class Client:
        def __init__(self, state, vol):
            self.state, self.vol, self.calls = state, vol, []

        def buy_limit_order(self, ticker, price, volume):
            self.calls.append(('limit', price, volume))
            return {'uuid': 'u'}

        def cancel_order(self, uuid):
            self.calls.append('cancel')
            self.state = 'cancel'

        def get_individual_order(self, uuid):
            trades = [{'funds': str(self.vol * 3_626_000)}] if self.vol else []
            return {'state': self.state, 'executed_volume': str(self.vol), 'trades': trades}

    def run(state, vol, qty=0.002):
        b = m.UpbitLiveBroker.__new__(m.UpbitLiveBroker)
        b._client = Client(state, vol)
        b._wait_for_fill = lambda uuid, max_wait_sec=8.0: b._client.get_individual_order(uuid)
        return b.buy_qty('KRW-ETH', qty, 7_252, reason='모아가기(정기 매수)'), b._client.calls

    try:
        r, calls = run('done', 0.002)
        assert r.success and r.qty == 0.002 and calls == [('limit', '3626000', '0.002')], (r, calls)
        ask['price'] = 150_000_000.0
        _, calls = run('done', 0.00007, qty=0.00007)
        assert calls == [('limit', '150000000', '0.00007')], f'지수 표기(7e-05)로 보내면 안 된다: {calls}'
        ask['price'] = 3_626_000.0
        r, calls = run('wait', 0)
        assert not r.success and 'cancel' in calls, '안 채워지면 취소하고 실패로 남긴다(매수 간격이 안 돌게)'
        r, calls = run('wait', 0.001)
        assert r.success and r.qty == 0.001 and 'cancel' in calls, '일부만 채워지면 남은 건 취소, 체결분만 기록'
    finally:
        m.get_trade_engine_settings, m.pyupbit.get_orderbook, m.time.sleep = orig
    print('✓ 실거래 수량 매수: 매도 1호가 지정가로 수량 그대로, 미체결분은 취소')


if __name__ == '__main__':
    test_evaluate_accumulation()
    test_db_settings_and_last_buy()
    test_trade_cycle_does_not_sell_and_keeps_buying()
    test_upbit_live_buy_qty_uses_limit_order()
    print('\n전부 통과')
