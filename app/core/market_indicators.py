"""자동매매 대시보드 상단의 "시장 지표" 카드용 조회 — 비트코인 RSI(4시간봉/일봉/주봉)와 BTC 도미넌스.

표시 전용이다 — 매매 루프는 이 값을 보지 않는다. 대시보드가 열려 있을 때만 서버가 요청 시점에
조회하고, 같은 값을 여러 번 부르지 않도록 짧게 캐시한다.

- BTC RSI: 업비트 KRW-BTC 캔들로 계산(app/core/exit_conditions.compute_rsi — 실매매 RSI와 같은 공식).
  차트에 지금 찍혀 있는 값과 맞추려고 진행 중인 봉까지 포함한다(include_current=True).
- BTC 도미넌스: 업비트 API에는 전체 암호화폐 시가총액이 없어서 CoinGecko 공개 API
  (/api/v3/global, 키 불필요)의 market_cap_percentage를 쓴다. 조회 실패 시 None으로 두고
  RSI 쪽은 그대로 보여준다(한쪽 소스 장애가 카드 전체를 깨지 않게).
"""
import time
from typing import Callable, Optional

import pandas as pd
import requests

from app.core.exit_conditions import compute_rsi
from app.utils.logger import get_logger

logger = get_logger()

BTC_TICKER = 'KRW-BTC'
RSI_PERIOD = 14
# (interval, 라벨) — pyupbit get_ohlcv interval 이름 그대로
RSI_TIMEFRAMES = [('minute240', '4시간봉'), ('day', '일봉'), ('week', '주봉')]
CANDLE_COUNT = 200  # 업비트 1회 조회 최대치 — EWM 평활 워밍업을 길게 줘서 차트 값에 가깝게

COINGECKO_GLOBAL_URL = 'https://api.coingecko.com/api/v3/global'
_REQUEST_TIMEOUT_SEC = 5

BTC_CACHE_TTL_SEC = 60        # RSI는 봉이 진행 중이라 계속 움직이므로 짧게
DOMINANCE_CACHE_TTL_SEC = 300  # CoinGecko 무료 API 호출 제한 대비(값도 천천히 변함)

_cache = {
    'btc': {'value': None, 'fetched_at': 0.0},
    'dominance': {'value': None, 'fetched_at': 0.0},
}


def calc_btc_indicators(get_candles_fn: Callable[[str, str, int], Optional[pd.DataFrame]]) -> dict:
    """KRW-BTC 현재가/24시간 등락률과 시간대별 RSI를 계산한다. get_candles_fn(ticker, interval, count)
    으로 캔들 조회를 주입받는다(테스트에서는 가짜 캔들을 넣는다)."""
    result = {'price': None, 'change_rate_24h': None, 'rsi': {}}
    for interval, label in RSI_TIMEFRAMES:
        entry = {'label': label, 'value': None, 'prev_closed': None}
        try:
            df = get_candles_fn(BTC_TICKER, interval, CANDLE_COUNT)
        except Exception as e:
            logger.error(f"[{BTC_TICKER}] {interval} 캔들 조회 실패: {e}")
            df = None
        if df is not None and len(df):
            now = compute_rsi(df, RSI_PERIOD, include_current=True)
            prev = compute_rsi(df, RSI_PERIOD)
            entry['value'] = round(now, 2) if now is not None else None
            entry['prev_closed'] = round(prev, 2) if prev is not None else None
            if interval == 'day' and len(df) >= 2:
                close_now = float(df['close'].iloc[-1])
                close_prev = float(df['close'].iloc[-2])
                result['price'] = close_now
                if close_prev:
                    result['change_rate_24h'] = round((close_now - close_prev) / close_prev * 100, 2)
        result['rsi'][interval] = entry
    if result['price'] is None and all(e['value'] is None for e in result['rsi'].values()):
        # 전부 실패했으면 빈 값을 캐시하지 않도록 예외로 올린다(_cached가 예전 값으로 대체).
        raise RuntimeError(f'{BTC_TICKER} 캔들을 하나도 받지 못했습니다')
    return result


def parse_coingecko_global(payload: dict) -> dict:
    """CoinGecko /api/v3/global 응답에서 도미넌스와 전체 시총 변화만 뽑는다."""
    data = (payload or {}).get('data') or {}
    pct = data.get('market_cap_percentage') or {}

    def _round(v):
        return round(float(v), 2) if v is not None else None

    return {
        'btc_dominance': _round(pct.get('btc')),
        'eth_dominance': _round(pct.get('eth')),
        'total_market_cap_usd': data.get('total_market_cap', {}).get('usd'),
        'total_market_cap_change_24h': _round(data.get('market_cap_change_percentage_24h_usd')),
        'source': 'CoinGecko',
    }


def _fetch_dominance() -> dict:
    resp = requests.get(COINGECKO_GLOBAL_URL, timeout=_REQUEST_TIMEOUT_SEC,
                        headers={'Accept': 'application/json'})
    resp.raise_for_status()
    return parse_coingecko_global(resp.json())


def _upbit_candles(ticker: str, interval: str, count: int):
    import pyupbit  # 서버 프로세스에서만 필요 — 순수 계산 함수 테스트가 pyupbit 없이 돌게 지연 임포트
    return pyupbit.get_ohlcv(ticker, interval=interval, count=count)


def _cached(key: str, ttl: float, fetch_fn):
    """캐시가 살아 있으면 그대로, 아니면 새로 조회. 조회 실패 시 예전 값(있으면)을 돌려주고
    stale=True로 표시한다 — 일시 장애 때문에 카드가 통째로 비지 않게."""
    slot = _cache[key]
    now = time.time()
    if slot['value'] is not None and now - slot['fetched_at'] < ttl:
        return slot['value'], slot['fetched_at'], False, None
    try:
        value = fetch_fn()
        slot['value'], slot['fetched_at'] = value, now
        return value, now, False, None
    except Exception as e:
        logger.error(f"시장 지표({key}) 조회 실패: {e}")
        return slot['value'], slot['fetched_at'], slot['value'] is not None, str(e)


def get_market_indicators() -> dict:
    """대시보드용 시장 지표 묶음. 각 블록은 독립적으로 실패할 수 있고, 실패하면 error에 사유를 담는다."""
    def _fmt(ts):
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts)) if ts else None

    btc, btc_at, btc_stale, btc_err = _cached(
        'btc', BTC_CACHE_TTL_SEC, lambda: calc_btc_indicators(_upbit_candles))
    dom, dom_at, dom_stale, dom_err = _cached('dominance', DOMINANCE_CACHE_TTL_SEC, _fetch_dominance)
    return {
        'btc': btc, 'btc_fetched_at': _fmt(btc_at), 'btc_stale': btc_stale, 'btc_error': btc_err,
        'dominance': dom, 'dominance_fetched_at': _fmt(dom_at), 'dominance_stale': dom_stale,
        'dominance_error': dom_err,
        'rsi_period': RSI_PERIOD,
    }
