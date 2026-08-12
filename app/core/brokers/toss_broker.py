"""토스증권 모의매매(paper trading) 브로커.

app/core/brokers/paper_broker.py(업비트)와 완전히 동일한 구조 — 실주문을 절대 보내지 않는다.
POST /api/v1/orders 등 주문 관련 엔드포인트를 호출하는 코드는 이 파일에도, app/core/toss_client.py에도
없다. 시세만 토스 Open API(읽기 전용, OAuth 토큰은 필요하지만 실주문 권한과 무관한 시세 조회 API)로
조회하고, 잔고/포지션은 app/utils/db_manager.py의 가상 원장(broker='toss')으로 시뮬레이션한다.
"""
from typing import List, Optional

from app.config import Config
from app.utils.logger import get_logger
from app.core.brokers.base import BrokerClient, OrderResult, Position
from app.core.toss_client import get_current_price as toss_get_current_price
from app.utils.db_manager import (
    get_or_create_paper_account,
    update_paper_account_cash,
    get_paper_positions,
    get_paper_position,
    upsert_paper_position,
    delete_paper_position,
)

logger = get_logger()


class TossBroker(BrokerClient):
    broker_name = "toss"
    mode = "paper"

    def __init__(self):
        # 계좌가 없으면 초기 가상 자본으로 생성 (있으면 그대로 둠 — 초기화 아님)
        get_or_create_paper_account(self.broker_name, self.mode, Config.TRADE_INITIAL_CASH_KRW)

    def get_current_price(self, ticker: str) -> Optional[float]:
        try:
            price = toss_get_current_price(ticker)
            return float(price) if price else None
        except Exception as e:
            logger.error(f"[{ticker}] 시세 조회 실패: {e}")
            return None

    def get_cash_balance(self) -> float:
        account = get_or_create_paper_account(self.broker_name, self.mode, Config.TRADE_INITIAL_CASH_KRW)
        return account['cash_balance']

    def get_positions(self) -> List[Position]:
        rows = get_paper_positions(self.broker_name, self.mode)
        return [Position(ticker=r['ticker'], qty=r['qty'], avg_buy_price=r['avg_buy_price']) for r in rows]

    def buy_market(self, ticker: str, amount_krw: float, reason: str = "") -> OrderResult:
        price = self.get_current_price(ticker)
        if not price:
            return OrderResult(False, ticker, 'BUY', message="시세 조회 실패")

        cash = self.get_cash_balance()
        if cash < amount_krw:
            return OrderResult(False, ticker, 'BUY', message=f"가상 잔고 부족(보유 {cash:,.0f}원 < 필요 {amount_krw:,.0f}원)")

        qty = amount_krw / price
        existing = get_paper_position(self.broker_name, self.mode, ticker)
        if existing:
            total_qty = existing['qty'] + qty
            new_avg_price = (existing['qty'] * existing['avg_buy_price'] + amount_krw) / total_qty
            upsert_paper_position(self.broker_name, self.mode, ticker, total_qty, new_avg_price)
        else:
            upsert_paper_position(self.broker_name, self.mode, ticker, qty, price)

        update_paper_account_cash(self.broker_name, self.mode, cash - amount_krw)
        logger.info(f"[토스 모의매수] {ticker} {qty:.4f}주 @ {price:,.0f}원 (총 {amount_krw:,.0f}원) — {reason}")
        return OrderResult(True, ticker, 'BUY', price=price, qty=qty, amount_krw=amount_krw, message=reason)

    def sell_market(self, ticker: str, qty: float, reason: str = "") -> OrderResult:
        price = self.get_current_price(ticker)
        if not price:
            return OrderResult(False, ticker, 'SELL', message="시세 조회 실패")

        position = get_paper_position(self.broker_name, self.mode, ticker)
        if not position or position['qty'] < qty - 1e-12:
            return OrderResult(False, ticker, 'SELL', message="보유 수량 부족")

        amount_krw = qty * price
        remaining_qty = position['qty'] - qty
        if remaining_qty <= 1e-12:
            delete_paper_position(self.broker_name, self.mode, ticker)
        else:
            upsert_paper_position(self.broker_name, self.mode, ticker, remaining_qty, position['avg_buy_price'])

        cash = self.get_cash_balance()
        update_paper_account_cash(self.broker_name, self.mode, cash + amount_krw)
        logger.info(f"[토스 모의매도] {ticker} {qty:.4f}주 @ {price:,.0f}원 (총 {amount_krw:,.0f}원) — {reason}")
        return OrderResult(True, ticker, 'SELL', price=price, qty=qty, amount_krw=amount_krw, message=reason)
