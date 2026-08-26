"""서버 자신의 아웃바운드(외부로 나가는) IP 조회.

업비트/토스 Open API 키의 "IP 허용 목록"에 등록해야 하는 건 이 서버가 실제로 그 API를 호출할 때
쓰는 발신 IP지, 대시보드를 열어본 방문자의 브라우저 IP(app/api/server.py의 _get_client_ip(),
접근 통제용으로 별도 유지)가 아니다 — 두 IP는 서로 다른 개념이고, 특히 서버가 방문자와 다른
네트워크(예: 클라우드 VPS)에 있으면 거의 항상 다르다. 방문자 IP를 이 용도로 잘못 보여주면
사용자가 엉뚱한 IP를 업비트에 등록하게 돼 no_authorization_ip 에러를 영원히 못 없앤다."""
import time
from typing import Optional

import requests

from app.utils.logger import get_logger

logger = get_logger()

# 하나가 막혀 있어도 다음 걸로 넘어가도록 여러 개를 순서대로 시도한다(전부 잘 알려진 무료 IP-echo
# 서비스 — 응답 본문이 IP 문자열 하나뿐이라 파싱이 간단함).
_IP_ECHO_URLS = [
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
]
_REQUEST_TIMEOUT_SEC = 3
_CACHE_TTL_SEC = 3600  # 아웃바운드 IP는 서버가 재배포/이전되지 않는 한 거의 안 바뀌므로 1시간 캐시

_cache = {'value': None, 'fetched_at': 0.0}


def _looks_like_ip(s: str) -> bool:
    s = (s or '').strip()
    parts = s.split('.')
    if len(parts) == 4:
        return all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)
    return ':' in s and 2 <= len(s) <= 45  # 대충 IPv6 형태만 걸러냄


def get_server_outbound_ip() -> Optional[str]:
    """서버의 아웃바운드 IP를 조회한다 — 실패해도 예외를 던지지 않고 None(또는 예전 캐시값)을
    반환한다(이 조회 하나 때문에 페이지 렌더링 자체가 깨지면 안 되므로). 1시간 캐시."""
    now = time.time()
    if _cache['value'] and now - _cache['fetched_at'] < _CACHE_TTL_SEC:
        return _cache['value']

    for url in _IP_ECHO_URLS:
        try:
            resp = requests.get(url, timeout=_REQUEST_TIMEOUT_SEC)
            ip = resp.text.strip()
            if ip and _looks_like_ip(ip):
                _cache['value'] = ip
                _cache['fetched_at'] = now
                return ip
        except Exception as e:
            logger.warning(f"서버 아웃바운드 IP 조회 실패({url}): {e}")
            continue

    return _cache['value']  # 전부 실패해도 예전에 성공한 값이 있으면 그거라도(아예 안 보여주는 것보단 나음)
