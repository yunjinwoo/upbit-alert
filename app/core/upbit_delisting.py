"""업비트 거래지원 종료(상장폐지) 예정 코인 / 투자유의 코인 — 실거래 표 라벨과 매수 차단용.

공식 Open API에는 거래지원 종료 일정이 없다. 종료가 확정되면 업비트 공지사항(거래 카테고리)에
"플레이댑(PDA) 거래지원 종료 안내 (3/25 14:00)" 같은 제목으로 올라오므로, 웹사이트가 쓰는 공지 목록
JSON(api-manager.upbit.com, 비공식)을 읽어 제목에서 티커와 종료 시각을 뽑는다. 투자유의 지정은 공식
API(/v1/market/all?is_details=true)의 market_event.warning으로 받는다(종료 전 단계 신호).

refresh_if_stale()을 실거래 사이클마다 부르면 30분에 한 번만 실제로 읽는다. 읽기에 실패하면 기존 목록을
유지한다(목록이 비어 매수 차단이 풀리지 않게).
"""
import re
from datetime import datetime, timedelta

import requests

from app.utils.logger import get_logger
from app.utils.db_manager import (
    get_upbit_market_alert_state,
    get_upbit_market_alerts,
    save_upbit_market_alerts,
)

logger = get_logger()

NOTICE_URL = 'https://api-manager.upbit.com/api/v1/announcements'
MARKET_ALL_URL = 'https://api.upbit.com/v1/market/all'
REFRESH_INTERVAL_MIN = 30
NOTICE_PAGES = 2            # 거래 카테고리 최근 40건 — 종료 공지는 보통 종료 1~2주 전에 올라온다
KEEP_AFTER_DELISTING_H = 24  # 종료 시각이 지나도 하루는 라벨을 남긴다(표에서 "왜 없어졌지?" 확인용)
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36',
    'Accept': 'application/json',
    'Origin': 'https://upbit.com',
    'Referer': 'https://upbit.com/',
}

_TICKER_RE = re.compile(r'\(([A-Z0-9]{1,15})\)')
_WHEN_RE = re.compile(r'\((\d{1,2})/(\d{1,2})\s*(\d{1,2}):(\d{2})\)')
# 종료 공지가 아니거나 종료가 취소된 공지 — 이런 제목은 건너뛴다
_CANCEL_RE = re.compile(r'철회|번복|취소|재개|유지')
# 특정 마켓만 종료되는 공지("BTC 마켓 거래지원 종료") — KRW 마켓이 언급되지 않으면 원화 거래엔 영향 없음
_OTHER_MARKET_RE = re.compile(r'(BTC|USDT)\s*마켓')


def _iter_notices(payload):
    """응답 구조가 바뀌어도 버티도록 {'title': ...}를 가진 dict를 전부 훑는다."""
    if isinstance(payload, dict):
        if isinstance(payload.get('title'), str):
            yield payload
        for v in payload.values():
            yield from _iter_notices(v)
    elif isinstance(payload, list):
        for v in payload:
            yield from _iter_notices(v)


def parse_delisting_notice(title: str, listed_at: str = None, now: datetime = None):
    """공지 제목 하나 → (['KRW-PDA', ...], 'YYYY-MM-DD HH:MM:SS' 또는 None). 종료 공지가 아니면 None."""
    if '거래지원 종료' not in title or _CANCEL_RE.search(title):
        return None
    if _OTHER_MARKET_RE.search(title) and 'KRW' not in title and '원화' not in title:
        return None
    tickers = [f'KRW-{sym}' for sym in dict.fromkeys(_TICKER_RE.findall(title))]
    if not tickers:
        return None
    delisting_at = None
    m = _WHEN_RE.search(title)
    if m:
        now = now or datetime.now()
        base = now
        if listed_at:
            try:
                base = datetime.fromisoformat(listed_at[:19])
            except ValueError:
                pass
        month, day, hour, minute = map(int, m.groups())
        year = base.year + (1 if month < base.month - 6 else 0)  # 12월 공지의 1월 종료
        try:
            delisting_at = datetime(year, month, day, hour, minute).strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            delisting_at = None
    return tickers, delisting_at


def fetch_delisting(now: datetime = None) -> dict:
    """{ticker: {delisting_at, notice_id, notice_title}} — 종료 시각이 KEEP_AFTER_DELISTING_H 넘게 지난 건 뺀다."""
    now = now or datetime.now()
    result = {}
    for page in range(1, NOTICE_PAGES + 1):
        res = requests.get(NOTICE_URL, params={'os': 'web', 'page': page, 'per_page': 20, 'category': 'trade'},
                           headers=HEADERS, timeout=10)
        res.raise_for_status()
        for notice in _iter_notices(res.json()):
            parsed = parse_delisting_notice(notice['title'], notice.get('listed_at'), now)
            if not parsed:
                continue
            tickers, delisting_at = parsed
            if delisting_at and datetime.strptime(delisting_at, '%Y-%m-%d %H:%M:%S') < now - timedelta(hours=KEEP_AFTER_DELISTING_H):
                continue
            for t in tickers:
                result.setdefault(t, {
                    'delisting_at': delisting_at,
                    'notice_id': str(notice.get('id') or ''),
                    'notice_title': notice['title'],
                })
    return result


def fetch_warning_tickers() -> set:
    """투자유의(market_event.warning 또는 옛 필드 market_warning=CAUTION)인 KRW 마켓 티커."""
    res = requests.get(MARKET_ALL_URL, params={'is_details': 'true'}, headers={'Accept': 'application/json'}, timeout=10)
    res.raise_for_status()
    out = set()
    for m in res.json():
        market = m.get('market', '')
        if not market.startswith('KRW-'):
            continue
        if (m.get('market_event') or {}).get('warning') or m.get('market_warning') == 'CAUTION':
            out.add(market)
    return out


def refresh(now: datetime = None) -> dict:
    """공지 + 투자유의를 읽어 저장한다. 공지 읽기가 실패하면 기존 목록을 유지하고 실패만 기록."""
    try:
        delisting = fetch_delisting(now)
    except Exception as e:
        logger.error(f"거래지원 종료 공지 조회 실패(기존 목록 유지): {e}")
        save_upbit_market_alerts(None, f'공지 조회 실패: {e}')
        return get_upbit_market_alerts()
    try:
        warning = fetch_warning_tickers()
    except Exception as e:
        logger.error(f"투자유의 종목 조회 실패(유의 라벨만 이전 값 유지): {e}")
        warning = {t for t, a in get_upbit_market_alerts().items() if a['warning']}
    alerts = {}
    for t in set(delisting) | warning:
        alerts[t] = {**delisting.get(t, {}), 'delisting': t in delisting, 'warning': t in warning}
    save_upbit_market_alerts(alerts)
    if delisting:
        summary = ', '.join('%s(%s)' % (t, a['delisting_at'] or '시각 미상') for t, a in delisting.items())
        logger.info(f"⛔ 거래지원 종료 예정: {summary}")
    return get_upbit_market_alerts()


def refresh_if_stale(max_age_min: int = REFRESH_INTERVAL_MIN) -> None:
    state = get_upbit_market_alert_state()
    if state and state.get('checked_at'):
        age = datetime.now() - datetime.strptime(state['checked_at'], '%Y-%m-%d %H:%M:%S')
        if age < timedelta(minutes=max_age_min):
            return
    refresh()


def delisting_tickers() -> set:
    """매수 차단 대상 — 거래지원 종료 예정 코인."""
    return {t for t, a in get_upbit_market_alerts().items() if a['delisting']}
