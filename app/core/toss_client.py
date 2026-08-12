"""토스증권(Toss Invest) Open API 클라이언트 — 읽기 전용(시세/캔들/종목정보)만 구현한다.

https://developers.tossinvest.com/docs — OAuth2 Client Credentials Grant 인증, 시세는
GET /api/v1/prices, 캔들은 GET /api/v1/candles(interval: '1d'|'1m'만 지원, count 최대 200)를 쓴다.

**주문(POST /api/v1/orders) 등 실주문 관련 엔드포인트는 이 파일 어디에도 구현하지 않는다** —
app/core/brokers/paper_broker.py(업비트)와 동일한 안전 원칙: 시세만 실제로 조회하고, 매수/매도
체결은 전부 DB 가상 원장(app/core/brokers/toss_broker.py)에만 반영한다.

캔들 조회 결과는 app/core/upbit_market_analysis.py의 calc_indicators()/app/core/entry_conditions.py가
그대로 재사용할 수 있도록 pyupbit.get_ohlcv()와 같은 형태(open/high/low/close/volume 컬럼의
DataFrame, timestamp 오름차순, 마지막 행은 진행 중인 캔들)로 맞춰서 반환한다.

⚠️ Toss candles 응답이 pyupbit처럼 "마지막 행 = 진행 중인 캔들"인지는 문서만으로 확정할 수 없어
(OpenAPI 스펙에 명시 안 됨) pyupbit와 동일하다고 가정했다 — 실 자격증명 연결 후
`python -c "from app.core.toss_client import get_candles; print(get_candles('005930','1d',5))"`로
마지막 행이 오늘 진행 중인 캔들인지 한 번 확인해볼 것(docs/auto-trade-toss-paper.md 참고).
"""
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import requests

from app.config import Config
from app.utils.logger import get_logger

logger = get_logger()

# 토큰 캐시 — Toss는 KIS와 달리 expires_in(초)을 명시적으로 주므로, "오늘 하루 유효" 가정(KIS 패턴,
# app/core/stock_monitor.get_access_token)이 아니라 실제 만료시각 기반으로 캐시한다.
_token_cache = {"access_token": None, "expires_at": 0.0}

INTERVAL_MAP = {
    'day': '1d',
    'minute1': '1m',
}


def _get_access_token() -> Optional[str]:
    """OAuth2 Client Credentials 토큰 발급/캐시 (만료 60초 전 재발급)."""
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]

    if not Config.TOSS_CLIENT_ID or not Config.TOSS_CLIENT_SECRET:
        logger.error("TOSS_CLIENT_ID/TOSS_CLIENT_SECRET이 설정되지 않았습니다(.env 확인).")
        return None

    try:
        res = requests.post(
            f"{Config.TOSS_API_BASE}/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": Config.TOSS_CLIENT_ID,
                "client_secret": Config.TOSS_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        if res.status_code != 200:
            logger.error(f"토스 토큰 발급 실패: {res.status_code} - {res.text}")
            return None
        data = res.json()
        token = data.get("access_token")
        expires_in = data.get("expires_in", 3600)
        _token_cache["access_token"] = token
        _token_cache["expires_at"] = time.time() + expires_in
        return token
    except Exception as e:
        logger.error(f"토스 토큰 요청 중 에러: {e}")
        return None


def _get(path: str, params: dict = None) -> Optional[dict]:
    token = _get_access_token()
    if not token:
        return None
    try:
        res = requests.get(
            f"{Config.TOSS_API_BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params or {},
            timeout=10,
        )
        if res.status_code == 429:
            retry_after = res.headers.get("Retry-After", "1")
            logger.warning(f"토스 API rate limit — {retry_after}초 대기 후 재시도 필요(호출부에서 처리)")
            return None
        if res.status_code != 200:
            logger.error(f"토스 API 요청 실패 [{path}]: {res.status_code} - {res.text}")
            return None
        return res.json()
    except Exception as e:
        logger.error(f"토스 API 요청 중 에러 [{path}]: {e}")
        return None


def get_current_price(symbol: str) -> Optional[float]:
    """현재가 조회 (GET /api/v1/prices). 실패 시 None."""
    data = _get("/api/v1/prices", {"symbols": symbol})
    if not data:
        return None
    result = data.get("result") or []
    if not result:
        return None
    try:
        return float(result[0]["lastPrice"])
    except (KeyError, TypeError, ValueError):
        return None


def get_prices(symbols: list) -> dict:
    """여러 종목 현재가 일괄 조회 (최대 200개/요청). 반환: {symbol: price}. 조회 실패한 종목은 빠진다."""
    if not symbols:
        return {}
    data = _get("/api/v1/prices", {"symbols": ",".join(symbols)})
    if not data:
        return {}
    out = {}
    for item in data.get("result") or []:
        try:
            out[item["symbol"]] = float(item["lastPrice"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _candles_to_df(candles: list) -> pd.DataFrame:
    """API 응답의 candles 배열을 pyupbit.get_ohlcv()와 같은 형태(open/high/low/close/volume,
    timestamp 오름차순 인덱스)의 DataFrame으로 변환."""
    rows = []
    for c in candles:
        try:
            rows.append({
                'timestamp': pd.to_datetime(c['timestamp']),
                'open': float(c['openPrice']),
                'high': float(c['highPrice']),
                'low': float(c['lowPrice']),
                'close': float(c['closePrice']),
                'volume': float(c['volume']),
            })
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
    df = pd.DataFrame(rows).sort_values('timestamp').set_index('timestamp')
    return df


def get_candles(symbol: str, interval: str, count: int = 100, before: str = None) -> Optional[pd.DataFrame]:
    """캔들 조회 (GET /api/v1/candles). interval: '1d' 또는 '1m'만 지원(count는 1~200).
    반환은 timestamp 오름차순 DataFrame(open/high/low/close/volume) — 실패 시 None."""
    params = {"symbol": symbol, "interval": interval, "count": min(max(count, 1), 200)}
    if before:
        params["before"] = before
    data = _get("/api/v1/candles", params)
    if not data:
        return None
    result = data.get("result") or {}
    return _candles_to_df(result.get("candles") or [])


def get_daily_candles_extended(symbol: str, count: int = 260) -> Optional[pd.DataFrame]:
    """일봉을 count개(200 초과 가능)만큼 확보 — Toss candles API의 count 상한(200)을 넘으면
    `before` 페이지네이션으로 이어서 조회한다(MA200 계산에 200개 초과분이 필요해서 도입).
    coin_screening의 CANDLE_COUNT=230처럼 여유치를 둔 만큼 보통 2회 호출로 충분하다."""
    first = get_candles(symbol, '1d', count=min(count, 200))
    if first is None or first.empty:
        return first

    df = first
    remaining = count - len(df)
    guard = 0  # 무한 루프 방지(페이지네이션 응답이 예상과 다를 경우 대비)
    while remaining > 0 and guard < 5:
        oldest_ts = df.index[0]
        before_str = oldest_ts.isoformat()  # 타임존 오프셋 포함 ISO8601이어야 함(예: '+09:00') — 'before'는 실측으로 확인
        more = get_candles(symbol, '1d', count=min(remaining, 200), before=before_str)
        if more is None or more.empty:
            break
        more = more[more.index < oldest_ts]  # 겹치는 구간 제거
        if more.empty:
            break
        df = pd.concat([more, df])
        remaining = count - len(df)
        guard += 1
        time.sleep(0.1)
    return df


def get_candles_resampled(symbol: str, interval: str, count: int) -> Optional[pd.DataFrame]:
    """app/core/entry_conditions.py용 헬퍼 — CONDITION_SPECS가 쓰는 interval 이름('day'/'minute5'/
    'minute1')을 Toss가 실제 지원하는 '1d'/'1m'으로 매핑한다. Toss는 5분봉을 직접 제공하지 않아
    ('interval' enum이 1d/1m뿐) 1분봉을 넉넉히 받아 pandas resample('5min')으로 합성한다."""
    if interval == 'minute5':
        # 5분봉 count개를 만들려면 최소 count*5개의 1분봉이 필요 — 리샘플 경계 보정을 위해 여유를 둠
        raw = get_candles(symbol, '1m', count=min(count * 5 + 10, 200))
        if raw is None or raw.empty:
            return raw
        resampled = raw.resample('5min').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum',
        }).dropna()
        return resampled.tail(count)

    mapped = INTERVAL_MAP.get(interval)
    if not mapped:
        logger.error(f"지원하지 않는 interval: {interval}")
        return None
    if mapped == '1d' and count > 200:
        return get_daily_candles_extended(symbol, count=count)
    return get_candles(symbol, mapped, count=count)


def get_stock_universe(market: str) -> list:
    """마켓별 전체 종목 조회 (GET /api/v1/stocks/all) — 현재 스크리닝 유니버스는
    stock_market_cap_daily(KIS 시가총액 랭킹)를 재사용해 이 함수는 직접 쓰이지 않지만,
    추후 전체 KRX 스캔으로 확장할 때를 대비해 클라이언트에 포함해둔다."""
    data = _get("/api/v1/stocks/all", {"market": market})
    if not data:
        return []
    return data.get("result") or []
