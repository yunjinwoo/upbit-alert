"""업비트 과거 캔들 수집 — 백테스트 캐시를 채운다. **서버에서만 동작한다.**

개발용 원격 세션에서는 업비트 API가 egress 정책으로 막혀 있어(CONNECT 403) 이 파일은 거기서 돌지
않는다. 그래서 네트워크를 쓰는 코드는 전부 여기에만 두고, 시뮬레이션(engine.py)과 순위 계산
(market_data.py)은 캐시만 읽는 순수 계산으로 분리했다 — 그쪽은 목 데이터로 어디서든 검증된다.

호출량: KRW 마켓 약 280종목 × (1시간봉 90일 = 2,160봉 ÷ 200봉/요청 = 11요청) ≈ 3,100 요청.
업비트 시세 API는 초당 10회 제한이라 8회/초로 눌러 6~7분쯤 걸린다. 중간에 끊겨도 캐시에 들어간
만큼은 남고 다시 돌리면 빠진 구간만 채운다(upsert + 구간 확인).

pyupbit.get_ohlcv를 쓰지 않고 REST를 직접 부르는 이유: 과거로 거슬러 올라가는 페이징(to 파라미터)과
호출 간격을 직접 통제해야 하고, 순위 계산에 필요한 candle_acc_trade_price/prev_closing_price가
pyupbit의 DataFrame에는 그대로 실려오지 않기 때문이다.
"""
import time
from datetime import datetime, timezone, timedelta
from typing import Callable, List, Optional

import requests

from app.backtest import candle_store as store
from app.backtest.candle_store import INTERVAL_URLS, INTERVAL_SECONDS
from app.utils.logger import get_logger

logger = get_logger()

API_BASE = 'https://api.upbit.com'
MARKET_ALL_URL = f'{API_BASE}/v1/market/all'
MAX_COUNT = 200                  # 업비트가 한 번에 주는 최대 캔들 수
MIN_REQUEST_INTERVAL_SEC = 0.125  # 초당 8회 — 시세 API 제한(초당 10회) 아래로 여유를 둔다
REQUEST_TIMEOUT_SEC = 10
MAX_RETRY = 5

_last_request_at = 0.0


def _throttle() -> None:
    global _last_request_at
    wait = MIN_REQUEST_INTERVAL_SEC - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _get(url: str, params: dict) -> list:
    """시세 API 호출 1회 — 초당 제한(429)은 기다렸다 다시 시도한다.

    수천 번 호출하는 작업이라 한 번의 429로 전체가 죽으면 안 된다. 429는 "잠깐 쉬라"는 뜻이므로
    지수 백오프로 물러났다가 재시도하고, 그 외 오류도 일시적인 네트워크 문제일 수 있어 같은 방식으로
    몇 번 더 시도한 뒤에야 포기한다.
    """
    for attempt in range(MAX_RETRY):
        _throttle()
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SEC)
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == MAX_RETRY - 1:
                raise
            logger.warning(f"캔들 조회 재시도({attempt + 1}/{MAX_RETRY}) {params}: {e}")
            time.sleep(2 ** attempt)
    return []


def fetch_krw_markets() -> List[str]:
    """KRW 마켓 전 종목. 지금 상장돼 있는 종목만 나온다 — 과거에 급등하고 상장폐지된 종목은 여기
    없으므로 백테스트 결과에는 생존 편향이 남는다(docs/backtest.md에 적어 둔 한계)."""
    data = _get(MARKET_ALL_URL, {'isDetails': 'false'})
    return [m['market'] for m in data if str(m.get('market', '')).startswith('KRW-')]


def _to_param(ts: int) -> str:
    """업비트 to 파라미터(ISO8601 UTC, exclusive) — 이 시각 "직전" 캔들부터 준다."""
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')


def fetch_range(market: str, interval: str, start_ts: int, end_ts: int) -> List[dict]:
    """[start_ts, end_ts] 구간 캔들을 과거로 거슬러 페이징해 모은다(정규화된 형태로).

    업비트는 "to 이전 200개"만 주므로 최신 쪽에서 시작해 뒤로 간다. 같은 응답이 계속 오거나
    (더 이상 과거가 없는 신규 상장 종목) 빈 응답이 오면 멈춘다 — 안 그러면 무한 루프가 된다.
    """
    url = API_BASE + INTERVAL_URLS[interval]
    collected: List[dict] = []
    cursor = end_ts + INTERVAL_SECONDS[interval]  # 마지막 봉까지 포함되도록 한 칸 뒤에서 시작
    while cursor > start_ts:
        raw = _get(url, {'market': market, 'count': MAX_COUNT, 'to': _to_param(cursor)})
        if not raw:
            break
        page = [store.normalize_candle(c) for c in raw]
        page.sort(key=lambda c: c['ts'])
        collected.extend(c for c in page if c['ts'] >= start_ts)
        oldest = page[0]['ts']
        if oldest >= cursor:   # 더 못 내려감 — 상장 이전이거나 응답이 안 밀린다
            break
        cursor = oldest
    return collected


def collect(days: int = 90, interval: str = 'minutes60', markets: List[str] = None,
            force: bool = False, progress_fn: Callable[[int, int, str, int], None] = None,
            db_path: str = None) -> dict:
    """최근 days일치 캔들을 KRW 전 종목에 대해 캐시에 채운다.

    일봉은 항상 같이 받는다 — 분봉만으로는 "당일 상승률"의 기준인 전일 종가를 알 수 없기 때문이다
    (업비트는 prev_closing_price를 일봉에만 실어 준다). 순위 계산은 전일 종가가 있어야 성립한다.

    force가 아니면 이미 그 구간을 덮고 있는 종목은 건너뛴다 — 수집이 중간에 끊겼을 때 처음부터
    다시 받지 않기 위한 이어받기다.
    """
    store.init_db(db_path)
    now = int(time.time())
    start_ts = now - days * 86400
    # 전일 종가와 RSI 워밍업이 구간 첫날부터 필요하므로 일봉은 넉넉히 더 받아둔다
    daily_start_ts = start_ts - 30 * 86400

    targets = markets or fetch_krw_markets()
    total = len(targets)
    stats = {'markets': total, 'fetched': 0, 'skipped': 0, 'rows': 0, 'failed': []}
    logger.info(f"백테스트 캔들 수집 시작 — {total}종목, {interval}, 최근 {days}일")

    for i, market in enumerate(targets, 1):
        try:
            rows = 0
            for iv, s_ts in (('days', daily_start_ts), (interval, start_ts)):
                if iv == 'days' and interval == 'days':
                    s_ts = daily_start_ts   # 봉 종류가 일봉이면 한 번만 받는다
                have = store.market_range(market, iv, db_path)
                if not force and have and have[0] <= s_ts and have[1] >= now - 2 * INTERVAL_SECONDS[iv]:
                    continue
                candles = fetch_range(market, iv, s_ts, now)
                rows += store.upsert_candles(market, iv, candles, db_path)
                store.record_collect(market, iv, db_path)
                if interval == 'days':
                    break
            if rows:
                stats['fetched'] += 1
                stats['rows'] += rows
            else:
                stats['skipped'] += 1
            if progress_fn:
                progress_fn(i, total, market, rows)
        except Exception as e:
            logger.error(f"[{market}] 캔들 수집 실패: {e}")
            stats['failed'].append(market)

    logger.info(
        f"백테스트 캔들 수집 완료 — 신규/갱신 {stats['fetched']}종목, 건너뜀 {stats['skipped']}종목, "
        f"{stats['rows']:,}행, 실패 {len(stats['failed'])}종목"
    )
    return stats
