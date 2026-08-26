"""토스증권 실계좌 브로커 — 실주문 가능. 매매 대상/실행 on-off는 전부 화면(DB)에서 제어한다.

app/core/brokers/upbit_live_broker.py와 완전히 같은 안전 원칙 — "실거래 실행" 스위치와
"실거래 승인" 체크박스(둘 다 DB, 기본값 꺼짐)를 모두 통과해야 실제로 주문이 나간다.

업비트와 다른 점(★ = 이 파일에서만 신경 쓸 부분):
- ★ 인증: OAuth2 client_credentials(app/core/toss_client.py) — 계좌 컨텍스트가 필요한 API는
  X-Tossinvest-Account 헤더에 accountSeq를 넣어야 하는데, 이건 GET /api/v1/accounts로 최초
  1회 조회해서 캐시한다(계좌를 새로 만들지 않는 한 안 바뀜).
- ★ 수량 단위: 코인처럼 소수점 매수가 안 된다 — 시장가 매수는 "1종목당 매수금액 // 현재가"로
  정수 주(株) 수를 구하고, 그 결과가 0이면(현재가가 매수금액보다 비싸면) 주문을 아예 안 낸다.
- ★ 체결 확인: 국내 주식 시장가 주문은 업비트 코인처럼 "낸 즉시 100% 체결"이 보장되지 않는다
  (호가창 물량에 따라 부분체결/미체결 가능). 주문 생성 응답엔 orderId만 오므로, 별도로
  GET /api/v1/orders/{orderId}를 폴링해 execution(filledQuantity/averageFilledPrice)을 확인한다.
- ★ 매수 대상 통화: 이 봇의 스크리닝/전략 설정(1종목당 매수금액 등)이 전부 원화(KRW) 기준이라,
  보유 종목도 get_holdings()에서 currency == 'KRW'(국내 상장 종목)만 다룬다 — 계좌가 해외주식을
  같이 보유하고 있어도(예: 미국 ETF) 이 봇 관리 대상에서는 제외한다(대시보드에도 안 뜸).

자세한 배경은 docs/auto-trade-toss-live.md 참고.
"""
import time
from typing import List, Optional

from app.config import Config
from app.utils.logger import get_logger
from app.utils.db_manager import get_trade_engine_settings
from app.core.brokers.base import BrokerClient, OrderResult, Position
from app.core import toss_client

logger = get_logger()

# 국내 주식은 최소 1주 단위로만 주문 가능 — 업비트의 MIN_ORDER_KRW(거래소 최소 주문금액)에 대응하는
# 개념이지만, 여기선 "그 금액으로 최소 1주도 못 산다"는 형태로 buy_market 안에서 직접 계산한다.
MIN_ORDER_SHARES = 1

# 착오주문 방지 — 이 값 이상이면 주문 바디에 confirmHighValueOrder=true를 명시해야 한다(토스 API 요구사항).
HIGH_VALUE_ORDER_KRW = 100_000_000


class TossLiveBroker(BrokerClient):
    broker_name = "toss"
    mode = "live"

    def __init__(self):
        if not Config.TOSS_CLIENT_ID or not Config.TOSS_CLIENT_SECRET:
            raise RuntimeError(".env에 TOSS_CLIENT_ID/TOSS_CLIENT_SECRET이 설정되어 있지 않습니다.")
        accounts = toss_client.get_accounts()
        if not accounts:
            raise RuntimeError("토스 계좌 조회에 실패했습니다(자격증명/IP 허용 목록을 확인하세요).")
        # 계좌가 여러 개면(위탁/연금 등) 첫 번째를 쓴다 — 지금은 단일 계좌 사용을 가정.
        self._account_seq = accounts[0]["accountSeq"]
        self._account_no = accounts[0].get("accountNo")

    def get_current_price(self, ticker: str) -> Optional[float]:
        """app/core/brokers/toss_broker.py(모의)와 동일한 재시도 로직 — 토스 Open API의 일시적
        오류(레이트리밋/네트워크 순간 오류)에 대비해 최대 2회까지 짧게 재시도한다."""
        last_error = None
        for attempt in range(2):
            try:
                price = toss_client.get_current_price(ticker)
                if price:
                    return float(price)
                logger.warning(f"[{ticker}] 시세 조회 결과가 비어있음 (attempt={attempt + 1})")
            except Exception as e:
                last_error = e
                logger.error(f"[{ticker}] 시세 조회 실패 (attempt={attempt + 1}): {e}")
            if attempt == 0:
                time.sleep(0.3)
        if last_error:
            logger.error(f"[{ticker}] 시세 조회 최종 실패: {last_error}")
        return None

    def get_cash_balance(self) -> float:
        """현금 기반 매수 가능 금액(KRW) — 토스 Open API엔 별도 "예수금" 엔드포인트가 없어
        buying-power(cashBuyingPower)를 그대로 쓴다. 업비트의 get_cash_balance와 동일한 역할."""
        power = toss_client.get_buying_power(self._account_seq, "KRW")
        return power or 0.0

    def get_positions(self) -> List[Position]:
        """국내(KRW) 보유 종목만 반환한다 — 해외주식은 이 봇의 관리 대상이 아니다(클래스 docstring
        참고). 수량 0(전량 매도됨) 종목은 제외."""
        holdings = toss_client.get_holdings(self._account_seq)
        if not holdings:
            return []
        positions = []
        for item in holdings.get("items") or []:
            if item.get("currency") != "KRW":
                continue
            try:
                qty = float(item["quantity"])
                avg_price = float(item["averagePurchasePrice"])
            except (KeyError, TypeError, ValueError):
                continue
            if qty <= 0:
                continue
            positions.append(Position(ticker=item["symbol"], qty=qty, avg_buy_price=avg_price))
        return positions

    def _blocked_reason(self) -> Optional[str]:
        if not get_trade_engine_settings(self.broker_name, self.mode)["enabled"]:
            return "실거래 스위치가 꺼져 있습니다 — 대시보드의 '실거래 실행' 스위치를 켜야 주문이 나갑니다."
        return None

    def _wait_for_fill(self, order_id: str, max_wait_sec: float = 8.0, poll_interval: float = 0.4) -> Optional[dict]:
        """시장가 주문이라도 국내 주식은 호가창 물량에 따라 즉시 전량 체결이 보장되지 않는다.
        상태가 종료 상태(FILLED/CANCELED/REJECTED 등)가 될 때까지 최대 max_wait_sec 폴링하고,
        시간 내 못 끝나면 마지막으로 조회된 상태를 그대로 반환한다(주문 자체는 이미 나간 상태이므로
        호출부는 이걸 실패로 취급하지 않는다 — 부분체결분만 반영하고 나머지는 다음 사이클의
        실제 잔고 재조회로 정정됨)."""
        deadline = time.time() + max_wait_sec
        terminal_statuses = {"FILLED", "CANCELED", "REJECTED", "CANCEL_REJECTED", "REPLACE_REJECTED"}
        order = None
        while time.time() < deadline:
            try:
                order = toss_client.get_order(self._account_seq, order_id)
            except Exception as e:
                logger.error(f"주문 상태 조회 실패(orderId={order_id}): {e}")
                order = None
            if isinstance(order, dict) and order.get("status") in terminal_statuses:
                return order
            time.sleep(poll_interval)
        return order

    @staticmethod
    def _extract_fill(order: Optional[dict]):
        """주문 조회 결과에서 (평균 체결가, 체결 수량)을 뽑는다. 미체결/조회 실패면 (None, None)."""
        if not isinstance(order, dict):
            return None, None
        execution = order.get("execution") or {}
        try:
            filled_qty = float(execution.get("filledQuantity") or 0)
        except (TypeError, ValueError):
            filled_qty = 0.0
        if filled_qty <= 0:
            return None, None
        try:
            avg_price = float(execution["averageFilledPrice"])
        except (KeyError, TypeError, ValueError):
            return None, None
        return avg_price, filled_qty

    def buy_market(self, ticker: str, amount_krw: float, reason: str = "") -> OrderResult:
        blocked = self._blocked_reason()
        if blocked:
            logger.warning(f"[실거래 매수 차단] {ticker} — {blocked}")
            return OrderResult(False, ticker, "BUY", message=blocked)

        price = self.get_current_price(ticker)
        if not price:
            return OrderResult(False, ticker, "BUY", message="시세 조회 실패")

        shares = int(amount_krw // price)
        if shares < MIN_ORDER_SHARES:
            message = f"매수금액 {amount_krw:,.0f}원으로 최소 {MIN_ORDER_SHARES}주(현재가 {price:,.0f}원)를 살 수 없음"
            logger.warning(f"[실거래 매수 차단] {ticker} — {message}")
            return OrderResult(False, ticker, "BUY", message=message)

        order_id = toss_client.create_order(
            self._account_seq, symbol=ticker, side="BUY", quantity=shares,
            confirm_high_value_order=(shares * price >= HIGH_VALUE_ORDER_KRW),
        )
        if not order_id:
            logger.error(f"[실매수] {ticker} 주문 거부/실패")
            return OrderResult(False, ticker, "BUY", message="주문 거부/실패")

        filled = self._wait_for_fill(order_id)
        fill_price, filled_qty = self._extract_fill(filled)
        if not filled_qty:
            status = (filled or {}).get("status", "확인불가")
            logger.warning(f"[실매수] {ticker} 주문 접수됐지만 체결 확인 지연(status={status}) — orderId={order_id} (다음 사이클에 실제 잔고로 반영됨)")
            # qty는 아직 확정 체결 수량이 아니라 "요청한" 수량이므로 감사로그에 확정 체결처럼
            # 남기지 않는다(app/core/brokers/upbit_live_broker.py의 buy_market과 동일한 관례 —
            # amount_krw만 남기고 qty/price는 비워 다음 사이클의 실제 잔고 반영을 기다린다).
            return OrderResult(True, ticker, "BUY", amount_krw=shares * price, message=reason)

        logger.info(f"[실매수] {ticker} {filled_qty:.0f}주 @ {fill_price:,.0f}원 (총 {filled_qty * fill_price:,.0f}원) — {reason}")
        return OrderResult(True, ticker, "BUY", price=fill_price, qty=filled_qty, amount_krw=filled_qty * fill_price, message=reason)

    def sell_market(self, ticker: str, qty: float, reason: str = "") -> OrderResult:
        blocked = self._blocked_reason()
        if blocked:
            logger.warning(f"[실거래 매도 차단] {ticker} — {blocked}")
            return OrderResult(False, ticker, "SELL", message=blocked)

        shares = int(qty)  # 국내 주식은 정수 주 단위만 — 실제 보유수량을 넘지 않도록 내림
        if shares < MIN_ORDER_SHARES:
            message = f"매도 가능 수량이 {MIN_ORDER_SHARES}주 미만(보유 {qty})"
            logger.warning(f"[실거래 매도 차단] {ticker} — {message}")
            return OrderResult(False, ticker, "SELL", message=message)

        # buy_market과 동일하게 고액 주문 확인 플래그를 계산한다 — 매수만 큰 금액이 되는 게 아니라
        # 많은 수량을 들고 있던 종목을 손절/익절 매도할 때도 똑같이 HIGH_VALUE_ORDER_KRW를 넘을 수
        # 있고, Toss API가 매도에도 이 플래그를 요구한다면 안 넘길 경우 정작 팔아야 할 때 거부당한다.
        price = self.get_current_price(ticker)
        confirm_high_value_order = bool(price and shares * price >= HIGH_VALUE_ORDER_KRW)

        order_id = toss_client.create_order(
            self._account_seq, symbol=ticker, side="SELL", quantity=shares,
            confirm_high_value_order=confirm_high_value_order,
        )
        if not order_id:
            logger.error(f"[실매도] {ticker} 주문 거부/실패")
            return OrderResult(False, ticker, "SELL", message="주문 거부/실패")

        filled = self._wait_for_fill(order_id)
        fill_price, filled_qty = self._extract_fill(filled)
        if not filled_qty:
            status = (filled or {}).get("status", "확인불가")
            logger.warning(f"[실매도] {ticker} 주문 접수됐지만 체결 확인 지연(status={status}) — orderId={order_id} (다음 사이클에 실제 잔고로 반영됨)")
            return OrderResult(True, ticker, "SELL", qty=shares, message=reason)

        logger.info(f"[실매도] {ticker} {filled_qty:.0f}주 @ {fill_price:,.0f}원 (총 {filled_qty * fill_price:,.0f}원) — {reason}")
        return OrderResult(True, ticker, "SELL", price=fill_price, qty=filled_qty, amount_krw=filled_qty * fill_price, message=reason)
