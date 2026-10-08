"""잔고 조회 실패 시 실거래 추적 종목이 사라지지 않는지 검증.

    python tests/test_balance_failure.py

예전엔 업비트 잔고 조회가 실패하면(429·네트워크 오류 등) 빈 목록을 돌려줘서
_reconcile_live_positions()가 "전부 팔렸다"로 보고 추적 행을 지웠다. 승인이 꺼진 보유 종목
(강제매수·수렴매수로 산 것, 승인을 나중에 끈 것)은 그 순간 관리 범위에서 영영 빠져 실거래 표에서
사라지고 손절/익절도 멈췄다.

확인하는 것:
  1. 브로커 — 조회 실패/이상 응답이면 get_positions()는 예외, get_raw_balances()/현금은 예전처럼 빈 값
  2. 동기화 — 조회 실패면 추적 행(고점·물타기 기록 포함)을 건드리지 않는다
  3. 매매 사이클 — 조회 실패면 주문 없이 사이클을 건너뛰고, 다음 사이클에 정상 조회되면 그대로 관리한다
"""
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 원격 세션엔 pyupbit가 없거나 임포트가 깨진다 — 이 테스트는 실제 호출을 안 하므로 빈 모듈로 충분하다.
for name in ('pyupbit', 'jwt'):
    if name not in sys.modules:
        stub = types.ModuleType(name)
        stub.get_current_price = lambda *a, **k: None
        stub.get_ohlcv = lambda *a, **k: None
        stub.get_tickers = lambda *a, **k: []
        stub.Upbit = object
        sys.modules[name] = stub

from app.core.brokers.base import BalanceUnavailableError, OrderResult, Position
from app.core.brokers.upbit_live_broker import UpbitLiveBroker


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get_balances(self):
        self.calls += 1
        r = self.responses.pop(0) if self.responses else self.last
        self.last = r
        if isinstance(r, Exception):
            raise r
        return r


def _broker(responses):
    broker = UpbitLiveBroker.__new__(UpbitLiveBroker)  # .env 키 없이 클라이언트만 갈아끼운다
    broker._client = FakeClient(responses)
    return broker


def test_broker():
    import app.core.brokers.upbit_live_broker as ulb
    ulb.time.sleep = lambda *_: None

    ok = [{'currency': 'KRW', 'balance': '1000'},
          {'currency': 'AAA', 'balance': '1', 'locked': '2', 'avg_buy_price': '100', 'unit_currency': 'KRW'}]
    assert _broker([ok]).get_positions() == [Position('KRW-AAA', 3.0, 100.0)]
    # 한 번 실패해도 재시도에서 성공하면 그대로 쓴다
    assert _broker([RuntimeError('429'), ok]).get_positions() == [Position('KRW-AAA', 3.0, 100.0)]

    for bad in (RuntimeError('429 Too Many Requests'), {'error': {'name': 'jwt_verification'}}, None):
        b = _broker([bad])
        try:
            b.get_positions()
            raise AssertionError(f'{bad!r}: 조회 실패인데 예외가 안 났다')
        except BalanceUnavailableError:
            pass
        assert b._client.calls == 2, '한 번 재시도한다'
        assert _broker([bad]).get_raw_balances() == [] and _broker([bad]).get_cash_balance() == 0.0
    assert isinstance(BalanceUnavailableError('x'), RuntimeError), '대시보드 API가 400으로 처리하는 RuntimeError 하위'
    print('✓ 브로커: 조회 실패/이상 응답이면 보유 목록은 예외, 현금·원본 잔고는 예전처럼 빈 값')


class FakeLiveBroker:
    broker_name, mode = 'upbit', 'live'

    def __init__(self):
        self.holdings = {'KRW-AAA': (10.0, 100.0)}
        self.prices = {'KRW-AAA': 100.0}
        self.fail = False
        self.orders = []

    def get_positions(self):
        if self.fail:
            raise BalanceUnavailableError('업비트 잔고 조회 실패')
        return [Position(t, q, a) for t, (q, a) in self.holdings.items()]

    def get_current_price(self, ticker):
        return self.prices.get(ticker)

    def get_cash_balance(self):
        return 0.0 if self.fail else 1_000_000

    def buy_market(self, ticker, amount_krw, reason=''):
        self.orders.append(('BUY', ticker, reason))
        return OrderResult(False, ticker, 'BUY', message='테스트')

    def sell_market(self, ticker, qty, reason=''):
        self.orders.append(('SELL', ticker, reason))
        return OrderResult(False, ticker, 'SELL', message='테스트')


def test_reconcile_and_cycle():
    from app.utils import db_manager
    from app.core import auto_trader
    auto_trader.send_slack_msg = lambda *a, **k: None
    with tempfile.TemporaryDirectory() as tmp:
        db_manager.DB_PATH = os.path.join(tmp, 'test.db')
        db_manager.init_db()
        db_manager.set_convergence_settings(enabled=False)
        # 승인 안 된(강제매수로 산) 추적 종목 — 추적 행만이 관리 범위를 붙잡고 있다
        db_manager.upsert_paper_position('upbit', 'live', 'KRW-AAA', 10.0, 100.0, peak_price=130.0)
        broker = FakeLiveBroker()

        broker.fail = True
        try:
            auto_trader._reconcile_live_positions(broker)
            raise AssertionError('조회 실패인데 동기화가 그냥 지나갔다')
        except BalanceUnavailableError:
            pass
        row = db_manager.get_paper_position('upbit', 'live', 'KRW-AAA')
        assert row is not None and row['peak_price'] == 130.0, f'추적 행이 지워지거나 초기화됐다: {row}'

        try:
            auto_trader.run_trade_cycle(broker=broker, trigger_type='auto_live')
            raise AssertionError('조회 실패인데 사이클이 진행됐다')
        except BalanceUnavailableError:
            pass
        assert broker.orders == [], broker.orders
        assert db_manager.get_paper_position('upbit', 'live', 'KRW-AAA') is not None

        # 다음 사이클에 정상 조회되면 계속 관리된다(고점 기록 유지)
        broker.fail = False
        auto_trader._reconcile_live_positions(broker)
        row = db_manager.get_paper_position('upbit', 'live', 'KRW-AAA')
        assert row is not None and row['peak_price'] == 130.0, row

        # 진짜로 다 팔린 경우(조회 성공 + 잔고 없음)는 예전처럼 추적 행을 지운다
        broker.holdings = {}
        auto_trader._reconcile_live_positions(broker)
        assert db_manager.get_paper_position('upbit', 'live', 'KRW-AAA') is None
    print('✓ 동기화/사이클: 조회 실패면 추적 행을 그대로 두고 주문 없이 건너뛴다, 실제 전량 매도는 예전처럼 정리')


if __name__ == '__main__':
    test_broker()
    test_reconcile_and_cycle()
    print('\n전부 통과')
