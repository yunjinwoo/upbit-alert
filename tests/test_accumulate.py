"""모아가기(docs/auto-trade-accumulate.md) 검증.

등록한 코인(BTC/ETH 등)은 정밀 매수조건을 통과하면 정해둔 금액만큼 사고(코인별 매수 간격에 1번),
자동 매도(손절/익절/RSI/물타기)는 하지 않는다.

    python tests/test_accumulate.py

확인하는 것:
  1. 판단 — 조건 통과면 BUY, 조건 꺼짐/결과 없음/오래됨/미충족/간격 대기/현금 부족이면 SKIP
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


def _decide(status=None, conditions_enabled=True, last_buy_hours_ago=None, cash=1_000_000, checked_min_ago=1):
    status_map = {}
    if status is not None:
        status_map['KRW-BTC'] = {'passed': status, 'checked_at': _ts(checked_min_ago / 60)}
    last_buy = {'KRW-BTC': _ts(last_buy_hours_ago)} if last_buy_hours_ago is not None else {}
    decisions = evaluate_accumulation(
        ['KRW-BTC'], cash, 50_000, 24, conditions_enabled=conditions_enabled,
        condition_status_map=status_map, last_buy_at=last_buy, get_price_fn=lambda t: 100_000_000.0, now=NOW,
    )
    return decisions[0]


def test_evaluate_accumulation():
    d = _decide(status=True)
    assert d.action == 'BUY' and d.amount_krw == 50_000 and d.reason.startswith('모아가기'), d

    cases = {
        '켜진 정밀조건 없음': _decide(status=True, conditions_enabled=False),
        '검사 결과 없음': _decide(status=None),
        '오래됨': _decide(status=True, checked_min_ago=30),
        '미충족': _decide(status=False),
        '매수 간격 대기': _decide(status=True, last_buy_hours_ago=5),
        '현금 부족': _decide(status=True, cash=10_000),
    }
    for keyword, d in cases.items():
        assert d.action == 'SKIP' and keyword in d.reason, (keyword, d)
    assert _decide(status=True, last_buy_hours_ago=25).action == 'BUY', '간격이 지나면 다시 산다'

    # 한 사이클에 여러 코인을 사면 현금을 누적 차감한다
    both = evaluate_accumulation(
        ['KRW-BTC', 'KRW-ETH'], 60_000, 50_000, 24, conditions_enabled=True,
        condition_status_map={t: {'passed': True, 'checked_at': _ts(0)} for t in ('KRW-BTC', 'KRW-ETH')},
        last_buy_at={}, get_price_fn=lambda t: 1000.0, now=NOW,
    )
    assert [d.action for d in both] == ['BUY', 'SKIP'], both
    print('✓ 판단: 조건 통과면 BUY, 조건 꺼짐/결과 없음/오래됨/미충족/간격 대기/현금 부족이면 SKIP')


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
        # 조건 1개 켜고, BTC 검사 결과는 통과로 캐시
        key = db_manager.get_trade_condition_settings('upbit')[0]['condition_key']
        db_manager.set_trade_condition_setting(key, enabled=True)
        db_manager.save_condition_status('upbit', 'live', 'KRW-BTC', True, {})
        db_manager.set_accumulate_settings(enabled=True, tickers='BTC', amount_krw=50_000, interval_hours=24)

        # BTC를 이미 들고 있고 -30% 손실(평소라면 손절 대상). 승인·추적 중이어도 팔면 안 된다.
        db_manager.set_candidate_approval('upbit', 'live', 'KRW-BTC', True)
        db_manager.upsert_paper_position('upbit', 'live', 'KRW-BTC', 0.001, 100_000_000)
        broker = FakeLiveBroker({'KRW-BTC': (0.001, 100_000_000)}, {'KRW-BTC': 70_000_000})

        auto_trader.run_trade_cycle(broker=broker)
        assert ('SELL', 'KRW-BTC', ) not in [o[:2] for o in broker.orders], broker.orders
        assert broker.orders == [('BUY', 'KRW-BTC', '모아가기+정밀조건충족')], broker.orders

        auto_trader.run_trade_cycle(broker=broker)
        assert len(broker.orders) == 1, f'간격(24시간) 안에는 한 번만 산다: {broker.orders}'

        # 모아가기를 끄면 원래대로 일반 청산 규칙(이 설정에선 물타기 → 손절)을 탄다
        db_manager.set_accumulate_settings(enabled=False)
        auto_trader.run_trade_cycle(broker=broker)
        later = broker.orders[1:]
        assert later and not later[0][2].startswith('모아가기'), broker.orders
        print(f'✓ 매매 사이클: 손절 구간 모아가기 코인을 안 팔고 보유 중에도 사며, 간격 안엔 1번만, '
              f'끄면 일반 청산 규칙을 탄다({later[0][2]})')


if __name__ == '__main__':
    test_evaluate_accumulation()
    test_db_settings_and_last_buy()
    test_trade_cycle_does_not_sell_and_keeps_buying()
    print('\n전부 통과')
