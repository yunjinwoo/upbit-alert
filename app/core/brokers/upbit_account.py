"""업비트 실계좌 현금 잔고 조회 — 읽기 전용, 참고용.

주의: 이 파일은 잔고 조회(GET /v1/accounts) 한 가지만 한다. buy_market/sell_market 등
실주문 메서드는 이 파일 어디에도 없고, 만들 계획도 없다(자동매매는 여전히
PaperBroker가 DB 가상 원장으로만 시뮬레이션 — app/core/brokers/paper_broker.py 참고).
대시보드(/auto-trade)의 "실제 업비트 현금 잔고" 카드용으로만 쓰인다.
"""
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Optional

import pyupbit

from app.config import Config
from app.utils.logger import get_logger

logger = get_logger()

# pyupbit.Upbit.get_balances()는 자체적으로 요청 타임아웃을 받지 않는다(내부적으로 requests를 쓰지만
# timeout 파라미터를 노출하지 않음) — 업비트 API가 느려지거나 응답 없이 멈추면 이 호출 하나가
# get_dashboard_summary() 전체를 그만큼 오래 붙잡는다(대시보드가 통째로 안 뜨는 사고로 이어짐).
# 별도 스레드에서 돌리고 결과를 기다리는 시간에만 상한을 둬서, 시간 내 안 끝나면 그 스레드는 백그라운드에
# 남겨둔 채(어차피 곧 끝나거나 프로세스가 회수) 호출부는 제때 None을 돌려받게 한다.
_BALANCE_FETCH_TIMEOUT_SEC = 5
_balance_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="upbit-balance")

# get_real_krw_balance()는 get_dashboard_summary()를 통해 대시보드가 로드/새로고침될 때마다 호출되는데,
# 프론트엔드는 DCA 체크박스/워치리스트 토글 등 이 잔고와 무관한 액션 후에도 매번 전체 요약을 다시
# 불러온다 — 그때마다 참고용 카드 하나 갱신하려고 업비트에 서명된 API 호출을 새로 보내는 건 낭비고,
# 레이트리밋 소모도 불필요하게 늘린다. 실제 주문 판단에는 이 값을 쓰지 않으므로(참고용 카드 전용),
# 초 단위로 살짝 지연돼도 문제없다 — 짧은 TTL 캐시로 중복 호출만 줄인다.
_BALANCE_CACHE_TTL_SEC = 20
_balance_cache: dict = {'value': None, 'fetched_at': 0.0}


def get_real_krw_balance() -> Optional[float]:
    """실계좌 KRW(원화) 현금 잔고를 조회한다(짧은 TTL로 캐시됨 — 위 주석 참고).

    .env에 UPBIT_ACCESS_KEY/UPBIT_SECRET_KEY가 없으면 None(대시보드는 카드를 숨김).
    조회 실패(키 오류/IP 미허용/네트워크/타임아웃 등)도 예외를 던지지 않고 None을 반환한다 —
    대시보드 요약 API 전체가 이 조회 하나 때문에 실패하거나 오래 멈추면 안 되므로.

    pyupbit.Upbit.get_balance()를 바로 쓰지 않고 get_balances()를 직접 호출하는 이유:
    IP 미허용 등 업비트 API 에러는 HTTP 200 + {"error": {...}} 형태로 오는데, get_balance()는
    이 dict를 잔고 리스트인 것처럼 순회하다 TypeError를 던지고 그 원인 메시지를 버린다.
    여기서는 error 여부를 먼저 확인해 원인을 로그에 남긴다(예: "IP 등록 필요" 안내용)."""
    if not Config.UPBIT_ACCESS_KEY or not Config.UPBIT_SECRET_KEY:
        return None

    now = time.time()
    if now - _balance_cache['fetched_at'] < _BALANCE_CACHE_TTL_SEC:
        return _balance_cache['value']

    try:
        upbit = pyupbit.Upbit(Config.UPBIT_ACCESS_KEY, Config.UPBIT_SECRET_KEY)
        try:
            balances = _balance_executor.submit(upbit.get_balances).result(timeout=_BALANCE_FETCH_TIMEOUT_SEC)
        except FutureTimeoutError:
            logger.error(f"업비트 실계좌 잔고 조회 타임아웃({_BALANCE_FETCH_TIMEOUT_SEC}초 초과)")
            return _balance_cache['value']  # 캐시된 이전 값이라도 있으면 그걸로(완전 실패보다 낫다)

        if isinstance(balances, dict) and 'error' in balances:
            err = balances['error']
            logger.error(
                f"업비트 실계좌 잔고 조회 실패: {err.get('name')} - {err.get('message')} "
                f"(업비트 Open API 발급 화면에서 이 서버의 아웃바운드 IP를 허용 목록에 등록했는지 확인)"
            )
            return None

        result = 0.0
        for item in balances:
            if item.get('currency') == 'KRW':
                result = float(item.get('balance', 0))
                break
        _balance_cache['value'] = result
        _balance_cache['fetched_at'] = now
        return result
    except Exception as e:
        logger.error(f"업비트 실계좌 현금 잔고 조회 실패: {e}")
        return None
