"""업비트 실계좌 현금 잔고 조회 — 읽기 전용, 참고용.

주의: 이 파일은 잔고 조회(GET /v1/accounts) 한 가지만 한다. buy_market/sell_market 등
실주문 메서드는 이 파일 어디에도 없고, 만들 계획도 없다(자동매매는 여전히
PaperBroker가 DB 가상 원장으로만 시뮬레이션 — app/core/brokers/paper_broker.py 참고).
대시보드(/auto-trade)의 "실제 업비트 현금 잔고" 카드용으로만 쓰인다.
"""
from typing import Optional

import pyupbit

from app.config import Config
from app.utils.logger import get_logger

logger = get_logger()


def get_real_krw_balance() -> Optional[float]:
    """실계좌 KRW(원화) 현금 잔고를 조회한다.

    .env에 UPBIT_ACCESS_KEY/UPBIT_SECRET_KEY가 없으면 None(대시보드는 카드를 숨김).
    조회 실패(키 오류/IP 미허용/네트워크 등)도 예외를 던지지 않고 None을 반환한다 —
    대시보드 요약 API 전체가 이 조회 하나 때문에 실패하면 안 되므로.

    pyupbit.Upbit.get_balance()를 바로 쓰지 않고 get_balances()를 직접 호출하는 이유:
    IP 미허용 등 업비트 API 에러는 HTTP 200 + {"error": {...}} 형태로 오는데, get_balance()는
    이 dict를 잔고 리스트인 것처럼 순회하다 TypeError를 던지고 그 원인 메시지를 버린다.
    여기서는 error 여부를 먼저 확인해 원인을 로그에 남긴다(예: "IP 등록 필요" 안내용)."""
    if not Config.UPBIT_ACCESS_KEY or not Config.UPBIT_SECRET_KEY:
        return None
    try:
        upbit = pyupbit.Upbit(Config.UPBIT_ACCESS_KEY, Config.UPBIT_SECRET_KEY)
        balances = upbit.get_balances()

        if isinstance(balances, dict) and 'error' in balances:
            err = balances['error']
            logger.error(
                f"업비트 실계좌 잔고 조회 실패: {err.get('name')} - {err.get('message')} "
                f"(업비트 Open API 발급 화면에서 이 서버의 아웃바운드 IP를 허용 목록에 등록했는지 확인)"
            )
            return None

        for item in balances:
            if item.get('currency') == 'KRW':
                return float(item.get('balance', 0))
        return 0.0
    except Exception as e:
        logger.error(f"업비트 실계좌 현금 잔고 조회 실패: {e}")
        return None
