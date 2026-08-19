"""업비트 실계좌 브로커 — 실주문 가능. 매매 대상/실행 on-off는 전부 화면(DB)에서 제어한다.

업비트 잔고 API(pyupbit.Upbit.get_balances)는 계좌 전체 자산을 반환한다. 종목 단위로 API 호출
자체를 제한하는 기능은 없으므로, 응답을 받은 뒤 애플리케이션에서 필요한 코인만 남긴다.
매매 대상 코인은 .env가 아니라 대시보드의 "실거래 승인" 체크박스(trade_candidate_approval,
mode='live')로 관리한다 — app/core/auto_trader.py의 get_approved_candidate_tickers 참고.

매수/매도는 다음 두 조건을 모두 만족해야 실제로 나간다:
1) 대시보드의 "실거래 실행" 스위치가 켜져 있을 것 (trade_engine_settings, broker='upbit', mode='live'
   — DB 저장, 기본값 꺼짐. app/utils/db_manager.get_trade_engine_settings 참고)
2) 주문 금액/수량이 업비트 최소 주문금액(MIN_ORDER_KRW) 이상일 것

자세한 배경은 docs/auto-trade-upbit-live.md 참고.
"""
import time
from typing import List, Optional

import pyupbit

from app.config import Config
from app.utils.logger import get_logger
from app.utils.db_manager import get_trade_engine_settings
from app.core.brokers.base import BrokerClient, OrderResult, Position

logger = get_logger()

# 업비트 시장가 주문 최소 금액(원) — 이보다 작은 금액/평가액의 주문은 거래소에서 어차피 거부되므로
# 미리 걸러서 불필요한 API 호출과 애매한 실패 메시지를 막는다.
MIN_ORDER_KRW = 5000


class UpbitLiveBroker(BrokerClient):
    broker_name = "upbit"
    mode = "live"

    def __init__(self):
        if not Config.UPBIT_ACCESS_KEY or not Config.UPBIT_SECRET_KEY:
            raise RuntimeError(".env에 UPBIT_ACCESS_KEY/UPBIT_SECRET_KEY가 설정되어 있지 않습니다.")
        self._client = pyupbit.Upbit(Config.UPBIT_ACCESS_KEY, Config.UPBIT_SECRET_KEY)

    def _selected_currencies(self) -> set:
        # 'KRW-BTC' -> 'BTC'
        return {t.split("-", 1)[1] for t in Config.UPBIT_SELECTED_TICKERS if "-" in t}

    def get_balances_for(self, tickers: set) -> List[dict]:
        """KRW + 주어진 티커 집합(예: 대시보드에서 실거래 승인한 코인)에 해당하는 잔고만 필터링."""
        currencies = {t.split("-", 1)[1] for t in tickers if "-" in t}
        return [
            b for b in self.get_raw_balances()
            if b.get("currency") == "KRW" or b.get("currency") in currencies
        ]

    def get_current_price(self, ticker: str) -> Optional[float]:
        try:
            price = pyupbit.get_current_price(ticker)
            return float(price) if price else None
        except Exception as e:
            logger.error(f"[{ticker}] 시세 조회 실패: {e}")
            return None

    def get_raw_balances(self) -> List[dict]:
        """계좌 전체 잔고 원본(필터 없음). 실패 시 빈 리스트."""
        try:
            balances = self._client.get_balances()
            return balances or []
        except Exception as e:
            logger.error(f"업비트 실계좌 잔고 조회 실패: {e}")
            return []

    def get_selected_balances(self) -> List[dict]:
        """KRW + Config.UPBIT_SELECTED_TICKERS로 필터링한 잔고 원본. KRW는 선택 목록과 무관하게 항상 포함."""
        selected = self._selected_currencies()
        return [
            b for b in self.get_raw_balances()
            if b.get("currency") == "KRW" or b.get("currency") in selected
        ]

    def get_cash_balance(self) -> float:
        for b in self.get_raw_balances():
            if b.get("currency") == "KRW":
                return float(b.get("balance") or 0)
        return 0.0

    def get_positions(self) -> List[Position]:
        """실제 보유 코인 전부(수량>0) — 대시보드에서 승인했는지 여부와 무관하게 있는 그대로
        반환한다. "이 중 어떤 종목을 봇이 손절/익절 관리 대상으로 볼지"는 이 메서드가 아니라
        호출부(app/core/auto_trader.py의 _reconcile_live_positions)가 승인 이력으로 범위를
        좁혀서 결정한다 — 그래야 봇과 무관하게 보유 중인 다른 코인들이 실거래 스위치를 켜는
        순간 갑자기 자동 손절/익절 대상이 되는 일을 막을 수 있다."""
        positions = []
        for b in self.get_raw_balances():
            currency = b.get("currency")
            if currency == "KRW":
                continue
            qty = float(b.get("balance") or 0) + float(b.get("locked") or 0)
            if qty <= 0:
                continue
            positions.append(Position(
                ticker=f"{b.get('unit_currency', 'KRW')}-{currency}",
                qty=qty,
                avg_buy_price=float(b.get("avg_buy_price") or 0),
            ))
        return positions

    def _blocked_reason(self) -> Optional[str]:
        if not get_trade_engine_settings(self.broker_name, self.mode)['enabled']:
            return "실거래 스위치가 꺼져 있습니다 — 대시보드의 '실거래 실행' 스위치를 켜야 주문이 나갑니다."
        return None

    def _wait_for_fill(self, uuid: str, max_wait_sec: float = 8.0, poll_interval: float = 0.4) -> Optional[dict]:
        """시장가 주문은 거의 즉시 체결되지만 주문 접수 응답 시점엔 아직 체결 정보(trades)가
        안 실려 있을 수 있어, 상태가 'done'(또는 'cancel')이 될 때까지 최대 max_wait_sec 동안
        폴링한다. 시간 내 못 끝나면 마지막으로 조회된 상태를 그대로 반환한다(호출부는 trades가
        비어있으면 "체결가/체결량 확인 필요"로 처리 — 주문 자체는 이미 나간 상태이므로 실패로
        취급하지 않는다)."""
        deadline = time.time() + max_wait_sec
        order = None
        while time.time() < deadline:
            try:
                order = self._client.get_individual_order(uuid)
            except Exception as e:
                logger.error(f"주문 상태 조회 실패(uuid={uuid}): {e}")
                order = None
            if isinstance(order, dict) and order.get('state') in ('done', 'cancel'):
                return order
            time.sleep(poll_interval)
        return order

    @staticmethod
    def _extract_fill(order: Optional[dict]):
        """주문 조회 결과에서 (평균 체결가, 체결 수량)을 뽑는다. 미체결/조회 실패면 (None, None) —
        이 경우 주문 자체는 이미 나갔으므로 trade_order_log에는 amount_krw(BUY)/qty(SELL)만
        남고 price는 비어있게 된다(다음 사이클의 _reconcile_live_positions가 실제 잔고로 정정)."""
        if not isinstance(order, dict):
            return None, None
        trades = order.get('trades') or []
        try:
            executed_volume = float(order.get('executed_volume', 0) or 0)
        except (TypeError, ValueError):
            executed_volume = 0.0
        if executed_volume <= 0 or not trades:
            return None, None
        try:
            total_funds = sum(float(t.get('funds', 0) or 0) for t in trades)
        except (TypeError, ValueError):
            return None, None
        avg_price = total_funds / executed_volume if executed_volume else None
        return avg_price, executed_volume

    def buy_market(self, ticker: str, amount_krw: float, reason: str = "") -> OrderResult:
        blocked = self._blocked_reason()
        if blocked:
            logger.warning(f"[실거래 매수 차단] {ticker} — {blocked}")
            return OrderResult(False, ticker, 'BUY', message=blocked)
        if amount_krw < MIN_ORDER_KRW:
            message = f"주문금액 {amount_krw:,.0f}원 < 최소주문금액 {MIN_ORDER_KRW:,.0f}원"
            logger.warning(f"[실거래 매수 차단] {ticker} — {message}")
            return OrderResult(False, ticker, 'BUY', message=message)
        try:
            resp = self._client.buy_market_order(ticker, amount_krw)
        except Exception as e:
            logger.error(f"[{ticker}] 실매수 주문 실패: {e}")
            return OrderResult(False, ticker, 'BUY', message=str(e))
        if not isinstance(resp, dict) or 'uuid' not in resp:
            logger.error(f"[실매수] {ticker} 주문 거부: {resp}")
            return OrderResult(False, ticker, 'BUY', message=f"주문 거부됨: {resp}")

        filled = self._wait_for_fill(resp['uuid'])
        price, qty = self._extract_fill(filled)
        if not qty:
            logger.warning(f"[실매수] {ticker} 주문 접수됐지만 체결 확인 지연 — uuid={resp['uuid']} (다음 사이클에 실제 잔고로 반영됨)")
            return OrderResult(True, ticker, 'BUY', amount_krw=amount_krw, message=reason)

        logger.info(f"[실매수] {ticker} {qty:.8f}개 @ {price:,.0f}원 (총 {qty * price:,.0f}원) — {reason}")
        return OrderResult(True, ticker, 'BUY', price=price, qty=qty, amount_krw=qty * price, message=reason)

    def sell_market(self, ticker: str, qty: float, reason: str = "") -> OrderResult:
        blocked = self._blocked_reason()
        if blocked:
            logger.warning(f"[실거래 매도 차단] {ticker} — {blocked}")
            return OrderResult(False, ticker, 'SELL', message=blocked)
        price = self.get_current_price(ticker)
        if price and qty * price < MIN_ORDER_KRW:
            message = f"평가금액 {qty * price:,.0f}원 < 최소주문금액 {MIN_ORDER_KRW:,.0f}원"
            logger.warning(f"[실거래 매도 차단] {ticker} — {message}")
            return OrderResult(False, ticker, 'SELL', message=message)
        try:
            resp = self._client.sell_market_order(ticker, qty)
        except Exception as e:
            logger.error(f"[{ticker}] 실매도 주문 실패: {e}")
            return OrderResult(False, ticker, 'SELL', message=str(e))
        if not isinstance(resp, dict) or 'uuid' not in resp:
            logger.error(f"[실매도] {ticker} 주문 거부: {resp}")
            return OrderResult(False, ticker, 'SELL', message=f"주문 거부됨: {resp}")

        filled = self._wait_for_fill(resp['uuid'])
        fill_price, filled_qty = self._extract_fill(filled)
        if not filled_qty:
            logger.warning(f"[실매도] {ticker} 주문 접수됐지만 체결 확인 지연 — uuid={resp['uuid']} (다음 사이클에 실제 잔고로 반영됨)")
            return OrderResult(True, ticker, 'SELL', qty=qty, message=reason)

        logger.info(f"[실매도] {ticker} {filled_qty:.8f}개 @ {fill_price:,.0f}원 (총 {filled_qty * fill_price:,.0f}원) — {reason}")
        return OrderResult(True, ticker, 'SELL', price=fill_price, qty=filled_qty, amount_krw=filled_qty * fill_price, message=reason)


def print_selected_balance():
    """`python main.py live_balance`에서 사용 — 실주문 없이 KRW + 선택 코인 잔고만 콘솔에 출력한다."""
    broker = UpbitLiveBroker()
    balances = broker.get_selected_balances()
    if not balances:
        print("조회된 잔고가 없습니다 (API 키를 확인하거나, 선택한 코인을 보유하고 있는지 확인하세요).")
        return

    header = f"{'통화':<8}{'주문가능':>18}{'거래중(locked)':>18}{'평단가':>14}{'현재가':>14}{'평가금액':>16}"
    print(header)
    print("-" * len(header))
    for b in balances:
        currency = b.get('currency')
        available = float(b.get('balance') or 0)
        locked = float(b.get('locked') or 0)
        if currency == 'KRW':
            print(f"{currency:<8}{available:>18,.0f}{locked:>18,.0f}{'-':>14}{'-':>14}{'-':>16}")
            continue
        ticker = f"{b.get('unit_currency', 'KRW')}-{currency}"
        avg_price = float(b.get('avg_buy_price') or 0)
        current_price = broker.get_current_price(ticker)
        eval_amount = current_price * (available + locked) if current_price else None
        print(
            f"{currency:<8}{available:>18,.6f}{locked:>18,.6f}{avg_price:>14,.0f}"
            f"{(current_price or 0):>14,.0f}{(eval_amount or 0):>16,.0f}"
        )
