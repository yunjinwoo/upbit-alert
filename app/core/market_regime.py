"""시장 판단(좋음 / 애매 / 나쁨) — 표시와 슬랙 알림 전용.

매매 루프는 이 판단을 보지 않는다. 국면에 따라 전략을 바꾸는 건 사람이 직접 한다 — 여기서는 "지금 시장이
어떤 상태로 보이는지"와 그 근거를 대시보드 시장 지표 카드에 띄우고, 판단이 바뀔 때 슬랙으로 알려준다.
설계와 기준은 docs/market-regime.md.

판정: 네 항목을 각각 +1 / 0 / -1로 매겨 더한다(기준값은 app/config.py의 MARKET_REGIME_*).
  1) BTC 추세        — 일봉 현재가가 20일선 위면 +1, 아래면 -1
  2) BTC 일봉 RSI     — 55 이상 +1, 45 이하 -1
  3) BTC 4시간봉 RSI  — 55 이상 +1, 45 이하 -1
  4) 시장 폭          — 업비트 원화마켓에서 오늘 오른 종목 비율 60% 이상 +1, 40% 이하 -1
합이 +2 이상이면 좋음, -2 이하면 나쁨, 그 사이는 애매. 단 BTC가 전일 대비 -5% 이상 빠졌으면 점수와
무관하게 나쁨이다(급락).

깜빡임 방지: 새 판정이 MARKET_REGIME_CONFIRM_COUNT번(기본 2번, 판정 주기 30분 → 약 1시간) 연속 나와야
확정 국면을 바꾸고 알림을 보낸다. 급락으로 나쁨이 된 경우만 기다리지 않고 바로 바꾼다.

BTC RSI와 현재가는 시장 지표 카드와 같은 계산(app/core/market_indicators.calc_btc_indicators)을 쓴다 —
카드에 보이는 숫자와 판정 근거가 어긋나지 않게. 도미넌스는 현재값만 있어 오르는지 내리는지 알 수 없으므로
판정에는 넣지 않고 기록(last_result)에만 남긴다.
"""
import time
from datetime import datetime
from typing import Callable, Optional

from app.config import Config
from app.utils.logger import get_logger

logger = get_logger()

REGIMES = {
    'good': {'label': '좋음', 'emoji': '🟢', 'strategy': '손절 짧게 · 수익 길게'},
    'neutral': {'label': '애매', 'emoji': '🟡', 'strategy': '당일 상승률 · 거래대금 위주'},
    'bad': {'label': '나쁨', 'emoji': '🔴', 'strategy': '빠른 손절 · 빠른 익절'},
}

TS_FORMAT = '%Y-%m-%d %H:%M:%S'


def regime_text(key: Optional[str]) -> str:
    r = REGIMES.get(key)
    return f"{r['emoji']} {r['label']}" if r else '—'


def _point(value, up, down) -> int:
    if value is None:
        return 0
    if value >= up:
        return 1
    if value <= down:
        return -1
    return 0


def score_regime(inputs: dict, cfg=Config) -> dict:
    """판정 재료(inputs)로 점수와 국면을 계산한다 — 순수 함수.

    inputs: price(BTC 현재가), ma20_day, rsi_day, rsi_4h, up_ratio(오른 종목 비율 %), change_rate_24h(전일 대비 %)
    값이 없는 항목(None)은 0점으로 두고 '데이터 없음'으로 표시한다. 값이 있는 항목이 MARKET_REGIME_MIN_ITEMS
    보다 적으면 regime=None(이번 판정 보류)을 돌려준다 — 조회 장애 때문에 애매로 잘못 바뀌는 걸 막기 위함."""
    price, ma20 = inputs.get('price'), inputs.get('ma20_day')
    rsi_day, rsi_4h = inputs.get('rsi_day'), inputs.get('rsi_4h')
    up_ratio, change = inputs.get('up_ratio'), inputs.get('change_rate_24h')

    items = []
    if price is not None and ma20:
        gap = (price - ma20) / ma20 * 100
        trend_point = 1 if price > ma20 else (-1 if price < ma20 else 0)
        items.append({'key': 'trend', 'label': 'BTC 20일선', 'point': trend_point,
                      'text': f"현재가가 20일선 {'위' if gap >= 0 else '아래'} ({gap:+.2f}%)"})
    else:
        items.append({'key': 'trend', 'label': 'BTC 20일선', 'point': 0, 'text': '데이터 없음', 'missing': True})

    for key, label, value in (('rsi_day', 'BTC 일봉 RSI', rsi_day), ('rsi_4h', 'BTC 4시간봉 RSI', rsi_4h)):
        if value is None:
            items.append({'key': key, 'label': label, 'point': 0, 'text': '데이터 없음', 'missing': True})
        else:
            items.append({'key': key, 'label': label,
                          'point': _point(value, cfg.MARKET_REGIME_RSI_UP, cfg.MARKET_REGIME_RSI_DOWN),
                          'text': f"{value:.1f}"})

    if up_ratio is None:
        items.append({'key': 'breadth', 'label': '상승 종목 비율', 'point': 0, 'text': '데이터 없음', 'missing': True})
    else:
        items.append({'key': 'breadth', 'label': '상승 종목 비율',
                      'point': _point(up_ratio, cfg.MARKET_REGIME_BREADTH_UP, cfg.MARKET_REGIME_BREADTH_DOWN),
                      'text': f"{up_ratio:.0f}%"})

    score = sum(i['point'] for i in items)
    available = sum(1 for i in items if not i.get('missing'))
    crash = change is not None and change <= -cfg.MARKET_REGIME_CRASH_PCT
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


def advance_state(state: Optional[dict], result: dict, now: datetime, confirm_count: int) -> tuple:
    """직전 상태(state)에 이번 판정(result)을 반영한 새 상태와, 확정 국면이 바뀌었으면 그 변경 내역을
    돌려준다 — 순수 함수. (new_state, change or None)

    - 처음(확정 국면 없음): 이번 판정을 그대로 확정하고 change를 돌려준다(from_regime=None, 시작 알림).
    - 이번 판정이 확정 국면과 같으면: 대기 중이던 전환을 취소한다.
    - 다르면: 같은 방향 판정이 연속 confirm_count번 쌓여야 확정. 급락(crash)은 바로 확정.
    - result['regime']이 None(데이터 부족)이면 상태는 판정 시각/결과만 갱신하고 대기 카운트는 그대로 둔다."""
    now_s = now.strftime(TS_FORMAT)
    state = dict(state or {})
    state['checked_at'] = now_s
    state['last_result'] = result
    regime = result.get('regime')
    confirmed = state.get('confirmed')

    if regime is None:
        return state, None

    def _confirm():
        state.update(confirmed=regime, confirmed_at=now_s, pending=None, pending_count=0)
        return state, {'changed_at': now_s, 'from_regime': confirmed, 'to_regime': regime,
                       'score': result.get('score'), 'detail': result}

    if confirmed is None:
        return _confirm()
    if regime == confirmed:
        state.update(pending=None, pending_count=0)
        return state, None
    if result.get('crash'):
        return _confirm()

    count = (state.get('pending_count') or 0) + 1 if state.get('pending') == regime else 1
    if count >= confirm_count:
        return _confirm()
    state.update(pending=regime, pending_count=count)
    return state, None


def format_change_message(change: dict, active_preset: str = None, preset_known: bool = False) -> str:
    """확정 국면이 바뀌었을 때 슬랙으로 보낼 문구. preset_known이면 지금 매매 설정에 적용 중인 전략 묶음
    (active_preset, 묶음과 다르면 None)을 같이 적는다 — 판단과 적용 중인 묶음이 다르면 바꿀지 고르라는 뜻."""
    detail = change.get('detail') or {}
    to_r = REGIMES[change['to_regime']]
    if change.get('from_regime'):
        head = f"📊 시장 판단 변경: {regime_text(change['from_regime'])} → {to_r['emoji']} *{to_r['label']}*"
    else:
        head = f"📊 시장 판단 시작: 현재 {to_r['emoji']} *{to_r['label']}*"
    score = detail.get('score')
    lines = [head + (f" (점수 {score:+d})" if score is not None else '')]
    if detail.get('crash'):
        lines.append(f"• ⚠️ BTC 전일 대비 {detail.get('change_rate_24h'):+.2f}% 급락 — 점수와 무관하게 나쁨")
    for item in detail.get('items') or []:
        lines.append(f"• {item['label']}: {item['text']} ({item['point']:+d})" if item['point'] else
                     f"• {item['label']}: {item['text']} (0)")
    lines.append(f"참고 전략: {to_r['strategy']} (자동 적용 안 함)")
    if preset_known:
        from app.core.strategy_presets import preset_text
        if active_preset == change['to_regime']:
            lines.append(f"지금 적용 중인 전략 묶음: {preset_text(active_preset)} — 판단과 같음")
        else:
            lines.append(f"지금 적용 중인 전략 묶음: {preset_text(active_preset)} — 바꾸려면 자동매매 화면 "
                         f"⚙️ 매매 기준 설정에서 {to_r['emoji']} {to_r['label']} 묶음을 적용하세요")
    return '\n'.join(lines)


# ── 조회(네트워크) + 루프 ─────────────────────────────────────────────────────────

def _fetch_up_ratio() -> Optional[float]:
    """업비트 원화마켓에서 전일 대비 오른 종목 비율(%). 실패하면 None."""
    from app.core.upbit_ranking import fetch_krw_tickers
    try:
        rows = [t for t in fetch_krw_tickers() if str(t.get('market', '')).startswith('KRW-')]
    except Exception as e:
        logger.error(f"시장 판단: 원화마켓 시세 조회 실패: {e}")
        return None
    if not rows:
        return None
    up = sum(1 for t in rows if float(t.get('signed_change_rate') or 0) > 0)
    return round(up / len(rows) * 100, 1)


def collect_inputs(get_candles_fn: Callable = None, up_ratio_fn: Callable = None) -> dict:
    """판정 재료를 모은다. BTC 쪽은 시장 지표 카드와 같은 계산을 쓴다."""
    from app.core.market_indicators import calc_btc_indicators, _upbit_candles
    inputs = {'price': None, 'ma20_day': None, 'rsi_day': None, 'rsi_4h': None,
              'change_rate_24h': None, 'up_ratio': None}
    try:
        btc = calc_btc_indicators(get_candles_fn or _upbit_candles)
        rsi = btc.get('rsi') or {}
        inputs.update(
            price=btc.get('price'), ma20_day=btc.get('ma20_day'), change_rate_24h=btc.get('change_rate_24h'),
            rsi_day=(rsi.get('day') or {}).get('value'), rsi_4h=(rsi.get('minute240') or {}).get('value'),
        )
    except Exception as e:
        logger.error(f"시장 판단: BTC 지표 조회 실패: {e}")
    inputs['up_ratio'] = (up_ratio_fn or _fetch_up_ratio)()
    return inputs


def _active_preset() -> tuple:
    """(적용 중인 묶음 key 또는 None, 조회 성공 여부). 조회에 실패해도 알림은 보내야 해서 예외를 삼킨다."""
    try:
        from app.core.strategy_presets import match_preset
        from app.utils.db_manager import get_trade_strategy_settings
        return match_preset(get_trade_strategy_settings()), True
    except Exception as e:
        logger.error(f"시장 판단: 적용 중인 전략 묶음 조회 실패: {e}")
        return None, False


def run_market_regime_check(inputs: dict = None, now: datetime = None, notify: Callable = None) -> dict:
    """1회 판정 → 상태 저장 → 확정 국면이 바뀌었으면 슬랙 알림. 새 상태와 변경 내역을 돌려준다."""
    from app.utils.db_manager import get_market_regime_state, save_market_regime_state
    if notify is None:
        from app.utils.slack import send_slack_msg as notify
    now = now or datetime.now()
    inputs = inputs if inputs is not None else collect_inputs()
    result = score_regime(inputs)
    result['inputs'] = inputs

    state, change = advance_state(get_market_regime_state(), result, now, Config.MARKET_REGIME_CONFIRM_COUNT)
    save_market_regime_state(state, change)

    if result['regime'] is None:
        logger.warning(f"시장 판단 보류 — 값이 있는 항목 {result['available']}개(최소 {Config.MARKET_REGIME_MIN_ITEMS})")
    else:
        logger.info(
            f"시장 판단: 이번 {regime_text(result['regime'])}(점수 {result['score']:+d}) / "
            f"확정 {regime_text(state.get('confirmed'))}"
            + (f" / {regime_text(state['pending'])} 전환 대기 {state['pending_count']}회" if state.get('pending') else '')
        )
    if change:
        try:
            notify(format_change_message(change, *_active_preset()))
        except Exception as e:
            logger.error(f"시장 판단 슬랙 알림 실패: {e}")
    return {'state': state, 'change': change}


def get_market_regime_snapshot() -> dict:
    """대시보드 시장 지표 카드용 — 저장된 상태 + 최근 변경 이력. 판정 루프가 안 돌았으면 state=None."""
    from app.utils.db_manager import get_market_regime_state, get_market_regime_history
    return {
        'state': get_market_regime_state(),
        'history': get_market_regime_history(5),
        'regimes': REGIMES,
        'confirm_count': Config.MARKET_REGIME_CONFIRM_COUNT,
        'check_interval_sec': Config.MARKET_REGIME_CHECK_INTERVAL_SEC,
    }


def run_market_regime_loop(interval_sec: int = None):
    interval_sec = interval_sec or Config.MARKET_REGIME_CHECK_INTERVAL_SEC
    logger.info(f"시장 판단 루프 시작 — {interval_sec}초마다 판정")
    while True:
        try:
            run_market_regime_check()
        except Exception as e:
            logger.error(f"시장 판단 루프 오류: {e}")
        time.sleep(interval_sec)
