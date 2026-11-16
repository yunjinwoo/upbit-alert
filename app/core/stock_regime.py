"""주식 시장 판단(좋음 / 애매 / 나쁨) — 코인 시장 판단(app/core/market_regime.py)과 같은 방식, 표시와 슬랙 알림 전용.

매매 루프는 이 판단을 보지 않는다. 판단이 바뀌면 슬랙으로 알리고, 업종 일자별지수 화면 '현재 상황'에 보여준다.

판정: 네 항목을 각각 +1 / 0 / -1로 매겨 더한다(BTC 판단과 같은 기준값 MARKET_REGIME_*).
  1) 코스피 20일선    — 현재 지수가 20일 평균(오늘 포함 최근 20거래일 종가) 위면 +1, 아래면 -1
  2) 코스피 일봉 RSI  — 55 이상 +1, 45 이하 -1
  3) 코스닥 일봉 RSI  — 55 이상 +1, 45 이하 -1   (BTC의 4시간봉 RSI 자리 — 국내 지수는 4시간봉이 없어 코스닥으로)
  4) 상승 종목 비율   — 코스피+코스닥 전체에서 오늘 오른 종목 비율 60% 이상 +1, 40% 이하 -1
합이 +2 이상이면 좋음, -2 이하면 나쁨, 그 사이는 애매. 코스피가 전일 대비 -3%(STOCK_REGIME_CRASH_PCT) 이상
빠졌으면 점수와 무관하게 나쁨(급락). 깜빡임 방지(연속 2회 확인)와 상태 전이는 코인 판단과 같은 함수(advance_state).

조회: KIS 업종 일자별지수(FHPUP02120000, 최근 거래일 종가 → 20일선·RSI)와 업종 현재지수(FHPUP02100000,
현재값·등락률·상승/하락/보합 종목 수). 평일 장 시간(09:00~15:40)에만 판정한다 — 장이 닫히면 값이 안 바뀌고,
밤새 30분마다 KIS를 부를 이유가 없다. market-regime-bot(코인 판단 루프)이 같은 주기로 함께 돌린다.
"""
from datetime import datetime
from typing import Callable, Optional

import requests

from app.config import Config
from app.core.market_regime import REGIMES, regime_text, advance_state, _point
from app.utils.logger import get_logger

logger = get_logger()

RSI_PERIOD = 14
MA_PERIOD = 20
TIMEOUT_SEC = 5
KOSPI, KOSDAQ = '0001', '1001'


def _to_float(v) -> Optional[float]:
    try:
        return float(str(v).replace(',', ''))
    except (TypeError, ValueError):
        return None


# ── 판정(순수 함수) ─────────────────────────────────────────────────────────────

def score_stock_regime(inputs: dict, cfg=Config) -> dict:
    """inputs: kospi(현재값), kospi_ma20, kospi_rsi, kosdaq_rsi, up_ratio(%), kospi_change(전일 대비 %).
    모양은 market_regime.score_regime 결과와 같다(items/score/regime/crash/available)."""
    price, ma20 = inputs.get('kospi'), inputs.get('kospi_ma20')
    change = inputs.get('kospi_change')
    items = []
    if price is not None and ma20:
        gap = (price - ma20) / ma20 * 100
        items.append({'key': 'trend', 'label': '코스피 20일선',
                      'point': 1 if price > ma20 else (-1 if price < ma20 else 0),
                      'text': f"현재 지수가 20일선 {'위' if gap >= 0 else '아래'} ({gap:+.2f}%)"})
    else:
        items.append({'key': 'trend', 'label': '코스피 20일선', 'point': 0, 'text': '데이터 없음', 'missing': True})

    for key, label in (('kospi_rsi', '코스피 일봉 RSI'), ('kosdaq_rsi', '코스닥 일봉 RSI')):
        value = inputs.get(key)
        if value is None:
            items.append({'key': key, 'label': label, 'point': 0, 'text': '데이터 없음', 'missing': True})
        else:
            items.append({'key': key, 'label': label,
                          'point': _point(value, cfg.MARKET_REGIME_RSI_UP, cfg.MARKET_REGIME_RSI_DOWN),
                          'text': f"{value:.1f}"})

    up_ratio = inputs.get('up_ratio')
    if up_ratio is None:
        items.append({'key': 'breadth', 'label': '상승 종목 비율', 'point': 0, 'text': '데이터 없음', 'missing': True})
    else:
        items.append({'key': 'breadth', 'label': '상승 종목 비율',
                      'point': _point(up_ratio, cfg.MARKET_REGIME_BREADTH_UP, cfg.MARKET_REGIME_BREADTH_DOWN),
                      'text': f"{up_ratio:.0f}%"})

    score = sum(i['point'] for i in items)
    available = sum(1 for i in items if not i.get('missing'))
    crash = change is not None and change <= -cfg.STOCK_REGIME_CRASH_PCT
    threshold = cfg.MARKET_REGIME_SCORE_THRESHOLD
    if crash:
        regime = 'bad'
    elif available < cfg.MARKET_REGIME_MIN_ITEMS:
        regime = None
    elif score >= threshold:
        regime = 'good'
    elif score <= -threshold:
        regime = 'bad'
    else:
        regime = 'neutral'
    return {'regime': regime, 'score': score, 'crash': crash, 'available': available,
            'change_rate_24h': change, 'items': items}


def calc_ma_rsi(closes: list) -> tuple:
    """종가(오래된 → 최신, 마지막이 오늘)로 (20일선, RSI14). 모자라면 None."""
    closes = [c for c in closes if c is not None]
    ma20 = round(sum(closes[-MA_PERIOD:]) / MA_PERIOD, 2) if len(closes) >= MA_PERIOD else None
    rsi = None
    if closes:
        import pandas as pd
        from app.core.exit_conditions import compute_rsi
        value = compute_rsi(pd.DataFrame({'close': closes}), RSI_PERIOD, include_current=True)
        rsi = round(float(value), 1) if value is not None else None
    return ma20, rsi


def is_market_hours(now: datetime, cfg=Config) -> bool:
    if now.weekday() >= 5:
        return False
    hm = now.strftime('%H:%M')
    return cfg.STOCK_REGIME_MARKET_OPEN <= hm <= cfg.STOCK_REGIME_MARKET_CLOSE


def format_stock_change_message(change: dict) -> str:
    detail = change.get('detail') or {}
    to_r = REGIMES[change['to_regime']]
    if change.get('from_regime'):
        head = f"📊 주식 시장 판단 변경: {regime_text(change['from_regime'])} → {to_r['emoji']} *{to_r['label']}*"
    else:
        head = f"📊 주식 시장 판단 시작: 현재 {to_r['emoji']} *{to_r['label']}*"
    score = detail.get('score')
    lines = [head + (f" (점수 {score:+d})" if score is not None else '')]
    if detail.get('crash'):
        lines.append(f"• ⚠️ 코스피 전일 대비 {detail.get('change_rate_24h'):+.2f}% 급락 — 점수와 무관하게 나쁨")
    for item in detail.get('items') or []:
        lines.append(f"• {item['label']}: {item['text']} ({item['point']:+d})" if item['point'] else
                     f"• {item['label']}: {item['text']} (0)")
    inputs = detail.get('inputs') or {}
    idx = []
    for name, v, c in (('코스피', inputs.get('kospi'), inputs.get('kospi_change')),
                       ('코스닥', inputs.get('kosdaq'), inputs.get('kosdaq_change'))):
        if v is not None:
            idx.append(f"{name} {v:,.2f}" + (f" ({c:+.2f}%)" if c is not None else ''))
    if idx:
        lines.append('📈 ' + ' · '.join(idx))
    lines.append(f"참고 전략: {to_r['strategy']} (자동 적용 안 함)")
    return '\n'.join(lines)


# ── 조회(네트워크) ──────────────────────────────────────────────────────────────

def _fetch_index_now(code: str) -> dict:
    """업종 현재지수 — 현재값/등락률/상승·하락·보합 종목 수."""
    from app.core.stock_indices import _headers
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
    return {
        'value': _to_float(out.get('bstp_nmix_prpr')),
        'change': _to_float(out.get('bstp_nmix_prdy_ctrt')),
        'up': _to_float(out.get('ascn_issu_cnt')),
        'down': _to_float(out.get('down_issu_cnt')),
        'flat': _to_float(out.get('stnr_issu_cnt')),
    }


def _fetch_daily_closes(code: str) -> list:
    """업종 일자별지수(최신순으로 옴) → [(YYYYMMDD, 종가)] 오래된 순."""
    from app.core.stock_monitor import fetch_sector_index_daily
    records = fetch_sector_index_daily(iscd=code) or []
    rows = sorted((r for r in records if r.get('stck_bsop_date')), key=lambda r: r['stck_bsop_date'])
    return [(r['stck_bsop_date'], _to_float(r.get('bstp_nmix_prpr'))) for r in rows]


def merge_today(daily: list, current: Optional[float], today: str) -> list:
    """일자별 종가에 오늘 현재값을 반영한 종가 리스트. 마지막 행이 오늘이면 그 값을 현재값으로 바꾸고,
    아니면(장 시작 직후 일자별에 오늘 행이 아직 없으면) 뒤에 붙인다."""
    closes = [c for _, c in daily]
    if current is None:
        return closes
    if daily and daily[-1][0] == today:
        return closes[:-1] + [current]
    return closes + [current]


def collect_stock_inputs(now_fn: Callable = None, closes_fn: Callable = None, today: str = None) -> dict:
    """판정 재료. 조회가 하나 실패해도 나머지로 판정한다(빈 항목은 '데이터 없음')."""
    now_fn = now_fn or _fetch_index_now
    closes_fn = closes_fn or _fetch_daily_closes
    inputs = {'kospi': None, 'kospi_change': None, 'kospi_ma20': None, 'kospi_rsi': None,
              'kosdaq': None, 'kosdaq_change': None, 'kosdaq_rsi': None, 'up_ratio': None}
    today = today or datetime.now().strftime('%Y%m%d')
    up = total = 0
    breadth_ok = True
    for code, prefix in ((KOSPI, 'kospi'), (KOSDAQ, 'kosdaq')):
        try:
            now = now_fn(code)
            inputs[prefix], inputs[f'{prefix}_change'] = now.get('value'), now.get('change')
            if None in (now.get('up'), now.get('down'), now.get('flat')):
                breadth_ok = False
            else:
                up += now['up']
                total += now['up'] + now['down'] + now['flat']
        except Exception as e:
            breadth_ok = False
            logger.error(f"주식 시장 판단: {prefix} 현재지수 조회 실패: {e}")
        try:
            ma20, rsi = calc_ma_rsi(merge_today(closes_fn(code), inputs[prefix], today))
            inputs[f'{prefix}_rsi'] = rsi
            if prefix == 'kospi':
                inputs['kospi_ma20'] = ma20
        except Exception as e:
            logger.error(f"주식 시장 판단: {prefix} 일자별지수 조회 실패: {e}")
    if breadth_ok and total > 0:
        inputs['up_ratio'] = round(up / total * 100, 1)
    return inputs


# ── 1회 판정 + 루프에서 부르는 진입점 ───────────────────────────────────────────

def run_stock_regime_check(inputs: dict = None, now: datetime = None, notify: Callable = None,
                           force: bool = False) -> Optional[dict]:
    """장 시간이면 1회 판정 → 상태 저장 → 확정 국면이 바뀌었으면 슬랙 알림. 장 밖이면 None(아무것도 안 함)."""
    from app.utils.db_manager import get_stock_regime_state, save_stock_regime_state
    now = now or datetime.now()
    if not force and inputs is None and not is_market_hours(now):
        return None
    if notify is None:
        from app.utils.slack import send_slack_msg as notify
    inputs = inputs if inputs is not None else collect_stock_inputs()
    result = score_stock_regime(inputs)
    result['inputs'] = inputs

    state, change = advance_state(get_stock_regime_state(), result, now, Config.MARKET_REGIME_CONFIRM_COUNT)
    save_stock_regime_state(state, change)

    if result['regime'] is None:
        logger.warning(f"주식 시장 판단 보류 — 값이 있는 항목 {result['available']}개")
    else:
        logger.info(f"주식 시장 판단: 이번 {regime_text(result['regime'])}(점수 {result['score']:+d}) / "
                    f"확정 {regime_text(state.get('confirmed'))}")
    if change:
        try:
            notify(format_stock_change_message(change))
        except Exception as e:
            logger.error(f"주식 시장 판단 슬랙 알림 실패: {e}")
    return {'state': state, 'change': change}


def get_stock_regime_snapshot() -> dict:
    """화면용 — 저장된 상태 + 최근 변경 이력. 판정이 한 번도 안 돌았으면 state=None."""
    from app.utils.db_manager import get_stock_regime_state, get_stock_regime_history
    return {
        'state': get_stock_regime_state(),
        'history': get_stock_regime_history(5),
        'regimes': REGIMES,
        'confirm_count': Config.MARKET_REGIME_CONFIRM_COUNT,
        'market_hours': f"{Config.STOCK_REGIME_MARKET_OPEN}~{Config.STOCK_REGIME_MARKET_CLOSE}",
    }
