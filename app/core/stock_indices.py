"""주요 주식 지수(코스피 · 코스닥 · 나스닥 · S&P500) 현재값 — 시장 판단 슬랙 알림에 참고용으로 붙인다.

판정 점수에는 넣지 않는다(코인 시장 판단은 BTC와 업비트 원화마켓만 본다). 알림을 받았을 때 주식 시장
분위기도 같이 보려는 용도다.

조회는 KIS(한국투자증권) 시세 API를 쓴다 — 국내 업종지수와 같은 토큰(app/core/stock_monitor.get_access_token).
  - 국내: 업종 현재지수 FHPUP02100000 (0001 코스피, 1001 코스닥)
  - 해외: 해외주식 종목/지수/환율 기간별시세 FHKST03030100 (N 시장, COMP 나스닥 종합, SPX S&P500)
    해외 지수는 미국 장이 닫혀 있으면 직전 종가가 온다.
하나가 실패해도 나머지는 보여준다(실패한 지수는 '조회 실패'). 어떤 경우에도 예외를 밖으로 던지지 않는다 —
지수 조회 때문에 시장 판단 알림이 안 가면 안 되기 때문.
"""
from datetime import datetime, timedelta
from typing import Callable, Optional

import requests

from app.config import Config
from app.utils.logger import get_logger

logger = get_logger()

TIMEOUT_SEC = 5

# (표시 이름, 구분, 코드) — 구분: domestic(국내 업종지수) / overseas(해외 지수)
INDICES = [
    ('코스피', 'domestic', '0001'),
    ('코스닥', 'domestic', '1001'),
    ('나스닥', 'overseas', 'COMP'),
    ('S&P500', 'overseas', 'SPX'),
]


def _to_float(v) -> Optional[float]:
    try:
        return float(str(v).replace(',', ''))
    except (TypeError, ValueError):
        return None


def _headers(tr_id: str) -> Optional[dict]:
    from app.core import stock_monitor
    if stock_monitor.ACCESS_TOKEN is None:
        stock_monitor.get_access_token()
    if stock_monitor.ACCESS_TOKEN is None:
        return None
    return {
        'content-type': 'application/json',
        'authorization': f'Bearer {stock_monitor.ACCESS_TOKEN}',
        'appkey': Config.KIS_APP_KEY,
        'appsecret': Config.KIS_APP_SECRET,
        'tr_id': tr_id,
        'custtype': 'P',
    }


def _fetch_domestic(code: str) -> tuple:
    """(현재값, 전일 대비 %) — 국내 업종 현재지수."""
    headers = _headers('FHPUP02100000')
    if headers is None:
        raise RuntimeError('KIS 토큰 없음')
    res = requests.get(f'{Config.KIS_URL_BASE}/uapi/domestic-stock/v1/quotations/inquire-index-price',
                       headers=headers, params={'FID_COND_MRKT_DIV_CODE': 'U', 'FID_INPUT_ISCD': code},
                       timeout=TIMEOUT_SEC)
    data = res.json()
    if data.get('rt_cd') != '0':
        raise RuntimeError(data.get('msg1') or 'API 오류')
    out = data.get('output') or {}
    return _to_float(out.get('bstp_nmix_prpr')), _to_float(out.get('bstp_nmix_prdy_ctrt'))


def _fetch_overseas(code: str) -> tuple:
    """(현재값, 전일 대비 %) — 해외 지수. 기간은 최근 1주일(휴장일이 껴도 직전 값이 나오게)."""
    headers = _headers('FHKST03030100')
    if headers is None:
        raise RuntimeError('KIS 토큰 없음')
    today = datetime.now()
    params = {
        'FID_COND_MRKT_DIV_CODE': 'N',
        'FID_INPUT_ISCD': code,
        'FID_INPUT_DATE_1': (today - timedelta(days=7)).strftime('%Y%m%d'),
        'FID_INPUT_DATE_2': today.strftime('%Y%m%d'),
        'FID_PERIOD_DIV_CODE': 'D',
    }
    res = requests.get(f'{Config.KIS_URL_BASE}/uapi/overseas-price/v1/quotations/inquire-daily-chartprice',
                       headers=headers, params=params, timeout=TIMEOUT_SEC)
    data = res.json()
    if data.get('rt_cd') != '0':
        raise RuntimeError(data.get('msg1') or 'API 오류')
    out = data.get('output1') or {}
    return _to_float(out.get('ovrs_nmix_prpr')), _to_float(out.get('prdy_ctrt'))


def fetch_stock_indices(fetch_fn: Callable = None) -> list:
    """INDICES 순서대로 [{'name', 'value', 'change_pct', 'error'}]. fetch_fn(kind, code) -> (value, pct)는
    테스트용으로 바꿔 끼울 수 있다."""
    def _default(kind, code):
        return _fetch_domestic(code) if kind == 'domestic' else _fetch_overseas(code)

    fetch_fn = fetch_fn or _default
    rows = []
    for name, kind, code in INDICES:
        row = {'name': name, 'value': None, 'change_pct': None, 'error': None}
        try:
            row['value'], row['change_pct'] = fetch_fn(kind, code)
            if row['value'] is None:
                row['error'] = '값 없음'
        except Exception as e:
            row['error'] = str(e) or e.__class__.__name__
            logger.error(f'주식 지수 조회 실패 — {name}({code}): {row["error"]}')
        rows.append(row)
    return rows


def format_indices_line(rows: list) -> str:
    """슬랙 한 줄: '📈 주식 지수: 코스피 2,612.34 (+0.52%) · 코스닥 … · 나스닥 … · S&P500 …'"""
    parts = []
    for r in rows or []:
        if r.get('value') is None:
            parts.append(f"{r['name']} 조회 실패")
        elif r.get('change_pct') is None:
            parts.append(f"{r['name']} {r['value']:,.2f}")
        else:
            parts.append(f"{r['name']} {r['value']:,.2f} ({r['change_pct']:+.2f}%)")
    return '📈 주식 지수: ' + (' · '.join(parts) if parts else '조회 실패')


def get_stock_indices_line(fetch_fn: Callable = None) -> Optional[str]:
    """알림에 붙일 한 줄. 무엇이 실패해도 예외 없이 문자열(또는 None)을 돌려준다."""
    try:
        return format_indices_line(fetch_stock_indices(fetch_fn))
    except Exception as e:
        logger.error(f'주식 지수 줄 만들기 실패: {e}')
        return None
