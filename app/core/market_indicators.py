"""자동매매 대시보드 상단의 "시장 지표" 카드용 조회 — 비트코인 RSI(4시간봉/일봉/주봉)와 BTC 도미넌스.

표시 전용이다 — 매매 루프는 이 값을 보지 않는다. 대시보드가 열려 있을 때만 서버가 요청 시점에
조회하고, 같은 값을 여러 번 부르지 않도록 짧게 캐시한다.

- BTC RSI: 업비트 KRW-BTC 캔들로 계산(app/core/exit_conditions.compute_rsi — 실매매 RSI와 같은 공식).
  차트에 지금 찍혀 있는 값과 맞추려고 진행 중인 봉까지 포함한다(include_current=True).
- BTC 도미넌스: 업비트 API에는 전체 암호화폐 시가총액이 없어서 CoinGecko 공개 API
  (/api/v3/global, 키 불필요)의 market_cap_percentage를 쓴다. 조회 실패 시 None으로 두고
  RSI 쪽은 그대로 보여준다(한쪽 소스 장애가 카드 전체를 깨지 않게).

외부 API 호출 제한 대응(2026-11 /monitor 로그에 429·타임아웃이 하루 수십 건 찍히던 문제):
- 업비트 시세 API는 같은 서버(IP)의 다른 프로세스(수집·매매 봇)와 초당 호출 한도를 나눠 쓴다. 그래서
  BTC 캔들 3종(4시간봉/일봉/주봉)을 0.04초 간격으로 몰아 부르면 다른 프로세스와 겹치는 순간 429가 났다.
  → 캔들 요청 사이에 최소 간격을 두고, 429/5xx는 잠깐 쉬었다 다시 시도한다.
- 봉 종류별로 마지막 성공 캔들을 따로 들고 있다가, 다시 받기에 실패하면 그 값을 쓴다(주봉 하나 실패로
  주봉 RSI가 비지 않게). 주봉은 천천히 변하므로 더 길게 캐시해서 호출 자체를 줄인다.
- CoinGecko 무료 API는 응답이 5초를 넘는 일이 잦아 읽기 제한시간을 늘리고 한 번 더 시도한다.
- 실패 직후 짧은 시간은 다시 부르지 않고 이전 값을 돌려준다(화면 주기 호출이 실패를 연달아 찍지 않게).
- 이전 값으로 대신 보여줄 수 있는 실패는 WARNING, 보여줄 값이 아예 없을 때만 ERROR로 남긴다.
"""
import threading
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
MA_DAY_PERIOD = 20  # BTC 일봉 이동평균 기간 — 시장 판단의 추세 항목
CANDLE_COUNT = 200  # 업비트 1회 조회 최대치 — EWM 평활 워밍업을 길게 줘서 차트 값에 가깝게

COINGECKO_GLOBAL_URL = 'https://api.coingecko.com/api/v3/global'
_REQUEST_TIMEOUT_SEC = 5
# CoinGecko는 연결은 빨라도 응답 본문이 5초를 넘는 일이 잦다 → (연결, 읽기) 따로, 읽기는 넉넉히
_COINGECKO_TIMEOUT = (5, 12)
# 업비트 캔들 REST — pyupbit.get_ohlcv는 내부 requests.get에 timeout이 없어서 연결이 멈추면 요청이
# 영원히 걸린다(화면에서 market-indicators가 계속 "보류 중"으로 남던 원인). 그래서 직접 부르고 제한시간을 건다.
UPBIT_CANDLE_URLS = {
    'minute240': 'https://api.upbit.com/v1/candles/minutes/240',
    'day': 'https://api.upbit.com/v1/candles/days',
    'week': 'https://api.upbit.com/v1/candles/weeks',
}

BTC_CACHE_TTL_SEC = 60        # RSI는 봉이 진행 중이라 계속 움직이므로 짧게
DOMINANCE_CACHE_TTL_SEC = 300  # CoinGecko 무료 API 호출 제한 대비(값도 천천히 변함)
# 실패 직후 이 시간 동안은 외부 API를 다시 부르지 않고 이전 값(또는 마지막 오류)을 돌려준다
FAILURE_COOLDOWN_SEC = {'btc': 20, 'dominance': 60}

# 봉 종류별 캔들 캐시 — 주봉은 일주일에 한 번 닫히므로 진행 중 봉이 조금 늦게 반영돼도 RSI 차이가 미미하다
CANDLE_CACHE_TTL_SEC = {'minute240': 55, 'day': 55, 'week': 600}
# 다시 받기에 실패했을 때 이전 캔들을 대신 써도 되는 최대 나이(이보다 오래되면 실패로 본다)
CANDLE_FALLBACK_MAX_AGE_SEC = 3600
# 업비트 캔들 요청 사이 최소 간격 — 업비트 시세 API는 IP 단위 초당 제한이라 같은 서버의 다른 봇과 나눠 쓴다
UPBIT_MIN_INTERVAL_SEC = 0.25
UPBIT_RETRY_DELAYS_SEC = (0.5, 1.5)  # 429/5xx 재시도 대기(시도 횟수 = 1 + len)
COINGECKO_RETRY_DELAYS_SEC = (2.0,)
_RETRY_STATUS = {429, 500, 502, 503, 504}

_cache = {
    'btc': {'value': None, 'fetched_at': 0.0, 'failed_at': 0.0, 'error': None},
    'dominance': {'value': None, 'fetched_at': 0.0, 'failed_at': 0.0, 'error': None},
}
# 키별 조회 잠금 — 캐시가 비었을 때 탭 여러 개/주기 호출이 겹쳐도 외부 API는 한 번만 부르고,
# 뒤에 온 요청은 그 결과(캐시)를 받는다.
_locks = {key: threading.Lock() for key in _cache}

# interval → {'df': DataFrame, 'fetched_at': float}
_candle_cache = {}
_candle_lock = threading.Lock()
_upbit_pace_lock = threading.Lock()
_upbit_last_call = [0.0]


def calc_btc_indicators(get_candles_fn: Callable[[str, str, int], Optional[pd.DataFrame]]) -> dict:
    """KRW-BTC 현재가/24시간 등락률과 시간대별 RSI를 계산한다. get_candles_fn(ticker, interval, count)
    으로 캔들 조회를 주입받는다(테스트에서는 가짜 캔들을 넣는다)."""
    result = {'price': None, 'change_rate_24h': None, 'ma20_day': None, 'rsi': {}}
    for interval, label in RSI_TIMEFRAMES:
        entry = {'label': label, 'value': None, 'prev_closed': None}
        try:
            df = get_candles_fn(BTC_TICKER, interval, CANDLE_COUNT)
        except Exception as e:
            # 다른 봉은 받았을 수 있어 카드가 통째로 비지는 않는다 → WARNING. 전부 실패하면 아래에서 예외로 올린다.
            logger.warning(f"[{BTC_TICKER}] {interval} 캔들 조회 실패: {e}")
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
            if interval == 'day' and len(df) >= MA_DAY_PERIOD:
                # 시장 판단(app/core/market_regime.py)의 "BTC 추세" 항목 — 현재가와 같은 기준이 되도록
                # 진행 중인 오늘 봉까지 넣은 20일 평균
                result['ma20_day'] = round(float(df['close'].iloc[-MA_DAY_PERIOD:].mean()), 2)
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


def _get_with_retry(url: str, delays, before: Callable = None, **kwargs):
    """requests.get + 429/5xx·타임아웃·연결 오류 재시도. 마지막 시도도 실패하면 그 예외를 그대로 올린다.
    429에 Retry-After가 오면(초 단위) 그만큼 기다리되 10초를 넘기지 않는다 — 화면 요청이 오래 묶이지 않게."""
    attempts = len(delays) + 1
    for i in range(attempts):
        last = i == attempts - 1
        if before:
            before()
        try:
            resp = requests.get(url, **kwargs)
        except (requests.Timeout, requests.ConnectionError):
            if last:
                raise
            time.sleep(delays[i])
            continue
        status = getattr(resp, 'status_code', 200)
        if status in _RETRY_STATUS and not last:
            wait = delays[i]
            retry_after = (getattr(resp, 'headers', None) or {}).get('Retry-After')
            try:
                wait = max(wait, min(float(retry_after), 10.0)) if retry_after else wait
            except (TypeError, ValueError):
                pass
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp


def _fetch_dominance() -> dict:
    resp = _get_with_retry(COINGECKO_GLOBAL_URL, COINGECKO_RETRY_DELAYS_SEC, timeout=_COINGECKO_TIMEOUT,
                           headers={'Accept': 'application/json'})
    return parse_coingecko_global(resp.json())


def parse_upbit_candles(rows: list) -> Optional[pd.DataFrame]:
    """업비트 캔들 응답(최신 봉이 앞)을 pyupbit.get_ohlcv와 같은 모양(과거→최신, open/high/low/close/
    volume/value 컬럼)의 DataFrame으로 바꾼다."""
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df['candle_date_time_kst'])
    df = df.rename(columns={'opening_price': 'open', 'high_price': 'high', 'low_price': 'low',
                            'trade_price': 'close', 'candle_acc_trade_volume': 'volume',
                            'candle_acc_trade_price': 'value'})
    return df[['open', 'high', 'low', 'close', 'volume', 'value']].sort_index()


def _upbit_pace():
    """직전 업비트 캔들 요청과 UPBIT_MIN_INTERVAL_SEC 이상 떨어뜨린다(프로세스 안에서만 유효)."""
    with _upbit_pace_lock:
        wait = _upbit_last_call[0] + UPBIT_MIN_INTERVAL_SEC - time.time()
        if wait > 0:
            time.sleep(wait)
        _upbit_last_call[0] = time.time()


def _upbit_candles_raw(ticker: str, interval: str, count: int):
    resp = _get_with_retry(UPBIT_CANDLE_URLS[interval], UPBIT_RETRY_DELAYS_SEC, before=_upbit_pace,
                           params={'market': ticker, 'count': count},
                           timeout=_REQUEST_TIMEOUT_SEC, headers={'Accept': 'application/json'})
    return parse_upbit_candles(resp.json())


def _upbit_candles(ticker: str, interval: str, count: int):
    """KRW-BTC 캔들(봉 종류별 캐시). 새로 받기에 실패하면 CANDLE_FALLBACK_MAX_AGE_SEC 안의 이전 캔들을
    돌려주고 WARNING만 남긴다 — 일시적인 429 때문에 RSI 칸이 비거나 ERROR가 쌓이지 않게."""
    key = (ticker, interval, count)
    with _candle_lock:
        now = time.time()
        slot = _candle_cache.get(key)
        if slot and now - slot['fetched_at'] < CANDLE_CACHE_TTL_SEC.get(interval, 55):
            return slot['df']
        try:
            df = _upbit_candles_raw(ticker, interval, count)
        except Exception as e:
            if slot and now - slot['fetched_at'] < CANDLE_FALLBACK_MAX_AGE_SEC:
                age = int(now - slot['fetched_at'])
                logger.warning(f"[{ticker}] {interval} 캔들 재조회 실패 → {age}초 전 값 사용: {e}")
                return slot['df']
            raise
        if df is not None and len(df):
            _candle_cache[key] = {'df': df, 'fetched_at': now}
        return df


def _cached(key: str, ttl: float, fetch_fn):
    """캐시가 살아 있으면 그대로, 아니면 새로 조회. 조회 실패 시 예전 값(있으면)을 돌려주고
    stale=True로 표시한다 — 일시 장애 때문에 카드가 통째로 비지 않게.
    실패 직후 FAILURE_COOLDOWN_SEC 동안은 다시 부르지 않는다(화면 주기 호출이 실패를 연달아 만들지 않게)."""
    slot = _cache[key]
    with _locks[key]:
        now = time.time()
        if slot['value'] is not None and now - slot['fetched_at'] < ttl:
            return slot['value'], slot['fetched_at'], False, None
        if slot.get('error') and now - slot.get('failed_at', 0.0) < FAILURE_COOLDOWN_SEC.get(key, 0):
            return slot['value'], slot['fetched_at'], slot['value'] is not None, slot['error']
        try:
            value = fetch_fn()
            slot['value'], slot['fetched_at'] = value, now
            slot['error'], slot['failed_at'] = None, 0.0
            return value, now, False, None
        except Exception as e:
            slot['error'], slot['failed_at'] = str(e), now
            if slot['value'] is not None:
                logger.warning(f"시장 지표({key}) 조회 실패 → 이전 값 표시: {e}")
            else:
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
