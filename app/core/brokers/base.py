"""거래소/증권사 브로커 공통 인터페이스.

자동매매 엔진(app/core/auto_trader.py)이 어떤 거래소인지 몰라도 매매를 실행할 수 있도록
BrokerClient 추상 인터페이스를 둔다. 지금은 업비트 모의매매(PaperBroker)만 구현돼 있지만,
향후 KIS(국내주식)·토스증권 실거래 브로커도 이 인터페이스만 구현하면 auto_trader.py를
그대로 재사용할 수 있다.

dataclass 스타일은 app/core/kis_models.py의 요청/응답 모델 관례를 따른다.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


class TradeCycleBusyError(Exception):
    """run_trade_cycle()이 같은 (broker, mode)에 대해 이미 다른 실행자(백그라운드 루프 또는 다른
    수동 요청)가 진행 중이라 실행을 건너뛸 때 던진다 — app/utils/db_manager.py의
    try_acquire_trade_cycle_lock() 참고. 백그라운드 루프는 이걸 일반 예외처럼 로그만 남기고 다음
    사이클로 넘어가면 되고, 대시보드 API(app/api/server.py)는 이걸 따로 잡아서 409로 응답해
    "지금 실행 중"이라는 걸 사용자에게 명확히 알려준다."""
    pass


@dataclass
class Position:
    """보유 포지션 1건."""
    ticker: str
    qty: float
    avg_buy_price: float


@dataclass
class OrderResult:
    """매수/매도 실행 결과 (모의/실거래 공통)."""
    success: bool
    ticker: str
    side: str  # 'BUY' / 'SELL'
    price: Optional[float] = None
    qty: Optional[float] = None
    amount_krw: Optional[float] = None
    message: str = ""


class BrokerClient(ABC):
    """거래소/증권사 공통 인터페이스. 구현체는 broker_name/mode를 반드시 지정해야 한다.

    broker_name: 'upbit' | 'kis' | 'toss' (향후 확장)
    mode: 'paper'(모의매매) | 'live'(실거래) — DB 기록 시 데이터 구분용
    """
    broker_name: str
    mode: str

    @abstractmethod
    def get_current_price(self, ticker: str) -> Optional[float]:
        """현재가 조회. 실패 시 None."""
        ...

    @abstractmethod
    def get_cash_balance(self) -> float:
        """매매 가능한 현금(KRW) 잔고."""
        ...

    @abstractmethod
    def get_positions(self) -> List[Position]:
        """보유 포지션 전체 조회."""
        ...

    @abstractmethod
    def buy_market(self, ticker: str, amount_krw: float, reason: str = "") -> OrderResult:
        """시장가 매수. amount_krw만큼 매수를 시도한다."""
        ...

    @abstractmethod
    def sell_market(self, ticker: str, qty: float, reason: str = "") -> OrderResult:
        """시장가 매도. qty만큼 매도를 시도한다."""
        ...
